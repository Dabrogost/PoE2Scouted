from __future__ import annotations

import io
import re
from typing import Annotated, Any

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel
from rapidfuzz import fuzz

from matcher import match_ocr_lines, normalize_text
from ocr import OCREngineError, extract_text_lines
from poe2scout import Poe2ScoutError, fetch_item_data, load_item_snapshot


DEFAULT_REALM = "poe2"
DEFAULT_LEAGUE = "Runes of Aldur"
DEFAULT_MIN_SCORE = 80
MAX_IMAGES = 4

app = FastAPI(title="PoE 2 Screenshot Price Checker")


class PriceResponse(BaseModel):
    realm: str
    league: str
    image_count: int
    item_count: int
    divine_exchange_rate_exalted: float | None
    divine_icon_url: str | None
    exalted_icon_url: str | None
    price_data_source: str
    price_data_age_seconds: float | None
    lexicon_item_count: int
    ocr_lines: list[str]
    results: list[dict[str, Any]]


class ItemSuggestion(BaseModel):
    text: str
    category: str | None
    price: float | None
    icon_url: str | None
    score: float


class SuggestionResponse(BaseModel):
    realm: str
    league: str
    query: str
    item_count: int
    price_data_source: str
    price_data_age_seconds: float | None
    suggestions: list[ItemSuggestion]


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return HTML


@app.post("/api/price", response_model=PriceResponse)
async def price_screenshot(
    image: Annotated[list[UploadFile], File()],
    realm: Annotated[str, Form()] = DEFAULT_REALM,
    league: Annotated[str, Form()] = DEFAULT_LEAGUE,
    min_score: Annotated[int, Form(ge=1, le=100)] = DEFAULT_MIN_SCORE,
) -> PriceResponse:
    realm = realm.strip() or DEFAULT_REALM
    league = league.strip() or DEFAULT_LEAGUE

    if not image:
        raise HTTPException(status_code=400, detail="Upload at least one image.")
    if len(image) > MAX_IMAGES:
        raise HTTPException(status_code=400, detail=f"Upload no more than {MAX_IMAGES} images.")

    image_payloads: list[bytes] = []
    for upload in image:
        if not upload.content_type or not upload.content_type.startswith("image/"):
            raise HTTPException(status_code=400, detail="Upload image files only.")

        image_bytes = await upload.read()
        if not image_bytes:
            raise HTTPException(status_code=400, detail="One uploaded image was empty.")

        try:
            with Image.open(io.BytesIO(image_bytes)) as uploaded_image:
                uploaded_image.verify()
        except (UnidentifiedImageError, OSError) as exc:
            raise HTTPException(status_code=400, detail="One uploaded file is not a readable image.") from exc

        image_payloads.append(image_bytes)

    try:
        ocr_lines: list[str] = []
        for image_bytes in image_payloads:
            ocr_lines.extend(extract_text_lines(image_bytes))
        return price_text_lines(
            ocr_lines,
            realm=realm,
            league=league,
            min_score=min_score,
            image_count=len(image_payloads),
        )
    except OCREngineError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Poe2ScoutError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/search", response_model=PriceResponse)
async def search_item(
    query: Annotated[str, Form()],
    realm: Annotated[str, Form()] = DEFAULT_REALM,
    league: Annotated[str, Form()] = DEFAULT_LEAGUE,
    min_score: Annotated[int, Form(ge=1, le=100)] = DEFAULT_MIN_SCORE,
) -> PriceResponse:
    realm = realm.strip() or DEFAULT_REALM
    league = league.strip() or DEFAULT_LEAGUE
    query = query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="Enter an item name to search.")

    try:
        item_data = fetch_item_data(realm=realm, league=league)
        snapshot_items = load_item_snapshot(realm=realm, league=league)
        resolved_query = resolve_manual_query(query, item_data.items, snapshot_items) or query
        return price_text_lines(
            [resolved_query],
            realm=realm,
            league=league,
            min_score=min_score,
            image_count=0,
            item_data=item_data,
            snapshot_items=snapshot_items,
        )
    except Poe2ScoutError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/suggestions", response_model=SuggestionResponse)
def item_suggestions(
    query: Annotated[str, Query(min_length=1)],
    realm: str = DEFAULT_REALM,
    league: str = DEFAULT_LEAGUE,
    limit: Annotated[int, Query(ge=1, le=20)] = 10,
) -> SuggestionResponse:
    realm = realm.strip() or DEFAULT_REALM
    league = league.strip() or DEFAULT_LEAGUE
    query = query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="Enter an item name to search.")

    try:
        item_data = fetch_item_data(realm=realm, league=league)
        snapshot_items = load_item_snapshot(realm=realm, league=league)
    except Poe2ScoutError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return SuggestionResponse(
        realm=realm,
        league=league,
        query=query,
        item_count=len(item_data.items),
        price_data_source=item_data.source,
        price_data_age_seconds=item_data.age_seconds,
        suggestions=search_item_suggestions(query, item_data.items, snapshot_items, limit=limit),
    )


def price_text_lines(
    lines: list[str],
    *,
    realm: str,
    league: str,
    min_score: int,
    image_count: int,
    item_data: Any | None = None,
    snapshot_items: list[dict[str, Any]] | None = None,
) -> PriceResponse:
    item_data = item_data or fetch_item_data(realm=realm, league=league)
    items = item_data.items
    if snapshot_items is None:
        snapshot_items = load_item_snapshot(realm=realm, league=league)
    results = match_ocr_lines(lines, items, lexicon_items=snapshot_items, min_score=min_score)
    market_info = currency_market_info(items)

    return PriceResponse(
        realm=realm,
        league=league,
        image_count=image_count,
        item_count=len(items),
        divine_exchange_rate_exalted=market_info["divine_exchange_rate_exalted"],
        divine_icon_url=market_info["divine_icon_url"],
        exalted_icon_url=market_info["exalted_icon_url"],
        price_data_source=item_data.source,
        price_data_age_seconds=item_data.age_seconds,
        lexicon_item_count=len(snapshot_items) if snapshot_items else len(items),
        ocr_lines=lines,
        results=results,
    )


def resolve_manual_query(query: str, items: list[dict[str, Any]], snapshot_items: list[dict[str, Any]]) -> str | None:
    exact_key = normalize_text(query)
    names = searchable_item_names(items, snapshot_items)
    if exact_key in names:
        return names[exact_key][0]

    suggestions = search_item_suggestions(query, items, snapshot_items, limit=1)
    if not suggestions:
        return None

    best = suggestions[0]
    return best.text if best.score >= 70 else None


def search_item_suggestions(
    query: str,
    items: list[dict[str, Any]],
    snapshot_items: list[dict[str, Any]],
    *,
    limit: int = 10,
) -> list[ItemSuggestion]:
    query_key = normalize_text(query)
    if not query_key:
        return []

    scored: list[tuple[float, str, dict[str, Any]]] = []
    for item_key, (name, item) in searchable_item_names(items, snapshot_items).items():
        score = suggestion_score(query_key, item_key)
        if score >= 70:
            scored.append((score, name, item))

    scored.sort(key=lambda item: (-item[0], item[1]))
    return [
        ItemSuggestion(
            text=name,
            category=_field(item, "category_api_id", "CategoryApiId"),
            price=_price_to_float(_field(item, "current_price", "CurrentPrice")),
            icon_url=_icon_url(item),
            score=score,
        )
        for score, name, item in scored[:limit]
    ]


def searchable_item_names(
    items: list[dict[str, Any]],
    snapshot_items: list[dict[str, Any]],
) -> dict[str, tuple[str, dict[str, Any]]]:
    names: dict[str, tuple[str, dict[str, Any]]] = {}
    for item in [*snapshot_items, *items]:
        name = item_search_name(item)
        key = normalize_text(name)
        if key:
            names[key] = (name, item)

    return names


def item_search_name(item: dict[str, Any]) -> str:
    text = _field(item, "text", "Text")
    name = _field(item, "name", "Name")
    item_type = _field(item, "type", "Type")

    if isinstance(text, str) and text.strip():
        return text.strip()
    if isinstance(name, str) and isinstance(item_type, str):
        return f"{name} {item_type}".strip()
    if isinstance(name, str) and name.strip():
        return name.strip()
    if isinstance(item_type, str) and item_type.strip():
        return item_type.strip()

    return ""


def suggestion_score(query_key: str, item_key: str) -> float:
    query_tokens = query_key.split()
    item_tokens = item_key.split()
    if not query_tokens or not item_tokens:
        return 0.0

    if query_key == item_key:
        return 120.0
    if item_key.startswith(query_key):
        return 115.0 - min(len(item_key) - len(query_key), 20) * 0.1
    if any(token.startswith(query_key) for token in item_tokens):
        return 110.0
    if all(any(token.startswith(query_token) for token in item_tokens) for query_token in query_tokens):
        return 105.0
    if query_key in item_key:
        return 95.0

    token_scores = [
        max(fuzz.ratio(query_token, item_token) for item_token in item_tokens)
        for query_token in query_tokens
    ]
    if token_scores and min(token_scores) >= 82:
        return 82.0 + min(token_scores) * 0.1

    fuzzy_score = float(fuzz.WRatio(query_key, item_key))
    return fuzzy_score if len(query_key) >= 6 and fuzzy_score >= 85 else 0.0


def currency_market_info(items: list[dict[str, Any]]) -> dict[str, Any]:
    divine = currency_item(items, "divine", "Divine Orb")
    exalted = currency_item(items, "exalted", "Exalted Orb")

    return {
        "divine_exchange_rate_exalted": _price_to_float(_field(divine, "current_price", "CurrentPrice"))
        if divine
        else None,
        "divine_icon_url": _icon_url(divine),
        "exalted_icon_url": _icon_url(exalted),
    }


def currency_item(items: list[dict[str, Any]], api_id: str, text: str) -> dict[str, Any] | None:
    for item in items:
        item_api_id = _field(item, "api_id", "ApiId")
        item_text = _field(item, "text", "Text")
        if item_api_id == api_id or item_text == text:
            return item

    return None


def _icon_url(item: dict[str, Any] | None) -> str | None:
    icon_url = _field(item, "icon_url", "IconUrl") if item is not None else None
    if not isinstance(icon_url, str) or not icon_url:
        return None

    return re.sub(r"^(https?://[^/]+)/+", r"\1/", icon_url)


def _price_to_float(price: Any) -> float | None:
    if price is None or price == "":
        return None

    try:
        return float(price)
    except (TypeError, ValueError):
        return None


def _field(item: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in item:
            return item[key]
    return None


HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>PoE 2 Screenshot Price Checker</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #0f1116;
      --panel: #161a21;
      --panel-2: #1d232c;
      --panel-3: #12161d;
      --line: #2b333f;
      --line-strong: #46505e;
      --text: #eef3f8;
      --muted: #9ba5b3;
      --accent: #d6b15f;
      --accent-soft: #2b261a;
      --danger: #f28b82;
      --ok: #72d6a0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }

    * { box-sizing: border-box; }

    body {
      margin: 0;
      min-height: 100vh;
      background: var(--bg);
      color: var(--text);
      overflow-x: hidden;
    }

    main {
      width: min(1180px, calc(100vw - 40px));
      margin: 0 auto;
      padding: 30px 0 42px;
    }

    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 18px;
      margin-bottom: 22px;
    }

    header > div { min-width: 0; }

    h1 {
      margin: 0;
      font-size: 30px;
      line-height: 1.1;
      letter-spacing: 0;
    }

    .muted {
      color: var(--muted);
      font-size: 14px;
    }

    .controls {
      display: grid;
      grid-template-columns: minmax(140px, 1fr) minmax(260px, 1.35fr) minmax(130px, 1fr) auto auto;
      gap: 10px;
      align-items: end;
      margin-bottom: 14px;
      padding: 14px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel-3);
    }

    label {
      display: grid;
      gap: 6px;
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }

    input[type="text"], input[type="number"], select {
      width: 100%;
      height: 40px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #0f131a;
      color: var(--text);
      padding: 0 11px;
      font-size: 14px;
    }

    select {
      cursor: pointer;
    }

    input[type="text"]:focus, input[type="number"]:focus, select:focus {
      outline: 0;
      border-color: var(--accent);
      box-shadow: 0 0 0 2px rgba(214, 177, 95, 0.18);
    }

    button, .file-button {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      height: 40px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--panel-2);
      color: var(--text);
      padding: 0 16px;
      font-size: 14px;
      cursor: pointer;
      white-space: nowrap;
      text-align: center;
      transition: border-color 120ms ease, background 120ms ease, color 120ms ease;
    }

    button:hover, .file-button:hover {
      border-color: var(--accent);
      background: #252c36;
    }

    #priceButton:not(:disabled), .file-button {
      border-color: #7c6a3f;
      background: #191714;
      color: #f4d997;
    }

    button:disabled {
      cursor: not-allowed;
      opacity: 0.48;
    }

    input[type="file"] { display: none; }

    .dropzone {
      min-height: 170px;
      border: 1px dashed var(--line-strong);
      border-radius: 8px;
      background: #131720;
      display: grid;
      place-items: center;
      padding: 20px;
      text-align: center;
      margin-bottom: 18px;
    }

    .dropzone.is-dragover {
      border-color: var(--accent);
      background: var(--accent-soft);
    }

    .manual-search {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 10px;
      align-items: end;
      margin: -4px 0 18px;
      padding: 14px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel-3);
    }

    #manualSearchButton:not(:disabled) {
      border-color: #7c6a3f;
      background: #191714;
      color: #f4d997;
    }

    #emptyState {
      color: #dce4ed;
      line-height: 1.45;
    }

    .preview-grid {
      width: 100%;
      display: none;
      grid-template-columns: repeat(auto-fit, minmax(140px, 180px));
      justify-content: center;
      gap: 10px;
    }

    .preview-tile {
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #131820;
      overflow: hidden;
      max-width: 180px;
    }

    .preview-tile img {
      width: 100%;
      height: 150px;
      object-fit: contain;
      display: block;
      background: #0d1015;
    }

    .preview-name {
      padding: 7px 8px;
      color: var(--muted);
      font-size: 12px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .status {
      display: flex;
      align-items: center;
      gap: 8px;
      min-height: 24px;
      color: var(--muted);
      margin: 2px 0 8px;
      font-size: 14px;
    }

    .status.error { color: var(--danger); }

    .status-spinner {
      width: 16px;
      height: 16px;
      border: 2px solid #3a4350;
      border-top-color: var(--accent);
      border-radius: 50%;
      display: none;
      flex: 0 0 16px;
      animation: spin 0.8s linear infinite;
    }

    .status.is-loading .status-spinner { display: inline-block; }

    body.is-pricing .dropzone {
      opacity: 0.75;
      pointer-events: none;
    }

    .loading-row td {
      color: var(--muted);
    }

    .loading-line {
      color: var(--muted);
    }

    @keyframes spin {
      to { transform: rotate(360deg); }
    }

    .market-rate {
      display: flex;
      align-items: center;
      flex-wrap: wrap;
      gap: 8px;
      width: fit-content;
      max-width: 100%;
      min-height: 30px;
      color: var(--muted);
      margin: 0 0 16px;
      padding: 4px 8px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--panel-3);
      font-size: 13px;
    }

    .rate-icon {
      width: 24px;
      height: 24px;
      object-fit: contain;
      border-radius: 4px;
      background: #11151b;
    }

    .rate-text {
      color: var(--text);
      min-width: 0;
    }

    .layout {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 320px;
      gap: 18px;
      align-items: start;
    }

    table {
      width: 100%;
      border-collapse: collapse;
      overflow: hidden;
      border-radius: 8px;
      background: var(--panel);
    }

    .table-wrap {
      overflow-x: auto;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
    }

    th, td {
      padding: 11px 12px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
      font-size: 14px;
      overflow-wrap: anywhere;
    }

    th {
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      background: #12161d;
    }

    tbody tr {
      background: var(--panel);
    }

    tbody tr:nth-child(even) {
      background: #141922;
    }

    tr:last-child td { border-bottom: 0; }

    .needs-review td { color: #ffd8a8; }

    .match-cell {
      display: flex;
      align-items: flex-start;
      gap: 9px;
      min-width: 0;
    }

    .item-icon {
      width: 32px;
      height: 32px;
      flex: 0 0 32px;
      object-fit: contain;
      border-radius: 4px;
      background: #11151b;
    }

    .match-text {
      min-width: 0;
    }

    .confidence {
      display: inline-flex;
      min-width: 46px;
      justify-content: center;
      border-radius: 999px;
      padding: 2px 8px;
      background: #243227;
      color: var(--ok);
    }

    .needs-review .confidence {
      background: #392b21;
      color: #ffd8a8;
    }

    .trade-only td { color: #c9d7ff; }

    .unmatched td { color: #ffd8a8; }

    .note {
      display: block;
      margin-top: 4px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.35;
    }

    aside {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      padding: 16px;
      min-width: 0;
    }

    aside h2 {
      margin: 0 0 10px;
      font-size: 16px;
      letter-spacing: 0;
    }

    .lines {
      display: grid;
      gap: 6px;
      max-height: 520px;
      overflow: auto;
      color: var(--muted);
      font-size: 13px;
    }

    .line {
      border-bottom: 1px solid #252b34;
      padding-bottom: 6px;
    }

    @media (max-width: 860px) {
      main { width: min(100vw - 20px, 1180px); padding-top: 18px; }
      header, .layout { display: grid; grid-template-columns: 1fr; }
      header { align-items: start; gap: 12px; }
      .controls { grid-template-columns: 1fr; padding: 12px; }
      .controls button { width: 100%; }
      .manual-search { grid-template-columns: 1fr; padding: 12px; }
      .manual-search button { width: 100%; }
      .file-button { width: 100%; }
      table { min-width: 720px; }
      .lines { max-height: 260px; }
    }

    @media (max-width: 520px) {
      main { width: calc(100vw - 14px); padding: 12px 0 24px; }
      h1 { font-size: 22px; }
      .muted { font-size: 13px; }
      .controls { gap: 9px; padding: 10px; }
      .dropzone { min-height: 145px; padding: 12px; }
      .manual-search { gap: 9px; padding: 10px; }
      .preview-grid { grid-template-columns: minmax(0, 180px); }
      .market-rate { width: 100%; }
      .rate-icon { width: 22px; height: 22px; }
      .rate-text { flex: 1 1 180px; }
      th, td { padding: 8px; font-size: 13px; }
      aside { padding: 10px; }
    }
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>PoE 2 Screenshot Price Checker</h1>
        <div class="muted">Paste or upload a screenshot, then match OCR text against Poe2Scout prices.</div>
      </div>
      <label class="file-button" for="fileInput">Upload</label>
      <input id="fileInput" type="file" accept="image/*" multiple>
    </header>

    <section class="controls">
      <label>Realm
        <select id="realmInput">
          <option value="poe2" selected>poe2</option>
        </select>
      </label>
      <label>League
        <select id="leagueInput">
          <option value="Runes of Aldur" selected>Runes of Aldur</option>
        </select>
      </label>
      <label>Review below
        <select id="scoreInput">
          <option value="70">70</option>
          <option value="75">75</option>
          <option value="80" selected>80</option>
          <option value="85">85</option>
          <option value="90">90</option>
          <option value="95">95</option>
          <option value="100">100</option>
        </select>
      </label>
      <button id="priceButton" disabled>Price</button>
      <button id="clearButton" disabled>Clear</button>
    </section>

    <section id="dropzone" class="dropzone">
      <div id="emptyState">
        <strong>Ctrl+V</strong> screenshots here or drop up to 4 images.
        <div class="muted">Press Enter to price the current batch.</div>
      </div>
      <div id="previewGrid" class="preview-grid" aria-label="Selected screenshot previews"></div>
    </section>

    <section class="manual-search">
      <label>Item search
        <input id="manualSearchInput" type="text" list="manualSearchSuggestions" autocomplete="off" placeholder="Divine Orb">
        <datalist id="manualSearchSuggestions"></datalist>
      </label>
      <button id="manualSearchButton" type="button" disabled>Search</button>
    </section>

    <div id="status" class="status" role="status" aria-live="polite">
      <span class="status-spinner" aria-hidden="true"></span>
      <span id="statusText"></span>
    </div>
    <div id="marketRate" class="market-rate">Poe2Scout prices are shown in exalted. Divine rate appears after pricing.</div>

    <section class="layout">
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Input text</th>
              <th>Matched item</th>
              <th>Price (exalted)</th>
              <th>Category</th>
              <th>Confidence</th>
            </tr>
          </thead>
          <tbody id="resultBody">
            <tr><td colspan="5" class="muted">No screenshot priced yet.</td></tr>
          </tbody>
        </table>
      </div>
      <aside>
        <h2 id="linesTitle">OCR Lines</h2>
        <div id="ocrLines" class="lines">
          <div class="muted">Extracted text appears here.</div>
        </div>
      </aside>
    </section>
  </main>

  <script>
    const fileInput = document.querySelector("#fileInput");
    const dropzone = document.querySelector("#dropzone");
    const previewGrid = document.querySelector("#previewGrid");
    const emptyState = document.querySelector("#emptyState");
    const manualSearchInput = document.querySelector("#manualSearchInput");
    const manualSearchSuggestions = document.querySelector("#manualSearchSuggestions");
    const manualSearchButton = document.querySelector("#manualSearchButton");
    const realmInput = document.querySelector("#realmInput");
    const leagueInput = document.querySelector("#leagueInput");
    const scoreInput = document.querySelector("#scoreInput");
    const priceButton = document.querySelector("#priceButton");
    const clearButton = document.querySelector("#clearButton");
    const statusNode = document.querySelector("#status");
    const statusText = document.querySelector("#statusText");
    const marketRate = document.querySelector("#marketRate");
    const resultBody = document.querySelector("#resultBody");
    const linesTitle = document.querySelector("#linesTitle");
    const ocrLines = document.querySelector("#ocrLines");
    const maxImages = 4;
    let currentFiles = [];
    let previewUrls = [];
    let isPricing = false;
    let hasResults = false;
    let suggestionTimer = null;
    let suggestionController = null;

    function setStatus(message, isError = false, isLoading = false) {
      statusText.textContent = message;
      statusNode.classList.toggle("error", isError);
      statusNode.classList.toggle("is-loading", isLoading);
    }

    function updateButtons() {
      const hasFiles = currentFiles.length > 0;
      const hasManualQuery = manualSearchInput.value.trim().length > 0;
      priceButton.disabled = isPricing || !hasFiles;
      manualSearchButton.disabled = isPricing || !hasManualQuery;
      clearButton.disabled = isPricing || (!hasFiles && !hasManualQuery && !hasResults);
      fileInput.disabled = isPricing;
      manualSearchInput.disabled = isPricing;
      realmInput.disabled = isPricing;
      leagueInput.disabled = isPricing;
      scoreInput.disabled = isPricing;
    }

    function setPricingState(active, mode = "ocr") {
      isPricing = active;
      document.body.classList.toggle("is-pricing", active);
      priceButton.textContent = active && mode === "ocr" ? "Pricing..." : "Price";
      manualSearchButton.textContent = active && mode === "manual" ? "Searching..." : "Search";
      updateButtons();

      if (active) {
        hasResults = true;
        resultBody.innerHTML = `<tr class="loading-row"><td colspan="5">${mode === "manual" ? "Searching Poe2Scout prices..." : "Pricing current screenshots..."}</td></tr>`;
        linesTitle.textContent = mode === "manual" ? "Manual Query" : "OCR Lines";
        ocrLines.innerHTML = `<div class="loading-line">${mode === "manual" ? "Looking up the typed item..." : "OCR is reading the image text..."}</div>`;
      }
    }

    function addFiles(files) {
      if (isPricing) return;

      const images = Array.from(files || []).filter((file) => file.type.startsWith("image/"));
      if (!images.length) return;

      const remaining = maxImages - currentFiles.length;
      if (remaining <= 0) {
        setStatus(`Already holding ${maxImages} images. Clear to start a new batch.`, true);
        return;
      }

      const accepted = images.slice(0, remaining);
      currentFiles.push(...accepted);
      renderPreviews();
      updateButtons();

      const skipped = images.length - accepted.length;
      setStatus(
        `${currentFiles.length}/${maxImages} images ready.${skipped > 0 ? ` ${skipped} skipped.` : ""}`
      );
    }

    function renderPreviews() {
      previewUrls.forEach((url) => URL.revokeObjectURL(url));
      previewUrls = currentFiles.map((file) => URL.createObjectURL(file));
      previewGrid.innerHTML = currentFiles.map((file, index) => `
        <div class="preview-tile">
          <img src="${previewUrls[index]}" alt="Screenshot ${index + 1}">
          <div class="preview-name">${escapeHtml(file.name || `Pasted image ${index + 1}`)}</div>
        </div>
      `).join("");

      previewGrid.style.display = currentFiles.length ? "grid" : "none";
      emptyState.style.display = currentFiles.length ? "none" : "block";
    }

    function imageFromClipboard(event) {
      const items = Array.from(event.clipboardData?.items || []);
      return items
        .filter((item) => item.type.startsWith("image/"))
        .map((item) => item.getAsFile())
        .filter(Boolean);
    }

    function imageFromDrop(event) {
      const files = Array.from(event.dataTransfer?.files || []);
      return files.filter((file) => file.type.startsWith("image/"));
    }

    fileInput.addEventListener("change", () => {
      addFiles(fileInput.files);
      fileInput.value = "";
    });

    document.addEventListener("paste", (event) => {
      const files = imageFromClipboard(event);
      if (files.length) addFiles(files);
    });

    dropzone.addEventListener("dragover", (event) => {
      event.preventDefault();
      dropzone.classList.add("is-dragover");
    });

    dropzone.addEventListener("dragleave", () => dropzone.classList.remove("is-dragover"));

    dropzone.addEventListener("drop", (event) => {
      event.preventDefault();
      dropzone.classList.remove("is-dragover");
      addFiles(imageFromDrop(event));
    });

    function clearAll() {
      previewUrls.forEach((url) => URL.revokeObjectURL(url));
      previewUrls = [];
      currentFiles = [];
      manualSearchInput.value = "";
      manualSearchSuggestions.innerHTML = "";
      hasResults = false;
      previewGrid.innerHTML = "";
      previewGrid.style.display = "none";
      emptyState.style.display = "block";
      resultBody.innerHTML = `<tr><td colspan="5" class="muted">No screenshot priced yet.</td></tr>`;
      linesTitle.textContent = "OCR Lines";
      ocrLines.innerHTML = `<div class="muted">Extracted text appears here.</div>`;
      renderMarketRate();
      setStatus("");
      updateButtons();
    }

    clearButton.addEventListener("click", clearAll);
    manualSearchInput.addEventListener("input", () => {
      updateButtons();
      queueManualSuggestions();
    });

    function formatPrice(price) {
      if (price === null || price === undefined || price === "") return "n/a";
      const amount = typeof price === "number" ? formatNumber(price) : `${price}`;
      return `${amount} exalted`;
    }

    function priceNote(row) {
      if (!row || !row.quantity || row.quantity <= 1 || row.unit_price === null || row.unit_price === undefined) return "";
      return `<span class="note">${escapeHtml(row.quantity)} x ${escapeHtml(formatPrice(row.unit_price))}</span>`;
    }

    function formatNumber(value) {
      if (!Number.isFinite(value)) return `${value}`;
      if (Number.isInteger(value)) return `${value}`;
      return value.toFixed(2);
    }

    function formatRateText(rate) {
      if (rate === null || rate === undefined || rate === "") return "Divine rate unavailable from Poe2Scout.";
      const value = typeof rate === "number" ? rate : Number(rate);
      if (!Number.isFinite(value)) return "Divine rate unavailable from Poe2Scout.";
      return `1 Divine Orb = ${formatNumber(value)} exalted`;
    }

    function formatAge(seconds) {
      const value = Number(seconds);
      if (!Number.isFinite(value)) return "";
      if (value < 60) return `${Math.round(value)}s old`;
      if (value < 3600) return `${Math.round(value / 60)}m old`;
      return `${Math.round(value / 3600)}h old`;
    }

    function priceSourceLabel(payload) {
      const age = formatAge(payload.price_data_age_seconds);
      if (payload.price_data_source === "live") return "live Poe2Scout prices";
      if (payload.price_data_source === "cache") return `cached Poe2Scout prices${age ? `, ${age}` : ""}`;
      if (payload.price_data_source === "stale_cache") return `stale cached prices${age ? `, ${age}` : ""}`;
      if (payload.price_data_source === "snapshot") return "bundled snapshot fallback";
      return "Poe2Scout prices";
    }

    function rateIcon(url, label) {
      if (!url) return "";
      return `<img class="rate-icon" src="${escapeHtml(url)}" alt="${escapeHtml(label)}" loading="lazy" referrerpolicy="no-referrer" onerror="this.style.display='none'">`;
    }

    function renderMarketRate(payload = null) {
      if (!payload) {
        marketRate.textContent = "Poe2Scout prices are shown in exalted. Divine rate appears after pricing.";
        return;
      }

      marketRate.innerHTML = `
        ${rateIcon(payload.divine_icon_url, "Divine Orb")}
        <span class="rate-text">Poe2Scout divine rate: ${escapeHtml(formatRateText(payload.divine_exchange_rate_exalted))}</span>
        ${rateIcon(payload.exalted_icon_url, "Exalted Orb")}
      `;
    }

    function itemIcon(row) {
      if (!row.icon_url) return "";
      const url = escapeHtml(row.icon_url);
      return `<img class="item-icon" src="${url}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.style.display='none'">`;
    }

    function escapeHtml(value) {
      return `${value ?? ""}`.replace(/[&<>"']/g, (char) => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#039;"
      })[char]);
    }

    function renderResults(results) {
      hasResults = true;
      updateButtons();

      if (!results.length) {
        resultBody.innerHTML = `<tr><td colspan="5" class="muted">No input rows found.</td></tr>`;
        return;
      }

      resultBody.innerHTML = results.map((row) => `
        <tr class="${row.source === "unmatched" ? "unmatched" : row.source === "trade_only" ? "trade-only" : row.needs_review ? "needs-review" : ""}">
          <td>${escapeHtml(row.ocr_text)}</td>
          <td>
            <div class="match-cell">
              ${itemIcon(row)}
              <div class="match-text">${escapeHtml(row.matched)}${row.message ? `<span class="note">${escapeHtml(row.message)}</span>` : ""}</div>
            </div>
          </td>
          <td>${escapeHtml(formatPrice(row.price))}${priceNote(row)}</td>
          <td>${escapeHtml(row.category)}</td>
          <td>${row.source === "trade_only" ? "Trade" : row.source === "unmatched" ? "Review" : `<span class="confidence">${Math.round(row.confidence)}</span>`}</td>
        </tr>
      `).join("");
    }

    function renderLines(lines, title = "OCR Lines", emptyMessage = "No text detected.") {
      linesTitle.textContent = title;
      if (!lines.length) {
        ocrLines.innerHTML = `<div class="muted">${escapeHtml(emptyMessage)}</div>`;
        return;
      }
      ocrLines.innerHTML = lines.map((line) => `<div class="line">${escapeHtml(line)}</div>`).join("");
    }

    function renderRequestError(linesMessage = "OCR lines were not updated.") {
      hasResults = true;
      updateButtons();
      resultBody.innerHTML = `<tr><td colspan="5" class="muted">Pricing failed. Check the status message and try again.</td></tr>`;
      ocrLines.innerHTML = `<div class="muted">${escapeHtml(linesMessage)}</div>`;
    }

    function pricedRowCount(payload) {
      return payload.results.filter((row) => row.source === "poe2scout" && row.price !== null && row.price !== undefined).length;
    }

    function reviewRowCount(payload) {
      return payload.results.filter((row) => row.needs_review).length;
    }

    function queueManualSuggestions() {
      window.clearTimeout(suggestionTimer);
      const query = manualSearchInput.value.trim();
      if (!query) {
        manualSearchSuggestions.innerHTML = "";
        return;
      }

      suggestionTimer = window.setTimeout(() => fetchManualSuggestions(query), 120);
    }

    async function fetchManualSuggestions(query) {
      if (suggestionController) suggestionController.abort();
      suggestionController = new AbortController();

      const params = new URLSearchParams({
        query,
        realm: realmInput.value.trim() || "poe2",
        league: leagueInput.value.trim() || "Runes of Aldur",
        limit: "12"
      });

      try {
        const response = await fetch(`/api/suggestions?${params.toString()}`, { signal: suggestionController.signal });
        if (!response.ok) return;
        const payload = await response.json();
        if (manualSearchInput.value.trim() !== query) return;
        renderManualSuggestions(payload.suggestions || []);
      } catch (error) {
        if (error.name !== "AbortError") manualSearchSuggestions.innerHTML = "";
      }
    }

    function renderManualSuggestions(suggestions) {
      manualSearchSuggestions.innerHTML = suggestions.map((suggestion) => {
        const price = suggestion.price === null || suggestion.price === undefined ? "" : `${formatNumber(Number(suggestion.price))} exalted`;
        const label = [suggestion.category, price].filter(Boolean).join(" - ");
        return `<option value="${escapeHtml(suggestion.text)}"${label ? ` label="${escapeHtml(label)}"` : ""}></option>`;
      }).join("");
    }

    async function priceCurrentBatch() {
      if (!currentFiles.length || priceButton.disabled) return;

      const formData = new FormData();
      currentFiles.forEach((file, index) => {
        formData.append("image", file, file.name || `clipboard-${index + 1}.png`);
      });
      formData.append("realm", realmInput.value.trim() || "poe2");
      formData.append("league", leagueInput.value.trim() || "Runes of Aldur");
      formData.append("min_score", scoreInput.value || "80");

      setPricingState(true, "ocr");
      setStatus(`Running OCR on ${currentFiles.length} image${currentFiles.length === 1 ? "" : "s"} and fetching Poe2Scout prices...`, false, true);

      try {
        const response = await fetch("/api/price", { method: "POST", body: formData });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || "Pricing failed.");

        renderResults(payload.results);
        renderLines(payload.ocr_lines);
        renderMarketRate(payload);
        const pricedRows = pricedRowCount(payload);
        const reviewRows = reviewRowCount(payload);
        setStatus(`Priced ${pricedRows} row${pricedRows === 1 ? "" : "s"} from ${payload.image_count} image${payload.image_count === 1 ? "" : "s"} against ${payload.item_count} ${payload.league} items using ${priceSourceLabel(payload)}; OCR lexicon has ${payload.lexicon_item_count} items${reviewRows ? `; ${reviewRows} row${reviewRows === 1 ? "" : "s"} need review.` : "."}`);
      } catch (error) {
        renderRequestError();
        setStatus(error.message, true);
      } finally {
        setPricingState(false);
      }
    }

    async function searchManualItem() {
      const query = manualSearchInput.value.trim();
      if (!query || manualSearchButton.disabled) return;

      const formData = new FormData();
      formData.append("query", query);
      formData.append("realm", realmInput.value.trim() || "poe2");
      formData.append("league", leagueInput.value.trim() || "Runes of Aldur");
      formData.append("min_score", scoreInput.value || "80");

      setPricingState(true, "manual");
      setStatus(`Searching Poe2Scout prices for "${query}"...`, false, true);

      try {
        const response = await fetch("/api/search", { method: "POST", body: formData });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || "Search failed.");

        renderResults(payload.results);
        renderLines(payload.ocr_lines, "Manual Query", "No manual query text.");
        renderMarketRate(payload);
        const pricedRows = pricedRowCount(payload);
        const reviewRows = reviewRowCount(payload);
        setStatus(`Priced ${pricedRows} manual search row${pricedRows === 1 ? "" : "s"} against ${payload.item_count} ${payload.league} items using ${priceSourceLabel(payload)}; OCR lexicon has ${payload.lexicon_item_count} items${reviewRows ? `; ${reviewRows} row${reviewRows === 1 ? "" : "s"} need review.` : "."}`);
      } catch (error) {
        renderRequestError("Manual query was not updated.");
        setStatus(error.message, true);
      } finally {
        setPricingState(false, "manual");
      }
    }

    priceButton.addEventListener("click", priceCurrentBatch);
    manualSearchButton.addEventListener("click", searchManualItem);
    manualSearchInput.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" || event.shiftKey || event.ctrlKey || event.altKey || event.metaKey) return;
      event.preventDefault();
      event.stopPropagation();
      searchManualItem();
    });

    document.addEventListener("keydown", (event) => {
      if (event.target === manualSearchInput) return;
      if (event.key !== "Enter" || event.shiftKey || event.ctrlKey || event.altKey || event.metaKey) return;
      event.preventDefault();
      priceCurrentBatch();
    });
  </script>
</body>
</html>
"""
