import requests
import pandas as pd
from telegram import Bot
from telegram.ext import Application, CommandHandler
import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime
import pytz
import json
import time
import numpy as np
import sqlite3
import logging
import threading
import websocket
import re
import base58

# ================== CONFIG ==================
TELEGRAM_TOKEN = '8304411899:AAF9CEYSMdD4vRfaRV63UYl-FCGcwYaorLw'
CHAT_ID = '-1002745894919'

# API Keys (GANTI KALAU PUNYA, KALAU TIDAK → OTOMATIS PAKAI FALLBACK)
MORALIS_API_KEY = ''
LUNARCRUSH_API_KEY = ''
CRYPTOPANIC_API_KEY = ''
ETHERSCAN_API_KEY = ''
BSCSCAN_API_KEY = ''
SOLSCAN_API_KEY = ''
SUI_API_KEY = ''

SCAN_INTERVAL = 5
WIB = pytz.timezone('Asia/Jakarta')
# ===========================================

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
bot = Bot(token=TELEGRAM_TOKEN)
websocket_data = {}

# DB
conn = sqlite3.connect('signals.db', check_same_thread=False)
cursor = conn.cursor()
cursor.execute('''CREATE TABLE IF NOT EXISTS signals
                 (id INTEGER PRIMARY KEY, token TEXT, symbol TEXT, chain TEXT, action TEXT, 
                  entry REAL, target REAL, sl REAL, score REAL, rsi REAL, timestamp TEXT, status TEXT, profit REAL)''')
conn.commit()

# CHAINS
CHAINS = ['ethereum', 'solana', 'bsc']

# WEBSOCKET
def on_message(ws, msg):
    try:
        data = json.loads(msg)
        addr = data.get('tokenAddress')
        chain = data.get('chainId')
        if addr and chain in CHAINS:
            websocket_data[addr] = data
    except: pass

def start_ws():
    def run():
        try:
            ws = websocket.WebSocketApp("wss://api.dexscreener.com/ws", on_message=on_message)
            ws.run_forever(ping_interval=30)
        except Exception as e:
            logging.error(f"WebSocket error: {e}")
    threading.Thread(target=run, daemon=True).start()

# GET TRENDING
def get_trending(chain):
    url = f"https://api.dexscreener.com/latest/dex/tokens/trending/{chain}"
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            return resp.json().get('pairs', [])[:20]
    except Exception as e:
        logging.error(f"Trending {chain} failed: {e}")
    return []

# FUNDAMENTAL
def get_fund(symbol):
    url = f"https://api.coingecko.com/api/v3/coins/{symbol.lower()}?localization=false"
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            d = resp.json()
            return {
                'price': d['market_data']['current_price'].get('usd', 0),
                'mc': d['market_data']['market_cap'].get('usd', 0),
                'change_4h': d['market_data'].get('price_change_percentage_4h_in_currency', {}).get('usd', 0),
            }
    except: pass
    return None

# CHART
def analyze_chart(prices):
    if len(prices) < 14: return 50, 50
    df = pd.DataFrame({'p': prices})
    delta = df['p'].diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = -delta.where(delta < 0, 0).rolling(14).mean()
    rs = gain / loss.replace(0, 0.0001)
    rsi = 100 - (100 / (1 + rs)).iloc[-1]
    breakout = 1 if prices[-1] > max(prices[-5:]) * 1.02 else 0
    return (breakout * 50 + (1 if 40 < rsi < 70 else 0) * 50), rsi

# SCAN TOKEN
async def scan_token(pair, chain):
    addr = pair['baseToken']['address']
    symbol = pair['baseToken']['symbol']
    name = pair['baseToken']['name']
    price = float(pair.get('priceUsd', 0))
    if not price: return

    fund = get_fund(symbol) or {}
    mc = fund.get('mc', 0)
    change = fund.get('change_4h', 0)
    vol24 = pair.get('volume', {}).get('h24', 0)
    prices = [float(p) for p in pair.get('priceChange', {}).get('values', [])[-24:] if p]

    chart_score, rsi = analyze_chart(prices)
    score = 0
    if vol24 > 1_000_000: score += 20
    if change > 10: score += 25
    if 1_000_000 < mc < 50_000_000: score += 20
    if websocket_data.get(addr): score += 15
    if chart_score > 50: score += 20
    if score < 70: return

    entry = price
    tp = price * 1.5
    sl = price * 0.9

    msg = f"*AI SIGNAL v3.1*\n" \
          f"*{name} ({symbol})*\n" \
          f"Chain: {chain.upper()}\n" \
          f"Score: *{score:.0f}*%\n" \
          f"Price: `${price:.6f}`\n" \
          f"Volume 24h: `${vol24:,.0f}`\n" \
          f"Entry: `${entry:.6f}`\n" \
          f"TP: `${tp:.6f}` | SL: `${sl:.6f}`\n" \
          f"Link: https://dexscreener.com/{chain}/{addr}\n" \
          f"Time: {datetime.now(WIB).strftime('%H:%M WIB')}"

    try:
        await bot.send_message(CHAT_ID, msg, parse_mode='Markdown')
        cursor.execute("INSERT INTO signals (token, symbol, chain, action, entry, target, sl, score, rsi, timestamp, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Pending')",
                       (addr, symbol, chain, 'BUY', entry, tp, sl, score, rsi, datetime.now(WIB).isoformat()))
        conn.commit()
    except Exception as e:
        logging.error(f"Gagal kirim sinyal: {e}")

# COMMANDS
async def feedback(update, context):
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Usage: /feedback <symbol> <hit/miss> [profit%]")
        return
    symbol, status = args[0], args[1]
    profit = float(args[2]) if len(args) > 2 else 0
    cursor.execute("UPDATE signals SET status=?, profit=? WHERE symbol=? AND status='Pending'", (status.upper(), profit, symbol))
    conn.commit()
    await update.message.reply_text(f"Feedback {symbol}: {status} {profit}%")

async def analyze(update, context):
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Usage: /analyze <address> <chain>")
        return
    addr, chain = args[0], args[1]
    for pair in get_trending(chain):
        if pair['baseToken']['address'].lower() == addr.lower():
            await scan_token(pair, chain)
            await update.message.reply_text("Analyzing...")
            return
    await update.message.reply_text("Token not found in trending")

# SCAN CHAIN
async def scan_chain(chain):
    pairs = get_trending(chain)
    for pair in pairs:
        await scan_token(pair, chain)
    logging.info(f"Scan {chain}: {len(pairs)} pairs")

# MAIN — FIXED EVENT LOOP
async def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("feedback", feedback))
    app.add_handler(CommandHandler("analyze", analyze))

    start_ws()
    scheduler = AsyncIOScheduler()
    for chain in CHAINS:
        scheduler.add_job(scan_chain, 'interval', minutes=SCAN_INTERVAL, args=[chain])
    scheduler.start()

    # Kirim pesan mulai
    try:
        await bot.send_message(CHAT_ID, "*BOT AI v3.1 AKTIF!* Real-time + DB + Commands!\nScan setiap 5 menit.", parse_mode='Markdown')
    except Exception as e:
        logging.error(f"Gagal kirim pesan: {e}")

    # JALANKAN POLLING DI THREAD TERPISAH (FIX EVENT LOOP)
    def run_polling():
        try:
            app.run_polling(drop_pending_updates=True)
        except Exception as e:
            logging.error(f"Polling error: {e}")

    polling_thread = threading.Thread(target=run_polling, daemon=True)
    polling_thread.start()

    # Jaga bot tetap hidup
    while True:
        await asyncio.sleep(3600)

# JALANKAN DENGAN EVENT LOOP BARU
if __name__ == '__main__':
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        logging.info("Bot dihentikan oleh user.")
    finally:
        loop.close()