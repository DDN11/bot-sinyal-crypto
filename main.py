import requests
from telegram import Bot
import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime
import pytz
import logging
import os

# ================== CONFIG ==================
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN', '8304411899:AAF9CEYSMdD4vRfaRV63UYl-FCGcwYaorLw')
CHAT_ID = os.getenv('CHAT_ID', '-1002745894919')

CMC_API_KEY = 'YOUR_CMC_KEY'  # GANTI DENGAN KEY CMC GRATIS

SCAN_INTERVAL = 1  # Scan tiap 3 menit
MIN_SCORE = 75     # THRESHOLD 75% — PREMIUM GEM!
WIB = pytz.timezone('Asia/Jakarta')
# ===========================================

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
bot = Bot(token=TELEGRAM_TOKEN)

# 1. CMC (PAKAI KEY)
def get_cmc():
    url = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest"
    params = {'start': '1', 'limit': '50', 'convert': 'USD'}
    headers = {'X-CMC_PRO_API_KEY': CMC_API_KEY}
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        if resp.status_code == 200:
            return [('CMC', coin) for coin in resp.json()['data']]
    except Exception as e:
        logging.error(f"CMC error: {e}")
    return []

# 2. CoinGecko (NO KEY)
def get_coingecko():
    url = "https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=volume_desc&per_page=50&page=1"
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            return [('CG', coin) for coin in resp.json()]
    except Exception as e:
        logging.error(f"CG error: {e}")
    return []

# 3. DexScreener (NO KEY)
def get_dexscreener():
    chains = ['solana', 'ethereum', 'bsc']
    results = []
    for chain in chains:
        url = f"https://api.dexscreener.com/latest/dex/tokens/trending/{chain}"
        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                for pair in resp.json().get('pairs', [])[:10]:
                    results.append(('DEX', pair, chain))
        except Exception as e:
            logging.error(f"DEX {chain} error: {e}")
    return results

# 4. Binance Public (NO KEY)
def get_binance():
    url = "https://api.binance.com/api/v3/ticker/24hr"
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            gainers = [c for c in resp.json() if c['symbol'].endswith('USDT') and float(c['priceChangePercent']) > 5]
            return [('BIN', c) for c in gainers[:10]]
    except Exception as e:
        logging.error(f"BIN error: {e}")
    return []

# 5. CryptoSlate (NO KEY) — TAMBAHAN BARU!
def get_cryptoslate():
    url = "https://api.cryptoslate.com/v1/data/trending"
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            return [('CS', coin) for coin in resp.json().get('data', [])[:10]]
    except Exception as e:
        logging.error(f"CS error: {e}")
    return []

# ANALISIS UNIFIED (TAMBAH CS)
def analyze_unified(source, data, chain=None):
    try:
        if source == 'CMC':
            symbol = data['symbol']
            price = data['quote']['USD']['price']
            vol24 = data['quote']['USD']['volume_24h']
            change1h = data['quote']['USD']['percent_change_1h']
            mc = data['quote']['USD']['market_cap']
            change24h = data['quote']['USD']['percent_change_24h']
            link = f"https://coinmarketcap.com/currencies/{data['slug']}"
        
        elif source == 'CG':
            symbol = data['symbol'].upper()
            price = data['current_price']
            vol24 = data['total_volume']
            change1h = data['price_change_percentage_1h_in_currency'] or 0
            mc = data['market_cap'] or 0
            change24h = data['price_change_percentage_24h'] or 0
            link = f"https://www.coingecko.com/en/coins/{data['id']}"
        
        elif source == 'DEX':
            symbol = data['baseToken']['symbol']
            price = float(data.get('priceUsd', 0))
            vol24 = data.get('volume', {}).get('h24', 0)
            change1h = data.get('priceChange', {}).get('h1', 0)
            mc = data.get('fdv', 0)
            change24h = data.get('priceChange', {}).get('h24', 0)
            link = f"https://dexscreener.com/{chain}/{data['baseToken']['address']}"
        
        elif source == 'BIN':
            symbol = data['symbol'].replace('USDT', '')
            price = float(data['lastPrice'])
            vol24 = float(data['quoteVolume'])
            change1h = float(data['priceChangePercent']) / 24
            mc = 0
            change24h = float(data['priceChangePercent'])
            link = f"https://www.binance.com/en/trade/{symbol}_USDT"
        
        elif source == 'CS':  # TAMBAHAN CRYPTOSLATE
            symbol = data['symbol']
            price = data['price']
            vol24 = data['volume_24h']
            change1h = data['percent_change_1h']
            mc = data['market_cap']
            change24h = data['percent_change_24h']
            link = f"https://cryptoslate.com/coins/{data['slug']}"
        
        score = 0
        if vol24 > 100_000_000: score += 30
        if change1h > 5: score += 25
        if 50_000 < mc < 5_000_000: score += 20
        if change24h > 10: score += 15
        
        if score >= MIN_SCORE:
            return {
                'source': source,
                'symbol': symbol,
                'price': price,
                'volume': vol24,
                'change1h': change1h,
                'change24h': change24h,
                'mc': mc,
                'score': score,
                'link': link
            }
    except: pass
    return None

# KIRIM SINYAL
async def send_signal(signal):
    msg = f"*{signal['symbol']}*\n" \
          f"*Action : BUY NOW!!! {signal['source']}*\n" \
          f"Score: *{signal['score']}/100*\n" \
          f"Price: `${signal['price']:.6f}`\n" \
          f"Volume 24h: `${signal['volume']:,.0f}`\n" \
          f"Change 1h: *+{signal['change1h']:.2f}%*\n" \
          f"Change 24h: *+{signal['change24h']:.2f}%*\n" \
          f"MC/FDV: `${signal['mc']:,.0f}`\n" \
          f"Entry: `${signal['price']:.6f}`\n" \
          f"TP1: `${signal['price']*1.5:.6f}` | TP2: `${signal['price']*2:.6f}`\n" \
          f"SL: `${signal['price']*0.85:.6f}`\n" \
          f"{signal['link']}\n" \
          f"{datetime.now(WIB).strftime('%H:%M WIB')}"
    try:
        await bot.send_message(CHAT_ID, msg, parse_mode='Markdown', disable_web_page_preview=True)
        logging.info(f"GEM {signal['source']} terkirim: {signal['symbol']}")
    except Exception as e:
        logging.error(f"Gagal kirim: {e}")

# SCAN SEMUA 5 API
async def scan_all():
    signals = []
    
    for src, coin in get_cmc():
        sig = analyze_unified(src, coin)
        if sig: signals.append(sig)
    
    for src, coin in get_coingecko():
        sig = analyze_unified(src, coin)
        if sig: signals.append(sig)
    
    for src, pair, chain in get_dexscreener():
        sig = analyze_unified(src, pair, chain)
        if sig: signals.append(sig)
    
    for src, coin in get_binance():
        sig = analyze_unified(src, coin)
        if sig: signals.append(sig)
    
    for src, coin in get_cryptoslate():  # TAMBAHAN BARU
        sig = analyze_unified(src, coin)
        if sig: signals.append(sig)
    
    for sig in signals:
        await send_signal(sig)
        await asyncio.sleep(2)
    
    logging.info(f"Scan 5 API selesai: {len(signals)} GEM ditemukan")

# MAIN
async def main():
    await bot.send_message(CHAT_ID, "*BOT AI v5.0 HYBRID 5 API (HANYA CMC PAKAI KEY) AKTIF!*\nCMC + CG + DEX + BIN + CRYPTOSLATE\nHanya GEM >80!\n#crypto #gem", parse_mode='Markdown')
    
    scheduler = AsyncIOScheduler()
    scheduler.add_job(scan_all, 'interval', minutes=SCAN_INTERVAL)
    scheduler.start()
    
    while True:
        await asyncio.sleep(3600)

if __name__ == '__main__':
    try:
        from keep_alive import keep_alive
        keep_alive()
    except: pass
    
    asyncio.run(main())
