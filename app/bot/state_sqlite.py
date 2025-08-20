from __future__ import annotations
import os
import sqlite3
from typing import Any, Dict, List, Tuple, Optional
from contextlib import contextmanager
from datetime import datetime


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS users (
  chat_id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS searches (
  id TEXT PRIMARY KEY,
  chat_id TEXT NOT NULL,
  text TEXT NOT NULL,
  label TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY (chat_id) REFERENCES users(chat_id) ON DELETE CASCADE
);

-- kind: 'category' | 'region' | 'sector'
CREATE TABLE IF NOT EXISTS search_filters (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  search_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  value_id TEXT NOT NULL,
  value_name TEXT NOT NULL,
  FOREIGN KEY (search_id) REFERENCES searches(id) ON DELETE CASCADE
);

-- per-search seen items
CREATE TABLE IF NOT EXISTS seen_items (
  search_id TEXT NOT NULL,
  item_id TEXT NOT NULL,
  PRIMARY KEY (search_id, item_id),
  FOREIGN KEY (search_id) REFERENCES searches(id) ON DELETE CASCADE
);

-- legacy single-search support
CREATE TABLE IF NOT EXISTS legacy_seen (
  chat_id TEXT NOT NULL,
  item_id TEXT NOT NULL,
  PRIMARY KEY (chat_id, item_id),
  FOREIGN KEY (chat_id) REFERENCES users(chat_id) ON DELETE CASCADE
);
"""


def _dict_search_row(row: Tuple) -> Dict[str, Any]:
    return {
        "id": row[0],
        "text": row[2],
        "label": row[3],
    }


@contextmanager
def _conn(db_path: str):
    con = sqlite3.connect(db_path, timeout=30, isolation_level=None)  # autocommit mode
    try:
        con.execute("PRAGMA journal_mode=WAL;")
        con.execute("PRAGMA foreign_keys=ON;")
        yield con
    finally:
        con.close()


class State:
    """SQLite-backed state with the same public API as the JSON-based State.

    Persisted:
      - users, searches (multi), filters, seen_ids per search (seen_items)
      - legacy seen_ids (legacy_seen), legacy single search compatible
    In-memory only:
      - draft per-utente (wizard transient state)
    """

    def __init__(self, db_path: str = "data/state.db"):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.db_path = db_path
        self._drafts: Dict[str, Dict[str, Any]] = {}
        with _conn(self.db_path) as con:
            for stmt in SCHEMA.split(";"):
                s = stmt.strip()
                if s:
                    con.execute(s + ";")

    # ---------------- Helpers ----------------

    def _ensure_user(self, con: sqlite3.Connection, chat_id: str) -> None:
        con.execute(
            "INSERT OR IGNORE INTO users(chat_id) VALUES (?)",
            (chat_id,),
        )

    def _load_searches_for_user(self, con: sqlite3.Connection, chat_id: str) -> List[Dict[str, Any]]:
        srows = con.execute(
            "SELECT id, chat_id, text, label, created_at FROM searches WHERE chat_id=? ORDER BY created_at ASC",
            (chat_id,),
        ).fetchall()

        searches: List[Dict[str, Any]] = []
        for sr in srows:
            sdict = _dict_search_row(sr)
            # load filters
            frows = con.execute(
                "SELECT kind, value_id, value_name FROM search_filters WHERE search_id=?",
                (sdict["id"],),
            ).fetchall()
            cats, regs, sets = [], [], []
            for kind, vid, vname in frows:
                if kind == "category":
                    cats.append({"id": vid, "name": vname})
                elif kind == "region":
                    regs.append({"id": vid, "name": vname})
                elif kind == "sector":
                    sets.append({"id": vid, "name": vname})
            sdict["categorie"] = cats
            sdict["regioni"] = regs
            sdict["settori"] = sets

            # legacy single-fields for compatibility (first-or-none)
            sdict["categoria"] = cats[0] if cats else None
            sdict["regione"] = regs[0] if regs else None
            sdict["settore"] = sets[0] if sets else None

            searches.append(sdict)
        return searches

    # ---------------- Public API (compat) ----------------

    def get_user(self, chat_id: str) -> Dict[str, Any]:
        """Return a dict compatible with the previous JSON state.

        Structure:
          {
            "searches": [...],
            "search": last or None (legacy),
            "seen_ids_map": { search_id: [ids...] },
            "seen_ids": [ids...] (legacy),
            "draft": {...} (in-memory only)
          }
        """
        with _conn(self.db_path) as con:
            self._ensure_user(con, chat_id)
            out: Dict[str, Any] = {}
            # searches
            searches = self._load_searches_for_user(con, chat_id)
            out["searches"] = searches
            out["search"] = (searches[-1] if searches else None)

            # seen_ids_map
            seen_map: Dict[str, List[str]] = {}
            for s in searches:
                rows = con.execute(
                    "SELECT item_id FROM seen_items WHERE search_id=?",
                    (s["id"],),
                ).fetchall()
                seen_map[s["id"]] = [r[0] for r in rows]
            out["seen_ids_map"] = seen_map

            # legacy seen_ids
            lrows = con.execute(
                "SELECT item_id FROM legacy_seen WHERE chat_id=?",
                (chat_id,),
            ).fetchall()
            out["seen_ids"] = [r[0] for r in lrows]

            # draft in-memory
            out["draft"] = self._drafts.get(chat_id)
            return out

    def set_user(self, chat_id: str, u: Dict[str, Any]) -> None:
        """Persist stable fields (searches, seen maps). Keeps `draft` in-memory.

        Expected keys in `u` (optional):
          - "searches": list of search dicts (id, text, label, categorie/regioni/settori)
          - "search": last search (ignored if searches present; derived automatically)
          - "seen_ids_map": {search_id: [item_ids]}
          - "seen_ids": [item_ids] (legacy)
          - "draft": in-memory only
        """
        with _conn(self.db_path) as con:
            self._ensure_user(con, chat_id)

            # handle drafts only in memory
            if "draft" in u:
                if u["draft"] is None:
                    self._drafts.pop(chat_id, None)
                else:
                    self._drafts[chat_id] = u["draft"]

            # Upsert searches
            if "searches" in u and isinstance(u["searches"], list):
                # get existing search ids for the user
                existing = set(
                    r[0] for r in con.execute(
                        "SELECT id FROM searches WHERE chat_id=?",
                        (chat_id,),
                    ).fetchall()
                )
                incoming_ids = set()

                for s in u["searches"]:
                    sid = s["id"]
                    incoming_ids.add(sid)
                    # upsert search
                    con.execute(
                        "INSERT INTO searches(id, chat_id, text, label) VALUES(?,?,?,?) "
                        "ON CONFLICT(id) DO UPDATE SET text=excluded.text, label=excluded.label",
                        (sid, chat_id, s["text"], s.get("label")),
                    )
                    # Replace filters for this search
                    con.execute("DELETE FROM search_filters WHERE search_id=?", (sid,))
                    for cat in s.get("categorie", []):
                        con.execute(
                            "INSERT INTO search_filters(search_id, kind, value_id, value_name) VALUES (?,?,?,?)",
                            (sid, "category", cat["id"], cat["name"]),
                        )
                    for reg in s.get("regioni", []):
                        con.execute(
                            "INSERT INTO search_filters(search_id, kind, value_id, value_name) VALUES (?,?,?,?)",
                            (sid, "region", reg["id"], reg["name"]),
                        )
                    for sett in s.get("settori", []):
                        con.execute(
                            "INSERT INTO search_filters(search_id, kind, value_id, value_name) VALUES (?,?,?,?)",
                            (sid, "sector", sett["id"], sett["name"]),
                        )

                # delete searches removed in incoming list
                to_delete = list(existing - incoming_ids)
                if to_delete:
                    con.executemany("DELETE FROM searches WHERE id=?", [(x,) for x in to_delete])

            # seen_ids_map upsert
            if "seen_ids_map" in u and isinstance(u["seen_ids_map"], dict):
                for sid, ids in u["seen_ids_map"].items():
                    # insert ignore duplicates
                    con.executemany(
                        "INSERT OR IGNORE INTO seen_items(search_id, item_id) VALUES (?,?)",
                        [(sid, iid) for iid in ids],
                    )

            # legacy seen_ids
            if "seen_ids" in u and isinstance(u["seen_ids"], list):
                con.executemany(
                    "INSERT OR IGNORE INTO legacy_seen(chat_id, item_id) VALUES (?,?)",
                    [(chat_id, iid) for iid in u["seen_ids"]],
                )

    def append_seen(self, chat_id: str, ids: List[str], search_id: Optional[str] = None) -> None:
        """Append new seen ids.

        If `search_id` is provided → add to per-search seen table.
        Else → legacy list per user.
        """
        if not ids:
            return
        with _conn(self.db_path) as con:
            self._ensure_user(con, chat_id)
            if search_id:
                con.executemany(
                    "INSERT OR IGNORE INTO seen_items(search_id, item_id) VALUES (?,?)",
                    [(search_id, iid) for iid in ids],
                )
            else:
                con.executemany(
                    "INSERT OR IGNORE INTO legacy_seen(chat_id, item_id) VALUES (?,?)",
                    [(chat_id, iid) for iid in ids],
                )

    def all_users(self) -> Dict[str, Dict[str, Any]]:
        """Return a dict {chat_id: user_dict} for compatibility with existing code.

        Per scalare davvero, conviene introdurre un iteratore che restituisca
        direttamente (chat_id, search) per alimentare il poller; ma questa API
        mantiene la compatibilità con l’implementazione attuale.
        """
        out: Dict[str, Dict[str, Any]] = {}
        with _conn(self.db_path) as con:
            rows = con.execute("SELECT chat_id FROM users").fetchall()
            for (chat_id,) in rows:
                out[chat_id] = self.get_user(chat_id)
        return out
