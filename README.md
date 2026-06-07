---
title: PoE2Scouted
emoji: 🔎
colorFrom: gray
colorTo: yellow
sdk: docker
app_port: 7860
pinned: false
---

# PoE2 Screenshot Pricer

A local browser app for pricing visible Path of Exile 2 screenshot text without a game overlay.

The app is intentionally simple:

1. You paste, drop, or upload 1 to 4 screenshots in the local page.
2. EasyOCR reads text from the image.
3. The OCR lines are cleaned and filtered.
4. Poe2Scout's item list for the selected realm and league is fetched once and cached.
5. RapidFuzz matches OCR text against the cached Poe2Scout names locally.
6. The page shows a table of likely matches with prices and confidence scores.

This is a screenshot item-name matcher, not a full rare-item valuation engine.

## Current Defaults

| Setting | Default | Where |
| --- | --- | --- |
| Realm | `poe2` | `app.py` |
| League | `Runes of Aldur` | `app.py` |
| Low-confidence threshold | `80` | `app.py` |
| Web server | `http://127.0.0.1:8000` | run command |
| Poe2Scout cache time | 15 minutes | `poe2scout.py` |
| Poe2Scout user agent | `poe2-screenshot-pricer/0.1 contact:dabrogost@gmail.com` | `poe2scout.py` |

Important realm detail: Poe2Scout's API accepts `poe2` for this app's current league data. The more obvious `pc` value returned `400 Invalid league name` in live testing.

## What It Can Price

This app can price items that exist in Poe2Scout's item list for the selected league. That includes market-indexed entries such as:

- Unique items
- Currency
- Essences
- Omens
- Fragments
- Other Poe2Scout-supported item categories

Skill gems and support gems are not priced through this app. They are not on the currency exchange, so OCR rows like `Skill: Repulsion` or `Support: Concussive Runes` are classified as `Trade only` and shown with a note to check trade instead. Poe2Scout gem categories such as `lineagesupportgems` are also treated as `Trade only` if OCR accidentally matches them.

It does not inspect rare item affixes, calculate DPS, evaluate rolls, or query the official trade site for comparable rare items. A rare item may match only by base name, which is not enough for real rare-gear pricing.

## Files

| File | Purpose |
| --- | --- |
| `app.py` | FastAPI app, HTML page, upload endpoint, request validation, and response shape. |
| `poe2scout.py` | Poe2Scout API client, request headers, JSON cache, and stale-cache fallback. |
| `ocr.py` | EasyOCR setup, model cache location, image conversion, and OCR line cleanup. |
| `matcher.py` | Text normalization, item-name indexing, fuzzy matching, deduping, and confidence flags. |
| `requirements.txt` | Python dependencies needed to run the local app. |
| `.gitignore` | Excludes `.venv`, `.cache`, bytecode, and logs. |

## Setup

From this folder:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app:app --host 127.0.0.1 --port 8000
```

If `python` is not on PATH but `.venv` already exists:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000
```

Then open:

```text
http://127.0.0.1:8000
```

First OCR use may download EasyOCR model files. This can take a little while and requires network access. The models are stored under `.cache/easyocr`.

## Using The App

1. Open the local page.
2. In Path of Exile 2, take a screenshot or use Print Screen.
3. Click the local page.
4. Press Ctrl+V, drop images, or use Upload.
5. Add up to 4 screenshots to the current batch.
6. Click Price or press Enter.
7. Click Clear when you want a blank slate for the next batch.

The result table shows:

| Column | Meaning |
| --- | --- |
| OCR text | The raw line EasyOCR extracted from the screenshot. |
| Matched item | The closest Poe2Scout item name/type match, with the returned `IconUrl` image when available. |
| Price | `CurrentPrice` from Poe2Scout for the selected league, shown as exalted. |
| Category | `CategoryApiId` from Poe2Scout. |
| Confidence | RapidFuzz `WRatio` score, rounded in the UI. |

For skill/support gem rows, the table shows `Trade only` and a note instead of a Poe2Scout price.

Rows below the review threshold are highlighted and returned with `needs_review: true`. Rows can also be marked for review when the fuzzy score is high but the OCR text and matched item do not share enough meaningful tokens. They are shown so you can inspect weak matches instead of the app silently hiding uncertainty.

## Poe2Scout API Details

The app uses this endpoint:

```text
GET https://api.poe2scout.com/{Realm}/Leagues/{LeagueName}/Items
```

For the default settings, the effective URL is:

```text
https://api.poe2scout.com/poe2/Leagues/Runes%20of%20Aldur/Items
```

The live API currently returns PascalCase fields such as:

```text
ItemId
CategoryApiId
Text
Name
Type
ApiId
CurrentPrice
IconUrl
```

`matcher.py` also supports the snake_case field names from earlier examples, such as `item_id`, `category_api_id`, and `current_price`.

## API Etiquette

Poe2Scout asks sustained API clients to include a `User-Agent` with contact information. This app sends:

```text
poe2-screenshot-pricer/0.1 contact:dabrogost@gmail.com
```

To override it for your own contact address:

```powershell
$env:POE2SCOUT_USER_AGENT = "your-app-name/0.1 contact:you@example.com"
```

Set the environment variable before starting Uvicorn.

## Caches

The app writes cache files inside the project folder:

| Cache | Path | Notes |
| --- | --- | --- |
| Poe2Scout item list | `.cache/poe2scout` | Fresh for 15 minutes. |
| EasyOCR models | `.cache/easyocr` | Downloaded by EasyOCR on first OCR use. |

If the Poe2Scout request fails and a stale item-list cache exists, `poe2scout.py` returns the stale cache instead of failing. This includes network errors and non-2xx HTTP responses. Invalid JSON still fails because that means the response shape itself was not usable.

To force a fresh Poe2Scout item list, delete the matching file under `.cache/poe2scout` or wait 15 minutes.

## Matching Behavior

The matcher builds a local index from each Poe2Scout item's:

- `Text`
- `Name`
- `Type`
- `Name + Type`
- `Text + Type`

The same logic also handles lowercase snake_case versions of those fields.

OCR text is normalized by:

- Lowercasing with `casefold`
- Removing apostrophes
- Replacing unsupported punctuation with spaces
- Collapsing repeated whitespace
- Ignoring short or obvious UI/stat lines such as `Requires`, `Quality`, `Stack Size`, and pure numbers

Matches use RapidFuzz `fuzz.WRatio`. Results are sorted by Poe2Scout `CurrentPrice` descending and capped at 50 rows. Confidence is only used as a tie-breaker for equal prices.

Before fuzzy matching, rows beginning with `Skill:` or `Support:` are classified as `Trade only`. After fuzzy matching, matched skill/support gem categories are also converted to `Trade only` rows with no price. This prevents skill/support gem names from being matched against unrelated currency-exchange items or shown as currency-exchange prices.

After fuzzy matching, the app also checks meaningful token alignment. Generic words such as `skill`, `support`, `rune`, `runes`, `of`, `the`, and `pile` do not count as strong evidence by themselves. This prevents lines like `Skill: Repulsion` from being treated as a confident match for an unrelated item such as `Vilenta's Propulsion`.

## HTTP API

The local page calls:

```text
POST /api/price
```

Multipart form fields:

| Field | Required | Description |
| --- | --- | --- |
| `image` | Yes | One to four image files. Repeat this multipart field once per image. Each file must have an image content type and be readable by Pillow. |
| `realm` | No | Defaults to `poe2`; blank values are reset to the default. |
| `league` | No | Defaults to `Runes of Aldur`; blank values are reset to the default. |
| `min_score` | No | Integer from 1 to 100. Defaults to 80. |

Response shape:

```json
{
  "realm": "poe2",
  "league": "Runes of Aldur",
  "image_count": 1,
  "item_count": 1266,
  "divine_exchange_rate_exalted": 85.61784382924093,
  "divine_icon_url": "https://...",
  "exalted_icon_url": "https://...",
  "ocr_lines": ["Chaos Orb"],
  "results": [
    {
      "ocr_text": "Chaos Orb",
      "matched": "Chaos Orb",
      "matched_key": "chaos orb",
      "item_id": 287,
      "api_id": "chaos",
      "category": "currency",
      "price": 5.6613546739552145,
      "icon_url": "https://...",
      "alignment_ok": true,
      "confidence": 100.0,
      "source": "poe2scout",
      "needs_review": false
    }
  ]
}
```

`item_count`, prices, and item IDs can change as Poe2Scout updates its data. The example above reflects a live smoke test during development, not a guaranteed permanent value.

## Dependencies

Installed packages:

| Package | Used for |
| --- | --- |
| `fastapi` | Local web app and API route. |
| `uvicorn[standard]` | Local ASGI server. |
| `python-multipart` | File uploads in FastAPI. |
| `requests` | Poe2Scout API requests and smoke checks. |
| `rapidfuzz` | Fuzzy text matching. |
| `Pillow` | Image validation and loading. |
| `easyocr` | OCR. |
| `numpy` | Image array conversion for EasyOCR. |
| `pandas` | Installed for table/export work, but not currently imported by the app. |

EasyOCR installs PyTorch-related dependencies. CPU mode is forced with `gpu=False`, so no GPU is required.

## Start, Check, Stop

Start:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000
```

Check that the page responds:

```powershell
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/ | Select-Object -ExpandProperty StatusCode
```

Find the process using port 8000:

```powershell
netstat -ano | findstr :8000
```

Stop a known process ID:

```powershell
Stop-Process -Id <PID>
```

## Troubleshooting

### `python` is not recognized

Use the virtual environment executable directly:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000
```

### EasyOCR downloads models on first use

That is expected. The files go into `.cache/easyocr`. If the download is interrupted, run the app again and submit another image.

### EasyOCR warning about `pin_memory`

You may see a PyTorch warning like:

```text
'pin_memory' argument is set as true but no accelerator is found
```

This is harmless for this app. OCR still runs on CPU.

### Poe2Scout returns `400`

Check the realm and league fields. For the current app default, use:

```text
realm: poe2
league: Runes of Aldur
```

### Results look wrong

OCR can misread game fonts. Use the OCR Lines panel to see what the model actually saw, then inspect rows with low confidence. The app is designed to flag weak matches rather than pretend they are certain. Skill and support gems are deliberately marked as `Trade only` because they are not currency-exchange price entries.

### No rare gear price

That is expected. This app matches names against Poe2Scout's indexed item list. Rare affix valuation would require a separate official-trade-site query feature.

## Known Gaps

- No manual correction UI yet.
- No trend/history columns yet.
- No clipboard hotkey outside the browser page.
- No rare-item affix pricing.
- No persisted user settings.
- No automated UI test suite yet.

## Development Smoke Checks

Compile the Python files:

```powershell
.\.venv\Scripts\python.exe -m py_compile app.py poe2scout.py ocr.py matcher.py
```

Check installed dependencies:

```powershell
.\.venv\Scripts\python.exe -m pip check
```

The live endpoint was smoke-tested by posting a generated image containing `Chaos Orb`. EasyOCR read it imperfectly as `ChacsOrb`, and RapidFuzz still matched it to Poe2Scout's `Chaos Orb` with confidence around 82.

An in-game list screenshot containing rows such as `Skill: Conductive Runes`, `Skill: Repulsion`, and `Verisium Pile` was also tested. EasyOCR read the main row text. `Verisium Pile` matched cleanly to `Verisium`; the skill/support rows were classified as `Trade only` instead of being fuzzy-matched against unrelated Poe2Scout items.

## License

This project is licensed under the GNU General Public License v3.0 (GPL-3.0) - see the [LICENSE](file:///c:/Users/kerns/Documents/A.)%20JS%20Projects/PoE2Scouted/LICENSE) file for details.

