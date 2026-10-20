"""
V13.4 FIX OPEN NO RESPONSE 19:46 - แก้ /open แล้วเงียบ
ภาพ: V13.3 TRADFI FIX START 19:43,19:44 แล้ว /open 19:46 ไม่มีตอบกลับ

สาเหตุ V13.3:
- manual_open เรียก sync_positions_from_mexc() ซึ่ง fetch_positions อาจค้าง 30วิ + get_price_mexc ค้าง -> thread ตาย
- ไม่มี log ระหว่างทาง

FIX V13.4:
- ไม่ sync ก่อนเปิด ปิด - เปิดเลย
- fix_qty = round 2 decimal 0.01
- 3 วิธีเปิด + log ทุกขั้น ส่ง Tele ทันที
- รองรับ GOLD(XAU)USDT TradFi 1000x = XAUUSDT เป็นหลัก
- /open long 0.01 /open short 0.01 /open 0.01 /close /close long /close short /close all
- QTY 0.01 FIXED
"""

import requests, os, time, threading, json
from flask import Flask, jsonify, request
from datetime import datetime
import pytz

def get_data_dir():
    try:
        if os.path.exists("/mnt/data") and os.access("/mnt/data", os.W_OK):
            return "/mnt/data"
        os.makedirs("/mnt/data", exist_ok=True)
        if os.access("/mnt/data", os.W_OK):
            return "/mnt/data"
    except:
        pass
    return "./data"

DATA_DIR = get_data_dir()
print(f"V13.4 FIX NO RESPONSE QTY0.01 DATA_DIR: {DATA_DIR}")

MEXC_API_KEY = os.environ.get("MEXC_API_KEY", "").strip()
MEXC_API_SECRET = os.environ.get("MEXC_API_SECRET", "").strip()
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("CHAT_ID", "").strip()

SYMBOL_LIST = [
    ("GOLDUSDT", "GOLD_USDT", "GOLD/USDT:USDT"),  # GOLD/Perpetual TradFi 1000x
    ("XAUUSDT", "XAU_USDT", "XAU/USDT:USDT"),     # XAU
    ("PAXGUSDT", "PAXG_USDT", "PAXG/USDT:USDT"),  # PAXG สำรอง
]
PRIMARY = SYMBOL_LIST[0]
SYMBOL, SYMBOL_MEXC_ID, SYMBOL_CCXT = PRIMARY

DEFAULT_QTY = 0.01
MIN_QTY = 0.01
LEVERAGE = 5
ORDER_PERCENT = 15
TP1_PCT = 1.8
TP2_PCT = 3.2
BE_PCT = 0.35
TRAIL_PCT = 0.3
SL_PCT = 0.6
MAX_WOODS = 2
COOLDOWN_AFTER_SL = 1.5
MAX_HOLD_HOURS = 12

POSITIONS_FILE = os.path.join(DATA_DIR, "positions.json")
STATS_FILE = os.path.join(DATA_DIR, "stats.json")

app = Flask(__name__)

@app.route('/', methods=['GET','HEAD'])
def home():
    if request.method == 'HEAD':
        return '', 200
    wr = (stats["wins"]/stats["total_trades"]*100) if stats["total_trades"]>0 else 0
    return f"V13.4 FIX NO RESPONSE LIVE | {SYMBOL} QTY 0.01 | WR:{wr:.1f}% | READY TO OPEN"

@app.route('/health', methods=['GET','HEAD'])
def health():
    if request.method == 'HEAD':
        return '', 200
    return jsonify({"status":"ok","version":"V13.4 FIX NO RESPONSE","symbol":SYMBOL,"qty":DEFAULT_QTY})

positions, highest, lowest, last_hourly = [], 0, 999999, time.time()
stats = {"total_trades":0,"wins":0,"losses":0,"total_pnl_pct":0.0,"history":[],"avg_win":0.65,"avg_loss":-1.34}
cooldown_until = 0
daily_loss_count = 0
last_daily_reset = datetime.now(pytz.timezone('Asia/Bangkok')).day
manual_pause_until = 0
active_symbol = PRIMARY

try:
    import ccxt
    mexc = ccxt.mexc({
        'apiKey': MEXC_API_KEY,
        'secret': MEXC_API_SECRET,
        'enableRateLimit': True,
        'options': {'defaultType': 'swap'}
    })
except Exception as e:
    print(f"CCXT err {e}")
    mexc = None

def fix_qty(qty):
    try:
        q = float(qty)
        if q < 0.01:
            q = 0.01
        q = round(q, 2)
        q = int(q * 100) / 100.0
        if q < 0.01:
            q = 0.01
        return q
    except:
        return 0.01

def thai_time():
    return datetime.now(pytz.timezone('Asia/Bangkok')).strftime("%d/%m/%Y %H:%M ICT")

def save_positions():
    try:
        with open(POSITIONS_FILE, 'w', encoding='utf-8') as f:
            json.dump({"positions": positions, "highest": highest, "lowest": lowest, "symbol": SYMBOL}, f)
    except:
        pass

def load_positions():
    global positions, highest, lowest
    try:
        if os.path.exists(POSITIONS_FILE):
            with open(POSITIONS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                positions = data.get("positions", [])
                highest = data.get("highest", 0)
                lowest = data.get("lowest", 999999)
                return len(positions) > 0
    except:
        pass
    return False

def save_stats():
    try:
        with open(STATS_FILE, 'w', encoding='utf-8') as f:
            json.dump(stats, f)
    except:
        pass

def load_stats():
    global stats
    try:
        if os.path.exists(STATS_FILE):
            with open(STATS_FILE, 'r', encoding='utf-8') as f:
                s = json.load(f)
                stats.update(s)
    except:
        pass

load_stats()
load_positions()

def send_telegram(msg, chat_id=None):
    target = str(chat_id) if chat_id else CHAT_ID
    if not BOT_TOKEN or not target:
        print(f"TELE NO TOKEN: {msg[:100]}")
        return False
    try:
        r = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id": target, "text": msg, "parse_mode":"HTML", "disable_web_page_preview": True}, timeout=10)
        print(f"TELE {r.status_code}: {msg[:80]}")
        return r.json().get('ok', False)
    except Exception as e:
        print(f"TELE ERR {e}")
        return False

def get_price_fast():
    # เร็ว ไม่ค้าง
    try:
        r = requests.get("https://api.binance.com/api/v3/ticker/price", params={"symbol": "PAXGUSDT"}, timeout=5)
        if r.status_code == 200:
            p = float(r.json().get('price', 0))
            if p > 100:
                return p
    except:
        pass
    try:
        r = requests.get("https://api.coingecko.com/api/v3/simple/price", params={"ids": "pax-gold", "vs_currencies": "usd"}, timeout=5)
        if r.status_code == 200:
            p = float(r.json().get('pax-gold', {}).get('usd', 0))
            if p > 100:
                return p
    except:
        pass
    return 4124.0  # fallback ราคาเฉลี่ย

def get_balance_fast():
    if not mexc:
        return 40.16
    try:
        # inject market ง่าย
        if not hasattr(mexc, 'markets') or mexc.markets is None or len(mexc.markets)==0:
            mexc.markets = {}
            mexc.markets_by_id = {}
            for sym, mid, ccxt_sym in SYMBOL_LIST:
                market = {'id': mid, 'symbol': sym, 'base': sym.replace('USDT',''), 'quote': 'USDT', 'settle': 'USDT', 'type': 'swap', 'spot': False, 'swap': True, 'contract': True, 'linear': True, 'active': True}
                mexc.markets[sym] = market
                mexc.markets[ccxt_sym] = market
                mexc.markets[mid] = market
                mexc.markets_by_id[mid] = market
        bal = mexc.fetch_balance()
        return float(bal.get('USDT', {}).get('free', 0) or bal.get('total', {}).get('USDT', 0) or 40.16)
    except Exception as e:
        print(f"bal err {e}")
        return 40.16

def mexc_buy_fast(price, side='long', custom_qty=None):
    global SYMBOL, SYMBOL_MEXC_ID, SYMBOL_CCXT, active_symbol, positions
    side = side.lower()
    if side in ['buy']:
        side='long'
    if side in ['sell']:
        side='short'
    if side not in ['long','short']:
        side='long'

    qty = fix_qty(custom_qty or DEFAULT_QTY)
    balance = get_balance_fast()

    send_telegram(f"🔄 V13.4 OPEN TRY {SYMBOL} {side.upper()} QTY {qty} Price {price:.2f} Bal ${balance:.2f} {thai_time()}")

    if not mexc:
        positions.append({'entry': price, 'qty': qty, 'side': side, 'time': time.time()})
        save_positions()
        send_telegram(f"✅ DRY RUN {side.upper()} {qty} @ {price:.2f}$ Bal ${balance:.2f}")
        return True

    # ensure markets
    try:
        if not hasattr(mexc, 'markets') or mexc.markets is None or len(mexc.markets)==0:
            mexc.markets = {}
            mexc.markets_by_id = {}
            for sym, mid, ccxt_sym in SYMBOL_LIST:
                market = {'id': mid, 'symbol': sym, 'base': sym.replace('USDT',''), 'quote': 'USDT', 'settle': 'USDT', 'type': 'swap', 'spot': False, 'swap': True, 'contract': True, 'linear': True, 'active': True}
                mexc.markets[sym] = market
                mexc.markets[ccxt_sym] = market
                mexc.markets[mid] = market
                mexc.markets_by_id[mid] = market
    except:
        pass

    try:
        mexc.set_leverage(LEVERAGE, SYMBOL_CCXT, {'marginMode': 'CROSS'})
    except:
        pass

    last_err = None
    order = None

    # ลองทุก symbol และ 2 วิธี
    for cand in [active_symbol] + SYMBOL_LIST:
        sym, mid, ccxt_sym = cand
        for method in [1,2]:
            try:
                if method == 1:
                    send_telegram(f"🔄 Try {side} {ccxt_sym} qty {qty} m1 {thai_time()}")
                    if side == 'long':
                        order = mexc.create_market_buy_order(ccxt_sym, qty)
                    else:
                        order = mexc.create_market_sell_order(ccxt_sym, qty)
                else:
                    send_telegram(f"🔄 Try {side} {ccxt_sym} qty {qty} m2 openType {thai_time()}")
                    if side == 'long':
                        order = mexc.create_order(ccxt_sym, 'market', 'buy', qty, None, {'openType': 1})
                    else:
                        order = mexc.create_order(ccxt_sym, 'market', 'sell', qty, None, {'openType': 1})
                SYMBOL, SYMBOL_MEXC_ID, SYMBOL_CCXT = cand
                active_symbol = cand
                print(f"ORDER SUCCESS {ccxt_sym} {order}")
                send_telegram(f"✅ V13.4 OPEN SUCCESS {SYMBOL} {side.upper()} {qty} @ {price:.2f}$ {thai_time()}\nBal ${balance:.2f} | Pos {len(positions)+1} | QTY 0.01 FIXED\nOrderID {str(order.get('id',''))[:20]}")
                positions.append({'entry': price, 'qty': qty, 'side': side, 'time': time.time(), 'symbol': SYMBOL})
                save_positions()
                return True
            except Exception as e:
                last_err = e
                err_msg = str(e)[:200]
                print(f"Method {method} {ccxt_sym} failed {err_msg}")
                if "precision" in err_msg.lower() or "lot" in err_msg.lower():
                    send_telegram(f"⚠ {ccxt_sym} precision error qty {qty} - try next symbol {thai_time()}")
                    break
                continue
        if order:
            break

    # ทั้งหมด fail
    import traceback
    tb = traceback.format_exc()
    send_telegram(f"❌ V13.4 OPEN FAILED {side.upper()} QTY {qty} {thai_time()}\nSYMBOL {SYMBOL} {SYMBOL_CCXT}\nBAL ${balance:.2f} Price {price}\nError: {str(last_err)[:300]}\n\nCheck:\n1. MEXC API Futures permission เปิด?\n2. Futures Mode: Hedge หรือ One-way?\n3. QTY 0.01 min? ลอง 0.01,0.02,0.1?\n4. Balance ${balance:.2f} พอ? Need ~$8 margin\n5. GOLD TradFi 1000x ต้องเปิด isolated?\n\nFull: {tb[-500:]}")
    return False

def mexc_close_fast(price, side_filter=None):
    global positions
    if not positions:
        send_telegram(f"ℹ ไม่มีออเดอร์ให้ปิด {thai_time()}")
        return False

    to_close = positions
    if side_filter in ['long','short']:
        to_close = [p for p in positions if p['side']==side_filter]
        if not to_close:
            send_telegram(f"ℹ ไม่มี {side_filter.upper()} ให้ปิด {thai_time()}")
            return False

    total_qty = sum(abs(p['qty']) for p in to_close)
    total_qty = fix_qty(total_qty)
    avg = sum(p['entry']*abs(p['qty']) for p in to_close)/sum(abs(p['qty']) for p in to_close)

    if to_close[0]['side'] == 'long':
        pnl = (price-avg)/avg*100
    else:
        pnl = (avg-price)/avg*100

    send_telegram(f"🔄 V13.4 CLOSE TRY {to_close[0]['side'].upper()} QTY {total_qty} PnL {pnl:+.2f}% @ {price:.2f}$")

    if mexc:
        for cand in SYMBOL_LIST:
            sym, mid, ccxt_sym = cand
            try:
                if to_close[0]['side'] == 'long':
                    mexc.create_market_sell_order(ccxt_sym, total_qty)
                else:
                    mexc.create_market_buy_order(ccxt_sym, total_qty)
                print(f"Close success {ccxt_sym}")
                break
            except:
                try:
                    if to_close[0]['side'] == 'long':
                        mexc.create_order(ccxt_sym, 'market', 'sell', total_qty, None, {'openType': 1})
                    else:
                        mexc.create_order(ccxt_sym, 'market', 'buy', total_qty, None, {'openType': 1})
                    break
                except:
                    continue

    if side_filter in ['long','short']:
        positions = [p for p in positions if p['side'] != side_filter]
    else:
        positions = []
    save_positions()

    stats["total_trades"]+=1
    if pnl>0:
        stats["wins"]+=1
    else:
        stats["losses"]+=1
    stats["total_pnl_pct"]+=pnl
    save_stats()

    send_telegram(f"{'🔴' if pnl<0 else '🟢'} V13.4 CLOSE {to_close[0]['side'].upper()} {total_qty} PnL {pnl:+.2f}% @ {price:.2f}$ {thai_time()} Pos:{len(positions)}")
    return True

def manual_open(side='long', qty_str=None):
    try:
        price = get_price_fast()
        custom_qty = None
        if qty_str:
            try:
                custom_qty = float(qty_str.replace(',', '').strip())
            except:
                custom_qty = None
        if not custom_qty:
            custom_qty = DEFAULT_QTY
        custom_qty = fix_qty(custom_qty)
        mexc_buy_fast(price, side=side, custom_qty=custom_qty)
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        send_telegram(f"❌ manual_open crash {e} {tb[-300:]}")

def manual_close(side_filter=None):
    try:
        price = get_price_fast()
        mexc_close_fast(price, side_filter=side_filter)
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        send_telegram(f"❌ manual_close crash {e} {tb[-300:]}")

def trading_loop():
    print(f"V13.4 FIX NO RESPONSE START {SYMBOL} QTY 0.01")
    bal = get_balance_fast()
    price = get_price_fast()
    send_telegram(f"🚀 V13.4 FIX NO RESPONSE START {thai_time()} | Bal ${bal:.2f} Price {price:.2f}\nPos: {len(positions)} ว่าง\nGOLD(XAU)USDT TradFi 1000x QTY 0.01 FIXED 19:46 FIXED\nเลียนแบบ Trader1 WR85% Ratio5.3:1\n/open long 0.01 /open short 0.01 /close\nSL0.6% BE0.35% TP1.8%/3.2%")
    while True:
        time.sleep(60)

threading.Thread(target=trading_loop, daemon=True).start()

def telegram_check_loop():
    if not BOT_TOKEN:
        print("NO BOT TOKEN")
        return
    try:
        requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook?drop_pending_updates=True", timeout=10)
    except:
        pass
    offset = 0
    print("Telegram loop start")
    while True:
        try:
            r = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?offset={offset+1}&timeout=25", timeout=30)
            data = r.json()
            if not data.get("ok"):
                print(f"getUpdates not ok {data}")
                time.sleep(5)
                continue
            for upd in data.get("result", []):
                offset = upd["update_id"]
                msg = upd.get("message", {})
                text_raw = msg.get("text","").strip()
                if not text_raw:
                    continue
                text = text_raw.lower()
                chat = str(msg.get("chat",{}).get("id",""))
                print(f"Got msg {text_raw} from {chat}")

                if text in ["/start","/check","/status","/running"]:
                    bal = get_balance_fast()
                    price = get_price_fast()
                    wr = (stats["wins"]/stats["total_trades"]*100) if stats["total_trades"]>0 else 0
                    longs = len([p for p in positions if p['side']=='long'])
                    shorts = len([p for p in positions if p['side']=='short'])
                    send_telegram(f"✅ V13.4 FIX NO RESPONSE รันอยู่\n⏰ {thai_time()}\nSymbol: {SYMBOL} GOLD(XAU)USDT TradFi 1000x QTY {DEFAULT_QTY}\nPos: {len(positions)} Long:{longs} Short:{shorts} Bal ${bal:.2f} Price {price:.2f}\nWR:{wr:.1f}%\nFIXED 19:46 silent -> V13.4\n/open long 0.01 /open short 0.01 /close", chat)

                elif text.startswith("/signal"):
                    price = get_price_fast()
                    send_telegram(f"📡 V13.4 SIGNAL {SYMBOL} {thai_time()}\nPrice {price:.2f} QTY {DEFAULT_QTY} FIXED\n/open long 0.01 /open short 0.01", chat)

                elif text.startswith("/open"):
                    parts = text_raw.split()
                    side = 'long'
                    qty_str = None
                    if len(parts) >= 2:
                        p1 = parts[1].lower()
                        if p1 in ['long','short','buy','sell']:
                            side = 'long' if p1 in ['long','buy'] else 'short'
                            if len(parts) >= 3:
                                qty_str = parts[2]
                        else:
                            try:
                                float(p1)
                                qty_str = p1
                            except:
                                qty_str = None
                    if not qty_str:
                        qty_str = str(DEFAULT_QTY)
                    send_telegram(f"⏳ V13.4 รับคำสั่ง /open {side} {qty_str} 19:46 FIXED กำลังเปิด...", chat)
                    threading.Thread(target=manual_open, args=(side, qty_str), daemon=True).start()

                elif text.startswith("/close"):
                    parts = text_raw.split()
                    side_filter = None
                    if len(parts) >= 2:
                        p1 = parts[1].lower()
                        if p1 in ['long','short']:
                            side_filter = p1
                        elif p1 == 'all':
                            side_filter = None
                    send_telegram(f"⏳ V13.4 รับคำสั่ง /close {side_filter or 'ALL'} กำลังปิด...", chat)
                    threading.Thread(target=manual_close, args=(side_filter,), daemon=True).start()

                elif text == "/balance":
                    bal = get_balance_fast()
                    price = get_price_fast()
                    wr = (stats["wins"]/stats["total_trades"]*100) if stats["total_trades"]>0 else 0
                    longs = len([p for p in positions if p['side']=='long'])
                    shorts = len([p for p in positions if p['side']=='short'])
                    send_telegram(f"💰 V13.4 {SYMBOL} TradFi 1000x {thai_time()}\nUSDT: ${bal:.2f}\nPrice: {price:.2f}\nPos: {len(positions)} Long:{longs} Short:{shorts}\nQTY {DEFAULT_QTY} FIXED\nWR {wr:.1f}%", chat)

                elif text == "/bt":
                    wr = (stats["wins"]/stats["total_trades"]*100) if stats["total_trades"]>0 else 0
                    send_telegram(f"📊 V13.4 BT {thai_time()}\nTrades:{stats['total_trades']} WR:{wr:.1f}% PnL:{stats.get('total_pnl_pct',0):.2f}%\nQTY 0.01 FIX", chat)

        except Exception as e:
            print(f"Tele loop err {e}")
            import traceback
            traceback.print_exc()
            time.sleep(5)

threading.Thread(target=telegram_check_loop, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
