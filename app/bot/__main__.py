"""Application entrypoint: wires Telegram handlers and the poller together."""
from __future__ import annotations
import asyncio, signal

from telegram import BotCommand
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from app.bot.config import TELEGRAM_BOT_TOKEN, DATA_FILE, POLL_INTERVAL_MINUTES
from app.bot.state_sqlite import State
from app.bot.handlers import cmd_start, cmd_setup, cmd_reset, cmd_stato, cmd_test, on_text, on_callback, cmd_nuova, \
    cmd_modifica
from app.bot.poll import Poll


async def post_init(application):
    await application.bot.set_my_commands([
        BotCommand("start", "Avvia il bot"),
        BotCommand("setup", "Configura una nuova ricerca"),
        BotCommand("stato", "Mostra la ricerca attuale"),
        BotCommand("test", "Esegui una ricerca di prova"),
        BotCommand("reset", "Cancella la configurazione"),
        BotCommand("help", "Mostra la guida"),
    ])


async def cmd_help(update, context):
    text = (
        "<b> Avvisami: inPA – Guida ai comandi</b>\n\n"
        "/start – Avvia il bot e ricevi il messaggio di benvenuto\n"
        "/setup – Configura una nuova ricerca (categoria e testo obbligatori, "
        "regione e settore opzionali)\n"
        "/stato – Mostra i parametri della ricerca attuale\n"
        "/test – Esegue subito una ricerca di prova e mostra i risultati più recenti\n"
        "/reset – Cancella la configurazione corrente e ricomincia da zero\n"
        "/help – Mostra questo messaggio di aiuto\n"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def main_async() -> None:
    """Create the app, register handlers, start the poller, and run until exit."""
    state = State(db_path="data/state.sqlite")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()
    app.bot_data["DEFAULT_PARSE_MODE"] = ParseMode.HTML

    # Command and callback handlers wired with the shared State instance

    app.add_handler(CommandHandler("start", lambda u, c: cmd_start(u, c, state)))
    app.add_handler(CommandHandler("setup", lambda u, c: cmd_setup(u, c, state)))  # alias
    app.add_handler(CommandHandler("nuova", lambda u, c: cmd_nuova(u, c, state)))
    app.add_handler(CommandHandler("modifica", lambda u, c: cmd_modifica(u, c, state)))
    app.add_handler(CommandHandler("stato", lambda u, c: cmd_stato(u, c, state)))
    app.add_handler(CommandHandler("test", lambda u, c: cmd_test(u, c, state)))
    app.add_handler(CommandHandler("reset", lambda u, c: cmd_reset(u, c, state)))

    app.add_handler(CallbackQueryHandler(lambda u, c: on_callback(u, c, state)))

    # Non-command text is only relevant during the first step of the wizard
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: on_text(u, c, state)))
    app.add_handler(CommandHandler("help", cmd_help))

    # Background polling job
    poller = Poll(state, POLL_INTERVAL_MINUTES)
    poller.start()

    # Graceful shutdown handling
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    # Start Telegram long-polling
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)

    # Block until a termination signal arrives
    await stop_event.wait()

    # Shutdown sequence
    await app.updater.stop()
    await app.stop()
    await app.shutdown()
    poller.shutdown()


if __name__ == "__main__":
    asyncio.run(main_async())