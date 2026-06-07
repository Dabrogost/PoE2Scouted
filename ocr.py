from __future__ import annotations

import io
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image


class OCREngineError(RuntimeError):
    pass


MODEL_DIR = Path(
    os.getenv(
        "EASYOCR_MODEL_DIR",
        str(Path.home() / ".cache" / "easyocr"),
    )
)
MAX_VERTICAL_MERGE_GAP = 14.0
MAX_HORIZONTAL_MERGE_GAP = 18.0


@dataclass
class OCRTextBox:
    text: str
    x_min: float
    y_min: float
    x_max: float
    y_max: float

    @property
    def width(self) -> float:
        return self.x_max - self.x_min

    @property
    def height(self) -> float:
        return self.y_max - self.y_min

    @property
    def x_center(self) -> float:
        return (self.x_min + self.x_max) / 2

    @property
    def y_center(self) -> float:
        return (self.y_min + self.y_max) / 2


def extract_text_lines(image_bytes: bytes) -> list[str]:
    reader = _easyocr_reader()

    with Image.open(io.BytesIO(image_bytes)) as image:
        image = image.convert("RGB")
        results = reader.readtext(_image_to_array(image), detail=1, paragraph=False)

    boxes = _text_boxes(results)
    if boxes:
        return _clean_lines(_group_text_boxes(boxes))

    return _clean_lines([_result_text(result) for result in results])


@lru_cache(maxsize=1)
def _easyocr_reader() -> Any:
    try:
        import easyocr
    except ImportError as exc:
        raise OCREngineError(
            "EasyOCR is not installed. Run `pip install -r requirements.txt`, then restart the server."
        ) from exc

    try:
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        return easyocr.Reader(
            ["en"],
            gpu=False,
            model_storage_directory=str(MODEL_DIR),
            user_network_directory=str(MODEL_DIR),
            verbose=False,
        )
    except Exception as exc:
        raise OCREngineError(f"EasyOCR failed to initialize: {exc}") from exc


def _image_to_array(image: Image.Image) -> Any:
    try:
        import numpy as np
    except ImportError as exc:
        raise OCREngineError("NumPy is required by the OCR pipeline.") from exc

    return np.asarray(image)


def _text_boxes(results: list[Any]) -> list[OCRTextBox]:
    boxes: list[OCRTextBox] = []

    for result in results:
        if not isinstance(result, (list, tuple)) or len(result) < 2:
            continue

        points = result[0]
        text = _result_text(result)
        if not text:
            continue

        try:
            xs = [float(point[0]) for point in points]
            ys = [float(point[1]) for point in points]
        except (TypeError, ValueError, IndexError):
            continue

        boxes.append(
            OCRTextBox(
                text=text,
                x_min=min(xs),
                y_min=min(ys),
                x_max=max(xs),
                y_max=max(ys),
            )
        )

    return boxes


def _result_text(result: Any) -> str:
    if isinstance(result, (list, tuple)) and len(result) >= 2:
        return re.sub(r"\s+", " ", str(result[1])).strip()

    return re.sub(r"\s+", " ", str(result)).strip()


def _group_text_boxes(boxes: list[OCRTextBox]) -> list[str]:
    groups: list[list[OCRTextBox]] = []

    for box in sorted(boxes, key=lambda item: (item.y_min, item.x_min)):
        target = _merge_target(groups, box)
        if target is None:
            groups.append([box])
        else:
            target.append(box)

    ordered_groups = sorted(groups, key=lambda group: (_group_y_min(group), _group_x_min(group)))
    return [_group_text(group) for group in ordered_groups]


def _merge_target(groups: list[list[OCRTextBox]], box: OCRTextBox) -> list[OCRTextBox] | None:
    candidates: list[tuple[float, list[OCRTextBox]]] = []

    for group in groups:
        if _same_item_group(group, box):
            distance = abs(box.y_center - _group_y_center(group)) + abs(box.x_center - _group_x_center(group)) / 8
            candidates.append((distance, group))

    if not candidates:
        return None

    return min(candidates, key=lambda item: item[0])[1]


def _same_item_group(group: list[OCRTextBox], box: OCRTextBox) -> bool:
    y_overlap = min(_group_y_max(group), box.y_max) - max(_group_y_min(group), box.y_min)
    x_gap = max(0.0, max(_group_x_min(group), box.x_min) - min(_group_x_max(group), box.x_max))

    if (
        y_overlap >= min(_group_height(group), box.height) * 0.45
        and x_gap <= MAX_HORIZONTAL_MERGE_GAP
        and _can_merge_horizontally(group, box, x_gap)
    ):
        return True

    vertical_gap = max(0.0, box.y_min - _group_y_max(group))
    x_overlap = min(_group_x_max(group), box.x_max) - max(_group_x_min(group), box.x_min)
    x_overlap_ratio = x_overlap / max(1.0, min(_group_width(group), box.width))

    return vertical_gap <= MAX_VERTICAL_MERGE_GAP and x_overlap_ratio >= 0.35 and _group_line_count(group) < 2


def _group_text(group: list[OCRTextBox]) -> str:
    lines: list[list[OCRTextBox]] = []

    for box in sorted(group, key=lambda item: (item.y_min, item.x_min)):
        line = next((candidate for candidate in lines if _same_text_line(candidate, box)), None)
        if line is None:
            lines.append([box])
        else:
            line.append(box)

    pieces: list[str] = []
    for line in sorted(lines, key=lambda item: _group_y_center(item)):
        pieces.append(" ".join(box.text for box in sorted(line, key=lambda item: item.x_min)))

    return " ".join(pieces)


def _can_merge_horizontally(group: list[OCRTextBox], box: OCRTextBox, x_gap: float) -> bool:
    group_text = " ".join(item.text for item in group)
    return x_gap <= 4 or _is_stack_count_text(group_text) or _is_stack_count_text(box.text)


def _is_stack_count_text(value: str) -> bool:
    return bool(re.fullmatch(r"\s*(?:\d+|[il])\s*x?\s*", value, re.I))


def _same_text_line(line: list[OCRTextBox], box: OCRTextBox) -> bool:
    y_overlap = min(_group_y_max(line), box.y_max) - max(_group_y_min(line), box.y_min)
    return y_overlap >= min(_group_height(line), box.height) * 0.45


def _group_line_count(group: list[OCRTextBox]) -> int:
    lines: list[list[OCRTextBox]] = []

    for box in sorted(group, key=lambda item: (item.y_min, item.x_min)):
        line = next((candidate for candidate in lines if _same_text_line(candidate, box)), None)
        if line is None:
            lines.append([box])
        else:
            line.append(box)

    return len(lines)


def _group_x_min(group: list[OCRTextBox]) -> float:
    return min(box.x_min for box in group)


def _group_x_max(group: list[OCRTextBox]) -> float:
    return max(box.x_max for box in group)


def _group_y_min(group: list[OCRTextBox]) -> float:
    return min(box.y_min for box in group)


def _group_y_max(group: list[OCRTextBox]) -> float:
    return max(box.y_max for box in group)


def _group_width(group: list[OCRTextBox]) -> float:
    return _group_x_max(group) - _group_x_min(group)


def _group_height(group: list[OCRTextBox]) -> float:
    return _group_y_max(group) - _group_y_min(group)


def _group_x_center(group: list[OCRTextBox]) -> float:
    return (_group_x_min(group) + _group_x_max(group)) / 2


def _group_y_center(group: list[OCRTextBox]) -> float:
    return (_group_y_min(group) + _group_y_max(group)) / 2


def _clean_lines(lines: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()

    for raw in lines:
        line = re.sub(r"\s+", " ", str(raw)).strip()
        if len(line) < 2:
            continue

        key = line.casefold()
        if key in seen:
            continue

        seen.add(key)
        cleaned.append(line)

    return cleaned
