import os
import json
import time
import threading
from websocket import create_connection
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler,
    ContextTypes
)

# --------------------------
# ENVIRONMENT VARIABLES
# --------------------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DERIV_API_TOKEN = os.getenv("DERIV_API_TOKEN")
DERIV_APP_ID = "82074"

analyzing = False
status_message = "Waiting..."


# --------------------------
# DERIV WEBSOCKET CONNECT
# --------------------------
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


# --------------------------
# PRICE ACTION LOGIC
# --------------------------
def analyze_market(chat_id, app):
    global analyzing, status_message

    async def send(msg):
        await app.bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown")

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

            # ------------ BOS (M5) ------------
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

            # ------------ ORDER BLOCK (M1) ------------
            status_message = "Waiting for OB tap..."
            time.sleep(1)

            ob = m1[-2]
            entry = ob["close"]
            sl = ob["low"] if trend == "BUY" else ob["high"]

            rr = abs(entry - sl) * 5
            tp = entry + rr if trend == "BUY" else entry - rr

            # ------------ CONFIRMATION WICK ------------
            status_message = "Checking wick confirmation..."
            time.sleep(1)

            wick_ok = False
            c = m1[-1]

            if trend == "BUY" and c["low"] <= ob["low"]:
                wick_ok = True
            if trend == "SELL" and c["high"] >= ob["high"]:
                wick_ok = True

            if not wick_ok:
                status_message = "Wick invalid... waiting"
                time.sleep(2)
                continue

            # ------------ SEND SIGNAL ------------
            msg = (
                f"📢 *REAL SIGNAL — V25*\n\n"
                f"Direction: {trend}\n"
                f"Entry: {entry}\n"
                f"SL: {sl}\n"
                f"TP (1:5): {tp}\n"
                f"Trend: {trend}\n"
                f"BOS: True\n"
                f"OB Tap: Confirmed\n"
                f"Wick Confirmation: Yes"
            )

            app.create_task(send(msg))
            time.sleep(10)

        except Exception as e:
            status_message = f"Error: {str(e)}"
            time.sleep(3)


# --------------------------
# COMMANDS
# --------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Welcome!\n\n"
        "⚠️ Disclaimer:\n"
        "- Only stake money you are willing to lose.\n"
        "- No bot or strategy is 100% correct.\n"
        "- This bot analyzes REAL V25 data.\n\n"
        "Use /analyze to begin."
    )


async def analyze(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global analyzing
    if analyzing:
        await update.message.reply_text("Already analyzing...")
        return

    analyzing = True
    chat_id = update.message.chat_id
    app = context.application
    t = threading.Thread(target=analyze_market, args=(chat_id, app))
    t.start()
    await update.message.reply_text("Analyzing the market now...")


async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global analyzing
    analyzing = False
    await update.message.reply_text("Analyzing stopped.")


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Current status:\n{status_message}")


# --------------------------
# RUN BOT
# --------------------------
def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("analyze", analyze))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(CommandHandler("status", status))

    app.run_polling()


if __name__ == "__main__":
    main()
