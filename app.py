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
MAX_IMAGES = 4

app = FastAPI(title="PoE2 Screenshot Pricer")


class PriceResponse(BaseModel):
    realm: str
    league: str
    image_count: int
    item_count: int
    ocr_lines: list[str]
    results: list[dict[str, Any]]


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
        items = fetch_items(realm=realm, league=league)
    except OCREngineError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Poe2ScoutError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    results = match_ocr_lines(ocr_lines, items, min_score=min_score)

    return PriceResponse(
        realm=realm,
        league=league,
        image_count=len(image_payloads),
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
      overflow-x: hidden;
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

    header > div { min-width: 0; }

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
      grid-template-columns: repeat(3, minmax(150px, 1fr)) auto auto;
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
      white-space: nowrap;
      text-align: center;
    }

    button:hover, .file-button:hover { border-color: var(--accent); }

    button:disabled {
      cursor: not-allowed;
      opacity: 0.55;
    }

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

    .preview-grid {
      width: 100%;
      display: none;
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: 10px;
    }

    .preview-tile {
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #131820;
      overflow: hidden;
    }

    .preview-tile img {
      width: 100%;
      aspect-ratio: 16 / 10;
      object-fit: cover;
      display: block;
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

    .table-wrap {
      overflow-x: auto;
      border-radius: 8px;
    }

    th, td {
      padding: 10px;
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

    .trade-only td { color: #c9d7ff; }

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
      padding: 14px;
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
      .controls { grid-template-columns: 1fr; }
      .file-button { width: 100%; }
      table { min-width: 720px; }
      .lines { max-height: 260px; }
    }

    @media (max-width: 520px) {
      main { width: calc(100vw - 14px); padding: 12px 0 24px; }
      h1 { font-size: 22px; }
      .muted { font-size: 13px; }
      .dropzone { min-height: 170px; padding: 12px; }
      .preview-grid { grid-template-columns: 1fr; }
      th, td { padding: 8px; font-size: 13px; }
      aside { padding: 10px; }
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
      <input id="fileInput" type="file" accept="image/*" multiple>
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
      <button id="clearButton" disabled>Clear</button>
    </section>

    <section id="dropzone" class="dropzone">
      <div id="emptyState">
        <strong>Ctrl+V</strong> screenshots here or drop up to 4 images.
        <div class="muted">Press Enter to price the current batch.</div>
      </div>
      <div id="previewGrid" class="preview-grid" aria-label="Selected screenshot previews"></div>
    </section>

    <div id="status" class="status"></div>

    <section class="layout">
      <div class="table-wrap">
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
      </div>
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
    const previewGrid = document.querySelector("#previewGrid");
    const emptyState = document.querySelector("#emptyState");
    const priceButton = document.querySelector("#priceButton");
    const clearButton = document.querySelector("#clearButton");
    const statusNode = document.querySelector("#status");
    const resultBody = document.querySelector("#resultBody");
    const ocrLines = document.querySelector("#ocrLines");
    const maxImages = 4;
    let currentFiles = [];
    let previewUrls = [];

    function setStatus(message, isError = false) {
      statusNode.textContent = message;
      statusNode.classList.toggle("error", isError);
    }

    function updateButtons() {
      const hasFiles = currentFiles.length > 0;
      priceButton.disabled = !hasFiles;
      clearButton.disabled = !hasFiles;
    }

    function addFiles(files) {
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
      previewGrid.innerHTML = "";
      previewGrid.style.display = "none";
      emptyState.style.display = "block";
      resultBody.innerHTML = `<tr><td colspan="5" class="muted">No screenshot priced yet.</td></tr>`;
      ocrLines.innerHTML = `<div class="muted">Extracted text appears here.</div>`;
      setStatus("");
      updateButtons();
    }

    clearButton.addEventListener("click", clearAll);

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
        <tr class="${row.source === "trade_only" ? "trade-only" : row.needs_review ? "needs-review" : ""}">
          <td>${escapeHtml(row.ocr_text)}</td>
          <td>${escapeHtml(row.matched)}${row.message ? `<span class="note">${escapeHtml(row.message)}</span>` : ""}</td>
          <td>${escapeHtml(formatPrice(row.price))}</td>
          <td>${escapeHtml(row.category)}</td>
          <td>${row.source === "trade_only" ? "Trade" : `<span class="confidence">${Math.round(row.confidence)}</span>`}</td>
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

    async function priceCurrentBatch() {
      if (!currentFiles.length || priceButton.disabled) return;

      const formData = new FormData();
      currentFiles.forEach((file, index) => {
        formData.append("image", file, file.name || `clipboard-${index + 1}.png`);
      });
      formData.append("realm", document.querySelector("#realmInput").value.trim() || "poe2");
      formData.append("league", document.querySelector("#leagueInput").value.trim() || "Runes of Aldur");
      formData.append("min_score", document.querySelector("#scoreInput").value || "80");

      setStatus(`Running OCR on ${currentFiles.length} image${currentFiles.length === 1 ? "" : "s"} and fetching Poe2Scout prices...`);
      priceButton.disabled = true;
      clearButton.disabled = true;

      try {
        const response = await fetch("/api/price", { method: "POST", body: formData });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || "Pricing failed.");

        renderResults(payload.results);
        renderLines(payload.ocr_lines);
        setStatus(`Matched ${payload.results.length} rows from ${payload.image_count} image${payload.image_count === 1 ? "" : "s"} against ${payload.item_count} ${payload.league} items.`);
      } catch (error) {
        setStatus(error.message, true);
      } finally {
        updateButtons();
      }
    }

    priceButton.addEventListener("click", priceCurrentBatch);

    document.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" || event.shiftKey || event.ctrlKey || event.altKey || event.metaKey) return;
      event.preventDefault();
      priceCurrentBatch();
    });
  </script>
</body>
</html>
"""
