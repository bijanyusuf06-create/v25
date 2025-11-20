import os
import json
import time
import threading
import logging
from websocket import create_connection
from telegram.ext import Updater, CommandHandler
from telegram.error import TelegramError

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DERIV_API_TOKEN = os.getenv("DERIV_API_TOKEN")
DERIV_APP_ID = os.getenv("DERIV_APP_ID", "82074")
if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
ADMIN_ID = os.getenv("ADMIN_ID")
try:
    ADMIN_ID = int(ADMIN_ID) if ADMIN_ID is not None else None
except Exception:
    ADMIN_ID = None
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)
_analyzing = False
_status_message = "Waiting..."
_thread_handle = None
_thread_lock = threading.Lock()

def _connect_deriv():
    url = f"wss://ws.derivws.com/websockets/v3?app_id={DERIV_APP_ID}"
    return create_connection(url, timeout=10)

def _safe_float(x, default=0.0):
    try:
        return float(x)
    except Exception:
        return default

def _parse_candles(raw):
    if not raw:
        return []
    parsed = []
    for c in raw:
        if not isinstance(c, dict):
            continue
        parsed.append({
            'open': _safe_float(c.get('open')),
            'high': _safe_float(c.get('high')),
            'low': _safe_float(c.get('low')),
            'close': _safe_float(c.get('close')),
            'epoch': int(c.get('epoch')) if c.get('epoch') is not None else None
        })
    return parsed

def get_candles(symbol, granularity, count=100):
    try:
        ws = _connect_deriv()
    except Exception as exc:
        logger.warning("connect failed: %s", exc)
        return []
    req = {"ticks_history": symbol, "granularity": granularity, "count": count, "style": "candles", "end": "latest"}
    try:
        ws.send(json.dumps(req))
        raw = ws.recv()
        data = json.loads(raw) if raw else {}
        ws.close()
        candles = data.get('candles') or []
        if isinstance(candles, dict):
            candles = candles.get('data', [])
        return _parse_candles(candles)
    except Exception as exc:
        logger.exception("fetch error: %s", exc)
        try:
            ws.close()
        except Exception:
            pass
        return []

def _send_safe(bot, chat_id, text, **kwargs):
    try:
        bot.send_message(chat_id=chat_id, text=text, **kwargs)
    except Exception as e:
        logger.exception("send failed: %s", e)

def analyze_market_loop(bot, chat_id, stop_event):
    global _status_message
    while not stop_event.is_set():
        try:
            _status_message = "Fetching candles..."
            m15 = get_candles("R_25", 900, count=10)
            m5 = get_candles("R_25", 300, count=10)
            m1 = get_candles("R_25", 60, count=10)
            if len(m15) < 5 or len(m5) < 5 or len(m1) < 3:
                _status_message = "Not enough data"
                _send_safe(bot, chat_id, f"Status: {_status_message}")
                time.sleep(3)
                continue
            _status_message = "Checking trend"
            last = m15[-5:]
            highs = [c['high'] for c in last]
            lows = [c['low'] for c in last]
            trend = 'UNCLEAR'
            try:
                if lows[1] > lows[0] and lows[2] > lows[1]:
                    trend = 'BUY'
                elif highs[1] < highs[0] and highs[2] < highs[1]:
                    trend = 'SELL'
            except Exception:
                trend = 'UNCLEAR'
            if trend == 'UNCLEAR':
                _status_message = 'Trend unclear'
                time.sleep(2)
                continue
            _status_message = f"Trend: {trend}"
            last5 = m5[-5:]
            bos = False
            try:
                if trend == 'BUY' and last5[-1]['close'] > last5[-2]['high']:
                    bos = True
                if trend == 'SELL' and last5[-1]['close'] < last5[-2]['low']:
                    bos = True
            except Exception:
                bos = False
            if not bos:
                _status_message = 'BOS not found'
                time.sleep(2)
                continue
            ob = m1[-2]
            entry = ob.get('close')
            sl = ob.get('low') if trend == 'BUY' else ob.get('high')
            if entry is None or sl is None:
                _status_message = 'Invalid prices'
                time.sleep(2)
                continue
            try:
                rr = abs(entry - sl) * 5
                tp = (entry + rr) if trend == 'BUY' else (entry - rr)
            except Exception:
                _status_message = 'Math error'
                time.sleep(2)
                continue
            c = m1[-1]
            wick_ok = False
            try:
                if trend == 'BUY' and c.get('low', 1e9) <= ob.get('low', 1e9):
                    wick_ok = True
                if trend == 'SELL' and c.get('high', -1e9) >= ob.get('high', -1e9):
                    wick_ok = True
            except Exception:
                wick_ok = False
            if not wick_ok:
                _status_message = 'Wick invalid'
                time.sleep(2)
                continue
            message = (
                f"📢 *REAL SIGNAL — V25*\n\n"
                f"Direction: {trend}\n"
                f"Entry: {entry}\n"
                f"SL: {sl}\n"
                f"TP (1:5): {tp}\n"
                f"BOS: True\n"
                f"OB Tap: Confirmed\n"
                f"Wick Confirmation: Yes"
            )
            _send_safe(bot, chat_id, message, parse_mode='Markdown')
            time.sleep(10)
        except Exception as e:
            logger.exception("loop error: %s", e)
            _status_message = f"Error: {e}"
            if ADMIN_ID:
                try:
                    bot.send_message(chat_id=ADMIN_ID, text=f"Analysis error: {e}")
                except Exception:
                    pass
            time.sleep(3)
    logger.info("analyze loop stopped for %s", chat_id)

def start(update, context):
    update.message.reply_text(
        "Welcome!\n\n"
        "⚠️ Disclaimer:\n"
        "- Only stake money you are willing to lose.\n"
        "- No bot or strategy is 100% correct.\n"
        "- This bot analyzes REAL V25 data.\n\n"
        "Use /analyze to begin."
    )

def analyze(update, context):
    global _analyzing, _thread_handle
    chat_id = update.effective_chat.id
    with _thread_lock:
        if _analyzing:
            update.message.reply_text("Already analyzing. Use /stop first.")
            return
        _analyzing = True
        stop_event = threading.Event()
        def runner():
            analyze_market_loop(context.bot, chat_id, stop_event)
        _thread_handle = threading.Thread(target=runner, daemon=True)
        _thread_handle.stop_event = stop_event
        _thread_handle.start()
    update.message.reply_text("Analyzing the market now...")

def stop(update, context):
    global _analyzing, _thread_handle
    with _thread_lock:
        if not _analyzing:
            update.message.reply_text("Not currently analyzing.")
            return
        try:
            if _thread_handle and hasattr(_thread_handle, 'stop_event'):
                _thread_handle.stop_event.set()
        except Exception:
            pass
        _analyzing = False
    update.message.reply_text("Analyzing stopped.")

def status(update, context):
    update.message.reply_text(f"Current status:\n{_status_message}")

def main():
    updater = Updater(token=TELEGRAM_TOKEN, use_context=True)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("analyze", analyze))
    dp.add_handler(CommandHandler("stop", stop))
    dp.add_handler(CommandHandler("status", status))
    updater.start_polling()
    logger.info("Bot started")
    updater.idle()

if __name__ == '__main__':
    main()
