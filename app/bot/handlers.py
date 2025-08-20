"""Telegram command and callback handlers
- Multi-select wizard with checkmarks
- Multiple saved searches per user: /nuova, /modifica, /stato, /test(last)
"""
from __future__ import annotations

from typing import Dict, Any, List, Optional, Iterable
from itertools import product
from uuid import uuid4

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from app.bot.catalogs import fetch_categories, fetch_regioni, fetch_settori
from app.bot.inpa import build_payload, search
from app.bot.notifier import summarize_item
from app.bot.state import State


# ------------------------------------------------------------------------------
# Utilities
# ------------------------------------------------------------------------------

def _toggle(lst: List[Dict[str, str]], item: Dict[str, str]) -> bool:
    """Toggle presence of item by 'id' in lst. Return True if added, False if removed."""
    idx = next((i for i, e in enumerate(lst) if e["id"] == item["id"]), None)
    if idx is None:
        lst.append(item)
        return True
    else:
        lst.pop(idx)
        return False


def _fmt_names(items: Iterable[Dict[str, str]], empty_label: str) -> str:
    names = [it["name"] for it in items]
    return ", ".join(names) if names else empty_label


def _keyboard_multiselect(
    items: List[Dict[str, Any]],
    kind: str,
    show_none: bool,
    selected: List[Dict[str, str]],
) -> InlineKeyboardMarkup:
    """Inline keyboard with toggle rows + bottom actions and selection indicators."""
    selected_ids = {s["id"] for s in selected}
    rows = []
    for it in items[:48]:  # safety bound
        is_sel = it["id"] in selected_ids
        mark = "✅" if is_sel else "▫️"
        label = it["name"]
        if kind == "reg" and "count" in it and it["count"] is not None:
            label = f"{label} ({it['count']})"
        rows.append([InlineKeyboardButton(f"{mark} {label}", callback_data=f"toggle:{kind}:{it['id']}")])

    actions = [InlineKeyboardButton("✅ Fine selezione", callback_data=f"done:{kind}")]
    if show_none:
        actions.append(InlineKeyboardButton("❌ Nessun filtro", callback_data=f"none:{kind}"))
    rows.append(actions)
    return InlineKeyboardMarkup(rows)


def _yesno_keyboard(kind: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Sì", callback_data=f"yes:{kind}"),
         InlineKeyboardButton("No", callback_data=f"no:{kind}")]
    ])


def _first_or_none(items: List[Dict[str, str]]) -> Optional[Dict[str, str]]:
    return items[0] if items else None


def _build_payloads_from_search(s: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Create all payload combinations from a multi-search config."""
    cats = s.get("categorie", [])
    regs = s.get("regioni", [])
    sets_ = s.get("settori", [])

    cats_iter = cats or [None]   # UX richiede almeno una cat, ma per safety
    regs_iter = regs or [None]
    sets_iter = sets_ or [None]

    payloads = []
    for cat, reg, sett in product(cats_iter, regs_iter, sets_iter):
        if cat is None:
            continue
        payloads.append(build_payload(
            text=s["text"],
            categoria_id=cat["id"],
            regione_id=(reg["id"] if reg else None),
            settore_id=(sett["id"] if sett else None),
        ))
    return payloads


def _kb_categories(selected: List[Dict[str, str]]) -> InlineKeyboardMarkup:
    cats = fetch_categories()
    items = [{"id": c["id"], "name": c["name"]} for c in cats]
    return _keyboard_multiselect(items, "cat", show_none=False, selected=selected)


def _kb_regions(selected: List[Dict[str, str]]) -> InlineKeyboardMarkup:
    regs = fetch_regioni()
    return _keyboard_multiselect(regs, "reg", show_none=True, selected=selected)


def _kb_sectors(selected: List[Dict[str, str]]) -> InlineKeyboardMarkup:
    sets_ = fetch_settori()
    items = [{"id": s["id"], "name": s["name"]} for s in sets_]
    return _keyboard_multiselect(items, "set", show_none=True, selected=selected)


def _compute_label(cfg: Dict[str, Any]) -> str:
    """Build a short human label for a saved search."""
    cat = _fmt_names(cfg.get("categorie", []), "categoria?")
    reg = _fmt_names(cfg.get("regioni", []), "tutte le regioni")
    setr = _fmt_names(cfg.get("settori", []), "tutti i settori")
    text = cfg.get("text", "")
    base = f"{text} · {cat}"
    tail = []
    if cfg.get("regioni"):
        tail.append(reg)
    if cfg.get("settori"):
        tail.append(setr)
    if tail:
        base += " · " + " · ".join(tail)
    return (base[:64] + "…") if len(base) > 65 else base


# ------------------------------------------------------------------------------
# Commands
# ------------------------------------------------------------------------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE, state: State):
    await update.message.reply_text(
        "Ciao! Comandi utili:\n"
        "/nuova – crea una nuova ricerca\n"
        "/modifica – gestisci (elimina) le ricerche salvate\n"
        "/stato – mostra tutte le ricerche\n"
        "/test – prova l’ultima ricerca creata\n"
        "/reset – cancella la ricerca ‘singola’ legacy (se usata)\n"
    )


# /setup rimane come alias storico per /nuova
async def cmd_setup(update: Update, context: ContextTypes.DEFAULT_TYPE, state: State):
    await cmd_nuova(update, context, state)


async def cmd_nuova(update: Update, context: ContextTypes.DEFAULT_TYPE, state: State):
    """Start a fresh wizard to create a NEW saved search (appended to searches)."""
    chat_id = str(update.effective_chat.id)
    u = state.get_user(chat_id)
    u.setdefault("searches", [])
    u["draft"] = {"step": "text", "text": "", "categorie": [], "regioni": [], "settori": []}
    state.set_user(chat_id, u)
    await update.message.reply_text("Inserisci la/le parola/e chiave della tua ricerca (ricordati che la ricerca "
                                    "avviene solo sul testo esatto, pertanto potresti preferire utilizzare "
                                    "una parola chiave troncata, ad esempio \"bibliotec\" al posto di \"bibliotecario\", "
                                    "così da poter ricevere notifiche sia per bandi che contengono la parola \"bibliotecario\", "
                                    "sia per bandi che contengono la parola \"bibliotecaria\" o \"biblioteca\"): ")


async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE, state: State):
    """Legacy: clears the single 'search' and seen_ids list."""
    chat_id = str(update.effective_chat.id)
    u = state.get_user(chat_id)
    u["search"] = None
    u["seen_ids"] = []
    # Non tocca u["searches"]
    state.set_user(chat_id, u)
    await update.message.reply_text("✅ Ricerca singola legacy cancellata. Le ricerche multiple restano invariate.")


async def cmd_modifica(update: Update, context: ContextTypes.DEFAULT_TYPE, state: State):
    """List saved searches and offer delete buttons."""
    chat_id = str(update.effective_chat.id)
    u = state.get_user(chat_id)
    searches = u.get("searches", [])
    if not searches:
        await update.message.reply_text("Non hai ancora ricerche salvate. Usa /nuova per crearne una.")
        return

    # Build inline keyboard: one row per search with delete button
    rows = []
    for s in searches:
        label = s.get("label") or _compute_label(s)
        rows.append([InlineKeyboardButton(f"🗑️ Elimina: {label}", callback_data=f"del:search:{s['id']}")])
    kb = InlineKeyboardMarkup(rows)
    await update.message.reply_text("Ricerche salvate (tocca per eliminare):", reply_markup=kb)


async def cmd_stato(update: Update, context: ContextTypes.DEFAULT_TYPE, state: State):
    """Show all saved searches."""
    chat_id = str(update.effective_chat.id)
    u = state.get_user(chat_id)
    searches = u.get("searches", [])
    if not searches:
        await update.message.reply_text("Non hai ricerche salvate. Usa /nuova per crearne una.")
        return

    parts = ["📄 Ricerche attive:"]
    for i, s in enumerate(searches, start=1):
        parts.append(
            f"\n<b>#{i}</b> {s.get('label') or _compute_label(s)}\n"
            f"• Testo: {s['text']}\n"
            f"• Categorie: {_fmt_names(s.get('categorie', []), '(almeno una)')}\n"
            f"• Regioni: {_fmt_names(s.get('regioni', []), '(tutte)')}\n"
            f"• Settori: {_fmt_names(s.get('settori', []), '(tutti)')}"
        )
    await update.message.reply_text("\n".join(parts), parse_mode=ParseMode.HTML)


async def cmd_test(update: Update, context: ContextTypes.DEFAULT_TYPE, state: State, limit_preview: int = 3):
    """Run a one-off aggregated search for the LAST added saved search."""
    chat_id = str(update.effective_chat.id)
    u = state.get_user(chat_id)
    searches = u.get("searches", [])
    target = searches[-1] if searches else u.get("search")  # fallback to legacy
    if not target:
        await update.message.reply_text("Non hai ricerche. Usa /nuova per crearne una.")
        return

    payloads = _build_payloads_from_search(target)
    if not payloads:
        await update.message.reply_text("La ricerca non è valida: seleziona almeno una categoria.")
        return

    merged: List[Dict[str, Any]] = []
    seen_ids = set()
    errors = 0

    for p in payloads:
        try:
            data = search(p)
            for it in data.get("content", []):
                if it.get("id") not in seen_ids:
                    seen_ids.add(it["id"])
                    merged.append(it)
        except Exception:
            errors += 1

    if not merged:
        await update.message.reply_text("Nessun risultato trovato.")
        return

    merged.sort(key=lambda x: x.get("dataPubblicazione", ""), reverse=True)
    preview = merged[:limit_preview]
    header = f"🔎 Ricerca: {target.get('label') or _compute_label(target)}\n" \
             f"Risultati aggregati: {len(merged)} (da {len(payloads)} chiamate)."
    if errors:
        header += f" ⚠️ {errors} richieste fallite"
    await update.message.reply_text(header)

    for it in preview:
        await update.message.reply_text(summarize_item(it), parse_mode=ParseMode.HTML)


# ------------------------------------------------------------------------------
# Step handlers (wizard for /nuova)
# ------------------------------------------------------------------------------

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE, state: State):
    """Capture the text for the first step, then open multi-select categories."""
    chat_id = str(update.effective_chat.id)
    u = state.get_user(chat_id)
    d = u.get("draft")
    if not d or d.get("step") != "text":
        return

    d["text"] = (update.message.text or "").strip()
    d["step"] = "cat_multi"
    u["draft"] = d
    state.set_user(chat_id, u)

    kb = _kb_categories(d["categorie"])
    await update.message.reply_text(
        "Seleziona una o più <b>categorie</b> (obbligatorio). "
        "Tocca di nuovo per deselezionare. Premi <b>✅ Fine selezione</b> per continuare.",
        parse_mode=ParseMode.HTML,
        reply_markup=kb
    )


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, state: State):
    """Handle inline keyboard callbacks for toggle/done/yes-no and delete."""
    q = update.callback_query
    await q.answer()
    chat_id = str(update.effective_chat.id)
    u = state.get_user(chat_id)
    d = u.get("draft") or {}

    data = q.data.split(":")  # e.g., toggle:cat:<id> / done:cat / yes:reg / del:search:<id>
    action, kind = data[0], data[1]

    # -------------------- Delete saved search --------------------
    if action == "del" and kind == "search":
        sid = data[2]
        searches = u.get("searches", [])
        new_list = [s for s in searches if s.get("id") != sid]
        if len(new_list) == len(searches):
            await q.answer("Ricerca non trovata.")
            return
        u["searches"] = new_list
        state.set_user(chat_id, u)
        await q.edit_message_text("✅ Ricerca eliminata.")
        return

    # -------------------- CATEGORIE (multi, obbligatorio) --------------------
    if kind == "cat":
        if action == "toggle":
            cat_id = data[2]
            cats = fetch_categories()
            c = next(({"id": x["id"], "name": x["name"]} for x in cats if x["id"] == cat_id), None)
            if c:
                d.setdefault("categorie", [])
                added = _toggle(d["categorie"], c)
                u["draft"] = d
                state.set_user(chat_id, u)
                kb = _kb_categories(d["categorie"])
                await q.edit_message_reply_markup(reply_markup=kb)
                await q.answer(("Aggiunta " if added else "Rimossa ") + c["name"])
            return

        if action == "done":
            if not d.get("categorie"):
                await q.answer("Seleziona almeno una categoria.", show_alert=True)
                return
            d["step"] = "ask_regione"
            u["draft"] = d
            state.set_user(chat_id, u)
            kb = _yesno_keyboard("reg")
            await q.edit_message_text("Vuoi filtrare per regione?", reply_markup=kb)
            return

    # -------------------- REGIONE: sì/no, multi, opzionale -------------------
    if kind == "reg":
        if action == "yes":
            d["step"] = "reg_multi"
            u["draft"] = d
            state.set_user(chat_id, u)
            kb = _kb_regions(d.get("regioni", []))
            await q.edit_message_text(
                f"Seleziona una o più <b>regioni</b> (selezionate: {len(d.get('regioni', []))}).\n"
                "Tocca di nuovo per deselezionare. Premi <b>✅ Fine selezione</b> o <b>❌ Nessun filtro</b>.",
                parse_mode=ParseMode.HTML, reply_markup=kb
            )
            return

        if action == "no":
            d["regioni"] = []
            d["step"] = "ask_settore"
            u["draft"] = d
            state.set_user(chat_id, u)
            kb = _yesno_keyboard("set")
            await q.edit_message_text("Vuoi filtrare per settore?", reply_markup=kb)
            return

        if action == "toggle" and d.get("step") == "reg_multi":
            reg_id = data[2]
            regs = fetch_regioni()
            r = next(({"id": x["id"], "name": x["name"]} for x in regs if x["id"] == reg_id), None)
            if r:
                d.setdefault("regioni", [])
                added = _toggle(d["regioni"], r)
                u["draft"] = d
                state.set_user(chat_id, u)
                kb = _kb_regions(d["regioni"])
                await q.edit_message_reply_markup(reply_markup=kb)
                await q.answer(("Aggiunta " if added else "Rimossa ") + r["name"])
            return

        if action == "done" and d.get("step") == "reg_multi":
            d["step"] = "ask_settore"
            u["draft"] = d
            state.set_user(chat_id, u)
            kb = _yesno_keyboard("set")
            await q.edit_message_text("Vuoi filtrare per settore?", reply_markup=kb)
            return

        if action == "none" and d.get("step") == "reg_multi":
            d["regioni"] = []
            d["step"] = "ask_settore"
            u["draft"] = d
            state.set_user(chat_id, u)
            kb = _yesno_keyboard("set")
            await q.edit_message_text("Vuoi filtrare per settore?", reply_markup=kb)
            return

    # -------------------- SETTORE: sì/no, multi, opzionale -------------------
    if kind == "set":
        if action == "yes":
            d["step"] = "set_multi"
            u["draft"] = d
            state.set_user(chat_id, u)
            kb = _kb_sectors(d.get("settori", []))
            await q.edit_message_text(
                f"Seleziona uno o più <b>settori</b> (selezionati: {len(d.get('settori', []))}).\n"
                "Tocca di nuovo per deselezionare. Premi <b>✅ Fine selezione</b> o <b>❌ Nessun filtro</b>.",
                parse_mode=ParseMode.HTML, reply_markup=kb
            )
            return

        if action == "no":
            d["settori"] = []
            await _finalize_and_save(q, state, u, d)
            return

        if action == "toggle" and d.get("step") == "set_multi":
            set_id = data[2]
            sets_ = fetch_settori()
            s = next(({"id": x["id"], "name": x["name"]} for x in sets_ if x["id"] == set_id), None)
            if s:
                d.setdefault("settori", [])
                added = _toggle(d["settori"], s)
                u["draft"] = d
                state.set_user(chat_id, u)
                kb = _kb_sectors(d["settori"])
                await q.edit_message_reply_markup(reply_markup=kb)
                await q.answer(("Aggiunto " if added else "Rimosso ") + s["name"])
            return

        if action == "done" and d.get("step") == "set_multi":
            await _finalize_and_save(q, state, u, d)
            return

        if action == "none" and d.get("step") == "set_multi":
            d["settori"] = []
            await _finalize_and_save(q, state, u, d)
            return


# ------------------------------------------------------------------------------
# Finalization (append a new saved search)
# ------------------------------------------------------------------------------

async def _finalize_and_save(q, state: State, u: Dict[str, Any], d: Dict[str, Any]) -> None:
    """Persist the multi-search by APPENDING it to `searches`, and present a summary."""
    if not d.get("categorie"):
        await q.answer("Seleziona almeno una categoria.", show_alert=True)
        return

    new_search = {
        "id": str(uuid4()),
        "text": d.get("text", ""),
        "categorie": d.get("categorie", []),
        "regioni": d.get("regioni", []),
        "settori": d.get("settori", []),

        # legacy single fields for backward compatibility with current poller
        "categoria": _first_or_none(d.get("categorie", [])),
        "regione": _first_or_none(d.get("regioni", [])),
        "settore": _first_or_none(d.get("settori", [])),
    }
    new_search["label"] = _compute_label(new_search)

    # append to searches
    searches = u.setdefault("searches", [])
    searches.append(new_search)

    # also copy to legacy 'search' so current poller keeps working with last one
    u["search"] = new_search

    # clear draft
    u.pop("draft", None)
    state.set_user(str(q.message.chat_id), u)

    summary = (
        "✅ Nuova ricerca salvata:\n"
        f"• Nome: {new_search['label']}\n"
        f"• Testo: {new_search['text']}\n"
        f"• Categorie: {_fmt_names(new_search['categorie'], '(almeno una)')}\n"
        f"• Regioni: {_fmt_names(new_search['regioni'], '(tutte)')}\n"
        f"• Settori: {_fmt_names(new_search['settori'], '(tutti)')}\n\n"
        "Userò questa come ultima ricerca per /test; puoi gestire l’elenco con /modifica."
    )
    await q.edit_message_text(summary)
