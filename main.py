import os
import time
import pandas as pd
import matplotlib.pyplot as plt
from io import BytesIO
from binance.client import Client
from telegram import Bot
import asyncio

# ===== INIT CLIENTS FROM REPLIT SECRETS =====
BINANCE_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_SECRET = os.getenv("BINANCE_API_SECRET")
TG_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

binance = Client(BINANCE_KEY, BINANCE_SECRET)
bot = Bot(token=TG_TOKEN)

# ===== SEND HELLO ON BOT START =====
async def send_hello():
    try:
        await bot.send_message(chat_id=str(CHAT_ID), text="Hello 👋 Bot is live & scanning BTC/ETH markets...")
        print("Hello message sent ✔")
    except Exception as e:
        print("Telegram Error:", e)

# ===== MARKETS & SETTINGS =====
crypto = ["BTCUSDT", "ETHUSDT"]
timeframes = ["1m", "5m", "15m", "30m"]
CANDLES = 50

sent_signals = set()

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
    c = df["c"].values[-10:]
    if c[-1] > c[0]: return "Uptrend"
    if c[-1] < c[0]: return "Downtrend"
    return "Sideways"

# ===== SIGNAL LOGIC (PRO STYLE) =====
def generate_signal(symbol, tf):
    df = get_binance_data(symbol, tf)
    price = df["c"].iloc[-1]
    ema50 = df["c"].ewm(span=50).mean().iloc[-1]
    sup, res = support_resistance(df)
    liq_high, liq_low = liquidity_levels(df)
    vol = df["v"].iloc[-1]
    avg_vol = df["v"].mean()
    trend = detect_trend(df)

    bias = None
    reason = ""

    # Pro trader style conditions
    if price > ema50 and price <= sup * 1.003 and vol > avg_vol * 1.2:
        bias = "BUY"
        reason = f"Strong EMA50 cross + Support retest + Volume spike + {trend}"
    elif price < ema50 and price >= res * 0.997 and vol > avg_vol * 1.2:
        bias = "SELL"
        reason = f"EMA50 rejection + Resistance zone + Volume spike + {trend}"

    if not bias:
        return None

    entry = price
    sl = entry - 20 if bias=="BUY" else entry + 20

    # Targets 1:2, 1:3, 1:4
    r = abs(entry - sl)
    tp2 = entry + 2*r if bias=="BUY" else entry - 2*r
    tp3 = entry + 3*r if bias=="BUY" else entry - 3*r
    tp4 = entry + 4*r if bias=="BUY" else entry - 4*r

    ob = order_block(df, bias)

    return {"bias":bias,"entry":entry,"sl":sl,"tp":[tp2,tp3,tp4],"sup":sup,"res":res,"liq_high":liq_high,"liq_low":liq_low,"ob":ob,"vol":vol,"tf":tf,"reason":reason,"df":df}

# ===== PLOT CHART =====
def plot_chart(df, sig):
    plt.figure(figsize=(10,4))
    plt.plot(df["c"], label="Close Price")
    plt.axhline(sig["sup"], linestyle="--", label="Support")
    plt.axhline(sig["res"], linestyle="--", label="Resistance")
    plt.axhline(sig["liq_high"], linestyle="--", label="Liquidity High")
    plt.axhline(sig["liq_low"], linestyle="--", label="Liquidity Low")
    plt.plot(df["c"].ewm(span=50).mean(), linestyle="-", label="EMA50")
    plt.title(f"{sig['bias']} Setup | TF: {sig['tf']}")
    plt.legend()

    buf = BytesIO()
    plt.savefig(buf, format="png")
    buf.seek(0)
    plt.close()
    return buf

# ===== SEND SIGNAL TO TELEGRAM =====
async def send_signal(sig, symbol, chart_buf):
    sig_id = f"{symbol}-{sig['bias']}-{sig['tf']}"
    if sig_id in sent_signals:
        return

    sent_signals.add(sig_id)

    emoji = "🟢 BUY" if sig["bias"]=="BUY" else "🔴 SELL"

    msg = f"""
{emoji} SIGNAL ({sig['tf']})

Crypto: {symbol}
💰 Entry: {sig['entry']:.2f}
🛑 Stop Loss: {sig['sl']:.2f}  (20 Points)

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

    await bot.send_photo(chat_id=str(CHAT_ID), photo=chart_buf, caption=msg)
    print(f"Signal sent: {sig_id} ✔")

# ===== MAIN LOOP =====
async def main():
    await send_hello()

    while True:
        for sym in crypto:
            for tf in timeframes:
                sig = generate_signal(sym, tf)
                if not sig:
                    continue
                chart_buf = plot_chart(sig["df"], sig)
                await send_signal(sig, sym, chart_buf)

        time.sleep(60)

if __name__ == "__main__":
    asyncio.run(main())