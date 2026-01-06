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

# ===== ENV VARIABLES =====
BINANCE_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_SECRET = os.getenv("BINANCE_API_SECRET")
TG_TOKEN = os.getenv("TELEGRAM_TOKEN")
MAIN_CHAT_ID = int(os.getenv("TELEGRAM_CHAT_ID"))

# ===== INIT CLIENTS =====
binance = Client(BINANCE_KEY, BINANCE_SECRET)
bot = Bot(token=TG_TOKEN)

# ===== USERS =====
ALLOWED_USERS = [MAIN_CHAT_ID]

# ===== TRACKED SIGNALS =====
sent_signals = set()
completed_trades = set()
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
        user_name = update.effective_user.first_name or "User"
        if new_id in ALLOWED_USERS:
            await update.message.reply_text(f"{user_name}, user already added ✅")
        else:
            ALLOWED_USERS.append(new_id)
            await update.message.reply_text(f"{user_name} added successfully ✅")
            await bot.send_message(chat_id=new_id, text=f"Hello {user_name} 👋 You are now a Pro Trader! Welcome to the bot.")
    except ValueError:
        await update.message.reply_text("Please provide a valid numeric chat_id")

# ===== SETTINGS =====
crypto = ["BTCUSDT", "ETHUSDT"]
timeframes = ["1m", "5m", "15m", "30m", "1h", "4h"]
CANDLES = 100

# ===== FETCH DATA =====
def get_binance_data(symbol, interval):
    k = binance.get_klines(symbol=symbol, interval=interval, limit=CANDLES)
    df = pd.DataFrame(k, columns=["t","o","h","l","c","v","ct","qav","tr","tbv","tq","i"])
    df = df[["o","h","l","c","v"]].astype(float)
    return df

# ===== HELPERS =====
def calc_ema50(df): return df["c"].ewm(span=50).mean().iloc[-1]
def support_resistance(df): return df["l"].min(), df["h"].max()
def liquidity_levels(df): return df["h"].rolling(5, center=True).max().iloc[-3], df["l"].rolling(5, center=True).min().iloc[-3]
def order_block(df,bias): return df["l"].iloc[-2] if bias=="BUY" else df["h"].iloc[-2]
def detect_trend(df):
    c = df["c"].values[-20:]
    if c[-1] > c[0]: return "Uptrend"
    if c[-1] < c[0]: return "Downtrend"
    return "Sideways"
def fair_value_gap(df):
    if len(df)<3: return None,None
    fvg_up,fvg_down=None,None
    for i in range(len(df)-2):
        c1,c2,c3=df.iloc[i],df.iloc[i+1],df.iloc[i+2]
        if c2["l"]>c1["h"]: fvg_up=(c1["h"],c2["l"])
        if c2["h"]<c1["l"]: fvg_down=(c2["h"],c1["l"])
    return fvg_up,fvg_down
def detect_chart_pattern(df):
    highs,lows=df["h"].values,df["l"].values
    if len(highs)>=3 and highs[-1]<highs[-2]>highs[-3]: return "Double Top","SELL"
    if len(lows)>=3 and lows[-1]>lows[-2]<lows[-3]: return "Double Bottom","BUY"
    return None,None
def candlestick_signal(df,bias):
    last=df.iloc[-1]
    if bias=="BUY" and last['c']<last['o']: return False
    if bias=="SELL" and last['c']>last['o']: return False
    return True
def algo_confirm(df,bias):
    ema50=calc_ema50(df)
    price=df["c"].iloc[-1]
    trend=detect_trend(df)
    fvg_up,fvg_down=fair_value_gap(df)
    pattern,pattern_dir=detect_chart_pattern(df)
    if bias=="BUY":
        return trend=="Uptrend" and price>ema50 and ((fvg_up is not None) or pattern_dir=="BUY") and candlestick_signal(df,bias)
    if bias=="SELL":
        return trend=="Downtrend" and price<ema50 and ((fvg_down is not None) or pattern_dir=="SELL") and candlestick_signal(df,bias)
    return False

# ===== GENERATE SIGNAL =====
def generate_signal(symbol,tf):
    df=get_binance_data(symbol,tf)
    price=df["c"].iloc[-1]
    sl_point=10 if symbol=="BTCUSDT" else 7
    ema50=calc_ema50(df)
    bias="BUY" if price>ema50 else "SELL" if price<ema50 else None
    if not bias or not algo_confirm(df,bias): return None
    sup,res=support_resistance(df)
    liq_high,liq_low=liquidity_levels(df)
    ob=order_block(df,bias)
    fvg_up,fvg_down=fair_value_gap(df)
    pattern,pattern_dir=detect_chart_pattern(df)
    entry=price
    sl=entry-sl_point if bias=="BUY" else entry+sl_point
    r=abs(entry-sl)
    tp=[entry+2*r,entry+3*r,entry+4*r] if bias=="BUY" else [entry-2*r,entry-3*r,entry-4*r]
    sig_id=f"{symbol}-{bias}-{tf}-{round(entry,2)}"
    return {"bias":bias,"entry":entry,"sl":sl,"tp":tp,"sup":sup,"res":res,"liq_high":liq_high,
            "liq_low":liq_low,"ob":ob,"vol":df["v"].iloc[-1],"tf":tf,"df":df,
            "fvg_up":fvg_up,"fvg_down":fvg_down,"pattern":pattern,"sig_id":sig_id}

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
    if sig["pattern"]: plt.text(len(df)-5,df["c"].iloc[-1],sig["pattern"],fontsize=10)
    plt.title(f"{sig['bias']} | TF: {sig['tf']}")
    plt.legend()
    buf=BytesIO()
    plt.savefig(buf,format="png")
    buf.seek(0)
    plt.close()
    return buf

# ===== SEND SIGNAL =====
async def send_signal(sig,symbol):
    if sig["sig_id"] in sent_signals: return
    sent_signals.add(sig["sig_id"])
    buf=plot_chart(sig["df"],sig)
    emoji="🟢 BUY" if sig["bias"]=="BUY" else "🔴 SELL"
    timestamp=datetime.now(INDIA_TZ).strftime("%Y-%m-%d %H:%M:%S IST")
    msg=f"""
{emoji} SIGNAL ({sig['tf']}) ⏰ {timestamp}

Crypto: {symbol}
💰 Entry: {sig['entry']:.2f}
🛑 Stop Loss: {sig['sl']:.2f}

🎯 Targets:
1:2 → {sig['tp'][0]:.2f}
1:3 → {sig['tp'][1]:.2f}
1:4 → {sig['tp'][2]:.2f}

📊 Levels:
Support → {sig['sup']:.2f}
Resistance → {sig['res']:.2f}
Liquidity High → {sig['liq_high']:.2f}
Liquidity Low → {sig['liq_low']:.2f}
Order Block → {sig['ob']:.2f}
📈 Volume → {sig['vol']:.2f}
"""
    for user in ALLOWED_USERS:
        await bot.send_photo(chat_id=user,photo=buf,caption=msg)
    print(f"Signal sent: {sig['sig_id']}")

# ===== TP/SL CHECK =====
async def check_tp_sl(sig, df):
    sig_id = sig["sig_id"]
    if sig_id in completed_trades: return
    price = df["c"].iloc[-1]
    if sig["bias"]=="BUY":
        if price>=sig["tp"][0]:
            for user in ALLOWED_USERS:
                await bot.send_message(chat_id=user,text=f"🎉 {sig['bias']} TP1 hit for {sig_id}! Congratulations ✅")
            completed_trades.add(sig_id)
        elif price<=sig["sl"]:
            for user in ALLOWED_USERS:
                await bot.send_message(chat_id=user,text=f"⚠️ {sig['bias']} SL hit for {sig_id}. Loss recorded ❌")
            completed_trades.add(sig_id)
    else:
        if price<=sig["tp"][0]:
            for user in ALLOWED_USERS:
                await bot.send_message(chat_id=user,text=f"🎉 {sig['bias']} TP1 hit for {sig_id}! Congratulations ✅")
            completed_trades.add(sig_id)
        elif price>=sig["sl"]:
            for user in ALLOWED_USERS:
                await bot.send_message(chat_id=user,text=f"⚠️ {sig['bias']} SL hit for {sig_id}. Loss recorded ❌")
            completed_trades.add(sig_id)

# ===== HELLO MESSAGE =====
async def send_hello_message():
    for user in ALLOWED_USERS:
        await bot.send_message(chat_id=user,text="Hello Pro Traders 👋 Bot is live & scanning BTC/ETH markets...")
    print("Hello Pro Traders message sent ✔")

# ===== MAIN LOOP =====
async def main_loop():
    await send_hello_message()
    while True:
        for sym in crypto:
            for tf in timeframes:
                sig=generate_signal(sym,tf)
                if sig:
                    await send_signal(sig,sym)
                    df=get_binance_data(sym,tf)
                    await check_tp_sl(sig,df)
        await asyncio.sleep(5)

# ===== RUN BOT =====
async def main():
    app=Application.builder().token(TG_TOKEN).build()
    app.add_handler(CommandHandler("adduser",adduser))
    asyncio.create_task(main_loop())
    await app.run_polling()

if __name__=="__main__":
    asyncio.run(main())