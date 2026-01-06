import os, hashlib, time
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from io import BytesIO
from binance.client import Client
from telegram import Bot
from dotenv import load_dotenv

# ===== LOAD ENV VARIABLES =====
load_dotenv()
BINANCE_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_SECRET = os.getenv("BINANCE_API_SECRET")
TG_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# ===== INIT =====
binance = Client(BINANCE_KEY, BINANCE_SECRET)
bot = Bot(token=TG_TOKEN)

# ===== MARKETS & TIMEFRAMES =====
crypto = ["BTCUSDT", "ETHUSDT"]
timeframes = ["1m","5m","15m","30m","1h","4h","1w"]
CANDLES = 50

# To prevent duplicate messages
last_signal_hash = {pair+tf+"BUY": None for pair in crypto for tf in timeframes}
last_signal_hash.update({pair+tf+"SELL": None for pair in crypto for tf in timeframes})

# ===== FETCH BINANCE DATA =====
def get_binance_data(symbol, interval="1h"):
    k = binance.get_klines(symbol=symbol, interval=interval, limit=CANDLES)
    df = pd.DataFrame(k, columns=["t","o","h","l","c","v","ct","qav","tr","tbv","tq","i"])
    df = df[["o","h","l","c","v"]].astype(float)
    df.rename(columns={"o":"open","h":"high","l":"low","c":"close","v":"volume"}, inplace=True)
    return df

# ===== LIQUIDITY LEVELS =====
def liquidity_levels(df):
    swing_high = df.high.rolling(5, center=True).max().iloc[-3]
    swing_low = df.low.rolling(5, center=True).min().iloc[-3]
    return swing_high, swing_low

# ===== SUPPORT & RESISTANCE =====
def support_resistance(df):
    support = df.low.rolling(20).min().iloc[-1]
    resistance = df.high.rolling(20).max().iloc[-1]
    return support, resistance

# ===== ORDER BLOCK =====
def order_block(df):
    prev = df.iloc[-2]
    if prev.close > prev.open:
        return "Bullish OB", prev.low, prev.high
    else:
        return "Bearish OB", prev.low, prev.high

# ===== SIGNAL CALCULATION =====
def calc_signals(df, tf):
    price = df.close.iloc[-1]
    ema50 = df.close.ewm(span=50, adjust=False).mean().iloc[-1]
    swing_high, swing_low = liquidity_levels(df)
    support, resistance = support_resistance(df)
    ob_name, ob_low, ob_high = order_block(df)
    vol = df.volume.iloc[-1]

    signals = []

    # ===== BUY SIGNAL =====
    if price > ema50:
        entry = price
        sl = entry - 20
        tp2 = entry + 20*2
        tp3 = entry + 20*3
        tp4 = entry + 20*4
        signals.append({
            "bias":"BUY","entry":entry,"sl":sl,"tp2":tp2,"tp3":tp3,"tp4":tp4,
            "liq_high":swing_high,"liq_low":swing_low,
            "support":support,"resistance":resistance,
            "ema50":ema50,"volume":vol,
            "ob":f"{ob_name} ({round(ob_low,2)}-{round(ob_high,2)})",
            "timeframe":tf
        })

    # ===== SELL SIGNAL =====
    if price < ema50:
        entry = price
        sl = entry + 20
        tp2 = entry - 20*2
        tp3 = entry - 20*3
        tp4 = entry - 20*4
        signals.append({
            "bias":"SELL","entry":entry,"sl":sl,"tp2":tp2,"tp3":tp3,"tp4":tp4,
            "liq_high":swing_high,"liq_low":swing_low,
            "support":support,"resistance":resistance,
            "ema50":ema50,"volume":vol,
            "ob":f"{ob_name} ({round(ob_low,2)}-{round(ob_high,2)})",
            "timeframe":tf
        })

    return signals

# ===== PLOT CHART =====
def plot_chart(df, symbol):
    plt.figure(figsize=(10,5))
    plt.plot(df.close, label="Close", color="blue")
    plt.plot(df.close.ewm(span=50).mean(), label="EMA50", color="magenta")
    swing_high, swing_low = liquidity_levels(df)
    support, resistance = support_resistance(df)
    plt.axhline(swing_high, color="orange", linestyle="--", label="Liquidity High")
    plt.axhline(swing_low, color="yellow", linestyle="--", label="Liquidity Low")
    plt.axhline(support, color="green", linestyle="--", label="Support")
    plt.axhline(resistance, color="red", linestyle="--", label="Resistance")
    plt.title(symbol)
    plt.legend()
    buf = BytesIO()
    plt.savefig(buf, format="png")
    buf.seek(0)
    plt.close()
    return buf

# ===== SEND TELEGRAM SIGNAL =====
def send_signal(data, symbol, chart_buf):
    emoji = "🟢" if data["bias"]=="BUY" else "🔴"
    arrow = "⬆️" if data["bias"]=="BUY" else "⬇️"
    
    msg = (f"{emoji} * 🧿PRO TRADING SIGNAL🧿* {arrow}\n"
           f"*Market:* {symbol} | *Timeframe:* {data['timeframe']}\n"
           f"*Direction:* {data['bias']}\n"
           f"*Entry:* {data['entry']:.2f}\n"
           f"*SL (20pt):* {data['sl']:.2f}\n"
           f"*Targets:*\n🎯1:2 → {data['tp2']:.2f}\n🎯1:3 → {data['tp3']:.2f}\n🎯1:4 → {data['tp4']:.2f}\n"
           f"💧 Liquidity Zones: {data['liq_low']:.2f} / {data['liq_high']:.2f}\n"
           f"📊 Volume: {data['volume']:.2f}\n"
           f"🟢 Support / 🔴 Resistance: {data['support']:.2f} / {data['resistance']:.2f}\n"
           f"🧱 Order Block: {data['ob']}\n"
           f"📈 EMA50: {data['ema50']:.2f}")

    h = hashlib.md5(msg.encode()).hexdigest()
    key = symbol + data["timeframe"] + data["bias"]
    if last_signal_hash[key] == h:
        return
    last_signal_hash[key] = h
    bot.send_photo(chat_id=CHAT_ID, photo=chart_buf, caption=msg, parse_mode="Markdown")

# ===== MAIN LOOP =====
if __name__ == "__main__":
    while True:
        for sym in crypto:
            for tf in timeframes:
                try:
                    df = get_binance_data(sym, tf)
                    chart_buf = plot_chart(df, sym)
                    signals = calc_signals(df, tf)
                    for signal in signals:
                        send_signal(signal, sym, chart_buf)
                except Exception as e:
                    print(f"Error {sym} {tf}: {e}")
        time.sleep(60)