import os
import json
import time
import threading
from flask import Flask, request
from websocket import create_connection
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes
)

# --------------------------------------
# ENVIRONMENT VARIABLES
# --------------------------------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DERIV_API_TOKEN = os.getenv("DERIV_API_TOKEN")
DERIV_APP_ID = "82074"
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # Example: https://your-app.leapcell.app

PORT = 8080  # Leapcell requirement

analyzing = False
status_message = "Waiting..."

# --------------------------------------
# FLASK APP (Webhook receiver)
# --------------------------------------
app_flask = Flask(__name__)
tg_app = None  # Created later


@app_flask.post("/")
def telegram_webhook():
    """Handle incoming Telegram updates."""
    if request.is_json:
        update = Update.de_json(request.get_json(), tg_app.bot)
        tg_app.update_queue.put(update)
    return "OK", 200


# --------------------------------------
# DERIV WEBSOCKET
# --------------------------------------
def connect_deriv():
    url = f"wss://ws.derivws.com/websockets/v3?app_id={DERIV_APP_ID}"
    return create_connection(url)


def get_candles(symbol, timeframe, count=100):
    ws = connect_deriv()
    msg = {
        "ticks_history": symbol,
        "granularity": timeframe,
        "count": count,
        "style": "candles"
    }
    ws.send(json.dumps(msg))
    data = json.loads(ws.recv())
    ws.close()
    return data.get("candles", [])


# --------------------------------------
# ANALYSIS LOGIC (runs in separate thread)
# --------------------------------------
def analyze_market(chat_id, application):
    global analyzing, status_message

    async def send(msg):
        await application.bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown")

    while analyzing:
        try:
            m15 = get_candles("R_25", 900)
            m5 = get_candles("R_25", 300)
            m1 = get_candles("R_25", 60)

            status_message = "Checking trend..."
            time.sleep(1)

            if len(m15) < 5:
                continue

            last = m15[-5:]
            highs = [c["high"] for c in last]
            lows = [c["low"] for c in last]

            if lows[1] > lows[0] and lows[2] > lows[1]:
                trend = "BUY"
            elif highs[1] < highs[0] and highs[2] < highs[1]:
                trend = "SELL"
            else:
                status_message = "Trend unclear... waiting"
                time.sleep(2)
                continue

            status_message = f"Trend: {trend}"

            # -------- BOS (M5) --------
            status_message = "Checking BOS..."
            time.sleep(1)

            last5 = m5[-5:]
            bos = False

            if trend == "BUY" and last5[-1]["close"] > last5[-2]["high"]:
                bos = True
            if trend == "SELL" and last5[-1]["close"] < last5[-2]["low"]:
                bos = True

            if not bos:
                status_message = "BOS not found... waiting"
                time.sleep(2)
                continue

            # -------- ORDER BLOCK (M1) --------
            status_message = "Waiting for OB tap..."
            time.sleep(1)

            ob = m1[-2]
            entry = ob["close"]
            sl = ob["low"] if trend == "BUY" else ob["high"]

            rr = abs(entry - sl) * 5
            tp = entry + rr if trend == "BUY" else entry - rr

            # -------- CONFIRM WICK --------
            status_message = "Checking wick confirmation..."
            time.sleep(1)

            c = m1[-1]
            wick_ok = (
                (trend == "BUY" and c["low"] <= ob["low"])
                or (trend == "SELL" and c["high"] >= ob["high"])
            )

            if not wick_ok:
                status_message = "Wick invalid... waiting"
                time.sleep(2)
                continue

            msg = (
                f"📢 *REAL SIGNAL — V25*\n\n"
                f"Direction: {trend}\n"
                f"Entry: {entry}\n"
                f"SL: {sl}\n"
                f"TP (1:5): {tp}\n"
                f"BOS: True\n"
                f"OB Tap: Confirmed\n"
                f"Wick Confirmation: Yes"
            )

            application.create_task(send(msg))

            time.sleep(10)

        except Exception as e:
            status_message = f"Error: {str(e)}"
            time.sleep(3)


# --------------------------------------
# BOT COMMANDS
# --------------------------------------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Welcome!\n\nUse /analyze to start market analysis.\nUse /stop to stop."
    )


async def analyze_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global analyzing
    if analyzing:
        await update.message.reply_text("Already analyzing...")
        return

    analyzing = True
    chat_id = update.message.chat_id
    application = context.application

    threading.Thread(target=analyze_market, args=(chat_id, application)).start()
    await update.message.reply_text("Started analyzing...")


async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global analyzing
    analyzing = False
    await update.message.reply_text("Stopped analyzing.")


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Status: {status_message}")


# --------------------------------------
# RUN APP (Webhook mode)
# --------------------------------------
def main():
    global tg_app

    tg_app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    tg_app.add_handler(CommandHandler("start", start_cmd))
    tg_app.add_handler(CommandHandler("analyze", analyze_cmd))
    tg_app.add_handler(CommandHandler("stop", stop_cmd))
    tg_app.add_handler(CommandHandler("status", status_cmd))

    # Set Telegram webhook
    tg_app.bot.set_webhook(url=WEBHOOK_URL)

    # Start bot in background
    threading.Thread(target=tg_app.run_polling, daemon=True).start()

    # Start Flask server (port 8080)
    app_flask.run(host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()
