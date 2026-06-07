from __future__ import annotations

import re
from typing import Any

from rapidfuzz import fuzz, process


NOISE_PATTERNS = (
    re.compile(r"^\d+(\.\d+)?$"),
    re.compile(r"^[il1]\s*x$", re.I),
    re.compile(r"^[kr][a-z]{2,8}shape\s+c[a-z]{5,12}i[a-z]{2,4}s?$", re.I),
    re.compile(r"^(level|requires|quality|armour|armor|evasion|energy shield|stack size)\b", re.I),
    re.compile(r"^(shift|ctrl|alt|right click|left click)\b", re.I),
)
STACK_COUNT_PATTERN = re.compile(r"^\s*(?:\d+|[il])\s*x\s+", re.I)
TRADE_ONLY_PATTERN = re.compile(r"^\s*(skill|support)\s*:\s*(?P<name>.+)$", re.I)
TRADE_ONLY_CATEGORIES = {
    "lineagesupportgems",
    "skillgems",
    "supportgems",
}
GENERIC_TOKENS = {
    "a",
    "an",
    "and",
    "for",
    "gem",
    "level",
    "of",
    "pile",
    "rune",
    "runes",
    "skill",
    "support",
    "the",
    "to",
}


def build_name_index(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}

    for item in items:
        for name in _candidate_names(item):
            normalized = normalize_text(name)
            if normalized:
                index[normalized] = item

    return index


def match_ocr_lines(
    ocr_lines: list[str],
    items: list[dict[str, Any]],
    *,
    min_score: int = 80,
    limit: int = 50,
) -> list[dict[str, Any]]:
    index = build_name_index(items)
    choices = list(index)
    if not choices:
        return []

    choice_positions = {choice: position for position, choice in enumerate(choices)}
    results_by_key: dict[str, dict[str, Any]] = {}
    suffixes = _detected_item_suffixes(ocr_lines, choices)

    for raw in _candidate_ocr_lines(ocr_lines):
        trade_only = _trade_only_result(raw)
        if trade_only is not None:
            _store_result(results_by_key, trade_only)
            continue

        cleaned = normalize_text(raw)
        if len(cleaned) < 3:
            continue
        if not _meaningful_tokens(cleaned):
            continue
        if cleaned in suffixes:
            continue

        match = _best_match(cleaned, choices, choice_positions, suffixes)
        if not match:
            continue

        matched_name, score, _ = match
        item = index[matched_name]
        category = _field(item, "category_api_id", "CategoryApiId")
        if _is_trade_only_category(category):
            _store_result(results_by_key, _matched_trade_only_result(raw, item, matched_name, score))
            continue

        alignment_ok = _has_meaningful_alignment(cleaned, matched_name, score)
        _store_result(
            results_by_key,
            {
                "ocr_text": _display_ocr_text(raw),
                "matched": _field(item, "text", "Text")
                or _field(item, "name", "Name")
                or _field(item, "type", "Type")
                or matched_name,
                "matched_key": matched_name,
                "item_id": _field(item, "item_id", "ItemId"),
                "api_id": _field(item, "api_id", "ApiId"),
                "category": category,
                "price": _field(item, "current_price", "CurrentPrice"),
                "icon_url": _field(item, "icon_url", "IconUrl"),
                "confidence": score,
                "alignment_ok": alignment_ok,
                "needs_review": score < min_score or not alignment_ok,
                "source": "poe2scout",
            }
        )

    return sorted(results_by_key.values(), key=_sort_key, reverse=True)[:limit]


def _sort_key(row: dict[str, Any]) -> tuple[int, int, float, float]:
    price = _price_to_float(row.get("price"))
    return (
        0 if row.get("source") == "trade_only" else 1,
        0 if price is None else 1,
        price if price is not None else float("-inf"),
        float(row.get("confidence") or 0),
    )


def _store_result(results_by_key: dict[str, dict[str, Any]], row: dict[str, Any]) -> None:
    key = _result_key(row)
    existing = results_by_key.get(key)
    if existing is None or _row_quality(row) > _row_quality(existing):
        results_by_key[key] = row


def _result_key(row: dict[str, Any]) -> str:
    if row.get("source") == "poe2scout":
        item_key = row.get("item_id") or row.get("api_id") or row.get("matched_key") or row.get("matched")
        return f"item:{item_key}"

    return f"{row.get('source')}:{row.get('matched_key') or normalize_text(str(row.get('ocr_text') or ''))}"


def _row_quality(row: dict[str, Any]) -> tuple[int, float, int]:
    return (
        1 if row.get("alignment_ok") else 0,
        float(row.get("confidence") or 0),
        len(normalize_text(str(row.get("ocr_text") or ""))),
    )


def _price_to_float(price: Any) -> float | None:
    if price is None or price == "":
        return None

    try:
        return float(price)
    except (TypeError, ValueError):
        return None


def normalize_text(value: str) -> str:
    value = value.casefold()
    value = STACK_COUNT_PATTERN.sub("", value)
    value = re.sub(r"['\u2019]\s*s\b", "s", value)
    value = value.replace("'", "")
    value = value.replace("\u2019", "")
    value = re.sub(r"^\s*(skill|support)\s*:\s*", "", value)
    value = re.sub(r"[^a-z0-9+% -]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _display_ocr_text(value: str) -> str:
    value = STACK_COUNT_PATTERN.sub("", value).strip()
    return re.sub(r"['\u2019]\s+s\b", "'s", value)


def _has_meaningful_alignment(query: str, matched_name: str, score: float) -> bool:
    if query == matched_name or query in matched_name or matched_name in query:
        return True

    query_tokens = _meaningful_tokens(query)
    matched_tokens = _meaningful_tokens(matched_name)
    if not query_tokens or not matched_tokens:
        return False

    exact_hits = sum(1 for token in query_tokens if token in matched_tokens)
    if len(query_tokens) == 1:
        return exact_hits == 1
    if len(query_tokens) == 2:
        return exact_hits == 2 or (exact_hits == 1 and score >= 92)

    return exact_hits / len(query_tokens) >= 0.67 or (exact_hits >= 2 and score >= 90)


def _meaningful_tokens(value: str) -> list[str]:
    return [token for token in normalize_text(value).split() if token not in GENERIC_TOKENS and len(token) > 1]


def _detected_item_suffixes(ocr_lines: list[str], choices: list[str]) -> set[str]:
    suffixes: set[str] = set()
    normalized_lines = {normalize_text(line) for line in ocr_lines}

    for line in normalized_lines:
        tokens = line.split()
        if not tokens or len(tokens) > 2:
            continue

        matching_choices = sum(1 for choice in choices if choice.endswith(f" {line}"))
        if matching_choices >= 3:
            suffixes.add(line)

    return suffixes


def _best_match(
    cleaned: str,
    choices: list[str],
    choice_positions: dict[str, int],
    suffixes: set[str],
) -> tuple[str, float, int] | None:
    for suffix in suffixes:
        if suffix in cleaned.split():
            continue

        completed = f"{cleaned} {suffix}"
        if completed in choice_positions:
            return (completed, 100.0, choice_positions[completed])

        contextual_match = process.extractOne(completed, choices, scorer=fuzz.WRatio)
        if contextual_match and _has_meaningful_alignment(completed, contextual_match[0], contextual_match[1]):
            return contextual_match

    return process.extractOne(cleaned, choices, scorer=fuzz.WRatio)


def _trade_only_result(raw: str) -> dict[str, Any] | None:
    match = TRADE_ONLY_PATTERN.match(raw)
    if not match:
        return None

    kind = match.group(1).title()
    item_name = match.group("name").strip()
    return {
        "ocr_text": _display_ocr_text(raw),
        "matched": f"{kind}: {item_name}",
        "matched_key": normalize_text(item_name),
        "item_id": None,
        "api_id": None,
        "category": "Trade only",
        "price": None,
        "icon_url": None,
        "confidence": 100.0,
        "alignment_ok": True,
        "needs_review": True,
        "source": "trade_only",
        "message": f"{kind} gems are not on the currency exchange. Check trade instead.",
    }


def _matched_trade_only_result(
    raw: str,
    item: dict[str, Any],
    matched_name: str,
    score: float,
) -> dict[str, Any]:
    matched = _field(item, "text", "Text") or _field(item, "name", "Name") or matched_name
    return {
        "ocr_text": _display_ocr_text(raw),
        "matched": matched,
        "matched_key": matched_name,
        "item_id": _field(item, "item_id", "ItemId"),
        "api_id": _field(item, "api_id", "ApiId"),
        "category": "Trade only",
        "price": None,
        "icon_url": _field(item, "icon_url", "IconUrl"),
        "confidence": score,
        "alignment_ok": False,
        "needs_review": True,
        "source": "trade_only",
        "message": "Skill and support gems are not on the currency exchange. Check trade instead.",
    }


def _is_trade_only_category(category: Any) -> bool:
    return isinstance(category, str) and category.casefold() in TRADE_ONLY_CATEGORIES


def _candidate_names(item: dict[str, Any]) -> set[str]:
    names: set[str] = set()

    for key in ("text", "name", "type", "Text", "Name", "Type"):
        value = _field(item, key)
        if isinstance(value, str) and value.strip():
            names.add(value)

    text = _field(item, "text", "Text")
    name = _field(item, "name", "Name")
    item_type = _field(item, "type", "Type")
    if isinstance(name, str) and isinstance(item_type, str):
        names.add(f"{name} {item_type}")
    if isinstance(text, str) and isinstance(item_type, str):
        names.add(f"{text} {item_type}")

    return names


def _field(item: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in item:
            return item[key]
    return None


def _candidate_ocr_lines(lines: list[str]) -> list[str]:
    candidates: list[str] = []
    cleaned_lines: list[str] = []

    for line in lines:
        cleaned = line.strip()
        if not cleaned:
            continue
        if any(pattern.search(cleaned) for pattern in NOISE_PATTERNS):
            continue

        cleaned_lines.append(cleaned)
        candidates.append(cleaned)

        parts = [part.strip() for part in re.split(r"\s{2,}|[|]+", cleaned) if part.strip()]
        candidates.extend(part for part in parts if part != cleaned)

    for index, line in enumerate(cleaned_lines[:-1]):
        normalized = normalize_text(line)
        next_line = cleaned_lines[index + 1]
        next_normalized = normalize_text(next_line)
        if normalized.endswith("rune of") and 0 < len(next_normalized.split()) <= 2:
            candidates.append(f"{line} {next_line}")

    return candidates
