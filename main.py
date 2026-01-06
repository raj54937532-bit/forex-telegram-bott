import os
import pandas as pd
import matplotlib.pyplot as plt
from io import BytesIO
from binance.client import Client
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes
import asyncio
from datetime import datetime
import pytz

# ===== INIT CLIENTS =====
BINANCE_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_SECRET = os.getenv("BINANCE_API_SECRET")
TG_TOKEN = os.getenv("TELEGRAM_TOKEN")
MAIN_CHAT_ID = int(os.getenv("TELEGRAM_CHAT_ID"))

binance = Client(BINANCE_KEY, BINANCE_SECRET)
bot = Bot(token=TG_TOKEN)

# ===== ALLOWED USERS =====
ALLOWED_USERS = [MAIN_CHAT_ID]

# ===== SIGNAL TRACKER =====
sent_signals = set()
active_trades = {}  # key: sig_id, value: dict(entry,sl,tp,bias,symbol,tf)
INDIA_TZ = pytz.timezone("Asia/Kolkata")

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
            await update.message.reply_text(f"Hello {user_name} 👋, user {new_id} added ✅")
            await bot.send_message(chat_id=new_id, text=f"Hello Pro Trader 👋 You have access to the trading bot.")
    except ValueError:
        await update.message.reply_text("Please provide a valid numeric chat_id")

# ===== MARKETS & TIMEFRAMES =====
crypto = ["BTCUSDT", "ETHUSDT"]
timeframes = ["1m", "5m", "15m", "30m"]  # Only short-term
CANDLES = 100

# ===== FETCH MARKET DATA =====
def get_binance_data(symbol, interval):
    k = binance.get_klines(symbol=symbol, interval=interval, limit=CANDLES)
    df = pd.DataFrame(k, columns=["t","o","h","l","c","v","ct","qav","tr","tbv","tq","i"])
    df = df[["o","h","l","c","v"]].astype(float)
    return df

# ===== TECHNICAL LEVELS & SMC =====
def calc_levels(df):
    sup = df["l"].min()
    res = df["h"].max()
    liq_high = df["h"].rolling(5, center=True).max().iloc[-3]
    liq_low = df["l"].rolling(5, center=True).min().iloc[-3]
    return sup, res, liq_high, liq_low

def detect_trend(df):
    if df["c"].iloc[-1] > df["c"].iloc[-20]:
        return "Uptrend"
    elif df["c"].iloc[-1] < df["c"].iloc[-20]:
        return "Downtrend"
    return "Sideways"

def ema50(df):
    return df["c"].ewm(span=50).mean().iloc[-1]

def fair_value_gap(df):
    if len(df)<3: return None,None
    fvg_up, fvg_down = None,None
    for i in range(len(df)-2):
        c1,c2,c3 = df.iloc[i],df.iloc[i+1],df.iloc[i+2]
        if c2["l"] > c1["h"]: fvg_up = (c1["h"],c2["l"])
        if c2["h"] < c1["l"]: fvg_down = (c2["h"],c1["l"])
    return fvg_up,fvg_down

def detect_chart_pattern(df):
    highs = df["h"].values
    lows = df["l"].values
    pattern = None
    direction = None
    if len(highs)>=3 and highs[-1]<highs[-2]>highs[-3]:
        pattern="Double Top"; direction="SELL"
    elif len(lows)>=3 and lows[-1]>lows[-2]<lows[-3]:
        pattern="Double Bottom"; direction="BUY"
    return pattern,direction

def candlestick_signal(df,bias):
    last = df.iloc[-1]
    if bias=="BUY" and last["c"]<last["o"]: return False
    if bias=="SELL" and last["c"]>last["o"]: return False
    return True

def algo_confirm(df,bias):
    trend = detect_trend(df)
    price = df["c"].iloc[-1]
    e50 = ema50(df)
    fvg_up,fvg_down = fair_value_gap(df)
    pattern,dirc = detect_chart_pattern(df)
    if bias=="BUY":
        if not (trend=="Uptrend" and price>e50 and ((fvg_up is not None) or dirc=="BUY")): return False
        if not candlestick_signal(df,"BUY"): return False
    if bias=="SELL":
        if not (trend=="Downtrend" and price<e50 and ((fvg_down is not None) or dirc=="SELL")): return False
        if not candlestick_signal(df,"SELL"): return False
    return True

# ===== SIGNAL LOGIC =====
def generate_signal(symbol,tf):
    df=get_binance_data(symbol,tf)
    price=df["c"].iloc[-1]
    sl_point = 16 if symbol=="BTCUSDT" else 7
    e50 = ema50(df)
    bias=None
    if price>e50: bias="BUY"
    elif price<e50: bias="SELL"
    if not bias or not algo_confirm(df,bias): return None

    sup,res,liq_high,liq_low=calc_levels(df)
    ob = df["l"].iloc[-2] if bias=="BUY" else df["h"].iloc[-2]
    fvg_up,fvg_down=fair_value_gap(df)
    pattern,pattern_dir=detect_chart_pattern(df)

    entry=price
    sl=entry-sl_point if bias=="BUY" else entry+sl_point
    r=abs(entry-sl)
    tp=[entry+2*r if bias=="BUY" else entry-2*r,
        entry+3*r if bias=="BUY" else entry-3*r,
        entry+4*r if bias=="BUY" else entry-4*r]

    reason=f"{bias} confirmed by SMC+FVG+Liquidity+EMA50+Trend"
    if pattern: reason+=f"+Pattern:{pattern}"

    return {"bias":bias,"entry":entry,"sl":sl,"tp":tp,"sup":sup,"res":res,
            "liq_high":liq_high,"liq_low":liq_low,"ob":ob,"vol":df["v"].iloc[-1],
            "tf":tf,"reason":reason,"df":df,"fvg_up":fvg_up,"fvg_down":fvg_down,
            "pattern":pattern}

# ===== PLOT CHART =====
def plot_chart(df,sig):
    plt.figure(figsize=(10,4))
    plt.plot(df["c"],label="Close")
    plt.plot(df["c"].ewm(span=50).mean(),label="EMA50")
    plt.axhline(sig["sup"],linestyle="--",color="green",label="Support")
    plt.axhline(sig["res"],linestyle="--",color="red",label="Resistance")
    plt.axhline(sig["liq_high"],linestyle="--",color="orange",label="Liquidity High")
    plt.axhline(sig["liq_low"],linestyle="--",color="purple",label="Liquidity Low")
    plt.axhline(sig["ob"],linestyle="--",color="blue",label="Order Block")
    if sig["fvg_up"]: plt.fill_between(range(len(df)),sig["fvg_up"][0],sig["fvg_up"][1],color="green",alpha=0.3)
    if sig["fvg_down"]: plt.fill_between(range(len(df)),sig["fvg_down"][0],sig["fvg_down"][1],color="red",alpha=0.3)
    if sig["pattern"]: plt.text(len(df)-5,df["c"].iloc[-1],sig["pattern"],fontsize=10,color="black")
    plt.title(f"{sig['bias']} | TF:{sig['tf']}")
    plt.legend()
    buf=BytesIO()
    plt.savefig(buf,format="png")
    buf.seek(0)
    plt.close()
    return buf

# ===== SEND SIGNAL & TRACK TP/SL =====
async def send_signal(sig,symbol):
    sig_id=f"{symbol}-{sig['bias']}-{sig['tf']}-{sig['entry']:.2f}"
    if sig_id not in active_trades:
        active_trades[sig_id] = {"entry":sig["entry"],"sl":sig["sl"],"tp":sig["tp"],
                                 "bias":sig["bias"],"symbol":symbol,"tf":sig["tf"]}

    # Check if TP/SL hit
    last_price = sig["df"]["c"].iloc[-1]
    trade = active_trades[sig_id]

    if trade["bias"]=="BUY":
        if last_price>=trade["tp"][0]:
            for user in ALLOWED_USERS:
                await bot.send_message(chat_id=user,text=f"🎉 {trade['symbol']} {trade['bias']} Target 1:2 Hit ✅")
            del active_trades[sig_id]
            return
        if last_price<=trade["sl"]:
            for user in ALLOWED_USERS:
                await bot.send_message(chat_id=user,text=f"⚠️ {trade['symbol']} {trade['bias']} Stop Loss Hit ❌")
            del active_trades[sig_id]
            return
    else:
        if last_price<=trade["tp"][0]:
            for user in ALLOWED_USERS:
                await bot.send_message(chat_id=user,text=f"🎉 {trade['symbol']} {trade['bias']} Target 1:2 Hit ✅")
            del active_trades[sig_id]
            return
        if last_price>=trade["sl"]:
            for user in ALLOWED_USERS:
                await bot.send_message(chat_id=user,text=f"⚠️ {trade['symbol']} {trade['bias']} Stop Loss Hit ❌")
            del active_trades[sig_id]
            return

    # Send initial signal only once
    if sig_id in sent_signals: return
    sent_signals.add(sig_id)
    chart_buf = plot_chart(sig["df"],sig)
    emoji = "🟢 BUY" if sig["bias"]=="BUY" else "🔴 SELL"
    timestamp = datetime.now(INDIA_TZ).strftime("%Y-%m-%d %H:%M:%S IST")
    msg=f"""
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
        await bot.send_photo(chat_id=user,photo=chart_buf,caption=msg)
    print(f"Signal sent: {sig_id} ✔")

# ===== HELLO =====
async def send_hello():
    for user in ALLOWED_USERS:
        await bot.send_message(chat_id=user,text="Hello Pro Traders 👋 Bot is live & scanning BTC/ETH markets...")
    print("Hello message sent ✔")

# ===== MAIN LOOP =====
async def main_loop():
    await send_hello()
    while True:
        for sym in crypto:
            for tf in timeframes:
                sig=generate_signal(sym,tf)
                if sig:
                    await send_signal(sig,sym)
        await asyncio.sleep(5)

# ===== RUN BOT =====
async def main():
    app=Application.builder().token(TG_TOKEN).build()
    app.add_handler(CommandHandler("adduser",adduser))
    asyncio.create_task(main_loop())
    await app.run_polling()

if __name__=="__main__":
    asyncio.run(main())