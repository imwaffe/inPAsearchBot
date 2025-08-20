"""Thin client for the INPA search endpoint.


This module provides helpers to construct a valid payload respecting the
constraints discussed (single region/sector/category) and to perform the
actual POST request to the `search-better` endpoint.
"""
from __future__ import annotations
import requests
from typing import Dict, Any
from app.bot.config import SEARCH_URL, HTTP_TIMEOUT


def build_payload(text: str, categoria_id: str, regione_id: str | None, settore_id: str | None) -> Dict[str, Any]:
    """Build the request body for a search.

    Args:
    text: Free-text query (required).
    categoria_id: Category identifier (required).
    regione_id: Region identifier (optional; pass None to omit).
    settore_id: Sector identifier (optional; pass None to omit).
    Returns:
    A JSON-serializable dict suitable for sending to INPA.
    """
    return {
        "text": text,
        "categoriaId": categoria_id,
        "regioneId": regione_id,
        "status": ["OPEN"], # Always search for open cases
        "settoreId": settore_id,
        "provinciaCodice": None,  # For future use, currently not needed
    }




def search(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Execute the search against the inPA API endpoint.

    Args:
    payload: The body created by :func:`build_payload`.
    Returns:
    The decoded JSON response (dict with `content`, pagination, etc.).
    Raises:
    requests.HTTPError: If the endpoint returns a non-2xx status.
    """
    headers = {"Content-Type": "application/json"}
    r = requests.post(SEARCH_URL, json=payload, headers=headers, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r.json()