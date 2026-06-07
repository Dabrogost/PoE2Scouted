from __future__ import annotations

import re
from dataclasses import dataclass
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
STACK_COUNT_CAPTURE_PATTERN = re.compile(r"^\s*(?P<count>\d+|[il])\s*x\s+", re.I)
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
CATEGORY_HINTS = {
    "essence": "essences",
    "essences": "essences",
    "rune": "runes",
    "runes": "runes",
    "orb": "currency",
    "scrap": "currency",
    "shard": "currency",
}
TOKEN_CORRECTIONS = {
    "0f": "of",
    "ol": "of",
    "ot": "of",
    "tbe": "the",
    "thc": "the",
    "runc": "rune",
    "runes": "runes",
}
MAX_JOINED_LINES = 3


@dataclass(frozen=True)
class OCRCandidate:
    text: str
    source_indices: tuple[int, ...]
    generated: bool = False


@dataclass
class ItemLexicon:
    choices: list[str]
    choice_positions: dict[str, int]
    tokens: set[str]
    token_choices: list[str]
    prefix_counts: dict[str, int]
    suffix_counts: dict[str, int]
    choices_by_category: dict[str, list[str]]
    max_choice_tokens: int


def build_name_index(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}

    for item in items:
        for name in _candidate_names(item):
            normalized = normalize_text(name)
            if normalized:
                index[normalized] = item

    return index


def _lexicon_only_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    lexicon_items: list[dict[str, Any]] = []

    for item in items:
        copy = dict(item)
        copy["_lexicon_only"] = True
        copy["CurrentPrice"] = None
        copy["current_price"] = None
        lexicon_items.append(copy)

    return lexicon_items


def build_item_lexicon(index: dict[str, dict[str, Any]]) -> ItemLexicon:
    choices = list(index)
    choice_positions = {choice: position for position, choice in enumerate(choices)}
    tokens: set[str] = set()
    prefix_counts: dict[str, int] = {}
    suffix_counts: dict[str, int] = {}
    choices_by_category: dict[str, list[str]] = {}
    max_choice_tokens = 1

    for choice in choices:
        choice_tokens = choice.split()
        if not choice_tokens:
            continue

        max_choice_tokens = max(max_choice_tokens, len(choice_tokens))
        tokens.update(token for token in choice_tokens if len(token) > 1)

        item = index[choice]
        category = _field(item, "category_api_id", "CategoryApiId")
        if isinstance(category, str) and category:
            choices_by_category.setdefault(category.casefold(), []).append(choice)

        for length in range(1, len(choice_tokens)):
            prefix = " ".join(choice_tokens[:length])
            suffix = " ".join(choice_tokens[length:])
            prefix_counts[prefix] = prefix_counts.get(prefix, 0) + 1
            suffix_counts[suffix] = suffix_counts.get(suffix, 0) + 1

    token_choices = sorted(token for token in tokens if len(token) >= 3)
    return ItemLexicon(
        choices=choices,
        choice_positions=choice_positions,
        tokens=tokens,
        token_choices=token_choices,
        prefix_counts=prefix_counts,
        suffix_counts=suffix_counts,
        choices_by_category=choices_by_category,
        max_choice_tokens=max_choice_tokens,
    )


def match_ocr_lines(
    ocr_lines: list[str],
    items: list[dict[str, Any]],
    *,
    lexicon_items: list[dict[str, Any]] | None = None,
    min_score: int = 80,
    limit: int = 50,
) -> list[dict[str, Any]]:
    index = build_name_index([*_lexicon_only_items(lexicon_items or []), *items])
    lexicon = build_item_lexicon(index)
    if not lexicon.choices:
        return []

    results_by_key: dict[str, dict[str, Any]] = {}
    suffixes = _detected_item_suffixes(ocr_lines, lexicon)
    evaluated_rows: list[tuple[OCRCandidate, dict[str, Any]]] = []

    for candidate in _candidate_ocr_lines(ocr_lines, lexicon):
        row = _match_candidate(candidate, index, lexicon, suffixes, min_score)
        if row is not None:
            evaluated_rows.append((candidate, row))

    covered_indices = {
        index
        for candidate, row in evaluated_rows
        if candidate.generated
        and len(candidate.source_indices) > 1
        and row.get("source") == "poe2scout"
        and row.get("alignment_ok")
        for index in candidate.source_indices
    }

    for candidate, row in evaluated_rows:
        if row.get("source") == "unmatched" and set(candidate.source_indices).issubset(covered_indices):
            continue
        _store_result(results_by_key, row)

    return sorted(results_by_key.values(), key=_sort_key, reverse=True)[:limit]


def _match_candidate(
    candidate: OCRCandidate,
    index: dict[str, dict[str, Any]],
    lexicon: ItemLexicon,
    suffixes: set[str],
    min_score: int,
) -> dict[str, Any] | None:
    raw = candidate.text
    trade_only = _trade_only_result(raw)
    if trade_only is not None:
        return trade_only

    cleaned = normalize_text(raw)
    corrected = _correct_normalized_text(cleaned, lexicon)
    if len(corrected) < 3:
        return None
    if not _meaningful_tokens(corrected):
        return _unmatched_result(raw, "OCR fragment was too short to price.")
    if corrected in suffixes and corrected not in lexicon.choice_positions:
        return _unmatched_result(raw, "OCR fragment looks like part of a longer item name.")
    if _is_ambiguous_prefix(corrected, lexicon):
        return _unmatched_result(raw, "OCR text is an incomplete item-name prefix.")
    if _is_single_token_fragment(corrected, lexicon):
        return _unmatched_result(raw, "OCR text looks like an incomplete item-name fragment.")

    match = _best_match(corrected, lexicon, suffixes)
    if not match:
        return _unmatched_result(raw, "No likely Poe2Scout item match.")

    matched_name, score, _ = match
    item = index[matched_name]
    category = _field(item, "category_api_id", "CategoryApiId")
    if _is_trade_only_category(category):
        return _matched_trade_only_result(raw, item, matched_name, score)

    alignment_ok = _has_meaningful_alignment(corrected, matched_name, score)
    quantity = _quantity(raw)
    lexicon_only = bool(item.get("_lexicon_only"))
    unit_price = None if lexicon_only else _price_to_float(_field(item, "current_price", "CurrentPrice"))
    return {
        "ocr_text": _display_ocr_text(raw),
        "matched": _display_matched_item(item, matched_name),
        "matched_key": matched_name,
        "item_id": _field(item, "item_id", "ItemId"),
        "api_id": _field(item, "api_id", "ApiId"),
        "category": category,
        "price": _total_price(unit_price, quantity),
        "unit_price": unit_price,
        "quantity": quantity,
        "icon_url": _icon_url(item),
        "confidence": score,
        "alignment_ok": alignment_ok,
        "needs_review": score < min_score or not alignment_ok or lexicon_only,
        "source": "poe2scout",
        "message": "Recognized from bundled item snapshot, but no live Poe2Scout price was available."
        if lexicon_only
        else None,
    }


def _sort_key(row: dict[str, Any]) -> tuple[int, int, float, float]:
    price = _price_to_float(row.get("price"))
    source_rank = 2
    if row.get("source") == "unmatched":
        source_rank = 0
    elif row.get("source") == "trade_only":
        source_rank = 1

    return (
        source_rank,
        0 if price is None else 1,
        price if price is not None else float("-inf"),
        float(row.get("confidence") or 0),
    )


def _store_result(results_by_key: dict[str, dict[str, Any]], row: dict[str, Any]) -> None:
    key = _result_key(row)
    existing = results_by_key.get(key)
    if existing is not None and row.get("source") == existing.get("source") == "poe2scout":
        _merge_priced_rows(existing, row)
        return

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


def _merge_priced_rows(existing: dict[str, Any], row: dict[str, Any]) -> None:
    existing_quantity = int(existing.get("quantity") or 1)
    row_quantity = int(row.get("quantity") or 1)
    quantity = existing_quantity + row_quantity
    unit_price = _price_to_float(existing.get("unit_price"))

    existing["quantity"] = quantity
    existing["price"] = _total_price(unit_price, quantity)
    existing["ocr_text"] = _quantity_label(quantity, str(existing.get("matched") or row.get("matched") or "Matched item"))
    existing["confidence"] = max(float(existing.get("confidence") or 0), float(row.get("confidence") or 0))
    existing["alignment_ok"] = bool(existing.get("alignment_ok")) and bool(row.get("alignment_ok"))
    existing["needs_review"] = bool(existing.get("needs_review")) or bool(row.get("needs_review"))


def _price_to_float(price: Any) -> float | None:
    if price is None or price == "":
        return None

    try:
        return float(price)
    except (TypeError, ValueError):
        return None


def _total_price(unit_price: float | None, quantity: int) -> float | None:
    if unit_price is None:
        return None

    return unit_price * quantity


def _quantity(value: str) -> int:
    match = STACK_COUNT_CAPTURE_PATTERN.match(value)
    if not match:
        return 1

    count = match.group("count")
    if count.isdigit():
        return max(1, int(count))

    return 1


def _quantity_label(quantity: int, item_name: str) -> str:
    return f"{quantity}x {item_name}" if quantity > 1 else item_name


def normalize_text(value: str) -> str:
    value = _canonicalize_stack_count(value)
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
    value = _canonicalize_stack_count(value).strip()
    return re.sub(r"['\u2019]\s+s\b", "'s", value)


def _canonicalize_stack_count(value: str) -> str:
    match = STACK_COUNT_CAPTURE_PATTERN.match(value)
    if not match:
        return value

    quantity = _quantity(value)
    remainder = value[match.end() :].strip()
    return f"{quantity}x {remainder}" if remainder else f"{quantity}x"


def _correct_normalized_text(value: str, lexicon: ItemLexicon) -> str:
    tokens = value.split()
    corrected: list[str] = []

    for token in tokens:
        replacement = TOKEN_CORRECTIONS.get(token)
        if replacement:
            corrected.extend(replacement.split())
            continue

        corrected.append(_correct_token(token, lexicon))

    return " ".join(corrected)


def _correct_token(token: str, lexicon: ItemLexicon) -> str:
    if token in lexicon.tokens or len(token) < 4 or token.isdigit():
        return token
    if not lexicon.token_choices:
        return token

    for variant in _token_variants(token):
        if variant in lexicon.tokens:
            return variant

    matches = [
        process.extractOne(variant, lexicon.token_choices, scorer=fuzz.WRatio)
        for variant in (token, *_token_variants(token))
    ]
    matches = [match for match in matches if match]
    if not matches:
        return token

    matched_token, score, _ = max(matches, key=lambda item: item[1])
    length_delta = abs(len(token) - len(matched_token))
    if score >= 86 and length_delta <= max(2, int(len(token) * 0.35)):
        return matched_token

    return token


def _token_variants(token: str) -> list[str]:
    variants: set[str] = set()

    replacements = (
        ("rn", "m"),
        ("vv", "w"),
        ("0", "o"),
        ("1", "l"),
        ("5", "s"),
        ("8", "b"),
    )
    for old, new in replacements:
        if old in token:
            variants.add(token.replace(old, new))

    if token.endswith("i"):
        variants.add(f"{token[:-1]}l")

    variants.discard(token)
    return sorted(variants)


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


def _is_single_token_fragment(cleaned: str, lexicon: ItemLexicon) -> bool:
    tokens = _meaningful_tokens(cleaned)
    return len(tokens) == 1 and cleaned not in lexicon.choice_positions


def _is_ambiguous_prefix(cleaned: str, lexicon: ItemLexicon) -> bool:
    if len(cleaned.split()) < 2:
        return False

    return lexicon.prefix_counts.get(cleaned, 0) >= 3


def _detected_item_suffixes(ocr_lines: list[str], lexicon: ItemLexicon) -> set[str]:
    suffixes: set[str] = set()
    normalized_lines = {_correct_normalized_text(normalize_text(line), lexicon) for line in ocr_lines}

    for line in normalized_lines:
        tokens = line.split()
        if not tokens or len(tokens) > 2:
            continue

        if lexicon.suffix_counts.get(line, 0) >= 3:
            suffixes.add(line)

    return suffixes


def _best_match(
    cleaned: str,
    lexicon: ItemLexicon,
    suffixes: set[str],
) -> tuple[str, float, int] | None:
    if cleaned in lexicon.choice_positions:
        return (cleaned, 100.0, lexicon.choice_positions[cleaned])

    for suffix in suffixes:
        if suffix == cleaned or suffix in cleaned.split():
            continue

        completed = f"{cleaned} {suffix}"
        if completed in lexicon.choice_positions:
            return (completed, 100.0, lexicon.choice_positions[completed])

        contextual_match = _extract_choice_match(completed, _choice_pool(completed, lexicon), lexicon)
        if contextual_match and _has_meaningful_alignment(completed, contextual_match[0], contextual_match[1]):
            return contextual_match

    return _extract_choice_match(cleaned, _choice_pool(cleaned, lexicon), lexicon)


def _choice_pool(cleaned: str, lexicon: ItemLexicon) -> list[str]:
    tokens = set(cleaned.split())
    for token, category in CATEGORY_HINTS.items():
        if token in tokens and lexicon.choices_by_category.get(category):
            return lexicon.choices_by_category[category]

    return lexicon.choices


def _extract_choice_match(
    query: str,
    choices: list[str],
    lexicon: ItemLexicon,
) -> tuple[str, float, int] | None:
    match = process.extractOne(query, choices, scorer=fuzz.WRatio)
    if not match:
        return None

    matched_name, score, _ = match
    return (matched_name, score, lexicon.choice_positions.get(matched_name, 0))


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
        "unit_price": None,
        "quantity": 1,
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
        "unit_price": None,
        "quantity": _quantity(raw),
        "icon_url": _icon_url(item),
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

    text = _field(item, "text", "Text")
    name = _field(item, "name", "Name")
    item_type = _field(item, "type", "Type")
    if isinstance(text, str) and text.strip():
        names.add(text)

    if isinstance(name, str) and isinstance(item_type, str):
        names.add(f"{name} {item_type}")
    if isinstance(text, str) and isinstance(item_type, str):
        names.add(f"{text} {item_type}")
    if isinstance(name, str) and name.strip() and not isinstance(item_type, str):
        names.add(name)
    if isinstance(item_type, str) and item_type.strip() and not isinstance(name, str):
        names.add(item_type)

    return names


def _display_matched_item(item: dict[str, Any], matched_name: str) -> str:
    text = _field(item, "text", "Text")
    name = _field(item, "name", "Name")
    item_type = _field(item, "type", "Type")

    for value in (text, name, item_type):
        if isinstance(value, str) and normalize_text(value) == matched_name:
            return value

    for left, right in ((name, item_type), (text, item_type)):
        if isinstance(left, str) and isinstance(right, str):
            combined = f"{left} {right}".strip()
            if normalize_text(combined) == matched_name:
                return combined

    return " ".join(token.capitalize() for token in matched_name.split())


def _icon_url(item: dict[str, Any]) -> str | None:
    icon_url = _field(item, "icon_url", "IconUrl")
    if not isinstance(icon_url, str) or not icon_url:
        return None

    return re.sub(r"^(https?://[^/]+)/+", r"\1/", icon_url)


def _unmatched_result(raw: str, message: str) -> dict[str, Any]:
    return {
        "ocr_text": _display_ocr_text(raw),
        "matched": "No match",
        "matched_key": normalize_text(raw),
        "item_id": None,
        "api_id": None,
        "category": "Review",
        "price": None,
        "unit_price": None,
        "quantity": _quantity(raw),
        "icon_url": None,
        "confidence": 0.0,
        "alignment_ok": False,
        "needs_review": True,
        "source": "unmatched",
        "message": message,
    }


def _field(item: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in item:
            return item[key]
    return None


def _candidate_ocr_lines(lines: list[str], lexicon: ItemLexicon) -> list[OCRCandidate]:
    candidates: list[OCRCandidate] = []
    seen: set[tuple[tuple[int, ...], str]] = set()
    cleaned_lines: list[tuple[int, str]] = []

    for line_index, line in enumerate(lines):
        cleaned = _canonicalize_stack_count(line.strip())
        if not cleaned:
            continue
        if any(pattern.search(cleaned) for pattern in NOISE_PATTERNS):
            continue

        normalized = _correct_normalized_text(normalize_text(cleaned), lexicon)
        subphrases = _known_subphrase_candidates(normalized, lexicon)
        if len(subphrases) > 1:
            for subphrase in subphrases:
                _add_candidate(
                    candidates,
                    seen,
                    _humanize_normalized_text(subphrase),
                    (line_index,),
                    generated=True,
                )
        else:
            cleaned_lines.append((line_index, cleaned))
            _add_candidate(candidates, seen, cleaned, (line_index,))

            parts = [part.strip() for part in re.split(r"\s{2,}|[|]+", cleaned) if part.strip()]
            for part in parts:
                if part != cleaned:
                    _add_candidate(candidates, seen, part, (line_index,), generated=True)

    for start in range(len(cleaned_lines)):
        for end in range(start + 2, min(len(cleaned_lines), start + MAX_JOINED_LINES) + 1):
            window = cleaned_lines[start:end]
            text = " ".join(line for _, line in window)
            if _should_join_lines(text, [line for _, line in window], lexicon):
                _add_candidate(
                    candidates,
                    seen,
                    text,
                    tuple(line_index for line_index, _ in window),
                    generated=True,
                )

    return candidates


def _add_candidate(
    candidates: list[OCRCandidate],
    seen: set[tuple[tuple[int, ...], str]],
    text: str,
    source_indices: tuple[int, ...],
    *,
    generated: bool = False,
) -> None:
    normalized = normalize_text(text)
    key = (source_indices, normalized)
    if not normalized or key in seen:
        return

    seen.add(key)
    candidates.append(OCRCandidate(text=text, source_indices=source_indices, generated=generated))


def _known_subphrase_candidates(normalized: str, lexicon: ItemLexicon) -> list[str]:
    tokens = normalized.split()
    if len(tokens) < 2:
        return []

    matches: list[tuple[int, int, str]] = []
    max_length = min(lexicon.max_choice_tokens, len(tokens))
    for start in range(len(tokens)):
        for end in range(min(len(tokens), start + max_length), start, -1):
            phrase = " ".join(tokens[start:end])
            if phrase in lexicon.choice_positions:
                matches.append((start, end, phrase))
                break

    selected: list[str] = []
    last_end = 0
    for start, end, phrase in sorted(matches):
        if start >= last_end:
            selected.append(phrase)
            last_end = end

    return selected if len(selected) > 1 else []


def _should_join_lines(text: str, parts: list[str], lexicon: ItemLexicon) -> bool:
    cleaned = _correct_normalized_text(normalize_text(text), lexicon)
    if cleaned in lexicon.choice_positions:
        return True

    part_is_fragment = any(
        _is_ambiguous_prefix(_correct_normalized_text(normalize_text(part), lexicon), lexicon)
        or _is_single_token_fragment(_correct_normalized_text(normalize_text(part), lexicon), lexicon)
        for part in parts
    )
    if not part_is_fragment:
        return False

    match = _extract_choice_match(cleaned, _choice_pool(cleaned, lexicon), lexicon)
    return bool(match and match[1] >= 94 and _has_meaningful_alignment(cleaned, match[0], match[1]))


def _humanize_normalized_text(value: str) -> str:
    small_words = {"a", "an", "and", "for", "of", "the", "to"}
    words = []
    for token in value.split():
        words.append(token if token in small_words else token.capitalize())

    return " ".join(words)
