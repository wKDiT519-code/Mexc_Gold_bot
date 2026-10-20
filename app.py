"""
V13.2 FIX PRECISION + QTY 0.01 + LONG/SHORT OPEN/CLOSE
แก้จาก V13.1 ที่ BUY ERROR 'precision' 20/10 19:12
สาเหตุ: MEXC GOLD/Perpetual GOLDUSDT Lot = 0.01 ไม่ใช่ 0.001
V13.1 ส่ง 0.001 -> precision error

FIX V13.2:
- QTY เริ่มต้น 0.01 (จาก 0.001) ตามที่ท่านสั่ง
- Precision: amount precision = 2 decimal, min 0.01
- mexc.amount_to_precision() + Manual round 2 decimal
- รองรับ /open long 0.01 /open short 0.01 /open 0.01 (default long)
- รองรับ /close long /close short /close all /close
- ปิดออเดอร์ทั้ง long และ short
- GOLD Perpetual GOLD/USDT:USDT
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
print(f"V13.2 FIX PRECISION QTY0.01 DATA_DIR: {DATA_DIR}")

MEXC_API_KEY = os.environ.get("MEXC_API_KEY", "").strip()
MEXC_API_SECRET = os.environ.get("MEXC_API_SECRET", "").strip()
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("CHAT_ID", "").strip()

SYMBOL_LIST = [
    ("GOLDUSDT", "GOLD_USDT", "GOLD/USDT:USDT"),
    ("PAXGUSDT", "PAXG_USDT", "PAXG/USDT:USDT"),
    ("XAUUSDT", "XAU_USDT", "XAU/USDT:USDT"),
]
PRIMARY = SYMBOL_LIST[0]
SYMBOL, SYMBOL_MEXC_ID, SYMBOL_CCXT = PRIMARY

# V13.2 FIX QTY
DEFAULT_QTY = 0.01  # ตามที่ท่านสั่ง qty=0.01
MIN_QTY = 0.01
QTY_PRECISION = 2
LEVERAGE = 5
ORDER_PERCENT = 15
TP1_PCT = 1.8
TP2_PCT = 3.2
BE_PCT = 0.35
TRAIL_PCT = 0.3
SL_PCT = 0.6
SL_ATR_MULT = 1.0
SL_MIN_PCT = 0.4
SL_MAX_PCT = 0.8
MAX_WOODS = 2
COOLDOWN_AFTER_SL = 1.5
MAX_HOLD_HOURS = 12
DAILY_MAX_LOSS = 2

POSITIONS_FILE = os.path.join(DATA_DIR, "positions.json")
STATS_FILE = os.path.join(DATA_DIR, "stats.json")

app = Flask(__name__)

@app.route('/', methods=['GET','HEAD'])
def home():
    if request.method == 'HEAD':
        return '', 200
    wr = (stats["wins"]/stats["total_trades"]*100) if stats["total_trades"]>0 else 0
    return f"V13.2 FIX PRECISION LIVE | {SYMBOL} QTY 0.01 | WR:{wr:.1f}% | LONG/SHORT"

@app.route('/health', methods=['GET','HEAD'])
def health():
    if request.method == 'HEAD':
        return '', 200
    return jsonify({"status":"ok","version":"V13.2 FIX PRECISION","symbol":SYMBOL,"qty":DEFAULT_QTY})

positions, highest, lowest, last_hourly = [], 0, 999999, time.time()
stats = {"total_trades":0,"wins":0,"losses":0,"total_pnl_pct":0.0,"history":[],"avg_win":0.65,"avg_loss":-1.34}
cooldown_until = 0
daily_loss_count = 0
last_daily_reset = datetime.now(pytz.timezone('Asia/Bangkok')).day
current_atr_sl = SL_PCT
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
    try:
        mexc.load_markets()
    except:
        pass
except Exception as e:
    print(f"CCXT err {e}")
    mexc = None

def ensure_all_gold_markets():
    if not mexc:
        return
    try:
        if not hasattr(mexc, 'markets') or mexc.markets is None:
            mexc.markets = {}
            mexc.markets_by_id = {}
        for sym, mid, ccxt_sym in SYMBOL_LIST:
            market = {
                'id': mid,
                'symbol': sym,
                'base': sym.replace('USDT',''),
                'quote': 'USDT',
                'settle': 'USDT',
                'type': 'swap',
                'spot': False,
                'swap': True,
                'contract': True,
                'linear': True,
                'active': True,
                'precision': {'amount': 0.01, 'price': 0.01},
                'limits': {'amount': {'min': 0.01}}
            }
            mexc.markets[sym] = market
            mexc.markets[ccxt_sym] = market
            mexc.markets[mid] = market
            mexc.markets_by_id[mid] = market
    except Exception as e:
        print(f"ensure err {e}")

def detect_active_symbol():
    global active_symbol, SYMBOL, SYMBOL_MEXC_ID, SYMBOL_CCXT
    if not mexc:
        return active_symbol
    ensure_all_gold_markets()
    try:
        all_pos = mexc.fetch_positions()
        for p in all_pos:
            info_str = str(p.get('info', {})) + str(p.get('symbol',''))
            for cand in SYMBOL_LIST:
                sym, mid, ccxt_sym = cand
                if mid in info_str or sym in info_str or 'GOLD' in info_str or 'PAXG' in info_str:
                    if float(p.get('contracts',0) or 0) != 0:
                        active_symbol = cand
                        SYMBOL, SYMBOL_MEXC_ID, SYMBOL_CCXT = cand
                        return cand
    except:
        pass
    return active_symbol

def fix_qty_precision(qty):
    """V13.2 FIX PRECISION - GOLDUSDT ต้อง 0.01"""
    try:
        qty = float(qty)
        if qty < MIN_QTY:
            qty = MIN_QTY
        # Round to 2 decimals
        qty = round(qty, QTY_PRECISION)
        # ถ้ามี ccxt ให้ใช้ amount_to_precision
        if mexc:
            try:
                qty = float(mexc.amount_to_precision(SYMBOL_CCXT, qty))
            except:
                try:
                    qty = float(mexc.amount_to_precision(SYMBOL, qty))
                except:
                    qty = round(qty, 2)
        return qty
    except:
        return DEFAULT_QTY

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

def sync_positions_from_mexc():
    global positions, highest, lowest
    if not mexc:
        return False
    ensure_all_gold_markets()
    detect_active_symbol()
    all_real = []
    for cand in SYMBOL_LIST:
        sym, mid, ccxt_sym = cand
        try:
            poss = []
            try:
                poss = mexc.fetch_positions([sym])
            except:
                try:
                    poss = mexc.fetch_positions([ccxt_sym])
                except:
                    continue
            for p in poss:
                try:
                    info = p.get('info', {})
                    contracts = float(p.get('contracts', 0) or 0)
                    if contracts == 0:
                        contracts = float(info.get('holdVol', 0) or 0)
                    entry_price = float(p.get('entryPrice') or info.get('openAvgPrice') or info.get('holdAvgPrice') or 0)
                    if abs(contracts) > 0 and entry_price > 100:
                        side_raw = p.get('side') or info.get('positionType', 1)
                        is_long = True
                        if isinstance(side_raw, str):
                            is_long = 'long' in side_raw.lower() or side_raw == '1' or 'buy' in side_raw.lower()
                        else:
                            try:
                                is_long = int(side_raw) == 1
                            except:
                                is_long = True
                        side = 'long' if is_long else 'short'
                        all_real.append({'entry': entry_price, 'qty': abs(contracts), 'side': side, 'time': time.time() - 3600, 'symbol': sym})
                except:
                    continue
        except:
            continue
    if all_real:
        positions = all_real
        longs = [p for p in positions if p['side']=='long']
        if longs:
            avg_entry = sum(p['entry']*p['qty'] for p in longs)/sum(p['qty'] for p in longs)
            highest = avg_entry
            lowest = avg_entry
        save_positions()
        print(f"RECOVERED {len(positions)} positions: {positions}")
        return True
    return False

load_stats()
load_positions()
sync_positions_from_mexc()

def send_telegram(msg, chat_id=None):
    target = str(chat_id) if chat_id else CHAT_ID
    if not BOT_TOKEN or not target:
        return False
    try:
        r = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id": target, "text": msg, "parse_mode":"HTML", "disable_web_page_preview": True}, timeout=15)
        return r.json().get('ok', False)
    except:
        return False

def get_price_mexc():
    try:
        r = requests.get("https://api.coingecko.com/api/v3/simple/price", params={"ids": "pax-gold", "vs_currencies": "usd"}, timeout=10)
        if r.status_code == 200:
            price = float(r.json().get('pax-gold', {}).get('usd', 0))
            if price > 100:
                return price
    except:
        pass
    for host in ["https://data-api.binance.vision", "https://api.binance.com"]:
        try:
            r = requests.get(f"{host}/api/v3/ticker/price", params={"symbol": "PAXGUSDT"}, timeout=10)
            if r.status_code == 200:
                price = float(r.json().get('price', 0))
                if price > 100:
                    return price
        except:
            pass
    return None

def get_klines_mexc(limit=200, timeframe='15m'):
    for host in ["https://data-api.binance.vision", "https://api.binance.com"]:
        try:
            r = requests.get(f"{host}/api/v3/klines", params={"symbol": "PAXGUSDT", "interval": timeframe, "limit": limit}, timeout=15)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list) and len(data) > 50:
                    ohlcv = []
                    for k in data:
                        ohlcv.append([int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])])
                    return ohlcv
        except:
            pass
    return None

def ema_series(prices, span):
    alpha = 2.0/(span+1)
    ema = []
    for i,p in enumerate(prices):
        if i==0:
            ema.append(p)
        else:
            ema.append(alpha*p + (1-alpha)*ema[-1])
    return ema

def sma_series(data, span):
    sma=[]
    for i in range(len(data)):
        if i < span-1:
            sma.append(sum(data[:i+1])/(i+1))
        else:
            sma.append(sum(data[i-span+1:i+1])/span)
    return sma

def calc_indicators(ohlcv):
    if not ohlcv or len(ohlcv) < 50:
        return None
    try:
        closes = [float(x[4]) for x in ohlcv]
        highs = [float(x[2]) for x in ohlcv]
        lows = [float(x[3]) for x in ohlcv]
        opens = [float(x[1]) for x in ohlcv]
        vols = [float(x[5]) for x in ohlcv]
        n = len(closes)
        ema12 = ema_series(closes, 12)
        ema26 = ema_series(closes, 26)
        ema20 = ema_series(closes, 20)
        ema200 = ema_series(closes, 200)
        vol_sma20 = sma_series(vols, 20)
        rsi = [50.0]*n
        gains = []
        losses = []
        for i in range(1,n):
            d = closes[i]-closes[i-1]
            gains.append(max(d,0))
            losses.append(max(-d,0))
        avg_gain = sum(gains[:14])/14 if len(gains)>=14 else 0.001
        avg_loss = sum(losses[:14])/14 if len(losses)>=14 else 0.001
        for i in range(14, n):
            if i>14 and i-1 < len(gains):
                avg_gain = (avg_gain*13 + gains[i-1])/14
                avg_loss = (avg_loss*13 + losses[i-1])/14
            rs = avg_gain/(avg_loss if avg_loss!=0 else 0.001)
            rsi[i] = 100 - (100/(1+rs))
        tr = [0.0]*n
        atr = [0.0]*n
        for i in range(n):
            if i==0:
                tr[i]=highs[i]-lows[i]
            else:
                tr[i]=max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
        atr[13]=sum(tr[:14])/14 if n>=14 else tr[0]
        for i in range(14,n):
            atr[i]=(atr[i-1]*13 + tr[i])/14
        plus_dm = [0.0]*n
        minus_dm = [0.0]*n
        for i in range(1,n):
            up_move = highs[i]-highs[i-1]
            down_move = lows[i-1]-lows[i]
            if up_move>down_move and up_move>0:
                plus_dm[i]=up_move
            if down_move>up_move and down_move>0:
                minus_dm[i]=down_move
        plus_dm14 = [0.0]*n
        minus_dm14 = [0.0]*n
        tr14 = [0.0]*n
        if n>=14:
            plus_dm14[13]=sum(plus_dm[:14])
            minus_dm14[13]=sum(minus_dm[:14])
            tr14[13]=sum(tr[:14])
        for i in range(14,n):
            plus_dm14[i]=plus_dm14[i-1] - plus_dm14[i-1]/14 + plus_dm[i]
            minus_dm14[i]=minus_dm14[i-1] - minus_dm14[i-1]/14 + minus_dm[i]
            tr14[i]=tr14[i-1] - tr14[i-1]/14 + tr[i]
        plus_di = [0.0]*n
        minus_di = [0.0]*n
        dx = [0.0]*n
        adx = [0.0]*n
        for i in range(n):
            if tr14[i]!=0:
                plus_di[i]=100*plus_dm14[i]/tr14[i]
                minus_di[i]=100*minus_dm14[i]/tr14[i]
                denom = plus_di[i]+minus_di[i]
                if denom!=0:
                    dx[i]=100*abs(plus_di[i]-minus_di[i])/denom
        if n>27:
            adx[26]=sum(dx[13:27])/14
            for i in range(27,n):
                adx[i]=(adx[i-1]*13 + dx[i])/14
        rows=[]
        for i in range(n):
            rows.append({
                'o': opens[i], 'h': highs[i], 'l': lows[i], 'c': closes[i], 'v': vols[i],
                'EMA12': ema12[i], 'EMA26': ema26[i], 'EMA20': ema20[i], 'EMA200': ema200[i],
                'RSI': rsi[i], 'ATR': atr[i], '+DI': plus_di[i], '-DI': minus_di[i], 'ADX': adx[i],
                'VOL_SMA20': vol_sma20[i]
            })
        return rows
    except Exception as e:
        print(f"calc err {e}")
        return None

def get_balance():
    if not mexc:
        return 40.16
    ensure_all_gold_markets()
    try:
        bal = mexc.fetch_balance()
        return float(bal.get('USDT', {}).get('free', 0) or bal.get('total', {}).get('USDT', 0) or 40.16)
    except:
        return 40.16

def format_balance_msg():
    try:
        balance = get_balance()
        wr = (stats["wins"]/stats["total_trades"]*100) if stats["total_trades"]>0 else 0
        upnl = 0
        if positions:
            longs = [p for p in positions if p['side']=='long']
            shorts = [p for p in positions if p['side']=='short']
            price = get_price_mexc() or (positions[0]['entry'] if positions else 0)
            if longs:
                total_qty = sum(abs(p['qty']) for p in longs)
                avg = sum(p['entry']*abs(p['qty']) for p in longs)/total_qty
                upnl = (price-avg)/avg*100
        ratio = abs(stats.get("avg_win",0.65)/stats.get("avg_loss",-1.34)) if stats.get("avg_loss",0)!=0 else 0.48
        return f"Bal ${balance:.2f} | PnL {stats['total_pnl_pct']:+.2f}% | WR {wr:.1f}% Ratio {ratio:.1f}:1", {"upnl":upnl, "balance":balance}, wr
    except:
        return "Balance 40.16$", {"upnl":0}, 0

def mexc_buy(price, side='long', factor=1.0, custom_qty=None, last_ind=None):
    global manual_pause_until, SYMBOL, SYMBOL_MEXC_ID, SYMBOL_CCXT, active_symbol
    side = side.lower()
    if side not in ['long','short','buy','sell']:
        side='long'
    if side in ['buy']:
        side='long'
    if side in ['sell']:
        side='short'

    if not mexc:
        qty = fix_qty_precision(custom_qty or DEFAULT_QTY)
        positions.append({'entry': price, 'qty': qty, 'side': side, 'time': time.time()})
        save_positions()
        bal_msg, _, wr = format_balance_msg()
        send_telegram(f"🚀 DRY RUN {side.upper()} {qty} @ {price:.2f}$ {bal_msg}")
        return True, qty

    ensure_all_gold_markets()
    sync_positions_from_mexc()

    # เช็คไม้เต็ม
    side_count = len([p for p in positions if p['side']==side])
    if side_count >= MAX_WOODS:
        send_telegram(f"⚠ {side.upper()} มี {side_count} ไม้แล้ว เต็ม {MAX_WOODS} - /close {side} ก่อน {thai_time()}")
        return False, 0

    try:
        try:
            mexc.set_leverage(LEVERAGE, SYMBOL_CCXT, {'marginMode': 'CROSS'})
        except:
            pass

        balance = get_balance()
        if custom_qty:
            qty = fix_qty_precision(custom_qty)
        else:
            usdt_to_use = balance * ORDER_PERCENT / 100 * factor
            qty = (usdt_to_use * LEVERAGE) / price
            qty = fix_qty_precision(qty)

        print(f"V13.2 REAL {side.upper()} {SYMBOL_CCXT} qty {qty} @ {price}")

        order = None
        last_err = None
        for cand in [active_symbol] + SYMBOL_LIST:
            sym, mid, ccxt_sym = cand
            try:
                if side == 'long':
                    order = mexc.create_market_buy_order(ccxt_sym, qty)
                else:
                    order = mexc.create_market_sell_order(ccxt_sym, qty)
                SYMBOL, SYMBOL_MEXC_ID, SYMBOL_CCXT = cand
                active_symbol = cand
                print(f"{side} success {ccxt_sym} qty {qty}")
                break
            except Exception as e:
                last_err = e
                print(f"{side} {ccxt_sym} failed {e}")
                continue

        if not order:
            raise last_err

        positions.append({'entry': price, 'qty': qty, 'side': side, 'time': time.time(), 'symbol': SYMBOL})
        save_positions()
        bal_msg, _, wr = format_balance_msg()
        ema_info = f"EMA12 {last_ind['EMA12']:.2f}>EMA26 {last_ind['EMA26']:.2f} RSI {last_ind['RSI']:.1f} ADX {last_ind['ADX']:.1f}" if last_ind else ""
        send_telegram(f"🚀 V13.2 OPEN {SYMBOL} {side.upper()} {qty} @ {price:.2f}$ {thai_time()}\n{bal_msg} WR {wr:.1f}%\n{ema_info}\nQTY 0.01 FIX PRECISION | Pos:{len(positions)} ({side})")
        return True, qty

    except Exception as e:
        import traceback
        err_full = traceback.format_exc()
        print(f"BUY ERR {err_full}")
        send_telegram(f"❌ {side.upper()} ERROR {str(e)[:200]} {thai_time()}\nQTY {custom_qty or DEFAULT_QTY} -> {fix_qty_precision(custom_qty or DEFAULT_QTY)}\nFull: {err_full[-400:]}")
        return False, 0

def close_position(price, reason, side_filter=None, last_ind=None):
    global cooldown_until, daily_loss_count, positions, highest, lowest
    if not positions:
        return False
    ensure_all_gold_markets()

    # กรอง side ที่จะปิด
    to_close = positions
    if side_filter in ['long','short']:
        to_close = [p for p in positions if p['side']==side_filter]
        if not to_close:
            send_telegram(f"ℹ ไม่มี {side_filter.upper()} ให้ปิด {thai_time()}")
            return False

    total_qty = sum(abs(p['qty']) for p in to_close)
    avg = sum(p['entry']*abs(p['qty']) for p in to_close)/total_qty

    # คำนวณ PnL ตาม side
    if to_close[0]['side'] == 'long':
        pnl = (price-avg)/avg*100
    else:
        pnl = (avg-price)/avg*100

    if mexc:
        for cand in SYMBOL_LIST:
            sym, mid, ccxt_sym = cand
            try:
                # ปิดฝั่งตรงข้าม
                if to_close[0]['side'] == 'long':
                    mexc.create_market_sell_order(ccxt_sym, total_qty)
                else:
                    mexc.create_market_buy_order(ccxt_sym, total_qty)
                print(f"Close {to_close[0]['side']} {ccxt_sym} qty {total_qty}")
                break
            except Exception as e:
                print(f"Close err {e}")
                continue

    stats["total_trades"]+=1
    if pnl>0:
        stats["wins"]+=1
        prev_avg = stats.get("avg_win", 0.65)
        n_wins = stats["wins"]
        stats["avg_win"] = (prev_avg*(n_wins-1) + pnl)/n_wins if n_wins>1 else pnl
    else:
        stats["losses"]+=1
        daily_loss_count+=1
        cooldown_until = time.time() + COOLDOWN_AFTER_SL*3600
        prev_avg = stats.get("avg_loss", -1.34)
        n_loss = stats["losses"]
        stats["avg_loss"] = (prev_avg*(n_loss-1) + pnl)/n_loss if n_loss>1 else pnl

    stats["total_pnl_pct"]+=pnl
    stats["history"].append({"time":thai_time(),"type":f"{to_close[0]['side'].upper()} {reason}","pnl":pnl})
    save_stats()

    # ลบออกจาก positions
    if side_filter in ['long','short']:
        positions = [p for p in positions if p['side'] != side_filter]
    else:
        positions = []

    if not positions:
        highest=0
        lowest=999999
    save_positions()

    bal_msg, _, wr = format_balance_msg()
    ratio = abs(stats.get("avg_win",0.65)/stats.get("avg_loss",-1.34)) if stats.get("avg_loss",0)!=0 else 0.48
    send_telegram(f"{'🔴' if pnl<0 else '🟢'} [{thai_time()}] V13.2 CLOSE {SYMBOL} {to_close[0]['side'].upper()} {reason} PnL {pnl:+.2f}% @ {price:.2f}$ {bal_msg} WR {wr:.1f}% Ratio {ratio:.1f}:1 | Pos:{len(positions)}")

    return True

def manual_open(side='long', qty_str=None):
    global manual_pause_until
    price = get_price_mexc()
    if not price:
        send_telegram(f"❌ เปิดไม่ได้ - ดึงราคาไม่ได้ {thai_time()}")
        return
    sync_positions_from_mexc()

    custom_qty = None
    if qty_str:
        try:
            # รองรับทั้ง "0.01" และ "long 0.01"
            qty_str = qty_str.replace(',', '').strip()
            custom_qty = float(qty_str)
        except:
            custom_qty = None

    if not custom_qty:
        custom_qty = DEFAULT_QTY

    ok, qty = mexc_buy(price, side=side, factor=1.0, custom_qty=custom_qty)
    if ok:
        manual_pause_until = time.time() + 300

def manual_close(side_filter=None):
    global manual_pause_until
    price = get_price_mexc()
    if not price:
        price = positions[0]['entry'] if positions else 0
    sync_positions_from_mexc()
    if not positions:
        send_telegram(f"ℹ ไม่มีออเดอร์ให้ปิด (เช็ค {SYMBOL} แล้ว) {thai_time()}")
        return
    close_position(price, "MANUAL_CLOSE", side_filter=side_filter)
    manual_pause_until = time.time() + 300

def trading_loop():
    global positions, highest, lowest, last_hourly, cooldown_until, current_atr_sl
    last_hourly = time.time()
    print(f"V13.2 FIX PRECISION QTY0.01 START {SYMBOL}")
    sync_positions_from_mexc()
    bal_msg, _, _ = format_balance_msg()
    pos_info = f"{len(positions)} Pos" if positions else "ว่าง"
    send_telegram(f"🚀 V13.2 FIX PRECISION START {thai_time()} | {bal_msg}\nPos: {pos_info}\nGOLD Perpetual QTY 0.01 FIX precision 20/10\nLONG/SHORT /open long 0.01 /open short 0.01 /close\nBE0.35% TRAIL0.3% SL0.6% TP1.8%/3.2%")
    while True:
        try:
            if manual_pause_until > time.time():
                time.sleep(10)
                continue
            ohlcv = get_klines_mexc(200, '15m')
            if not ohlcv or len(ohlcv)<50:
                time.sleep(60)
                continue
            rows = calc_indicators(ohlcv)
            if not rows:
                time.sleep(60)
                continue
            last = rows[-1]
            price = float(last['c'])
            if price > highest:
                highest = price
            if price < lowest:
                lowest = price

            # MTF
            mtf_1h_up = True
            mtf_4h_up = True
            try:
                ohlcv_1h = get_klines_mexc(100, '1h')
                ohlcv_4h = get_klines_mexc(100, '4h')
                if ohlcv_1h and len(ohlcv_1h)>50:
                    r1 = calc_indicators(ohlcv_1h)
                    if r1:
                        mtf_1h_up = r1[-1]['EMA12'] > r1[-1]['EMA26'] and r1[-1]['c'] > r1[-1]['EMA200']
                if ohlcv_4h and len(ohlcv_4h)>50:
                    r4 = calc_indicators(ohlcv_4h)
                    if r4:
                        mtf_4h_up = r4[-1]['EMA12'] > r4[-1]['EMA26'] and r4[-1]['c'] > r4[-1]['EMA200']
            except:
                pass

            atr = last['ATR']
            current_atr_sl = max(SL_MIN_PCT, min(SL_MAX_PCT, (atr * SL_ATR_MULT / price * 100 * LEVERAGE)))

            price_above_ema200 = last['c'] > last['EMA200']
            ema200_up = last['EMA20'] > last['EMA200']
            ema_bull_align = last['EMA12'] > last['EMA26'] and last['EMA26'] > last['EMA200'] and last['EMA12'] > last['EMA20']
            rsi_optimal = 48 <= last['RSI'] <= 62
            adx_strong = last['ADX'] > 28 and last['+DI'] > 24 and last['+DI'] > last['-DI'] + 10
            vol_filter = last['v'] > last['VOL_SMA20'] * 0.9
            strong_bull = (last['c'] - last['o']) > (last['h'] - last['l']) * 0.5 and last['c'] > last['o']

            buy_signal = price_above_ema200 and ema200_up and ema_bull_align and rsi_optimal and adx_strong and vol_filter and mtf_1h_up and mtf_4h_up and strong_bull

            if cooldown_until > time.time():
                time.sleep(60)
                continue

            if positions:
                longs = [p for p in positions if p['side']=='long']
                if longs:
                    total_qty = sum(abs(p['qty']) for p in longs)
                    avg = sum(p['entry']*abs(p['qty']) for p in longs)/total_qty
                    pnl = (price-avg)/avg*100
                    if pnl >= TP2_PCT:
                        close_position(price, f"TP2 {TP2_PCT}%", side_filter='long', last_ind=last)
                    elif pnl >= BE_PCT and price <= highest * (1 - TRAIL_PCT/100):
                        close_position(price, f"TRAIL {TRAIL_PCT}%", side_filter='long', last_ind=last)
                    elif price <= avg*(1-current_atr_sl/100):
                        close_position(price, f"HARD_SL -{current_atr_sl:.1f}%", side_filter='long', last_ind=last)
                    elif not ema_bull_align:
                        close_position(price, f"EMA_ALIGN_BEAR", side_filter='long', last_ind=last)

            else:
                if buy_signal and len([p for p in positions if p['side']=='long']) < MAX_WOODS:
                    mexc_buy(price, side='long', factor=1.0, custom_qty=DEFAULT_QTY, last_ind=last)

            if time.time()-last_hourly >= 14400:
                last_hourly=time.time()
                bal_msg, _, wr = format_balance_msg()
                status = "✅ READY LONG" if buy_signal else "⏳ รอ FILTER"
                send_telegram(f"📊 V13.2 [{thai_time()}] Price {price:.2f} Pos:{len(positions)} {bal_msg} WR {wr:.1f}%\nEMA12 {last['EMA12']:.2f} EMA26 {last['EMA26']:.2f} EMA200 {last['EMA200']:.2f} RSI {last['RSI']:.1f}\n{status} QTY0.01 FIXED | /open long 0.01 /open short 0.01 /close")
            time.sleep(60)
        except Exception as e:
            print(f"Loop err {e}")
            import traceback
            traceback.print_exc()
            time.sleep(30)

threading.Thread(target=trading_loop, daemon=True).start()

def telegram_check_loop():
    if not BOT_TOKEN:
        return
    try:
        requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook?drop_pending_updates=True", timeout=10)
    except:
        pass
    offset = 0
    while True:
        try:
            r = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?offset={offset+1}&timeout=30", timeout=35)
            data = r.json()
            if not data.get("ok"):
                time.sleep(5)
                continue
            for upd in data.get("result", []):
                offset = upd["update_id"]
                msg = upd.get("message", {})
                text_raw = msg.get("text","").strip()
                text = text_raw.lower()
                chat = str(msg.get("chat",{}).get("id",""))
                # เก็บ text_raw ไว้ parse qty
                if text in ["/start","/check","/status","/running"]:
                    sync_positions_from_mexc()
                    wr = (stats["wins"]/stats["total_trades"]*100) if stats["total_trades"]>0 else 0
                    ratio = abs(stats.get("avg_win",0.65)/stats.get("avg_loss",-1.34)) if stats.get("avg_loss",0)!=0 else 0.48
                    pos_info = f"{len(positions)} Pos" if positions else "ว่าง"
                    longs = len([p for p in positions if p['side']=='long'])
                    shorts = len([p for p in positions if p['side']=='short'])
                    total_qty = sum(abs(p['qty']) for p in positions) if positions else 0
                    avg = sum(p['entry']*abs(p['qty']) for p in positions)/total_qty if total_qty>0 else 0
                    price = get_price_mexc()
                    send_telegram(f"✅ V13.2 FIX PRECISION รันอยู่\n⏰ {thai_time()}\nSymbol: {SYMBOL} GOLD Perpetual QTY {DEFAULT_QTY}\nPos:{pos_info} Long:{longs} Short:{shorts} Qty:{total_qty} Avg:{avg:.2f} Price:{price}\nWR:{wr:.1f}% Ratio:{ratio:.1f}:1\nFIX precision 20/10 + QTY 0.01\n/open long 0.01 /open short 0.01 /close", chat)

                elif text.startswith("/signal"):
                    ohlcv = get_klines_mexc(200, '15m')
                    if ohlcv:
                        rows = calc_indicators(ohlcv)
                        if rows:
                            last = rows[-1]
                            price_above_ema200 = last['c'] > last['EMA200']
                            ema_align = last['EMA12'] > last['EMA26'] and last['EMA26'] > last['EMA200']
                            rsi_opt = 48 <= last['RSI'] <= 62
                            adx_strong = last['ADX'] > 28
                            buy_signal = price_above_ema200 and ema_align and rsi_opt and adx_strong
                            send_telegram(f"📡 V13.2 SIGNAL {SYMBOL} {thai_time()}\nPrice {last['c']:.2f} EMA12 {last['EMA12']:.2f} EMA26 {last['EMA26']:.2f} EMA200 {last['EMA200']:.2f}\nRSI {last['RSI']:.1f} ADX {last['ADX']:.1f} +DI {last['+DI']:.1f} -DI {last['-DI']:.1f}\nPrice>EMA200:{price_above_ema200} Align:{ema_align} RSI48-62:{rsi_opt} ADX28+:{adx_strong}\n{'✅ READY LONG' if buy_signal else '⏳ รอ FILTER'}\nQTY {DEFAULT_QTY} FIXED | /open long 0.01", chat)

                elif text.startswith("/open"):
                    # Parse: /open long 0.01 /open short 0.01 /open 0.01 /open long /open short
                    parts = text_raw.split()
                    side = 'long'  # default
                    qty_str = None
                    if len(parts) >= 2:
                        p1 = parts[1].lower()
                        if p1 in ['long','short','buy','sell']:
                            side = 'long' if p1 in ['long','buy'] else 'short'
                            if len(parts) >= 3:
                                qty_str = parts[2]
                        else:
                            # /open 0.01
                            try:
                                float(p1)
                                qty_str = p1
                            except:
                                qty_str = None
                    if not qty_str:
                        qty_str = str(DEFAULT_QTY)
                    threading.Thread(target=manual_open, args=(side, qty_str), daemon=True).start()
                    send_telegram(f"⏳ V13.2 กำลังเปิด {SYMBOL} {side.upper()} {qty_str} ... (QTY 0.01 FIXED)", chat)

                elif text.startswith("/close"):
                    # /close /close long /close short /close all
                    parts = text_raw.split()
                    side_filter = None
                    if len(parts) >= 2:
                        p1 = parts[1].lower()
                        if p1 in ['long','short']:
                            side_filter = p1
                        elif p1 == 'all':
                            side_filter = None
                    threading.Thread(target=manual_close, args=(side_filter,), daemon=True).start()
                    if side_filter:
                        send_telegram(f"⏳ กำลังปิด {SYMBOL} {side_filter.upper()} {len([p for p in positions if p['side']==side_filter])} ไม้ ...", chat)
                    else:
                        send_telegram(f"⏳ กำลังปิด {SYMBOL} ทั้งหมด {len(positions)} ไม้ ...", chat)

                elif text == "/balance":
                    sync_positions_from_mexc()
                    bal = get_balance()
                    price = get_price_mexc()
                    avg = 0
                    upnl = 0
                    longs = len([p for p in positions if p['side']=='long'])
                    shorts = len([p for p in positions if p['side']=='short'])
                    if positions:
                        total_qty = sum(abs(p['qty']) for p in positions)
                        avg = sum(p['entry']*abs(p['qty']) for p in positions)/total_qty
                        # คำนวณ upnl รวม
                        if longs>0:
                            long_avg = sum(p['entry']*abs(p['qty']) for p in positions if p['side']=='long')/sum(abs(p['qty']) for p in positions if p['side']=='long') if longs>0 else 0
                            upnl = (price-long_avg)/long_avg*100 if long_avg>0 and price else 0
                    wr = (stats["wins"]/stats["total_trades"]*100) if stats["total_trades"]>0 else 0
                    send_telegram(f"💰 V13.2 {SYMBOL} Perpetual {thai_time()}\nUSDT: ${bal:.2f}\nPrice: {price}\nPos: {len(positions)} Long:{longs} Short:{shorts} Avg:{avg:.2f} uPnL:{upnl:+.2f}%\nQTY {DEFAULT_QTY} FIXED Precision 2 decimal\nWR {wr:.1f}%", chat)

                elif text == "/bt":
                    wr = (stats["wins"]/stats["total_trades"]*100) if stats["total_trades"]>0 else 0
                    send_telegram(f"📊 V13.2 BT {thai_time()}\nTrades:{stats['total_trades']} WR:{wr:.1f}% PnL:{stats.get('total_pnl_pct',0):.2f}%\nQTY 0.01 FIX", chat)
        except Exception as e:
            print(f"Tele err {e}")
            import traceback
            traceback.print_exc()
            time.sleep(10)

threading.Thread(target=telegram_check_loop, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
