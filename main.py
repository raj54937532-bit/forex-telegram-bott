---

# 🔹 5️⃣ main.py (Fresh Ultra Smart Bot)

```python
# main.py
import MetaTrader5 as mt5
import pandas as pd
import requests
import matplotlib.pyplot as plt
from datetime import datetime
import time
import os
from config import BOT_TOKEN, CHAT_ID

# -------------------- MEMORY --------------------
sent_signals = {}  # Smart memory to avoid repeat signals
pro_message_sent = False  # Send "Hello Pro traders" only once

# -------------------- TELEGRAM FUNCTION --------------------
def send_signal(message, screenshot=None, inline_buttons=None):
    if screenshot:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
        with open(screenshot, 'rb') as photo:
            data = {"chat_id": CHAT_ID, "caption": message}
            if inline_buttons:
                data["reply_markup"] = inline_buttons
            requests.post(url, data=data, files={"photo": photo})
    else:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        data = {"chat_id": CHAT_ID, "text": message}
        if inline_buttons:
            data["reply_markup"] = inline_buttons
        requests.post(url, data=data)

# -------------------- INITIALIZE MT5 --------------------
if not mt5.initialize():
    print("❌ MT5 connection failed:", mt5.last_error())
else:
    print("✅ MT5 connected successfully")

# -------------------- SYMBOLS & TIMEFRAMES --------------------
symbols = [
    "EURUSD","GBPUSD","USDJPY","AUDUSD","USDCAD","USDCHF","NZDUSD",
    "BTCUSDT","ETHUSDT","BNBUSDT","SOLUSDT","XRPUSDT","ADAUSDT",
    "GOLD","SILVER","USOIL","BRENT",
    "DJI","SP500","NASDAQ","DAX","FTSE100","NIKKEI225","HANGSENG"
]

timeframes = [mt5.TIMEFRAME_M5, mt5.TIMEFRAME_M15, mt5.TIMEFRAME_H1]

# -------------------- FUNCTIONS --------------------
def get_data(symbol, timeframe, n=100):
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, n)
    data = pd.DataFrame(rates)
    data['time'] = pd.to_datetime(data['time'], unit='s')
    data['EMA20'] = data['close'].ewm(span=20, adjust=False).mean()
    data['EMA50'] = data['close'].ewm(span=50, adjust=False).mean()
    delta = data['close'].diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()
    rs = avg_gain / avg_loss
    data['RSI'] = 100 - (100 / (1 + rs))
    return data

def calculate_sl_tp(last_price):
    sl = round(last_price * 0.995, 2)
    tp = round(last_price * 1.01, 2)
    return sl, tp

def generate_chart(data, symbol, tf):
    plt.figure(figsize=(8,4))
    plt.plot(data['time'], data['close'], label='Close', color='blue')
    plt.plot(data['time'], data['EMA20'], label='EMA20', color='green')
    plt.plot(data['time'], data['EMA50'], label='EMA50', color='red')
    plt.title(f"{symbol} - TF:{tf}")
    plt.xlabel("Time")
    plt.ylabel("Price")
    plt.legend()
    filename = f"{symbol}_{tf}.png"
    plt.savefig(filename)
    plt.close()
    return filename

def check_pro_message():
    global pro_message_sent
    message_from_group = "Hello Pro traders"  # Replace with Telegram fetch if needed
    if "Hello Pro traders" in message_from_group and not pro_message_sent:
        send_signal("💬 Hello Pro traders! Bot is online and scanning signals 🚀")
        pro_message_sent = True

def create_inline_buttons():
    return '{"inline_keyboard":[[{"text":"Mark as done","callback_data":"done"},{"text":"Ignore","callback_data":"ignore"}]]}'

# -------------------- MAIN LOOP --------------------
while True:
    check_pro_message()

    for symbol in symbols:
        for tf in timeframes:
            data = get_data(symbol, tf)
            last = data.iloc[-1]
            last_price = last['close']
            sl, tp = calculate_sl_tp(last_price)
            chart_file = generate_chart(data, symbol, tf)
            key = f"{symbol}_{tf}"

            # BUY
            if last['EMA20'] > last['EMA50'] and last['RSI'] > 55:
                if sent_signals.get(key) != 'BUY':
                    message = f"🟢 BUY {symbol}\nTF: {tf}\nPrice: {last_price}\nRSI: {int(last['RSI'])}\nEMA20 > EMA50\nSL: {sl} TP: {tp}"
                    send_signal(message, chart_file, create_inline_buttons())
                    sent_signals[key] = 'BUY'
                    print(f"{datetime.now()} → Sent BUY {symbol} TF:{tf}")

            # SELL
            elif last['EMA20'] < last['EMA50'] and last['RSI'] < 45:
                if sent_signals.get(key) != 'SELL':
                    message = f"🔴 SELL {symbol}\nTF: {tf}\nPrice: {last_price}\nRSI: {int(last['RSI'])}\nEMA20 < EMA50\nSL: {sl} TP: {tp}"
                    send_signal(message, chart_file, create_inline_buttons())
                    sent_signals[key] = 'SELL'
                    print(f"{datetime.now()} → Sent SELL {symbol} TF:{tf}")

            # Remove chart
            if os.path.exists(chart_file):
                os.remove(chart_file)

    time.sleep(60)