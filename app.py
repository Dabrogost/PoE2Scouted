from __future__ import annotations

import io
from typing import Annotated, Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel

from matcher import match_ocr_lines
from ocr import OCREngineError, extract_text_lines
from poe2scout import Poe2ScoutError, fetch_items


DEFAULT_REALM = "poe2"
DEFAULT_LEAGUE = "Runes of Aldur"
DEFAULT_MIN_SCORE = 80

app = FastAPI(title="PoE2 Screenshot Pricer")


class PriceResponse(BaseModel):
    realm: str
    league: str
    item_count: int
    ocr_lines: list[str]
    results: list[dict[str, Any]]


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return HTML


@app.post("/api/price", response_model=PriceResponse)
async def price_screenshot(
    image: Annotated[UploadFile, File()],
    realm: Annotated[str, Form()] = DEFAULT_REALM,
    league: Annotated[str, Form()] = DEFAULT_LEAGUE,
    min_score: Annotated[int, Form(ge=1, le=100)] = DEFAULT_MIN_SCORE,
) -> PriceResponse:
    realm = realm.strip() or DEFAULT_REALM
    league = league.strip() or DEFAULT_LEAGUE

    if not image.content_type or not image.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Upload an image file.")

    image_bytes = await image.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="The uploaded image was empty.")

    try:
        with Image.open(io.BytesIO(image_bytes)) as uploaded_image:
            uploaded_image.verify()
    except (UnidentifiedImageError, OSError) as exc:
        raise HTTPException(status_code=400, detail="The uploaded file is not a readable image.") from exc

    try:
        ocr_lines = extract_text_lines(image_bytes)
        items = fetch_items(realm=realm, league=league)
    except OCREngineError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Poe2ScoutError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    results = match_ocr_lines(ocr_lines, items, min_score=min_score)

    return PriceResponse(
        realm=realm,
        league=league,
        item_count=len(items),
        ocr_lines=ocr_lines,
        results=results,
    )


HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>PoE2 Screenshot Pricer</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #101217;
      --panel: #191d24;
      --panel-2: #202630;
      --line: #323a46;
      --text: #edf1f5;
      --muted: #a8b0bc;
      --accent: #d9b86f;
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
    }

    main {
      width: min(1180px, calc(100vw - 32px));
      margin: 0 auto;
      padding: 28px 0 40px;
    }

    header {
      display: flex;
      align-items: end;
      justify-content: space-between;
      gap: 18px;
      margin-bottom: 20px;
    }

    h1 {
      margin: 0;
      font-size: 28px;
      line-height: 1.1;
      letter-spacing: 0;
    }

    .muted {
      color: var(--muted);
      font-size: 14px;
    }

    .controls {
      display: grid;
      grid-template-columns: repeat(3, minmax(150px, 1fr)) auto;
      gap: 10px;
      align-items: end;
      margin-bottom: 14px;
    }

    label {
      display: grid;
      gap: 6px;
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
    }

    input[type="text"], input[type="number"] {
      width: 100%;
      height: 38px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--panel);
      color: var(--text);
      padding: 0 10px;
      font-size: 14px;
    }

    button, .file-button {
      height: 38px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--panel-2);
      color: var(--text);
      padding: 0 14px;
      font-size: 14px;
      cursor: pointer;
    }

    button:hover, .file-button:hover { border-color: var(--accent); }

    input[type="file"] { display: none; }

    .dropzone {
      min-height: 220px;
      border: 1px dashed var(--line);
      border-radius: 8px;
      background: var(--panel);
      display: grid;
      place-items: center;
      padding: 18px;
      text-align: center;
      margin-bottom: 18px;
    }

    .dropzone.is-dragover { border-color: var(--accent); background: #1e232b; }

    .preview {
      max-width: 100%;
      max-height: 360px;
      border-radius: 6px;
      border: 1px solid var(--line);
      display: none;
    }

    .status {
      min-height: 24px;
      color: var(--muted);
      margin-bottom: 14px;
      font-size: 14px;
    }

    .status.error { color: var(--danger); }

    .layout {
      display: grid;
      grid-template-columns: 1fr 320px;
      gap: 18px;
      align-items: start;
    }

    table {
      width: 100%;
      border-collapse: collapse;
      overflow: hidden;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
    }

    th, td {
      padding: 10px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
      font-size: 14px;
    }

    th {
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      background: #161a20;
    }

    tr:last-child td { border-bottom: 0; }

    .needs-review td { color: #ffd8a8; }

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

    aside {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      padding: 14px;
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
      header, .layout { display: grid; }
      .controls { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>PoE2 Screenshot Pricer</h1>
        <div class="muted">Paste or upload a screenshot, then match OCR text against Poe2Scout prices.</div>
      </div>
      <label class="file-button" for="fileInput">Upload</label>
      <input id="fileInput" type="file" accept="image/*">
    </header>

    <section class="controls">
      <label>Realm
        <input id="realmInput" type="text" value="poe2">
      </label>
      <label>League
        <input id="leagueInput" type="text" value="Runes of Aldur">
      </label>
      <label>Review below
        <input id="scoreInput" type="number" min="1" max="100" value="80">
      </label>
      <button id="priceButton" disabled>Price</button>
    </section>

    <section id="dropzone" class="dropzone">
      <div id="emptyState">
        <strong>Ctrl+V</strong> a screenshot here or drop an image.
        <div class="muted">The API fetch is cached locally so batches stay quick.</div>
      </div>
      <img id="preview" class="preview" alt="Selected screenshot preview">
    </section>

    <div id="status" class="status"></div>

    <section class="layout">
      <table>
        <thead>
          <tr>
            <th>OCR text</th>
            <th>Matched item</th>
            <th>Price</th>
            <th>Category</th>
            <th>Confidence</th>
          </tr>
        </thead>
        <tbody id="resultBody">
          <tr><td colspan="5" class="muted">No screenshot priced yet.</td></tr>
        </tbody>
      </table>
      <aside>
        <h2>OCR Lines</h2>
        <div id="ocrLines" class="lines">
          <div class="muted">Extracted text appears here.</div>
        </div>
      </aside>
    </section>
  </main>

  <script>
    const fileInput = document.querySelector("#fileInput");
    const dropzone = document.querySelector("#dropzone");
    const preview = document.querySelector("#preview");
    const emptyState = document.querySelector("#emptyState");
    const priceButton = document.querySelector("#priceButton");
    const statusNode = document.querySelector("#status");
    const resultBody = document.querySelector("#resultBody");
    const ocrLines = document.querySelector("#ocrLines");
    let currentFile = null;
    let previewUrl = null;

    function setStatus(message, isError = false) {
      statusNode.textContent = message;
      statusNode.classList.toggle("error", isError);
    }

    function setFile(file) {
      currentFile = file;
      priceButton.disabled = !file;
      if (!file) return;

      if (previewUrl) URL.revokeObjectURL(previewUrl);
      previewUrl = URL.createObjectURL(file);
      preview.src = previewUrl;
      preview.style.display = "block";
      emptyState.style.display = "none";
      setStatus(`${file.name || "Pasted image"} ready.`);
    }

    function imageFromClipboard(event) {
      const items = Array.from(event.clipboardData?.items || []);
      const imageItem = items.find((item) => item.type.startsWith("image/"));
      return imageItem?.getAsFile() || null;
    }

    function imageFromDrop(event) {
      const files = Array.from(event.dataTransfer?.files || []);
      return files.find((file) => file.type.startsWith("image/")) || null;
    }

    fileInput.addEventListener("change", () => setFile(fileInput.files[0]));

    document.addEventListener("paste", (event) => {
      const file = imageFromClipboard(event);
      if (file) setFile(file);
    });

    dropzone.addEventListener("dragover", (event) => {
      event.preventDefault();
      dropzone.classList.add("is-dragover");
    });

    dropzone.addEventListener("dragleave", () => dropzone.classList.remove("is-dragover"));

    dropzone.addEventListener("drop", (event) => {
      event.preventDefault();
      dropzone.classList.remove("is-dragover");
      const file = imageFromDrop(event);
      if (file) setFile(file);
    });

    function formatPrice(price) {
      if (price === null || price === undefined || price === "") return "n/a";
      if (typeof price === "number") return Number.isInteger(price) ? `${price}` : price.toFixed(2);
      return `${price}`;
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
      if (!results.length) {
        resultBody.innerHTML = `<tr><td colspan="5" class="muted">No likely Poe2Scout item names found.</td></tr>`;
        return;
      }

      resultBody.innerHTML = results.map((row) => `
        <tr class="${row.needs_review ? "needs-review" : ""}">
          <td>${escapeHtml(row.ocr_text)}</td>
          <td>${escapeHtml(row.matched)}</td>
          <td>${escapeHtml(formatPrice(row.price))}</td>
          <td>${escapeHtml(row.category)}</td>
          <td><span class="confidence">${Math.round(row.confidence)}</span></td>
        </tr>
      `).join("");
    }

    function renderLines(lines) {
      if (!lines.length) {
        ocrLines.innerHTML = `<div class="muted">No text detected.</div>`;
        return;
      }
      ocrLines.innerHTML = lines.map((line) => `<div class="line">${escapeHtml(line)}</div>`).join("");
    }

    priceButton.addEventListener("click", async () => {
      if (!currentFile) return;

      const formData = new FormData();
      formData.append("image", currentFile, currentFile.name || "clipboard.png");
      formData.append("realm", document.querySelector("#realmInput").value.trim() || "poe2");
      formData.append("league", document.querySelector("#leagueInput").value.trim() || "Runes of Aldur");
      formData.append("min_score", document.querySelector("#scoreInput").value || "80");

      setStatus("Running OCR and fetching Poe2Scout prices...");
      priceButton.disabled = true;

      try {
        const response = await fetch("/api/price", { method: "POST", body: formData });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || "Pricing failed.");

        renderResults(payload.results);
        renderLines(payload.ocr_lines);
        setStatus(`Matched ${payload.results.length} rows against ${payload.item_count} ${payload.league} items.`);
      } catch (error) {
        setStatus(error.message, true);
      } finally {
        priceButton.disabled = false;
      }
    });
  </script>
</body>
</html>
"""
