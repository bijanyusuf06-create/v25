import os
import asyncio
import random
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ---- CONFIG ----
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))

# ---- TELEGRAM BOT ----
app_bot = ApplicationBuilder().token(BOT_TOKEN).build()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bot is online ✅")

app_bot.add_handler(CommandHandler("start", start))

# ---- FLASK APP FOR PING ----
app_flask = Flask(__name__)

@app_flask.route("/")
def home():
    return "Bot is running!"

# ---- RUN BOTH ----
async def main():
    import nest_asyncio
    nest_asyncio.apply()
    from threading import Thread

    # Run Telegram bot
    Thread(target=lambda: asyncio.run(app_bot.run_polling())).start()
    # Run Flask server
    app_flask.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

if __name__ == "__main__":
    asyncio.run(main())
