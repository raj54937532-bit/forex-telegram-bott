import os
import asyncio
import io
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from binance.client import Client
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes
from dotenv import load_dotenv

# === Load API keys from .env ===
load_dotenv()
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ARYAN_CHAT_ID = "CHAT_ID_OF_ARYAN"  # Replace with Aryan's chat ID

# Admin list
ADMIN_IDS = [ARYAN_CHAT_ID]

client = Client(BINANCE_API_KEY, BINANCE_API_SECRET)
telegram = Bot(token=TELEGRAM_TOKEN)

# Allowed users + names
allowed_users = {ARYAN_CHAT_ID: "Aryan"}

# Track last signals to prevent duplicates
last_signals = {}
active_trades = {}

# === Add user + Welcome ===
async def add_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.message.chat_id) not in ADMIN_IDS:
        return
    try:
        chat_id = context.args[0]
        name = context.args[1]
        allowed_users[chat_id] = name
        welcome_msg = f"Welcome {name} 🤝\nYou're now part of the Pro Traders list.\nBot access granted."
        await telegram.send_message(chat_id=chat_id, text=welcome_msg)
        await update.message.reply_text(f"User {name} added and welcomed.")
    except:
        await update.message.reply_text("Use: /adduser <chat_id> <name>")

# === Remove user ===
async def remove_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.message.chat_id) not in ADMIN_IDS:
        return
    try:
        chat_id = context.args[0]
        removed_name = allowed_users.get(chat_id, "User")
        allowed_users.pop(chat_id, None)
        await update.message.reply_text(f"{removed_name} removed from access.")
    except:
        await update.message.reply_text("Use: /removeuser <chat_id>")

# === EMA50 calculation ===
def calc_ema50(closes):
    ema = closes[0]
    k = 2/(50+1)
    for price in closes:
        ema = (price * k) + (ema * (1-k))
    return ema

# === Swing Support/Resistance ===
def get_swing_sr(highs, lows):
    res = sorted(highs)[-3:][-1]
    sup = sorted(lows)[:3][0]
    return sup, res

# === Scan Logic with stop loss, RR, no duplicate signals ===
def scan_tf(symbol, tf):
    global last_signals, active_trades
    data = client.get_klines(symbol=symbol, interval=tf, limit=100)
    highs = [float(k[2]) for k in data]
    lows = [float(k[3]) for k in data]
    closes = [float(k[4]) for k in data]
    opens = [float(k[1]) for k in data]
    volumes = [float(k[5]) for k in data]
    price = closes[-1]
    ema50 = calc_ema50(closes)
    sup, res = get_swing_sr(highs, lows)

    last_high = highs[-1]
    last_low = lows[-1]

    # Stop loss points
    sl_points = 10 if symbol == "BTCUSDT" else 7 if symbol == "ETHUSDT" else 20
    signal_id = f"{symbol}_{tf}"

    # BUY
    if price > ema50 and last_low < sup:
        entry = price
        sl = last_low - sl_points
        tps = [entry + (entry-sl)*2, entry + (entry-sl)*3, entry + (entry-sl)*4]
        if last_signals.get(signal_id) != "BUY":
            last_signals[signal_id] = "BUY"
            active_trades[signal_id] = {"side":"BUY","entry":entry,"sl":sl,"tps":tps,"tp_hit":[False]*3,"sl_hit":False}
            return ("BUY", entry, sl, tps, sup, res, ema50, volumes[-1])

    # SELL
    if price < ema50 and last_high > res:
        entry = price
        sl = last_high + sl_points
        tps = [entry - (sl-entry)*2, entry - (sl-entry)*3, entry - (sl-entry)*4]
        if last_signals.get(signal_id) != "SELL":
            last_signals[signal_id] = "SELL"
            active_trades[signal_id] = {"side":"SELL","entry":entry,"sl":sl,"tps":tps,"tp_hit":[False]*3,"sl_hit":False}
            return ("SELL", entry, sl, tps, sup, res, ema50, volumes[-1])

    last_signals[signal_id] = None
    return None

# === TP/SL Hit Notification ===
def check_tp_sl(symbol, tf):
    signal_id = f"{symbol}_{tf}"
    if signal_id not in active_trades:
        return
    trade = active_trades[signal_id]
    data = client.get_klines(symbol=symbol, interval=tf, limit=1)
    price = float(data[-1][4])
    side, sl, tps = trade['side'], trade['sl'], trade['tps']

    # BUY
    if side == "BUY":
        for i,tp in enumerate(tps):
            if price >= tp and not trade['tp_hit'][i]:
                trade['tp_hit'][i] = True
                msg = f"{symbol} — {tf} ✅ TP{i+1} hit at {price:.2f}"
                for chat_id in allowed_users:
                    asyncio.create_task(telegram.send_message(chat_id=chat_id, text=msg))
        if price <= sl and not trade['sl_hit']:
            trade['sl_hit'] = True
            msg = f"{symbol} — {tf} ❌ Stop Loss hit at {price:.2f}"
            for chat_id in allowed_users:
                asyncio.create_task(telegram.send_message(chat_id=chat_id, text=msg))
    
    # SELL
    if side == "SELL":
        for i,tp in enumerate(tps):
            if price <= tp and not trade['tp_hit'][i]:
                trade['tp_hit'][i] = True
                msg = f"{symbol} — {tf} ✅ TP{i+1} hit at {price:.2f}"
                for chat_id in allowed_users:
                    asyncio.create_task(telegram.send_message(chat_id=chat_id, text=msg))
        if price >= sl and not trade['sl_hit']:
            trade['sl_hit'] = True
            msg = f"{symbol} — {tf} ❌ Stop Loss hit at {price:.2f}"
            for chat_id in allowed_users:
                asyncio.create_task(telegram.send_message(chat_id=chat_id, text=msg))

# === Telegram Signal + Chart + Volume ===
async def send_signal(symbol, tf, result):
    if not result:
        return
    side, entry, sl, tps, sup, res, ema, volume = result
    msg = (
        f"{symbol} — {tf}\n"
        f"📌 Bias: {side}\n"
        f"🔎 Support: {sup:.2f} | Resistance: {res:.2f}\n"
        f"🎯 Entry: {entry:.2f}\n"
        f"🛑 SL: {sl:.2f}\n"
        f"🏁 TP1: {tps[0]:.2f} | TP2: {tps[1]:.2f} | TP3: {tps[2]:.2f}\n"
        f"🔁 EMA50: {ema:.2f}\n"
        f"📊 Recent Volume: {volume:.2f}\n"
        "Note: Follow discipline."
    )

    data = client.get_klines(symbol=symbol, interval=tf, limit=50)
    closes = [float(k[4]) for k in data]
    highs = [float(k[2]) for k in data]
    lows = [float(k[3]) for k in data]
    opens = [float(k[1]) for k in data]
    volumes = [float(k[5]) for k in data]

    ema_values = [calc_ema50(closes[:i+1]) for i in range(len(closes))]

    plt.figure(figsize=(10,5))
    plt.plot(closes, label="Close", color="blue")
    plt.plot(ema_values, label="EMA50", color="orange")
    plt.fill_between(range(len(highs)), lows, highs, color='lightgrey', alpha=0.3)
    plt.axhline(sup, color='green', linestyle='--', alpha=0.5, label="Support")
    plt.axhline(res, color='red', linestyle='--', alpha=0.5, label="Resistance")

    # Liquidity zone
    recent_high = max(highs[-10:])
    recent_low = min(lows[-10:])
    plt.axhspan(recent_low, recent_high, color='yellow', alpha=0.2, label="Liquidity Zone")

    # Volume bars
    plt.bar(range(len(volumes)), volumes, color='purple', alpha=0.3, label="Volume")

    # Order blocks
    for i in range(len(closes)-2):
        if closes[i] < opens[i] and closes[i+1] > closes[i]:
            plt.gca().add_patch(patches.Rectangle((i,lows[i]),1,highs[i]-lows[i],facecolor='green',alpha=0.3))
        if closes[i] > opens[i] and closes[i+1] < closes[i]:
            plt.gca().add_patch(patches.Rectangle((i,lows[i]),1,highs[i]-lows[i],facecolor='red',alpha=0.3))

    plt.title(f"{symbol} — {tf}")
    plt.legend()
    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format="png")
    buf.seek(0)
    plt.close()

    for chat_id in allowed_users:
        await telegram.send_message(chat_id=chat_id, text=msg)
        await telegram.send_photo(chat_id=chat_id, photo=buf)

# === Runner ===
async def runner():
    symbols = ["BTCUSDT","ETHUSDT"]
    tfs = ["1m","5m","15m","30m","1h","4h"]
    while True:
        for symbol in symbols:
            for tf in tfs:
                result = scan_tf(symbol, tf)
                await send_signal(symbol, tf, result)
                check_tp_sl(symbol, tf)
        await asyncio.sleep(10)

# === Start Bot ===
app = Application.builder().token(TELEGRAM_TOKEN).build()
app.add_handler(CommandHandler("adduser", add_user))
app.add_handler(CommandHandler("removeuser", remove_user))

loop = asyncio.get_event_loop()
loop.create_task(runner())
app.run_polling()