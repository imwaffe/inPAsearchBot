"""Background scheduler for INPA polling with multi-search support.

- Per user, supports multiple saved searches in u["searches"].
- Maintains per-search seen_ids: u["seen_ids_map"][search_id] = [ids...]
- Backward compatibility:
    * If no u["searches"], falls back to legacy u["search"] + u["seen_ids"].
- For each search:
    * Build all combinations (categorie × regioni × settori)
    * Call INPA per combination
    * Merge + dedupe by `id`
    * Notify only NEW items (w.r.t. per-search seen_ids)
"""
from __future__ import annotations

from typing import Dict, Any, List, Iterable, Optional
from itertools import product

from apscheduler.schedulers.background import BackgroundScheduler

from .state import State
from .inpa import build_payload, search
from .notifier import tg_send, summarize_item


def _iter_payloads(search_cfg: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    """Yield payloads for all combinations of the multi-select filters."""
    cats = search_cfg.get("categorie", []) or [search_cfg.get("categoria")]  # tolerate legacy
    cats = [c for c in cats if c]  # drop None
    regs = search_cfg.get("regioni", [])
    sets_ = search_cfg.get("settori", [])

    # If lists are empty → meaning "all", substitute [None]
    regs_iter = regs or [None]
    sets_iter = sets_ or [None]

    for cat, reg, sett in product(cats, regs_iter, sets_ or [None]):
        yield build_payload(
            text=search_cfg["text"],
            categoria_id=cat["id"],
            regione_id=(reg["id"] if reg else None),
            settore_id=(sett["id"] if sett else None),
        )


def _label(search_cfg: Dict[str, Any]) -> str:
    """Human-friendly short label (already stored at creation, but compute if missing)."""
    if "label" in search_cfg and search_cfg["label"]:
        return search_cfg["label"]
    text = search_cfg.get("text", "")
    cat = ", ".join([c["name"] for c in search_cfg.get("categorie", [])]) or (
        search_cfg.get("categoria", {}) or {}
    ).get("name", "categoria?")
    reg = ", ".join([r["name"] for r in search_cfg.get("regioni", [])])
    setr = ", ".join([s["name"] for s in search_cfg.get("settori", [])])
    parts = [text, cat]
    if reg:
        parts.append(reg)
    if setr:
        parts.append(setr)
    s = " · ".join([p for p in parts if p])
    return (s[:64] + "…") if len(s) > 65 else s


class Poll:
    """Periodic task runner encapsulating the polling strategy."""
    def __init__(self, state: State, minutes: int):
        self.state = state
        self.scheduler = BackgroundScheduler(timezone="Europe/Rome")
        self.scheduler.add_job(
            self.run_once, "interval", minutes=minutes, max_instances=1, coalesce=True
        )

    def start(self) -> None:
        self.scheduler.start()

    def shutdown(self) -> None:
        self.scheduler.shutdown(wait=False)

    # ---------------- Core loop ----------------

    def run_once(self) -> None:
        users = self.state.all_users()
        for chat_id, u in users.items():
            searches: List[Dict[str, Any]] = u.get("searches", []) or []
            if not searches:
                # Legacy fallback: single search behavior
                legacy = u.get("search")
                if legacy:
                    self._process_one_search(chat_id, u, legacy, legacy_id="__legacy__")
                continue

            # Ensure per-search seen map exists
            seen_map: Dict[str, List[str]] = u.setdefault("seen_ids_map", {})
            changed = False

            for s in searches:
                sid = s.get("id") or _label(s)  # sid should be present; fallback safe
                changed |= self._process_one_search(chat_id, u, s, legacy_id=sid)

            if changed:
                # Persist only if we mutated seen_ids_map
                self.state.set_user(chat_id, u)

    # ---------------- Single-search processing ----------------

    def _process_one_search(self, chat_id: str, u: Dict[str, Any], search_cfg: Dict[str, Any], legacy_id: str) -> bool:
        """Return True if user's state changed (seen_ids updated)."""
        payloads = list(_iter_payloads(search_cfg))
        if not payloads:
            return False

        merged: List[Dict[str, Any]] = []
        merge_ids = set()
        errors = 0

        for p in payloads:
            try:
                data = search(p)
            except Exception:
                errors += 1
                continue
            for it in data.get("content", []):
                iid = it.get("id")
                if iid and iid not in merge_ids:
                    merge_ids.add(iid)
                    merged.append(it)

        if not merged:
            # Optionally inform user if all failed
            if errors and not merge_ids:
                tg_send(chat_id, f"⚠️ Errore controllo INPA ({_label(search_cfg)}): {errors} richieste fallite.")
            return False

        merged.sort(key=lambda x: x.get("dataPubblicazione", ""), reverse=True)

        # Determine the correct seen store:
        if u.get("searches"):
            # multi-search mode
            seen_map: Dict[str, List[str]] = u.setdefault("seen_ids_map", {})
            seen_for_this = set(seen_map.get(legacy_id, []))
        else:
            # legacy single-search
            seen_for_this = set(u.get("seen_ids", []))

        new_items = [it for it in merged if it.get("id") not in seen_for_this]
        if not new_items:
            return False

        # Notify (prefix label if multiple searches)
        label = _label(search_cfg)
        multi_mode = bool(u.get("searches"))
        for it in new_items:
            msg = ("🆕 <b>" + label + "</b>\n\n" if multi_mode else "🆕 Nuovo bando INPA:\n\n") + summarize_item(it)
            tg_send(chat_id, msg, disable_preview=True)

        # Record as seen
        ids = [it["id"] for it in new_items if "id" in it]
        if u.get("searches"):
            seen_map = u.setdefault("seen_ids_map", {})
            # extend + dedupe
            prev = seen_map.get(legacy_id, [])
            seen_map[legacy_id] = list(dict.fromkeys(prev + ids))
            return True
        else:
            # legacy single list
            prev = u.get("seen_ids", [])
            u["seen_ids"] = list(dict.fromkeys(prev + ids))
            return True
