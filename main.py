import os, hashlib, time
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from io import BytesIO
from binance.client import Client
from telegram import Bot
from dotenv import load_dotenv
import mplfinance as mpf

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
timeframes = ["1m","5m","15m","30m"]
CANDLES = 50

# Prevent duplicate signals
last_signal_hash = {pair+tf+"BUY": None for pair in crypto for tf in timeframes}
last_signal_hash.update({pair+tf+"SELL": None for pair in crypto for tf in timeframes})

MIN_VOLUME = 0.0005  # adjust per market

# ===== FETCH BINANCE DATA =====
def get_binance_data(symbol, interval="1m"):
    k = binance.get_klines(symbol=symbol, interval=interval, limit=CANDLES)
    df = pd.DataFrame(k, columns=["t","o","h","l","c","v","ct","qav","tr","tbv","tq","i"])
    df = df[["o","h","l","c","v"]].astype(float)
    df.rename(columns={"o":"Open","h":"High","l":"Low","c":"Close","v":"Volume"}, inplace=True)
    df.index = pd.to_datetime(df["t"], unit="ms")
    return df

# ===== INDICATORS =====
def liquidity_levels(df):
    swing_high = df.High.rolling(5, center=True).max().iloc[-3]
    swing_low = df.Low.rolling(5, center=True).min().iloc[-3]
    return swing_high, swing_low

def support_resistance(df):
    support = df.Low.rolling(20).min().iloc[-1]
    resistance = df.High.rolling(20).max().iloc[-1]
    return support, resistance

def order_block(df):
    prev = df.iloc[-2]
    if prev.Close > prev.Open:
        return "Bullish OB", prev.Low, prev.High
    else:
        return "Bearish OB", prev.Low, prev.High

# ===== SIGNAL CALCULATION =====
def calc_signals(df, tf):
    price = df.Close.iloc[-1]
    prev_price = df.Close.iloc[-2]
    ema50_series = df.Close.ewm(span=50, adjust=False).mean()
    ema50 = ema50_series.iloc[-1]
    prev_ema50 = ema50_series.iloc[-2]

    swing_high, swing_low = liquidity_levels(df)
    support, resistance = support_resistance(df)
    ob_name, ob_low, ob_high = order_block(df)
    vol = df.Volume.iloc[-1]

    signals = []

    if vol < MIN_VOLUME:
        return signals

    # ===== BUY SIGNAL =====
    if price > ema50 and prev_price <= prev_ema50 and price > swing_high:
        entry = price
        sl = swing_low - 0.1  # Pro trader style nearest swing
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
    if price < ema50 and prev_price >= prev_ema50 and price < swing_low:
        entry = price
        sl = swing_high + 0.1
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

# ===== PLOT CHART (Candlestick + Markers) =====
def plot_chart(df, signal=None):
    mc = mpf.make_marketcolors(up='g', down='r', wick='i', edge='i', volume='in')
    s  = mpf.make_mpf_style(marketcolors=mc)
    
    addplots = []
    if signal:
        # Entry marker
        addplots.append(mpf.make_addplot([signal["entry"]]*len(df), type='scatter', markersize=50, marker='^' if signal["bias"]=="BUY" else 'v', color='lime' if signal["bias"]=="BUY" else 'red'))
        # SL marker
        addplots.append(mpf.make_addplot([signal["sl"]]*len(df), type='scatter', markersize=40, marker='x', color='blue'))
        # TP1 marker
        addplots.append(mpf.make_addplot([signal["tp2"]]*len(df), type='scatter', markersize=40, marker='o', color='yellow'))
        # TP2 marker
        addplots.append(mpf.make_addplot([signal["tp3"]]*len(df), type='scatter', markersize=40, marker='o', color='orange'))
        # TP3 marker
        addplots.append(mpf.make_addplot([signal["tp4"]]*len(df), type='scatter', markersize=40, marker='o', color='magenta'))

    fig, axlist = mpf.plot(df, type='candle', style=s, addplot=addplots, volume=True, returnfig=True)
    buf = BytesIO()
    fig.savefig(buf, format='png')
    buf.seek(0)
    mpf.close(fig)
    return buf

# ===== SEND TELEGRAM SIGNAL =====
def send_signal(data, symbol, chart_buf):
    emoji = "🟢" if data["bias"]=="BUY" else "🔴"
    arrow = "⬆️" if data["bias"]=="BUY" else "⬇️"
    
    msg = (f"{emoji} *PRO TRADER SIGNAL* {arrow}\n"
           f"*Market:* {symbol} | *Timeframe:* {data['timeframe']}\n"
           f"*Direction:* {data['bias']}\n"
           f"*Entry:* {data['entry']:.2f}\n"
           f"*SL:* {data['sl']:.2f}\n"
           f"*Targets:* 1:2 → {data['tp2']:.2f}, 1:3 → {data['tp3']:.2f}, 1:4 → {data['tp4']:.2f}\n"
           f"💧 Liquidity: {data['liq_low']:.2f}/{data['liq_high']:.2f}\n"
           f"📊 Volume: {data['volume']:.2f}\n"
           f"🟢 Support / 🔴 Resistance: {data['support']:.2f}/{data['resistance']:.2f}\n"
           f"🧱 Order Block: {data['ob']}\n"
           f"📈 EMA50: {data['ema50']:.2f}")

    h = hashlib.md5(msg.encode()).hexdigest()
    key = symbol + data["timeframe"] + data["bias"]
    if last_signal_hash[key] == h:
        return
    last_signal_hash[key] = h
    bot.send_photo(chat_id=CHAT_ID, photo=chart_buf, caption=msg, parse_mode='Markdown')

# ===== MAIN LOOP =====
if __name__ == "__main__":
    while True:
        for sym in crypto:
            for tf in timeframes:
                try:
                    df = get_binance_data(sym, tf)
                    signals = calc_signals(df, tf)
                    for sig in signals:
                        chart_buf = plot_chart(df, sig)
                        send_signal(sig, sym, chart_buf)
                except Exception as e:
                    print(f"Error {sym} {tf}: {e}")
        time.sleep(60)