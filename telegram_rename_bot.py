"""
Telegram File Renamer Bot - Railway Compatible (Multi-User Fixed)
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
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# ── Configuration ──────────────────────────────────────────────────────────────
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

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
    return name


# ── Handlers ───────────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 *Welcome to File Renamer Bot!*\n\n"
        "Send me any file and I'll help you rename it.\n\n"
        "Type /help for instructions.",
        parse_mode="Markdown",
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "📖 *How to use:*\n\n"
        "1️⃣ Send me a file as a *Document*.\n"
        "2️⃣ I'll ask you for the new filename.\n"
        "3️⃣ Type the new name (with or without extension).\n"
        "4️⃣ I'll send the file back renamed! ✅\n\n"
        "💡 *Tips:*\n"
        "• If you omit the extension, the original one is kept.\n"
        "• Type /cancel at any time to abort.",
        parse_mode="Markdown",
    )


async def receive_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.message

    try:
        if message.document:
            file_obj = message.document
            original_name = file_obj.file_name or "file"
            file_id = file_obj.file_id
        elif message.audio:
            file_obj = message.audio
            original_name = file_obj.file_name or (file_obj.title or "audio") + ".mp3"
            file_id = file_obj.file_id
        elif message.video:
            file_obj = message.video
            original_name = file_obj.file_name or "video.mp4"
            file_id = file_obj.file_id
        elif message.photo:
            file_obj = message.photo[-1]
            original_name = "photo.jpg"
            file_id = file_obj.file_id
        elif message.voice:
            file_obj = message.voice
            original_name = "voice.ogg"
            file_id = file_obj.file_id
        elif message.video_note:
            file_obj = message.video_note
            original_name = "video_note.mp4"
            file_id = file_obj.file_id
        else:
            await message.reply_text("⚠️ Please send a file, document, photo, audio, or video.")
            return ConversationHandler.END

        # Store per-user state in user_data (isolated per user automatically)
        context.user_data["file_id"] = file_id
        context.user_data["original_name"] = original_name

        extension = get_extension(original_name)
        await message.reply_text(
            f"📁 Got it! Original filename: `{original_name}`\n\n"
            f"✏️ Please type the *new filename*.\n"
            f"_(Extension `{extension or 'none'}` will be kept if you don't include one.)_\n\n"
            f"Type /cancel to abort.",
            parse_mode="Markdown",
        )
        return WAITING_FOR_NAME

    except Exception as e:
        logger.error("Error in receive_file (user=%s): %s", update.effective_user.id, e)
        await message.reply_text("❌ Something went wrong. Please try again.")
        return ConversationHandler.END


async def receive_new_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    new_name_raw = update.message.text.strip()

    if not new_name_raw:
        await update.message.reply_text("⚠️ Name cannot be empty. Please try again or /cancel.")
        return WAITING_FOR_NAME

    # Guard: session expired after bot restart
    if "file_id" not in context.user_data:
        await update.message.reply_text(
            "⚠️ Session expired (bot may have restarted). Please send the file again."
        )
        return ConversationHandler.END

    original_name: str = context.user_data["original_name"]
    file_id: str = context.user_data["file_id"]

    # Build final filename
    original_ext = get_extension(original_name)
    new_ext = get_extension(new_name_raw)
    final_name = new_name_raw if new_ext else new_name_raw + original_ext
    final_name = sanitize_filename(final_name)

    if not final_name or final_name in ("_", ""):
        await update.message.reply_text("⚠️ Invalid filename. Please try again or /cancel.")
        return WAITING_FOR_NAME

    await update.message.reply_text(f"⏳ Renaming to `{final_name}`…", parse_mode="Markdown")

    tmp_dir = tempfile.mkdtemp()
    try:
        tg_file = await context.bot.get_file(file_id)
        download_path = os.path.join(tmp_dir, "original_file")
        await tg_file.download_to_drive(download_path)

        final_path = os.path.join(tmp_dir, final_name)
        shutil.copy2(download_path, final_path)

        with open(final_path, "rb") as f:
            await update.message.reply_document(
                document=f,
                filename=final_name,
                caption=f"✅ Here is your renamed file: `{final_name}`",
                parse_mode="Markdown",
            )
        logger.info(
            "Renamed: %s → %s (user=%s)",
            original_name, final_name, update.effective_user.id,
        )

    except Exception as e:
        logger.error("Error during rename (user=%s): %s", update.effective_user.id, e)
        await update.message.reply_text(
            "❌ Something went wrong while processing your file. Please try again."
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        context.user_data.clear()

    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("❌ Cancelled. Send me a file whenever you're ready!")
    return ConversationHandler.END


async def unknown_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🤔 Please send me a file to rename, or type /help."
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Unhandled error: %s", context.error, exc_info=context.error)

    # Try to notify the user if we have an update
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

    logger.info("Starting bot...")

    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .connect_timeout(30)
        .read_timeout(60)       # Increased: large files take longer
        .write_timeout(60)      # Increased: uploading renamed file
        .pool_timeout(30)
        .concurrent_updates(True)   # ← Key fix: handle multiple users simultaneously
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
        per_user=True,   # Each user has isolated state
        per_chat=False,  # Don't isolate by chat — avoids group conflicts
        per_message=False,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(conv_handler)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, unknown_text))
    app.add_error_handler(error_handler)

    logger.info("Bot is running with concurrent_updates=True!")

    # Railway-safe polling (no close_loop=False)
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
