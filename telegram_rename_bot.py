"""
Telegram File Renamer Bot
=========================
Allows users to send any file and rename it via a simple chat flow.

Setup:
  1. pip install python-telegram-bot==20.7
  2. Create a bot via @BotFather on Telegram and get your token.
  3. Set the token below (BOT_TOKEN) or use an environment variable.
  4. Run: python telegram_rename_bot.py

Usage (in Telegram):
  /start  – Welcome message
  /help   – Instructions
  Send any file → bot asks for the new name → bot sends back the renamed file
"""

import os
import logging
import tempfile
import shutil
from pathlib import Path

from telegram import Update, Document
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# ── Configuration ──────────────────────────────────────────────────────────────
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8821222065:AAEdsY1T3RiVeLL5Wx1q43zkjtAf7LiG4N8")

# Conversation states
WAITING_FOR_NAME = 1

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ── Helpers ────────────────────────────────────────────────────────────────────
def get_extension(file_name: str) -> str:
    """Return the file extension including the dot, e.g. '.pdf'"""
    return Path(file_name).suffix  # keeps original extension


# ── Handlers ───────────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 *Welcome to File Renamer Bot!*\n\n"
        "Send me any file (document, photo, video, audio…) and I'll help you rename it.\n\n"
        "Type /help for instructions.",
        parse_mode="Markdown",
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "📖 *How to use:*\n\n"
        "1️⃣ Send me a file (as a *Document* for best results).\n"
        "2️⃣ I'll ask you for the new filename.\n"
        "3️⃣ Type the new name (with or without extension).\n"
        "4️⃣ I'll send the file back with the new name! ✅\n\n"
        "💡 *Tips:*\n"
        "• If you omit the extension, the original one is kept.\n"
        "• Type /cancel at any time to abort.\n"
        "• Photos are sent as documents to preserve the filename.",
        parse_mode="Markdown",
    )


async def receive_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Triggered when the user sends a file.
    Stores file info in user_data and asks for the new name.
    """
    message = update.message

    # Determine which kind of attachment was sent
    if message.document:
        file_obj = message.document
        original_name = file_obj.file_name or "file"
        file_id = file_obj.file_id
        file_type = "document"
    elif message.audio:
        file_obj = message.audio
        original_name = file_obj.file_name or (file_obj.title or "audio") + ".mp3"
        file_id = file_obj.file_id
        file_type = "audio"
    elif message.video:
        file_obj = message.video
        original_name = file_obj.file_name or "video.mp4"
        file_id = file_obj.file_id
        file_type = "video"
    elif message.photo:
        # Photos: take the highest resolution
        file_obj = message.photo[-1]
        original_name = "photo.jpg"
        file_id = file_obj.file_id
        file_type = "photo"
    elif message.voice:
        file_obj = message.voice
        original_name = "voice.ogg"
        file_id = file_obj.file_id
        file_type = "voice"
    elif message.video_note:
        file_obj = message.video_note
        original_name = "video_note.mp4"
        file_id = file_obj.file_id
        file_type = "video_note"
    else:
        await message.reply_text("⚠️ I can only rename files, documents, photos, audio, and videos.")
        return ConversationHandler.END

    # Save info for the next step
    context.user_data["file_id"] = file_id
    context.user_data["original_name"] = original_name
    context.user_data["file_type"] = file_type

    extension = get_extension(original_name)
    await message.reply_text(
        f"📁 Got it! Original filename: `{original_name}`\n\n"
        f"✏️ Please type the *new filename*.\n"
        f"_(Extension `{extension or 'none'}` will be kept if you don't include one.)_\n\n"
        f"Type /cancel to abort.",
        parse_mode="Markdown",
    )
    return WAITING_FOR_NAME


async def receive_new_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Triggered after the user types the new filename.
    Downloads the stored file, renames it, and sends it back.
    """
    new_name_raw = update.message.text.strip()

    if not new_name_raw:
        await update.message.reply_text("⚠️ Name cannot be empty. Please try again or /cancel.")
        return WAITING_FOR_NAME

    original_name: str = context.user_data["original_name"]
    file_id: str = context.user_data["file_id"]

    # Build the final filename (preserve extension if user didn't supply one)
    original_ext = get_extension(original_name)
    new_ext = get_extension(new_name_raw)
    if new_ext:
        final_name = new_name_raw          # user supplied an extension
    else:
        final_name = new_name_raw + original_ext  # append original extension

    # Sanitise filename (remove path separators)
    final_name = final_name.replace("/", "_").replace("\\", "_")

    await update.message.reply_text(f"⏳ Renaming to `{final_name}`…", parse_mode="Markdown")

    # Download the file into a temp directory
    tmp_dir = tempfile.mkdtemp()
    try:
        tg_file = await context.bot.get_file(file_id)
        download_path = os.path.join(tmp_dir, "original_file")
        await tg_file.download_to_drive(download_path)

        # Copy to the final filename path
        final_path = os.path.join(tmp_dir, final_name)
        shutil.copy2(download_path, final_path)

        # Send the renamed file back as a document
        with open(final_path, "rb") as f:
            await update.message.reply_document(
                document=f,
                filename=final_name,
                caption=f"✅ Here is your renamed file: `{final_name}`",
                parse_mode="Markdown",
            )
    except Exception as e:
        logger.error("Error during rename: %s", e)
        await update.message.reply_text(
            "❌ Something went wrong while processing your file. Please try again."
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # Clear user data
    context.user_data.clear()
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("❌ Operation cancelled. Send me a file whenever you're ready!")
    return ConversationHandler.END


async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🤔 I didn't understand that. Send me a file to rename, or type /help."
    )


# ── Main ───────────────────────────────────────────────────────────────────────
def main() -> None:
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        raise ValueError(
            "Please set your bot token!\n"
            "  • Edit BOT_TOKEN in this file, OR\n"
            "  • Set the TELEGRAM_BOT_TOKEN environment variable."
        )

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Conversation: file → new name → done
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
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(conv_handler)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, unknown))

    logger.info("Bot is running… Press Ctrl+C to stop.")
    app.run_polling()


if __name__ == "__main__":
    main()
