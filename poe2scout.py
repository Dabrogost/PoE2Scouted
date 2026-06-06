from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests


API_BASE = "https://api.poe2scout.com"
DEFAULT_USER_AGENT = "local-poe2-screenshot-pricer/0.1 contact:kernskaden@gmail.com"
DEFAULT_CACHE_SECONDS = 15 * 60
BASE_DIR = Path(__file__).resolve().parent
CACHE_DIR = BASE_DIR / ".cache" / "poe2scout"


class Poe2ScoutError(RuntimeError):
    pass


def fetch_items(
    realm: str,
    league: str,
    *,
    cache_seconds: int = DEFAULT_CACHE_SECONDS,
    force_refresh: bool = False,
) -> list[dict[str, Any]]:
    realm = realm.strip()
    league = league.strip()

    if not realm or not league:
        raise Poe2ScoutError("Realm and league are required.")

    cache_path = _cache_path(realm, league)

    if not force_refresh:
        cached = _read_fresh_cache(cache_path, cache_seconds)
        if cached is not None:
            return cached

    url = f"{API_BASE}/{quote(realm, safe='')}/Leagues/{quote(league, safe='')}/Items"
    headers = {
        "Accept": "application/json",
        "User-Agent": os.getenv("POE2SCOUT_USER_AGENT", DEFAULT_USER_AGENT),
    }

    try:
        response = requests.get(url, headers=headers, timeout=20)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        stale = _read_cache(cache_path)
        if stale is not None:
            return stale
        raise Poe2ScoutError(f"Poe2Scout request failed: {exc}") from exc
    except ValueError as exc:
        raise Poe2ScoutError("Poe2Scout returned invalid JSON.") from exc

    if not isinstance(payload, list):
        raise Poe2ScoutError("Poe2Scout returned an unexpected response shape.")

    _write_cache(cache_path, payload)
    return payload


def _cache_path(realm: str, league: str) -> Path:
    key = re.sub(r"[^a-zA-Z0-9_.-]+", "_", f"{realm}_{league}").strip("_")
    return CACHE_DIR / f"{key}.json"


def _read_fresh_cache(path: Path, cache_seconds: int) -> list[dict[str, Any]] | None:
    if not path.exists():
        return None
    if time.time() - path.stat().st_mtime > cache_seconds:
        return None
    return _read_cache(path)


def _read_cache(path: Path) -> list[dict[str, Any]] | None:
    try:
        with path.open("r", encoding="utf-8") as file:
            payload = json.load(file)
    except (OSError, ValueError):
        return None

    return payload if isinstance(payload, list) else None


def _write_cache(path: Path, payload: list[dict[str, Any]]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as file:
            json.dump(payload, file)
    except OSError:
        pass
