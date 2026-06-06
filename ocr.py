from __future__ import annotations

import io
import os
import re
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


def extract_text_lines(image_bytes: bytes) -> list[str]:
    reader = _easyocr_reader()

    with Image.open(io.BytesIO(image_bytes)) as image:
        image = image.convert("RGB")
        results = reader.readtext(_image_to_array(image), detail=0, paragraph=False)

    return _clean_lines(results)


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
