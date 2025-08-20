# app/bot/notifier.py
from __future__ import annotations
import html
import json
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Dict, Any, List, Optional

from telegram.constants import ParseMode

from .config import TELEGRAM_BOT_TOKEN, HTTP_TIMEOUT

TZ = ZoneInfo("Europe/Rome")

I18N_IT = {
    "published": "Pubblicato",
    "deadline": "Scadenza",
    "agency": "Ente",
    "locations": "Sedi",
    "details": "Apri la pagina su inPA",
    "apply": "Candidati",
    "new_item": "🆕 Nuovo bando INPA",
    "digest_title": "🔎 Nuovi bandi INPA",
    "professionist": "Figura ricercata",
    "procedure": "Tipo di procedura",
}


def fmt_dt_iso_to_local(iso: Optional[str]) -> str:
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(TZ)
        return dt.strftime("%d/%m/%Y %H:%M")
    except Exception:
        return iso


def escape_html_text(s: Optional[str]) -> str:
    return s


def truncate(s: str, limit: int = 3500) -> str:
    if len(s) <= limit:
        return s
    return s[:limit - 1] + "…"


def item_urls(it: Dict[str, Any]) -> Dict[str, str]:
    details = f"https://www.inpa.gov.it/bandi-e-avvisi/dettaglio-bando-avviso/?concorso_id={it['id']}"
    apply_link = it.get("linkReindirizzamento")
    return {"details": details, "apply": apply_link}


def build_buttons(it: Dict[str, Any]) -> Optional[str]:
    """Return JSON-serialized InlineKeyboardMarkup or None."""
    urls = item_urls(it)
    buttons = [[{"text": "🔗 Dettagli", "url": urls["details"]}]]
    if urls["apply"]:
        buttons[0].append({"text": "📨 Candidati", "url": urls["apply"]})
    markup = {"inline_keyboard": buttons}
    return json.dumps(markup)


def summarize_item(it: Dict[str, Any], lang: Dict[str, str] = I18N_IT) -> str:
    title = escape_html_text(it.get("titolo", "(senza titolo)"))
    code  = escape_html_text(it.get("codice", "")) or ""
    ente  = escape_html_text(", ".join(it.get("entiRiferimento", [])) or "—")
    sedi  = escape_html_text(", ".join(it.get("sedi", [])) or "—")
    pubb  = escape_html_text(fmt_dt_iso_to_local(it.get("dataPubblicazione")))
    scad  = escape_html_text(fmt_dt_iso_to_local(it.get("dataScadenza")))
    prof  = escape_html_text(it.get("figuraRicercata", {}) or "")
    proc  = escape_html_text(it.get("tipoProcedura", {}) or "")

    parts = [
        f"<b>{title}</b>\n",
        f"Codice: <code>{code}</code>\n\n" if code else "",
        f"👷🏽 <b>{lang['professionist']}</b>: {prof}",
        f"📚 <b>{lang['procedure']}</b>: {proc}\n"
        f"🏫 <b>{lang['agency']}</b>: {ente}",
        f"📍 <b>{lang['locations']}</b>: {sedi}",
        f"📅 <b>{lang['published']}</b>: {pubb}",
        f"⏰ <b>{lang['deadline']}</b>: {scad}\n",
        f"🔗 <a href=\"{html.escape(item_urls(it)['details'])}\">{lang['details']}</a>",
    ]
    msg = "\n".join([p for p in parts if p])
    return truncate(msg)


def summarize_digest_html(items: List[Dict[str, Any]], max_items: int = 5, lang: Dict[str, str] = I18N_IT) -> str:
    hdr = f"<b>{lang['digest_title']}</b>"
    rows = []
    for i, it in enumerate(items[:max_items], start=1):
        title = escape_html_text(it.get("titolo", "(senza titolo)"))
        urls = item_urls(it)
        # Righe compatte con link sui dettagli
        rows.append(f"{i}. <a href=\"{html.escape(urls['details'])}\">{title}</a>")
    if len(items) > max_items:
        rows.append(f"… (+{len(items) - max_items})")
    body = "\n".join(rows)
    return truncate(hdr + "\n\n" + body)

def tg_send(
    chat_id: str,
    text: str,
    *,
    parse_mode: str = ParseMode.HTML,
    disable_preview: bool = True,
    reply_markup_json: Optional[str] = None,
) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": str(disable_preview).lower(),
    }
    if reply_markup_json:
        data["reply_markup"] = reply_markup_json
    try:
        requests.post(url, data=data, timeout=HTTP_TIMEOUT)
    except Exception as e:
        print("Telegram send error:", e)


def send_item(chat_id: str, it: Dict[str, Any]) -> None:
    """Send a single rich item with buttons."""
    title = I18N_IT["new_item"]
    body  = summarize_item_html(it)
    text  = f"{title}\n\n{body}"
    buttons = build_buttons(it)
    # Disattivo anteprima se ho già i bottoni
    tg_send(chat_id, text, parse_mode=ParseMode.HTML, disable_preview=True, reply_markup_json=buttons)


def send_digest(chat_id: str, items: List[Dict[str, Any]]) -> None:
    """Send a compact digest of N items with links."""
    text = summarize_digest_html(items)
    # Anteprima disabilitata: ci sono già link multipli
    tg_send(chat_id, text, parse_mode=ParseMode.HTML, disable_preview=True)
