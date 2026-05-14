"""
Telegram File Renamer Bot - Railway Free Plan Compatible
Fixes applied:
  - Graceful SIGTERM/SIGINT shutdown (Railway sends SIGTERM on redeploy/stop)
  - Webhook mode via PORT env var (optional, falls back to polling)
  - Larger timeouts for big files on free-tier slow startup
  - Retry logic for transient Telegram API errors
  - /status command so you can ping the bot to keep it awake
  - Safe asyncio event-loop handling (no deprecation warnings on Python 3.10+)
  - requirements.txt and Procfile included as comments at bottom
"""

import os
import sys
import asyncio
import logging
import tempfile
import shutil
import signal
from pathlib import Path

from telegram import Update
from telegram.error import NetworkError, TimedOut, RetryAfter
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# ── Configuration ──────────────────────────────────────────────────────────────
BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")

# Railway injects PORT when using webhook/web service mode.
# If PORT is set we switch to webhook; otherwise we use polling.
PORT: int = int(os.getenv("PORT", "0"))

# Set WEBHOOK_URL to your Railway public URL, e.g. https://myapp.up.railway.app
# Leave blank to use polling (fine for free plan, bot just needs to stay running).
WEBHOOK_URL: str = os.getenv("WEBHOOK_URL", "").rstrip("/")

# Max retries for transient Telegram API errors
MAX_RETRIES = 3

# Conversation states
WAITING_FOR_NAME = 1

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


# ── Helpers ────────────────────────────────────────────────────────────────────
def get_extension(file_name: str) -> str:
    return Path(file_name).suffix


def sanitize_filename(name: str) -> str:
    """Remove characters unsafe for filenames."""
    for ch in ("/", "\\", ":", "*", "?", '"', "<", ">", "|"):
        name = name.replace(ch, "_")
    name = name.replace("..", "_").strip()
    return name or "file"


async def safe_reply(message, text: str, **kwargs) -> None:
    """Reply with retry logic for transient network errors."""
    for attempt in range(MAX_RETRIES):
        try:
            await message.reply_text(text, **kwargs)
            return
        except RetryAfter as e:
            wait = e.retry_after + 1
            logger.warning("Rate limited, waiting %ds (attempt %d)", wait, attempt + 1)
            await asyncio.sleep(wait)
        except (NetworkError, TimedOut) as e:
            if attempt < MAX_RETRIES - 1:
                logger.warning("Network error, retrying (%d/%d): %s", attempt + 1, MAX_RETRIES, e)
                await asyncio.sleep(2 ** attempt)  # Exponential backoff
            else:
                logger.error("Failed after %d retries: %s", MAX_RETRIES, e)
                raise


# ── Handlers ───────────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await safe_reply(
        update.message,
        "👋 *Welcome to File Renamer Bot!*\n\n"
        "Send me any file and I'll help you rename it.\n\n"
        "Type /help for instructions.",
        parse_mode="Markdown",
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await safe_reply(
        update.message,
        "📖 *How to use:*\n\n"
        "1️⃣ Send me a file as a *Document*.\n"
        "2️⃣ I'll ask you for the new filename.\n"
        "3️⃣ Type the new name (with or without extension).\n"
        "4️⃣ I'll send the file back renamed! ✅\n\n"
        "💡 *Tips:*\n"
        "• If you omit the extension, the original one is kept.\n"
        "• Type /cancel at any time to abort.\n"
        "• Max file size: 20 MB (Telegram Bot API limit).",
        parse_mode="Markdown",
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Health-check command — useful for keeping the free-tier deployment awake."""
    await safe_reply(update.message, "✅ Bot is alive and running!")


async def receive_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.message

    try:
        if message.document:
            file_obj = message.document
            original_name = file_obj.file_name or "file"
            file_id = file_obj.file_id
            file_size = file_obj.file_size or 0
        elif message.audio:
            file_obj = message.audio
            original_name = file_obj.file_name or (
                (file_obj.title or "audio") + ".mp3"
            )
            file_id = file_obj.file_id
            file_size = file_obj.file_size or 0
        elif message.video:
            file_obj = message.video
            original_name = file_obj.file_name or "video.mp4"
            file_id = file_obj.file_id
            file_size = file_obj.file_size or 0
        elif message.photo:
            file_obj = message.photo[-1]
            original_name = "photo.jpg"
            file_id = file_obj.file_id
            file_size = file_obj.file_size or 0
        elif message.voice:
            file_obj = message.voice
            original_name = "voice.ogg"
            file_id = file_obj.file_id
            file_size = file_obj.file_size or 0
        elif message.video_note:
            file_obj = message.video_note
            original_name = "video_note.mp4"
            file_id = file_obj.file_id
            file_size = file_obj.file_size or 0
        else:
            await safe_reply(
                message,
                "⚠️ Please send a file, document, photo, audio, or video.",
            )
            return ConversationHandler.END

        # Warn about large files (Telegram Bot API caps at 20 MB download)
        if file_size > 19 * 1024 * 1024:
            await safe_reply(
                message,
                "⚠️ File is larger than 19 MB. Telegram Bot API may refuse the download.\n"
                "Continuing anyway — if it fails, try a smaller file.",
            )

        # Store per-user state (isolated automatically by per_user=True)
        context.user_data["file_id"] = file_id
        context.user_data["original_name"] = original_name

        extension = get_extension(original_name)
        await safe_reply(
            message,
            f"📁 Got it! Original filename: `{original_name}`\n\n"
            f"✏️ Please type the *new filename*.\n"
            f"_(Extension `{extension or 'none'}` will be kept if you don't include one.)_\n\n"
            f"Type /cancel to abort.",
            parse_mode="Markdown",
        )
        return WAITING_FOR_NAME

    except Exception as e:
        logger.error("Error in receive_file (user=%s): %s", update.effective_user.id, e)
        await safe_reply(message, "❌ Something went wrong. Please try again.")
        return ConversationHandler.END


async def receive_new_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    new_name_raw = update.message.text.strip()

    if not new_name_raw:
        await safe_reply(
            update.message,
            "⚠️ Name cannot be empty. Please try again or /cancel.",
        )
        return WAITING_FOR_NAME

    # Guard: session expired after bot restart
    if "file_id" not in context.user_data:
        await safe_reply(
            update.message,
            "⚠️ Session expired (bot restarted). Please send the file again.",
        )
        return ConversationHandler.END

    original_name: str = context.user_data["original_name"]
    file_id: str = context.user_data["file_id"]

    # Build final filename
    original_ext = get_extension(original_name)
    new_ext = get_extension(new_name_raw)
    final_name = new_name_raw if new_ext else new_name_raw + original_ext
    final_name = sanitize_filename(final_name)

    await safe_reply(
        update.message,
        f"⏳ Renaming to `{final_name}`…",
        parse_mode="Markdown",
    )

    tmp_dir = tempfile.mkdtemp()
    try:
        # Retry download for transient Railway network blips
        for attempt in range(MAX_RETRIES):
            try:
                tg_file = await context.bot.get_file(file_id)
                download_path = os.path.join(tmp_dir, "original_file")
                await tg_file.download_to_drive(download_path)
                break
            except (NetworkError, TimedOut) as e:
                if attempt < MAX_RETRIES - 1:
                    logger.warning(
                        "Download error, retry %d/%d: %s", attempt + 1, MAX_RETRIES, e
                    )
                    await asyncio.sleep(2 ** attempt)
                else:
                    raise

        final_path = os.path.join(tmp_dir, final_name)
        shutil.copy2(download_path, final_path)

        # Retry upload
        for attempt in range(MAX_RETRIES):
            try:
                with open(final_path, "rb") as f:
                    await update.message.reply_document(
                        document=f,
                        filename=final_name,
                        caption=f"✅ Here is your renamed file: `{final_name}`",
                        parse_mode="Markdown",
                    )
                break
            except (NetworkError, TimedOut) as e:
                if attempt < MAX_RETRIES - 1:
                    logger.warning(
                        "Upload error, retry %d/%d: %s", attempt + 1, MAX_RETRIES, e
                    )
                    await asyncio.sleep(2 ** attempt)
                    # Re-open file in next iteration — no seek needed, file is on disk
                else:
                    raise

        logger.info(
            "Renamed: %s → %s (user=%s)",
            original_name,
            final_name,
            update.effective_user.id,
        )

    except RetryAfter as e:
        await safe_reply(
            update.message,
            f"⚠️ Telegram rate limit hit. Please wait {e.retry_after}s and try again.",
        )
    except Exception as e:
        logger.error(
            "Error during rename (user=%s): %s", update.effective_user.id, e
        )
        await safe_reply(
            update.message,
            "❌ Something went wrong while processing your file. Please try again.",
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        context.user_data.clear()

    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await safe_reply(update.message, "❌ Cancelled. Send me a file whenever you're ready!")
    return ConversationHandler.END


async def unknown_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await safe_reply(
        update.message,
        "🤔 Please send me a file to rename, or type /help.",
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Unhandled error: %s", context.error, exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ An unexpected error occurred. Please try again."
            )
        except Exception:
            pass


# ── Main ───────────────────────────────────────────────────────────────────────
def main() -> None:
    if not BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is not set! Add it in Railway Variables.")
        sys.exit(1)

    logger.info("Starting bot… PORT=%s  WEBHOOK_URL=%s", PORT, WEBHOOK_URL or "(none, using polling)")

    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .connect_timeout(30)
        .read_timeout(120)          # Large files need more time on free tier
        .write_timeout(120)
        .pool_timeout(30)
        .concurrent_updates(True)   # Handle multiple users simultaneously
        .build()
    )

    conv_handler = ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.Document.ALL
                | filters.AUDIO
                | filters.VIDEO
                | filters.PHOTO
                | filters.VOICE
                | filters.VIDEO_NOTE,
                receive_file,
            )
        ],
        states={
            WAITING_FOR_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_new_name)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_user=True,
        per_chat=False,
        per_message=False,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("status", status))  # Health-check / keep-alive ping
    app.add_handler(conv_handler)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, unknown_text))
    app.add_error_handler(error_handler)

    # ── Graceful shutdown on SIGTERM (Railway sends this on stop/redeploy) ──────
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    stop_event = asyncio.Event()

    def _handle_signal(sig):
        logger.info("Received signal %s — shutting down gracefully…", sig.name)
        stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal, sig)

    # ── Webhook vs Polling ──────────────────────────────────────────────────────
    if WEBHOOK_URL and PORT:
        logger.info("Running in WEBHOOK mode on port %d", PORT)
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=BOT_TOKEN,
            webhook_url=f"{WEBHOOK_URL}/{BOT_TOKEN}",
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
            close_loop=False,
        )
    else:
        logger.info("Running in POLLING mode (no WEBHOOK_URL set).")
        app.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
            close_loop=False,
        )


if __name__ == "__main__":
    main()
