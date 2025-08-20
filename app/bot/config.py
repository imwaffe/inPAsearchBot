"""Application configuration and endpoint constants.


This module centralizes environment-driven configuration and well-known API
endpoints used across the bot, so the rest of the codebase can import from
here without duplicating literals. It fails fast when critical variables are
missing (e.g., the Telegram bot token).
"""
from __future__ import annotations
import os


# --- Telegram & scheduling ---------------------------------------------------
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
if not TELEGRAM_BOT_TOKEN:
# Fail fast to surface misconfiguration at container start.
    raise RuntimeError("Missing TELEGRAM_BOT_TOKEN environment variable")


# Polling interval (minutes) for the background scheduler that queries INPA.
POLL_INTERVAL_MINUTES = int(os.environ.get("POLL_INTERVAL_MINUTES", "30"))


# Path to the JSON state file (mounted as a Docker volume for persistence).
DATA_FILE = os.environ.get("DATA_FILE", "/app/data/data.json")


# Default HTTP timeout (seconds) for all outbound requests.
HTTP_TIMEOUT = int(os.environ.get("HTTP_TIMEOUT", "30"))


# Timezone name used by the scheduler (informational; timestamps from the API
# are handled as-is in this baseline implementation).
TZ = os.environ.get("TZ", "Europe/Rome")


# --- INPA endpoints ----------------------------------------------------------
INPA_BASE = "https://portale.inpa.gov.it/concorsi-smart/api/concorso"
SEARCH_URL = f"{INPA_BASE}-public-area/search-better"
CATEGORIES_URL = f"{INPA_BASE}/get-categorie"
SETTORI_URL = f"{INPA_BASE}/get-settori"
REGIONI_URL = f"{INPA_BASE}/get-count-by-regione"