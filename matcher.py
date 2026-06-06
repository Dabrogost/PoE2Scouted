from __future__ import annotations

import re
from typing import Any

from rapidfuzz import fuzz, process


NOISE_PATTERNS = (
    re.compile(r"^\d+(\.\d+)?$"),
    re.compile(r"^(level|requires|quality|armour|armor|evasion|energy shield|stack size)\b", re.I),
    re.compile(r"^(shift|ctrl|alt|right click|left click)\b", re.I),
)
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

    results: list[dict[str, Any]] = []
    seen_matches: set[tuple[str, str]] = set()

    for raw in _candidate_ocr_lines(ocr_lines):
        trade_only = _trade_only_result(raw)
        if trade_only is not None:
            results.append(trade_only)
            continue

        cleaned = normalize_text(raw)
        if len(cleaned) < 3:
            continue

        match = process.extractOne(cleaned, choices, scorer=fuzz.WRatio)
        if not match:
            continue

        matched_name, score, _ = match
        item = index[matched_name]
        category = _field(item, "category_api_id", "CategoryApiId")
        if _is_trade_only_category(category):
            results.append(_matched_trade_only_result(raw, item, matched_name, score))
            continue

        alignment_ok = _has_meaningful_alignment(cleaned, matched_name, score)
        dedupe_key = (
            cleaned,
            str(_field(item, "item_id", "ItemId") or _field(item, "api_id", "ApiId") or matched_name),
        )
        if dedupe_key in seen_matches:
            continue

        seen_matches.add(dedupe_key)
        results.append(
            {
                "ocr_text": raw,
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

    return sorted(results, key=_sort_key, reverse=True)[:limit]


def _sort_key(row: dict[str, Any]) -> tuple[int, float]:
    return (0 if row.get("source") == "trade_only" else 1, float(row.get("confidence") or 0))


def normalize_text(value: str) -> str:
    value = value.casefold()
    value = value.replace("'", "")
    value = re.sub(r"^\s*(skill|support)\s*:\s*", "", value)
    value = re.sub(r"[^a-z0-9+% -]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


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


def _trade_only_result(raw: str) -> dict[str, Any] | None:
    match = TRADE_ONLY_PATTERN.match(raw)
    if not match:
        return None

    kind = match.group(1).title()
    item_name = match.group("name").strip()
    return {
        "ocr_text": raw,
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
        "ocr_text": raw,
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

    for line in lines:
        cleaned = line.strip()
        if not cleaned:
            continue
        if any(pattern.search(cleaned) for pattern in NOISE_PATTERNS):
            continue

        candidates.append(cleaned)

        parts = [part.strip() for part in re.split(r"\s{2,}|[|]+", cleaned) if part.strip()]
        candidates.extend(part for part in parts if part != cleaned)

    return candidates
