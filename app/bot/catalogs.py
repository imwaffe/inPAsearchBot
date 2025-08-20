"""Catalog retrieval utilities for categories, sectors, and regions.


These functions encapsulate HTTP calls to the public INPA endpoints that
expose vocabularies used to build the setup wizard (human-readable names and
IDs). Consumers should handle caching if repeated refreshes are desired.
"""
from __future__ import annotations
import requests
from typing import List, Dict, Any
from app.bot.config import CATEGORIES_URL, SETTORI_URL, REGIONI_URL, HTTP_TIMEOUT


def fetch_categories() -> List[Dict[str, Any]]:
    """Get the list of categories.

    Returns:
    A list of dicts, at least with keys: `id`, `name`.
    Raises:
    requests.HTTPError: If the endpoint returns a non-2xx status.
    """
    r = requests.get(CATEGORIES_URL, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r.json()


def fetch_settori() -> List[Dict[str, Any]]:
    """Get the list of sectors (settori)."""
    r = requests.get(SETTORI_URL, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r.json()


def fetch_regioni() -> List[Dict[str, Any]]:
    """Get the list of regions and normalize field names.


    The raw endpoint returns `zonaId` and `zonaDenominazione`. This function
    maps them into a consistent structure `{id, name, count}` for downstream
    UI code.
    """
    r = requests.get(REGIONI_URL, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    raw = r.json()
    return [
        {"id": x["zonaId"], "name": x["zonaDenominazione"], "count": x.get("concorsiCount", 0)}
        for x in raw
    ]