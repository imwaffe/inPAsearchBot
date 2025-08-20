"""JSON-backed persistence layer (no database required).


Structure stored on disk (single file):


{
    "users": {
        "<chat_id>": {
            "search": {
                "text": str, # required
                "categoria": {"id": str, "name": str}, # required
                "regione": {"id": str, "name": str} | None,
                "settore": {"id": str, "name": str} | None
            },
            "seen_ids": ["<concorso_id>", ...]
        }
    }
}


Design goals:
- Keep it threadsafe (scheduler + bot handlers) via a module-level lock.
- Fail safe and atomic writes (write to temp file, then replace).
- Keep file small: cap the number of stored `seen_ids`.
"""
from __future__ import annotations
import json, os, threading
from typing import Any, Dict


_lock = threading.Lock()


class State:
    """Encapsulates read/write operations to the JSON state file.

    The class provides small, focused methods that operate on user-specific
    nodes. It does not implement business logic; callers compose higher-level
    behavior (e.g., appending new seen IDs after notifications are sent).
    """


    def __init__(self, path: str):
        """Create the state file if missing and ensure parent directory exists.
        Args:
        path: Absolute path to the JSON file.
        """
        self.path = path
        if not os.path.exists(os.path.dirname(path)):
            os.makedirs(os.path.dirname(path), exist_ok=True)
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"users": {}}, f)


    # --- Low-level helpers ---------------------------------------------------
    def _load(self) -> Dict[str, Any]:
        """Read and parse the JSON file into a Python dict."""
        with open(self.path, "r", encoding="utf-8") as f:
            return json.load(f)


    def _save(self, obj: Dict[str, Any]) -> None:
        """Atomically write the JSON file by using a temp file + replace."""
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)


    # --- User-level operations ----------------------------------------------
    def get_user(self, chat_id: str) -> Dict[str, Any]:
        """Return (and create if missing) the node for a given Telegram chat.
        Args:
        chat_id: The Telegram chat identifier as string.
        Returns:
        The user dict with keys `search` and `seen_ids`.
        """
        with _lock:
            data = self._load()
            return data["users"].setdefault(chat_id, {"search": None, "seen_ids": []})


    def set_user(self, chat_id: str, user_obj: Dict[str, Any]) -> None:
        """Persist the full user node for the given chat id."""
        with _lock:
            data = self._load()
            data["users"][chat_id] = user_obj
            self._save(data)


    def append_seen(self, chat_id: str, ids: list[str], cap: int = 500) -> None:
        """Append concorso IDs to `seen_ids`, deduplicating and capping length.
        Args:
        chat_id: Telegram chat id.
        ids: Iterable of concorso identifiers just notified.
        cap: Maximum number of IDs to retain to keep the file small.
        """
        with _lock:
            data = self._load()
            u = data["users"].setdefault(chat_id, {"search": None, "seen_ids": []})
            seen = u.setdefault("seen_ids", [])
            for i in ids:
                if i not in seen:
                    seen.append(i)
            if len(seen) > cap:
                u["seen_ids"] = seen[-cap:]
            self._save(data)


    def all_users(self) -> Dict[str, Any]:
        """Return the entire `users` mapping (read-only snapshot)."""
        with _lock:
            return self._load().get("users", {})