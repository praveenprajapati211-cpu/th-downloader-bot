import os
import json
import asyncio
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from telegram import Update, InputFile
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

import yt_dlp

# ---------- CONFIG ----------
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN') or 'PUT_YOUR_TOKEN_HERE'
ADMIN_USER_ID = int(os.environ.get('ADMIN_USER_ID') or 0)

DATA_DIR = Path('./data')
DATA_DIR.mkdir(exist_ok=True)
PREMIUM_FILE = DATA_DIR / 'premium.json'
USAGE_FILE = DATA_DIR / 'usage.json'
TMP_DIR = Path(tempfile.gettempdir()) / 'tg_downloader'
TMP_DIR.mkdir(exist_ok=True)

FREE_DAILY_LIMIT = 3
MAX_FILESIZE_BYTES = 50 * 1024 * 1024

YTDLP_OPTS_BASE = {
    'format': 'best',
    'outtmpl': str(TMP_DIR / '%(id)s.%(ext)s'),
    'noplaylist': True,
    'quiet': True,
    'no_warnings': True,
}

# Helpers
def load_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            return default
    return default

def save_json(path: Path, data):
    path.write_text(json.dumps(data, indent=2), encoding='utf-8')

def ensure_premium_file():
    if not PREMIUM_FILE.exists():
        save_json(PREMIUM_FILE, {'premium_users': []})

def ensure_usage_file():
    if not USAGE_FILE.exists():
        save_json(USAGE_FILE, {})

def is_premium(user_id: int) -> bool:
    ensure_premium_file()
    data = load_json(PREMIUM_FILE, {'premium_users': []})
    return user_id in data.get('premium_users', [])

def add_premium(user_id: int):
    ensure_premium_file()
    data = load_json(PREMIUM_FILE, {'premium_users': []})
    if user_id not in data['premium_users']:
        data['premium_users'].append(user_id)
        save_json(PREMIUM_FILE, data)

def remove_premium(user_id: int):
    ensure_premium_file()
    data = load_json(PREMIUM_FILE, {'premium_users': []})
    if user_id in data['premium_users']:
        data['premium_users'].remove(user_id)
        save_json(PREMIUM_FILE, data)

def check_and_increment_usage(user_id: int) -> bool:
    ensure_usage_file()
    data = load_json(USAGE_FILE, {})
    today = datetime.utcnow().date().isoformat()
    user_str = str(user_id)
    user_data = data.get(user_str, {})
    if user_data.get('date') != today:
        user_data = {'date': today, 'count': 0}
    if user_data['count'] >= FREE_DAILY_LIMIT:
        return False
    user_data['count'] += 1
    data[user_str] = user_data
    save_json(USAGE_FILE, data)
    return True

async def download_with_yt_dlp(url: str):
    loop = asyncio.get_event_loop()
    opts = YTDLP_OPTS_BASE.copy()
    ytdl = yt_dlp.YoutubeDL(opts)
    def download():
        return ytdl.extract_info(url, download=True)
    info = await loop.run_in_executor(None, download)
    return info

def find_downloaded_file(info: dict) -> Optional[Path]:
    if not info:
        return None
    filename = None
    if 'requested_downloads' in info and info['requested_downloads']:
        filename = info['requested_downloads'][0].get('filepath')
    if not filename:
        ext = info.get('ext')
        id_ = info.get('id')
        if id_ and ext:
            candidate = TMP_DIR / f"{id_}.{ext}"
            if candidate.exists():
                filename = str(candidate)
    if filename:
        return Path(filename)
    return None

# ---------- BOT HANDLERS ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🎥 Send me a YouTube/Instagram/TikTok link and I'll download it for you!\n"
        "✅ Free users: Limited downloads daily\n"
        "💎 Premium users: Unlimited downloads\n"
        "Use /buy to learn more about Premium."
    )
    await update.message.reply_text(text)

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if is_premium(uid):
        await update.message.reply_text("You are a Premium user ✅ Unlimited downloads.")
        return
    ensure_usage_file()
    data = load_json(USAGE_FILE, {})
    today = datetime.utcnow().date().isoformat()
    user_data = data.get(str(uid), {'date': today, 'count': 0})
    left = max(0, FREE_DAILY_LIMIT - user_data.get('count', 0))
    await update.message.reply_text(f"Free downloads left today: {left}")

async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "💎 To become Premium:\n"
        "1) Pay ₹99 to UPI: your-upi@bank\n"
        "2) After payment, contact admin.\n"
        "Admin will add you using /add_premium <user_id>."
    )
    await update.message.reply_text(text)

async def add_premium_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("Only admin can use this command.")
        return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /add_premium <user_id>")
        return
    try:
        user_id = int(args[0])
    except ValueError:
        await update.message.reply_text("Invalid user id.")
        return
    add_premium(user_id)
    await update.message.reply_text(f"Added {user_id} as premium user.")

async def remove_premium_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("Only admin can use this command.")
        return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /remove_premium <user_id>")
        return
    try:
        user_id = int(args[0])
    except ValueError:
        await update.message.reply_text("Invalid user id.")
        return
    remove_premium(user_id)
    await update.message.reply_text(f"Removed {user_id} from premium users.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    text = message.text or ''
    if not text.startswith("http"):
        await message.reply_text("Please send a valid video link.")
        return
    uid = update.effective_user.id
    if not is_premium(uid):
        if not check_and_increment_usage(uid):
            await message.reply_text("⚠️ Free limit reached. Use /buy to upgrade.")
            return
    msg = await message.reply_text("Downloading... ⏳")
    try:
        info = await download_with_yt_dlp(text)
        filepath = find_downloaded_file(info)
        if not filepath or not filepath.exists():
            await msg.edit_text("Download failed.")
            return
        if filepath.stat().st_size > MAX_FILESIZE_BYTES:
            await msg.edit_text("File too large to send via Telegram.")
            return
        await msg.edit_text("Uploading...")
        await message.reply_document(document=InputFile(open(filepath, 'rb'), filename=filepath.name))
        await msg.delete()
        filepath.unlink(missing_ok=True)
    except Exception as e:
        await msg.edit_text(f"Error: {e}")

# ---------- MAIN ----------
def main():
    if TELEGRAM_TOKEN == 'PUT_YOUR_TOKEN_HERE':
        print('Set TELEGRAM_TOKEN environment variable!')
        return
    ensure_premium_file()
    ensure_usage_file()
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('status', status))
    app.add_handler(CommandHandler('buy', buy))
    app.add_handler(CommandHandler('add_premium', add_premium_cmd))
    app.add_handler(CommandHandler('remove_premium', remove_premium_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print('Bot started...')
    app.run_polling()

if __name__ == '__main__':
    main()
