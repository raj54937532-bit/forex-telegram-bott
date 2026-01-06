import os
import time
import pandas as pd
import matplotlib.pyplot as plt
from io import BytesIO
from binance.client import Client
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes
import asyncio
from datetime import datetime
import pytz
import numpy as np

# ===== INIT CLIENTS =====
BINANCE_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_SECRET = os.getenv("BINANCE_API_SECRET")
TG_TOKEN = os.getenv("TELEGRAM_TOKEN")
MAIN_CHAT_ID = int(os.getenv("TELEGRAM_CHAT_ID"))

binance = Client(BINANCE_KEY, BINANCE_SECRET)
bot = Bot(token=TG_TOKEN)

# ===== ALLOWED USERS =====
ALLOWED_USERS = [MAIN_CHAT_ID]

# ===== RESTRICTED DECORATOR =====
def restricted(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_chat.id
        if user_id not in ALLOWED_USERS:
            await update.message.reply_text("❌ You are not allowed to use this bot.")
            return
        return await func(update, context)
    return wrapper

# ===== ADD USER COMMAND =====
@restricted
async def adduser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        await update.message.reply_text("Usage: /adduser <chat_id>")
        return
    try:
        new_id = int(context.args[0])
        user_name = update.effective_user.first_name if update.effective_user else "User"
        if new_id in ALLOWED_USERS:
            await update.message.reply_text(f"{user_name}, this user is already added ✅")
        else:
            ALLOWED_USERS.append(new_id)
            await update.message.reply_text(f"Hello {user_name} 👋, user {new_id} has been added successfully ✅")
            await bot.send_message(chat_id=new_id, text=f"Hello {user_name} 👋! You have been added to the trading bot.")
    except ValueError:
        await update.message.reply_text("Please provide a valid numeric chat_id")

# ===== MARKETS & SETTINGS =====
crypto = ["BTCUSDT", "ETHUSDT"]
timeframes = ["1m", "5m", "15m", "30m"]
CANDLES = 100
sent_signals = set()
INDIA_TZ = pytz.timezone("Asia/Kolkata")

# ===== FETCH MARKET DATA =====
def get_binance_data(symbol, interval):
    k = binance.get_klines(symbol=symbol, interval=interval, limit=CANDLES)
    df = pd.DataFrame(k, columns=["t","o","h","l","c","v","ct","qav","tr","tbv","tq","i"])
    df = df[["o","h","l","c","v"]].astype(float)
    return df

# ===== LEVELS =====
def support_resistance(df):
    return df["l"].min(), df["h"].max()

def liquidity_levels(df):
    return df["h"].rolling(5, center=True).max().iloc[-3], df["l"].rolling(5, center=True).min().iloc[-3]

def order_block(df, bias):
    return df["l"].iloc[-2] if bias == "BUY" else df["h"].iloc[-2]

def detect_trend(df):
    c = df["c"].values[-20:]
    if c[-1] > c[0]: return "Uptrend"
    if c[-1] < c[0]: return "Downtrend"
    return "Sideways"

# ===== FAIR VALUE GAP =====
def fair_value_gap(df):
    if len(df) < 3:
        return None, None
    fvg_up = None
    fvg_down = None
    for i in range(len(df)-2):
        c1, c2, c3 = df.iloc[i], df.iloc[i+1], df.iloc[i+2]
        if c2["l"] > c1["h"]: fvg_up = (c1["h"], c2["l"])
        if c2["h"] < c1["l"]: fvg_down = (c2["h"], c1["l"])
    return fvg_up, fvg_down

# ===== CHART PATTERN =====
def detect_chart_pattern(df):
    highs = df["h"].values
    lows = df["l"].values
    pattern = None
    direction = None
    if len(highs) >= 3 and highs[-1] < highs[-2] > highs[-3]:
        pattern = "Double Top"
        direction = "SELL"
    elif len(lows) >=3 and lows[-1] > lows[-2] < lows[-3]:
        pattern = "Double Bottom"
        direction = "BUY"
    return pattern, direction

# ===== CANDLESTICK FILTER =====
def candlestick_signal(df, bias):
    last_candle = df.iloc[-1]
    body = abs(last_candle['c'] - last_candle['o'])
    if bias=="BUY" and last_candle['c'] < last_candle['o']:
        return False
    if bias=="SELL" and last_candle['c'] > last_candle['o']:
        return False
    return True

# ===== ALGO FILTER =====
def algo_confirm(df, bias):
    # Trend + EMA + Liquidity + FVG + Candlestick
    ema50 = df["c"].ewm(span=50).mean().iloc[-1]
    price = df["c"].iloc[-1]
    trend = detect_trend(df)
    liq_high, liq_low = liquidity_levels(df)
    sup, res = support_resistance(df)
    fvg_up, fvg_down = fair_value_gap(df)
    pattern, pattern_dir = detect_chart_pattern(df)

    # Bias filter
    if bias=="BUY":
        if not (trend=="Uptrend" and price>ema50 and ((fvg_up is not None) or pattern_dir=="BUY")):
            return False
        if not candlestick_signal(df, "BUY"):
            return False
    if bias=="SELL":
        if not (trend=="Downtrend" and price<ema50 and ((fvg_down is not None) or pattern_dir=="SELL")):
            return False
        if not candlestick_signal(df, "SELL"):
            return False
    return True

# ===== SIGNAL LOGIC =====
def generate_signal(symbol, tf):
    df = get_binance_data(symbol, tf)
    price = df["c"].iloc[-1]
    sl_point = 16 if symbol=="BTCUSDT" else 8
    ema50 = df["c"].ewm(span=50).mean().iloc[-1]

    # Initial bias
    bias = None
    if price > ema50:
        bias="BUY"
    elif price < ema50:
        bias="SELL"

    # Algo confirm
    if not bias or not algo_confirm(df, bias):
        return None

    # Levels
    sup, res = support_resistance(df)
    liq_high, liq_low = liquidity_levels(df)
    ob = order_block(df, bias)
    fvg_up, fvg_down = fair_value_gap(df)
    pattern, pattern_dir = detect_chart_pattern(df)

    # Entry/SL/TP
    entry = price
    sl = entry - sl_point if bias=="BUY" else entry + sl_point
    r = abs(entry-sl)
    tp2 = entry + 2*r if bias=="BUY" else entry - 2*r
    tp3 = entry + 3*r if bias=="BUY" else entry - 3*r
    tp4 = entry + 4*r if bias=="BUY" else entry - 4*r

    reason = f"{bias} confirmed by SMC + FVG + Liquidity + EMA50 + Trend"
    if pattern: reason += f" + Pattern: {pattern}"

    return {"bias":bias,"entry":entry,"sl":sl,"tp":[tp2,tp3,tp4],
            "sup":sup,"res":res,"liq_high":liq_high,"liq_low":liq_low,
            "ob":ob,"vol":df["v"].iloc[-1],"tf":tf,"reason":reason,"df":df,
            "fvg_up":fvg_up,"fvg_down":fvg_down,"pattern":pattern}

# ===== PLOT CHART =====
def plot_chart(df, sig):
    plt.figure(figsize=(10,4))
    plt.plot(df["c"], label="Close")
    plt.plot(df["c"].ewm(span=50).mean(), label="EMA50")
    plt.axhline(sig["sup"], linestyle="--", color="green", label="Support")
    plt.axhline(sig["res"], linestyle="--", color="red", label="Resistance")
    plt.axhline(sig["liq_high"], linestyle="--", color="orange", label="Liquidity High")
    plt.axhline(sig["liq_low"], linestyle="--", color="purple", label="Liquidity Low")
    plt.axhline(sig["ob"], linestyle="--", color="blue", label="Order Block")
    if sig["fvg_up"]:
        plt.fill_between(range(len(df)), sig["fvg_up"][0], sig["fvg_up"][1], color="green", alpha=0.3)
    if sig["fvg_down"]:
        plt.fill_between(range(len(df)), sig["fvg_down"][0], sig["fvg_down"][1], color="red", alpha=0.3)
    if sig["pattern"]:
        plt.text(len(df)-5, df["c"].iloc[-1], sig["pattern"], fontsize=10, color="black")
    plt.title(f"{sig['bias']} | TF: {sig['tf']}")
    plt.legend()
    buf = BytesIO()
    plt.savefig(buf, format="png")
    buf.seek(0)
    plt.close()
    return buf

# ===== SEND SIGNAL =====
async def send_signal(sig, symbol):
    sig_id = f"{symbol}-{sig['bias']}-{sig['tf']}-{sig['entry']:.2f}"
    if sig_id in sent_signals: return
    sent_signals.add(sig_id)
    chart_buf = plot_chart(sig["df"], sig)
    emoji = "🟢 BUY" if sig["bias"]=="BUY" else "🔴 SELL"
    timestamp = datetime.now(INDIA_TZ).strftime("%Y-%m-%d %H:%M:%S IST")
    msg = f"""
{emoji} SIGNAL ({sig['tf']}) ⏰ {timestamp}

Crypto: {symbol}
💰 Entry: {sig['entry']:.2f}
🛑 Stop Loss: {sig['sl']:.2f}

🎯 Targets:
1:2 → {sig['tp'][0]:.2f}
1:3 → {sig['tp'][1]:.2f}
1:4 → {sig['tp'][2]:.2f}

📊 Market Levels:
Support → {sig['sup']:.2f}
Resistance → {sig['res']:.2f}
Liquidity High → {sig['liq_high']:.2f}
Liquidity Low → {sig['liq_low']:.2f}
Order Block → {sig['ob']:.2f}
📈 Volume → {sig['vol']:.2f}

🧠 Reason: {sig['reason']}
"""
    for user in ALLOWED_USERS:
        await bot.send_photo(chat_id=user, photo=chart_buf, caption=msg)
    print(f"Signal sent: {sig_id} ✔")

# ===== HELLO =====
async def send_hello_message():
    for user in ALLOWED_USERS:
        await bot.send_message(chat_id=user, text="Hello 👋 Bot is live & scanning BTC/ETH markets...")
    print("Hello message sent ✔")

# ===== MAIN LOOP =====
async def main_loop():
    await send_hello_message()
    while True:
        for sym in crypto:
            for tf in timeframes:
                sig = generate_signal(sym, tf)
                if sig:
                    await send_signal(sig, sym)
        await asyncio.sleep(5)

# ===== RUN BOT =====
async def main():
    app = Application.builder().token(TG_TOKEN).build()
    app.add_handler(CommandHandler("adduser", adduser))
    asyncio.create_task(main_loop())
    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())