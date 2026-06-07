from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests


API_BASE = "https://api.poe2scout.com"
DEFAULT_USER_AGENT = "poe2-screenshot-pricer/0.1 contact:dabrogost@gmail.com"
DEFAULT_CACHE_SECONDS = 15 * 60
BASE_DIR = Path(__file__).resolve().parent
CACHE_DIR = BASE_DIR / ".cache" / "poe2scout"
SNAPSHOT_PATH = BASE_DIR / "data" / "poe2scout_items_snapshot.json"


@dataclass(frozen=True)
class ItemData:
    items: list[dict[str, Any]]
    source: str
    age_seconds: float | None = None


class Poe2ScoutError(RuntimeError):
    pass


def fetch_items(
    realm: str,
    league: str,
    *,
    cache_seconds: int = DEFAULT_CACHE_SECONDS,
    force_refresh: bool = False,
) -> list[dict[str, Any]]:
    return fetch_item_data(
        realm=realm,
        league=league,
        cache_seconds=cache_seconds,
        force_refresh=force_refresh,
    ).items


def fetch_item_data(
    realm: str,
    league: str,
    *,
    cache_seconds: int = DEFAULT_CACHE_SECONDS,
    force_refresh: bool = False,
) -> ItemData:
    realm = realm.strip()
    league = league.strip()

    if not realm or not league:
        raise Poe2ScoutError("Realm and league are required.")

    cache_path = _cache_path(realm, league)

    if not force_refresh:
        cached = _read_cache_info(cache_path)
        if cached is not None:
            items, age_seconds = cached
            if age_seconds <= cache_seconds:
                return ItemData(items=items, source="cache", age_seconds=age_seconds)

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
        fallback = _fallback_item_data(cache_path, realm, league)
        if fallback is not None:
            return fallback
        raise Poe2ScoutError(f"Poe2Scout request failed: {exc}") from exc
    except ValueError as exc:
        fallback = _fallback_item_data(cache_path, realm, league)
        if fallback is not None:
            return fallback
        raise Poe2ScoutError("Poe2Scout returned invalid JSON.") from exc

    if not isinstance(payload, list):
        raise Poe2ScoutError("Poe2Scout returned an unexpected response shape.")

    _write_cache(cache_path, payload)
    return ItemData(items=payload, source="live", age_seconds=0.0)


def load_item_snapshot(realm: str | None = None, league: str | None = None) -> list[dict[str, Any]]:
    try:
        with SNAPSHOT_PATH.open("r", encoding="utf-8") as file:
            payload = json.load(file)
    except (OSError, ValueError):
        return []

    if isinstance(payload, list):
        return payload if all(isinstance(item, dict) for item in payload) else []

    if not isinstance(payload, dict):
        return []

    snapshot_realm = payload.get("realm")
    snapshot_league = payload.get("league")
    if realm is not None and snapshot_realm != realm:
        return []
    if league is not None and snapshot_league != league:
        return []

    items = payload.get("items")
    return items if isinstance(items, list) and all(isinstance(item, dict) for item in items) else []


def _cache_path(realm: str, league: str) -> Path:
    key = re.sub(r"[^a-zA-Z0-9_.-]+", "_", f"{realm}_{league}").strip("_")
    return CACHE_DIR / f"{key}.json"


def _read_cache(path: Path) -> list[dict[str, Any]] | None:
    try:
        with path.open("r", encoding="utf-8") as file:
            payload = json.load(file)
    except (OSError, ValueError):
        return None

    return payload if isinstance(payload, list) else None


def _read_cache_info(path: Path) -> tuple[list[dict[str, Any]], float] | None:
    if not path.exists():
        return None

    payload = _read_cache(path)
    if payload is None:
        return None

    return payload, max(0.0, time.time() - path.stat().st_mtime)


def _fallback_item_data(path: Path, realm: str, league: str) -> ItemData | None:
    cached = _read_cache_info(path)
    if cached is not None:
        items, age_seconds = cached
        return ItemData(items=items, source="stale_cache", age_seconds=age_seconds)

    snapshot = load_item_snapshot(realm=realm, league=league)
    if snapshot:
        return ItemData(items=snapshot, source="snapshot", age_seconds=None)

    return None


def _write_cache(path: Path, payload: list[dict[str, Any]]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as file:
            json.dump(payload, file)
    except OSError:
        pass
