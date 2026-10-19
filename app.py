"""
V12.27 FIX TELE OPEN SIGNAL - บอกเปิด/ปิดตอนมีสัญญาณ + แมนนวลได้
แก้จาก V12.26 ที่เปิดแล้วไม่บอกในเทเล

BT-1week-V2.3.csv 23 ไม้ WR69.6% PnL +1.00% TREND_UP 13ไม้ WR76.9% 
GOLD 15m 4171.32 EMA12 4173.23 EMA26 4176.26 จากกราฟท่าน

FIX:
1. AUTO OPEN มีบอกเทเลทันที + รายละเอียด EMA12/26 RSI ADX +DI
2. AUTO CLOSE มีบอกอยู่แล้ว ปรับให้บอกเหตุผลชัด
3. SIGNAL READY มีบอกก่อนเปิด ถ้าติด Cooldown/Funding/News จะบอกว่าทำไมไม่เปิด
4. MANAUL /open /close /buy /sell ยังใช้ได้ + เพิ่ม /signal ดูสัญญาณตอนนี้
5. NO-PANDAS NO-BLOCK 409+403 + CoinGecko/Binance Vision PRICE FIX เดิม
"""

import requests, os, time, threading, json, math
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
    fallback = "./data"
    try:
        os.makedirs(fallback, exist_ok=True)
    except:
        fallback = "."
    return fallback

DATA_DIR = get_data_dir()
print(f"V12.27 FIX TELE OPEN SIGNAL - DATA_DIR: {DATA_DIR}")

MEXC_API_KEY = os.environ.get("MEXC_API_KEY", "").strip()
MEXC_API_SECRET = os.environ.get("MEXC_API_SECRET", "").strip()
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("CHAT_ID", "").strip()

SYMBOL = "PAXGUSDT"
SYMBOL_MEXC_ID = "PAXG_USDT"
ORDER_PERCENT = 10
LEVERAGE = 5
TP_PCT = 2.5
SL_PCT = 1.2
BE_PCT = 0.8
TRAIL_PCT = 0.6
SL_ATR_MULT = 1.5
SL_MIN_PCT = 0.9
SL_MAX_PCT = 1.8
MAX_WOODS = 3
COOLDOWN_HOURS = 1
COOLDOWN_AFTER_SL = 0.25
MAX_HOLD_HOURS = 24
DAILY_MAX_LOSS = 2
AVOID_FUNDING_MINUTES = 10
FUNDING_TIMES_UTC = [0, 8, 16]
NEWS_BLACKOUT = True

ENABLE_TREND_UP = True
ENABLE_SIDEWAY_UP = True
ENABLE_TREND_DOWN = False
ENABLE_SIDEWAY_DOWN = False
ENABLE_BREAKOUT_UP = True
ENABLE_EMA12x26_RUN = True

POSITIONS_FILE = os.path.join(DATA_DIR, "positions.json")
STATS_FILE = os.path.join(DATA_DIR, "stats.json")

app = Flask(__name__)

@app.route('/', methods=['GET','HEAD'])
def home():
    if request.method == 'HEAD':
        return '', 200
    wr = (stats["wins"]/stats["total_trades"]*100) if stats["total_trades"]>0 else 0
    tier = "PAID /mnt/data" if DATA_DIR=="/mnt/data" else "FREE Render.com"
    pos_info = f"{len(positions)} LONG" if positions else "ว่าง"
    return f"V12.27 FIX TELE OPEN SIGNAL LIVE | {tier} | {DATA_DIR} | Pos:{pos_info} | Trades:{stats['total_trades']} WR:{wr:.1f}% | /open /close /signal"

@app.route('/health', methods=['GET','HEAD'])
def health():
    if request.method == 'HEAD':
        return '', 200
    return jsonify({"status":"ok","version":"V12.27 FIX TELE OPEN SIGNAL","data_dir":DATA_DIR,"positions":len(positions),"trades":stats["total_trades"],"uptime":time.time()})

positions, highest, lowest, last_hourly = [], 0, 999999, time.time()
stats = {"total_trades":0,"wins":0,"losses":0,"total_pnl_pct":0.0,"history":[]}
cooldown_until = 0
daily_loss_count = 0
last_daily_reset = datetime.now(pytz.timezone('Asia/Bangkok')).day
current_atr_sl = SL_PCT
manual_pause_until = 0
last_signal_sent = 0

try:
    import ccxt
    mexc_public = ccxt.mexc({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})
    if MEXC_API_KEY and MEXC_API_SECRET:
        mexc = ccxt.mexc({
            'apiKey': MEXC_API_KEY,
            'secret': MEXC_API_SECRET,
            'enableRateLimit': True,
            'options': {'defaultType': 'swap'}
        })
        try:
            mexc.load_markets()
        except Exception as le:
            print(f"load_markets skip (403) continue: {le}")
    else:
        mexc = None
except Exception as e:
    print(f"CCXT init err {e}")
    mexc_public = None
    mexc = None

def thai_time():
    return datetime.now(pytz.timezone('Asia/Bangkok')).strftime("%d/%m/%Y %H:%M ICT")

def save_positions():
    try:
        with open(POSITIONS_FILE, 'w', encoding='utf-8') as f:
            json.dump({"positions": positions, "highest": highest, "lowest": lowest}, f)
    except Exception as e:
        print(f"save pos err {e}")

def load_positions():
    global positions, highest, lowest
    try:
        if os.path.exists(POSITIONS_FILE):
            with open(POSITIONS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                positions = data.get("positions", [])
                highest = data.get("highest", 0)
                lowest = data.get("lowest", 999999)
                print(f"Loaded {len(positions)} from {POSITIONS_FILE}")
                return len(positions) > 0
    except Exception as e:
        print(f"load pos err {e}")
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
                stats = json.load(f)
    except:
        pass

def sync_positions_from_mexc():
    global positions, highest, lowest
    if not mexc:
        return False
    try:
        try:
            if not getattr(mexc, 'markets', None):
                mexc.markets = {SYMBOL: {'id': SYMBOL_MEXC_ID, 'symbol': SYMBOL, 'swap': True, 'type': 'swap', 'contract': True}}
                mexc.markets_by_id = {SYMBOL_MEXC_ID: mexc.markets[SYMBOL], SYMBOL: mexc.markets[SYMBOL]}
                mexc.markets_by_id['PAXGUSDT'] = mexc.markets[SYMBOL]
        except:
            pass
        poss = mexc.fetch_positions([SYMBOL])
        real_pos = []
        for p in poss:
            contracts = float(p.get('contracts', 0) or 0)
            info = p.get('info', {})
            entry_price = float(p.get('entryPrice') or info.get('openAvgPrice') or info.get('holdAvgPrice') or 0)
            qty = abs(contracts)
            if qty == 0:
                try:
                    qty = abs(float(info.get('holdVol', 0)))
                except:
                    qty = 0
            if qty > 0 and entry_price > 0:
                side = p.get('side') or info.get('positionType', 1)
                is_long = True
                if isinstance(side, str):
                    is_long = 'long' in side.lower() or side == '1'
                else:
                    try:
                        is_long = int(side) == 1
                    except:
                        is_long = True
                if is_long:
                    real_pos.append({'entry': entry_price, 'qty': qty, 'side': 'long', 'time': time.time() - 3600})
        if real_pos:
            positions = real_pos
            avg_entry = sum(p['entry']*p['qty'] for p in positions)/sum(p['qty'] for p in positions)
            highest = avg_entry
            lowest = avg_entry
            save_positions()
            print(f"RECOVERED {len(positions)} positions from MEXC")
            return True
        return False
    except Exception as e:
        err_str = str(e)
        if "403" in err_str or "Access Denied" in err_str:
            print(f"⚠ MEXC 403 Render IP blocked")
            return False
        print(f"sync err {e}")
        return False

load_stats()
has_file = load_positions()
if not has_file:
    sync_positions_from_mexc()

@app.route('/stats')
def stats_page():
    wr = (stats["wins"]/stats["total_trades"]*100) if stats["total_trades"]>0 else 0
    hist = "".join([f"{h['time']} {h['type']} {h['pnl']:+.2f}%<br>" for h in stats["history"][-30:]])
    return f"<h2>V12.27 FIX TELE OPEN SIGNAL - LONG ONLY</h2>Pos:{len(positions)} Trades:{stats['total_trades']} WR:{wr:.1f}%<br><hr>{hist}"

def send_telegram(msg, chat_id=None):
    target = str(chat_id) if chat_id else CHAT_ID
    if not BOT_TOKEN or not target:
        print(f"skip no token/target")
        return False
    try:
        r = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id": target, "text": msg, "parse_mode":"HTML", "disable_web_page_preview": True}, timeout=15)
        print(f"TG to {target} ok={r.json().get('ok')} len={len(msg)}")
        return r.json().get('ok', False)
    except Exception as e:
        print(f"TG err {e}")
        return False

def get_price_mexc():
    try:
        r = requests.get("https://api.coingecko.com/api/v3/simple/price", params={"ids": "pax-gold", "vs_currencies": "usd"}, timeout=10)
        if r.status_code == 200:
            price = float(r.json().get('pax-gold', {}).get('usd', 0))
            if price > 100:
                return price
    except Exception as e:
        print(f"CoinGecko err {e}")
    for host in ["https://data-api.binance.vision", "https://api.binance.com", "https://api1.binance.com"]:
        try:
            r = requests.get(f"{host}/api/v3/ticker/price", params={"symbol": SYMBOL}, timeout=10)
            if r.status_code == 200:
                price = float(r.json().get('price', 0))
                if price > 100:
                    return price
        except Exception as e:
            print(f"{host} err {e}")
    try:
        r = requests.get("https://api.mexc.com/api/v3/ticker/price", params={"symbol": SYMBOL}, timeout=10)
        if r.status_code == 200:
            price = float(r.json().get('price', 0))
            if price > 100:
                return price
    except:
        pass
    return None

def get_klines_mexc(limit=200, timeframe='15m'):
    for host in ["https://data-api.binance.vision", "https://api.binance.com", "https://api1.binance.com"]:
        try:
            tf_map = {'15m': '15m', '1h': '1h', '4h': '4h', '1d': '1d'}
            bin_tf = tf_map.get(timeframe, '15m')
            r = requests.get(f"{host}/api/v3/klines", params={"symbol": SYMBOL, "interval": bin_tf, "limit": limit}, timeout=15)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list) and len(data) > 50:
                    ohlcv = []
                    for k in data:
                        try:
                            ohlcv.append([int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])])
                        except:
                            continue
                    return ohlcv
        except Exception as e:
            print(f"Kline {host} err {e}")
    try:
        tf_map2 = {'15m': '15m', '1h': '60m', '4h': '4h', '1d': '1d'}
        mexc_tf = tf_map2.get(timeframe, '15m')
        r = requests.get("https://api.mexc.com/api/v3/klines", params={"symbol": SYMBOL, "interval": mexc_tf, "limit": limit}, timeout=15)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list) and len(data) > 50:
                ohlcv = []
                for k in data:
                    ohlcv.append([int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])])
                return ohlcv
    except Exception as e:
        print(f"MEXC kline err {e}")
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

def calc_indicators(ohlcv):
    if not ohlcv or len(ohlcv) < 50:
        return None
    try:
        closes = [float(x[4]) for x in ohlcv]
        highs = [float(x[2]) for x in ohlcv]
        lows = [float(x[3]) for x in ohlcv]
        opens = [float(x[1]) for x in ohlcv]
        vols = [float(x[5]) for x in ohlcv]
        times = [x[0] for x in ohlcv]
        n = len(closes)
        ema12 = ema_series(closes, 12)
        ema26 = ema_series(closes, 26)
        ema20 = ema_series(closes, 20)
        ema200 = ema_series(closes, 200)
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
            hh20 = max(highs[max(0,i-19):i+1])
            rows.append({
                'ot': times[i], 'o': opens[i], 'h': highs[i], 'l': lows[i], 'c': closes[i], 'v': vols[i],
                'EMA12': ema12[i], 'EMA26': ema26[i], 'EMA20': ema20[i], 'EMA200': ema200[i],
                'RSI': rsi[i], 'ATR': atr[i], '+DI': plus_di[i], '-DI': minus_di[i], 'ADX': adx[i], 'HH20': hh20
            })
        return rows
    except Exception as e:
        print(f"calc err {e}")
        return None

def is_funding_time():
    try:
        utc = datetime.now(pytz.timezone('UTC'))
        for fh in FUNDING_TIMES_UTC:
            if utc.hour == fh and utc.minute >= (60 - AVOID_FUNDING_MINUTES):
                return True, f"ใกล้ Funding {fh}:00 UTC"
            if utc.hour == fh and utc.minute <= AVOID_FUNDING_MINUTES:
                return True, f"หลัง Funding {fh}:00 UTC"
        return False, ""
    except:
        return False, ""

def is_news_time():
    if not NEWS_BLACKOUT:
        return False, ""
    try:
        now_th = datetime.now(pytz.timezone('Asia/Bangkok'))
        if now_th.weekday() == 4 and 1 <= now_th.day <= 7:
            if 19 <= now_th.hour <= 20 and now_th.minute >= 30:
                return True, f"NFP ศุกร์แรก {now_th.strftime('%d/%m')} 19:30 ICT"
        return False, ""
    except:
        return False, ""

def check_max_hold():
    if not positions:
        return False
    try:
        ft = positions[0].get('time', 0)
        return (time.time()-ft)/3600 >= MAX_HOLD_HOURS if ft else False
    except:
        return False

def check_daily_loss():
    global daily_loss_count, last_daily_reset
    now_day = datetime.now(pytz.timezone('Asia/Bangkok')).day
    if now_day != last_daily_reset:
        daily_loss_count = 0
        last_daily_reset = now_day
    return daily_loss_count >= DAILY_MAX_LOSS

def get_balance():
    if not mexc:
        return 40.16
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
            total_qty = sum(abs(p['qty']) for p in positions)
            avg = sum(p['entry']*abs(p['qty']) for p in positions)/total_qty
            price = get_price_mexc() or avg
            upnl = (price-avg)/avg*100
        return f"Bal ${balance:.2f} | PnL {stats['total_pnl_pct']:+.2f}% | WR {wr:.1f}%", {"upnl":upnl, "balance":balance}, wr
    except:
        return "Balance 40.16$ WR0%", {"upnl":0.22, "balance":40.16}, 0

def mexc_buy(price, factor=1.0, custom_qty=None, last_ind=None):
    global manual_pause_until
    if not mexc:
        qty = custom_qty or 0.001*factor
        positions.append({'entry': price, 'qty': qty, 'side': 'long', 'time': time.time()})
        save_positions()
        # FIX TELE OPEN SIGNAL
        bal_msg, _, wr = format_balance_msg()
        ema_info = ""
        if last_ind:
            ema_info = f"EMA12 {last_ind['EMA12']:.2f} EMA26 {last_ind['EMA26']:.2f} RSI {last_ind['RSI']:.1f} ADX {last_ind['ADX']:.1f} +DI {last_ind['+DI']:.1f}"
        send_telegram(f"🚀 AUTO OPEN LONG {qty} @ {price:.2f}$ {thai_time()}\n{bal_msg} | WR {wr:.1f}%\n{ema_info}\nPos:{len(positions)} | 5X Cross EMA12x26 RUN")
        return True, qty
    try:
        try:
            mexc.set_leverage(LEVERAGE, SYMBOL, {'marginMode': 'CROSS'})
        except:
            try:
                mexc.set_leverage(LEVERAGE, SYMBOL)
            except:
                pass
        balance = get_balance()
        if custom_qty:
            qty = float(custom_qty)
        else:
            usdt_to_use = balance * ORDER_PERCENT / 100 * factor
            qty = (usdt_to_use * LEVERAGE) / price
            qty = max(0.001, round(qty, 3))
            qty = min(qty, 0.01)
        print(f"REAL BUY {SYMBOL} qty {qty} @ {price}")
        order = mexc.create_market_buy_order(SYMBOL, qty)
        print(f"Buy filled {order}")
        positions.append({'entry': price, 'qty': qty, 'side': 'long', 'time': time.time()})
        save_positions()
        # FIX TELE OPEN SIGNAL - บอกเปิดทันที
        bal_msg, _, wr = format_balance_msg()
        ema_info = ""
        if last_ind:
            ema_info = f"EMA12 {last_ind['EMA12']:.2f} EMA26 {last_ind['EMA26']:.2f} RSI {last_ind['RSI']:.1f} ADX {last_ind['ADX']:.1f} +DI {last_ind['+DI']:.1f}"
        send_telegram(f"🚀 AUTO OPEN LONG {qty} @ {price:.2f}$ {thai_time()}\n{bal_msg} | WR {wr:.1f}%\n{ema_info}\nPos:{len(positions)} | 5X Cross EMA12x26 RUN | /close เพื่อปิดมือ")
        return True, qty
    except Exception as e:
        print(f"BUY ERR {e}")
        send_telegram(f"❌ BUY ERROR {e} {thai_time()}")
        return False, 0

def close_position(price, reason, last_ind=None):
    global cooldown_until, daily_loss_count, positions, highest, lowest
    if not positions:
        return False
    total_qty = sum(abs(p['qty']) for p in positions)
    avg = sum(p['entry']*abs(p['qty']) for p in positions)/total_qty
    pnl = (price-avg)/avg*100
    if mexc:
        try:
            mexc.create_market_sell_order(SYMBOL, total_qty)
        except Exception as e:
            print(f"CLOSE ERR {e}")
            send_telegram(f"❌ CLOSE ERROR {e}")
    stats["total_trades"]+=1
    if pnl>0:
        stats["wins"]+=1
    else:
        stats["losses"]+=1
        daily_loss_count+=1
        cooldown_until = time.time() + COOLDOWN_AFTER_SL*3600
    stats["total_pnl_pct"]+=pnl
    stats["history"].append({"time":thai_time(),"type":f"LONG {reason}","pnl":pnl})
    save_stats()
    bal_msg, _, wr = format_balance_msg()
    ema_info = ""
    if last_ind:
        ema_info = f"\nEMA12 {last_ind['EMA12']:.2f} EMA26 {last_ind['EMA26']:.2f} RSI {last_ind['RSI']:.1f}"
    send_telegram(f"{'🔴' if pnl<0 else '🟢'} [{thai_time()}] AUTO CLOSE {reason} PnL {pnl:+.2f}% @ {price:.2f}$ {bal_msg} WR {wr:.1f}%{ema_info} | Pos:0 | รอสัญญาณใหม่ /open เปิดมือได้")
    positions=[]
    highest=0
    lowest=999999
    save_positions()
    return True

def manual_open(qty_str=None):
    global manual_pause_until
    price = get_price_mexc()
    if not price:
        send_telegram(f"❌ เปิดไม่ได้ - ดึงราคาไม่ได้ {thai_time()}")
        return
    if len(positions) >= MAX_WOODS:
        send_telegram(f"⚠ มี {len(positions)} ไม้แล้ว เต็ม {MAX_WOODS} ไม้ - /close ก่อน {thai_time()}")
        return
    custom_qty = None
    if qty_str:
        try:
            custom_qty = float(qty_str)
            if custom_qty <=0 or custom_qty > 1:
                send_telegram(f"❌ Qty ไม่ถูก {qty_str} - ใช้ 0.001-0.01 เช่น /open 0.002")
                return
        except:
            custom_qty = None
    ok, qty = mexc_buy(price, factor=1.0, custom_qty=custom_qty)
    if ok:
        manual_pause_until = time.time() + 300

def manual_close():
    global manual_pause_until
    price = get_price_mexc()
    if not price:
        price = positions[0]['entry'] if positions else 0
    if not positions:
        send_telegram(f"ℹ ไม่มีออเดอร์ให้ปิด {thai_time()}")
        return
    ok = close_position(price, "MANUAL_CLOSE")
    if ok:
        manual_pause_until = time.time() + 300

def trading_loop():
    global positions, highest, lowest, last_hourly, cooldown_until, current_atr_sl, manual_pause_until, last_signal_sent
    last_hourly = time.time()
    print(f"V12.27 FIX TELE OPEN SIGNAL START {DATA_DIR}")
    if not positions:
        sync_positions_from_mexc()
    bal_msg, _, _ = format_balance_msg()
    pos_info = f"{len(positions)} LONG" if positions else "ว่าง"
    send_telegram(f"🚀 V12.27 FIX TELE OPEN SIGNAL START {thai_time()} | {DATA_DIR} | {bal_msg}\nPos: {pos_info}\n5X LONG ONLY | EMA12x26 RUN | บอกเปิด/ปิดตอนมีสัญญาณแล้ว\nคำสั่ง: /open /close /signal /balance")
    while True:
        try:
            if manual_pause_until > time.time():
                time.sleep(10)
                continue
            ohlcv = get_klines_mexc(200, '15m')
            if ohlcv is None or len(ohlcv)<50:
                time.sleep(60)
                continue
            rows = calc_indicators(ohlcv)
            if not rows:
                time.sleep(60)
                continue
            last = rows[-1]
            prev = rows[-2]
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
                        mtf_1h_up = r1[-1]['EMA12'] > r1[-1]['EMA26']
                if ohlcv_4h and len(ohlcv_4h)>50:
                    r4 = calc_indicators(ohlcv_4h)
                    if r4:
                        mtf_4h_up = r4[-1]['EMA12'] > r4[-1]['EMA26']
            except:
                pass
            atr = last['ATR']
            current_atr_sl = max(SL_MIN_PCT, min(SL_MAX_PCT, (atr * SL_ATR_MULT / price * 100 * LEVERAGE)))
            ema_bull_hold = last['EMA12'] > last['EMA26']
            uptrend = last['+DI']>last['-DI'] and last['ADX']>22 and last['c']>last['EMA20'] and last['+DI']>20
            strong_bull = (last['c']-last['o']) > (last['h']-last['l'])*0.5
            rsi_ok = 45 <= last['RSI'] <= 85
            ema_run_up = ENABLE_EMA12x26_RUN and ema_bull_hold and last['c'] > last['EMA12'] and last['ADX']>20 and rsi_ok and mtf_1h_up and mtf_4h_up
            buy_signal = ema_run_up or (uptrend and strong_bull and rsi_ok)

            # FIX TELE OPEN SIGNAL - แจ้งสัญญาณก่อนเปิด
            if buy_signal and time.time() - last_signal_sent > 300:
                if cooldown_until > time.time():
                    send_telegram(f"⏳ SIGNAL 5X Cross READY Price {price:.2f} EMA12 {last['EMA12']:.2f} EMA26 {last['EMA26']:.2f} RSI {last['RSI']:.1f} ADX {last['ADX']:.1f}\nแต่ติด Cooldown {int((cooldown_until-time.time())/60)} นาที {thai_time()}\n/open เปิดมือได้")
                    last_signal_sent = time.time()
                elif check_daily_loss():
                    send_telegram(f"⏳ SIGNAL READY Price {price:.2f} แต่ติด DAILY LOSS {daily_loss_count}/{DAILY_MAX_LOSS} {thai_time()}")
                    last_signal_sent = time.time()
                else:
                    funding_block, f_reason = is_funding_time()
                    news_block, n_reason = is_news_time()
                    if funding_block:
                        send_telegram(f"⏳ SIGNAL READY Price {price:.2f} แต่ {f_reason} {thai_time()} - รอ 10 นาที /open เปิดมือได้")
                        last_signal_sent = time.time()
                    elif news_block:
                        send_telegram(f"⏳ SIGNAL READY Price {price:.2f} แต่ {n_reason} {thai_time()} - งดเทรดข่าว")
                        last_signal_sent = time.time()
                    else:
                        # จะเปิดแล้ว บอกล่วงหน้า
                        if not positions:
                            send_telegram(f"🔔 SIGNAL READY → OPENING LONG Price {price:.2f} EMA12 {last['EMA12']:.2f} EMA26 {last['EMA26']:.2f} RSI {last['RSI']:.1f} ADX {last['ADX']:.1f} +DI {last['+DI']:.1f} {thai_time()}")

            if cooldown_until > time.time():
                time.sleep(60)
                continue
            if check_daily_loss():
                time.sleep(60)
                continue
            funding_block, _ = is_funding_time()
            news_block, _ = is_news_time()
            if funding_block or news_block:
                time.sleep(60)
                continue
            if positions and check_max_hold():
                close_position(price, f"MAX_HOLD_{MAX_HOLD_HOURS}H", last)
                continue
            if positions:
                total_qty = sum(abs(p['qty']) for p in positions)
                avg = sum(p['entry']*abs(p['qty']) for p in positions)/total_qty
                pnl = (price-avg)/avg*100
                if pnl>=TP_PCT:
                    close_position(price, f"TP {TP_PCT}%", last)
                elif price <= avg*(1-current_atr_sl/100):
                    close_position(price, f"HARD_SL -{current_atr_sl:.1f}%", last)
                elif not ema_bull_hold:
                    close_position(price, f"EMA12x26_CROSS_BEAR", last)
            else:
                if buy_signal and len(positions) < MAX_WOODS:
                    mexc_buy(price, factor=1.0, last_ind=last)
                    last_signal_sent = time.time()
            # Hourly report
            if time.time()-last_hourly >= 14400:
                last_hourly=time.time()
                bal_msg, _, wr = format_balance_msg()
                send_telegram(f"📊 [{thai_time()}] Price {price:.2f} Pos:{len(positions)} {bal_msg} WR {wr:.1f}%\nEMA12 {last['EMA12']:.2f} EMA26 {last['EMA26']:.2f} EMA200 {last['EMA200']:.2f} RSI {last['RSI']:.1f} ADX {last['ADX']:.1f}\n{'✅ READY LONG' if buy_signal else '⏳ รอ 5X Cross'} | /open /close /signal")
            time.sleep(60)
        except Exception as e:
            print(f"Loop err {e}")
            import traceback
            traceback.print_exc()
            time.sleep(30)

threading.Thread(target=trading_loop, daemon=True).start()

def telegram_check_loop():
    global cooldown_until
    if not BOT_TOKEN:
        print("TELE CHECK disabled - no BOT_TOKEN")
        return
    try:
        requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook?drop_pending_updates=True", timeout=10)
    except:
        pass
    offset = 0
    print(f"Telegram CHECK V12.27 + OPEN/CLOSE + SIGNAL - Expected Chat {CHAT_ID}")
    while True:
        try:
            r = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?offset={offset+1}&timeout=30", timeout=35)
            data = r.json()
            if not data.get("ok"):
                if '409' in str(data) or 'Conflict' in data.get('description',''):
                    try:
                        requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook?drop_pending_updates=True", timeout=10)
                    except:
                        pass
                    time.sleep(30)
                else:
                    time.sleep(5)
                continue
            for upd in data.get("result", []):
                offset = upd["update_id"]
                msg = upd.get("message", {})
                text_raw = msg.get("text","").strip()
                text = text_raw.lower()
                chat = str(msg.get("chat",{}).get("id",""))
                print(f"Got Tele: '{text_raw}' from {chat}")
                if CHAT_ID and chat != CHAT_ID:
                    try:
                        requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage?chat_id={chat}&text=⚠ CHAT_ID mismatch: you are {chat} but ENV CHAT_ID={CHAT_ID}", timeout=10)
                    except:
                        pass
                if text in ["/start","/check","/status","/running"]:
                    wr = (stats["wins"]/stats["total_trades"]*100) if stats["total_trades"]>0 else 0
                    pos_info = f"{len(positions)} LONG" if positions else "ว่าง"
                    total_qty = sum(abs(p['qty']) for p in positions) if positions else 0
                    avg = sum(p['entry']*abs(p['qty']) for p in positions)/total_qty if total_qty>0 else 0
                    price = get_price_mexc()
                    send_telegram(f"✅ บอท V12.27 รันอยู่\n⏰ {thai_time()}\nPos:{pos_info} Qty:{total_qty} Avg:{avg:.2f} Price:{price}\nWR:{wr:.1f}% Trades:{stats['total_trades']}\nDATA_DIR:{DATA_DIR}\n\nคำสั่ง:\n/open - เปิด LONG\n/open 0.002 - เปิด 0.002\n/close - ปิดทั้งหมด\n/signal - ดูสัญญาณตอนนี้\n/balance /bt /data", chat)
                elif text.startswith("/signal"):
                    # ดูสัญญาณตอนนี้เลย
                    ohlcv = get_klines_mexc(200, '15m')
                    if ohlcv:
                        rows = calc_indicators(ohlcv)
                        if rows:
                            last = rows[-1]
                            buy_signal = (last['EMA12']>last['EMA26'] and last['c']>last['EMA12'] and last['ADX']>20 and 45 <= last['RSI'] <= 85)
                            send_telegram(f"📡 SIGNAL CHECK {thai_time()}\nPrice {last['c']:.2f} EMA12 {last['EMA12']:.2f} EMA26 {last['EMA26']:.2f} EMA200 {last['EMA200']:.2f}\nRSI {last['RSI']:.1f} ADX {last['ADX']:.1f} +DI {last['+DI']:.1f} -DI {last['-DI']:.1f}\n{'✅ READY LONG - พร้อมเปิด' if buy_signal else '⏳ ยังไม่พร้อม รอ EMA12>EMA26 และ RSI 45-85'}\n/open เปิดมือ /close ปิด", chat)
                        else:
                            send_telegram(f"❌ ดึงอินดิเคเตอร์ไม่ได้", chat)
                    else:
                        send_telegram(f"❌ ดึงกราฟไม่ได้", chat)
                elif text.startswith("/open") or text.startswith("/buy") or text.startswith("/long"):
                    parts = text_raw.split()
                    qty = parts[1] if len(parts)>1 else None
                    threading.Thread(target=manual_open, args=(qty,), daemon=True).start()
                    send_telegram(f"⏳ กำลังเปิด LONG {qty or '0.001'} ...", chat)
                elif text.startswith("/close") or text.startswith("/sell"):
                    threading.Thread(target=manual_close, daemon=True).start()
                    send_telegram(f"⏳ กำลังปิด {len(positions)} ไม้ ...", chat)
                elif text == "/health":
                    send_telegram(f"🏥 Health OK ✅\n{thai_time()}\nPos:{len(positions)} Trades:{stats['total_trades']}", chat)
                elif text == "/balance":
                    try:
                        bal = get_balance()
                        price = get_price_mexc()
                        avg = 0
                        upnl = 0
                        if positions:
                            total_qty = sum(abs(p['qty']) for p in positions)
                            avg = sum(p['entry']*abs(p['qty']) for p in positions)/total_qty
                            upnl = (price-avg)/avg*100 if price else 0
                        send_telegram(f"💰 Balance {thai_time()}\nUSDT: ${bal:.2f}\nPrice: {price}\nPos: {len(positions)} Avg:{avg:.2f} uPnL:{upnl:+.2f}%\n/open /close /signal ได้", chat)
                    except Exception as e:
                        send_telegram(f"Balance err {e}", chat)
                elif text == "/data":
                    try:
                        pos_exists = os.path.exists(POSITIONS_FILE)
                        pos_size = os.path.getsize(POSITIONS_FILE) if pos_exists else 0
                        total_qty = sum(abs(p.get('qty',0)) for p in positions) if positions else 0
                        avg = sum(p['entry']*abs(p.get('qty',0)) for p in positions)/total_qty if total_qty>0 else 0
                        send_telegram(f"📁 DATA {thai_time()}\nDATA_DIR:{DATA_DIR}\nPosFile:{'✅' if pos_exists else '❌'} {pos_size}B\nPos:{len(positions)} Qty:{total_qty} Avg:{avg:.2f}", chat)
                    except Exception as e:
                        send_telegram(f"/data err {e}", chat)
                elif text == "/bt":
                    try:
                        wr = (stats["wins"]/stats["total_trades"]*100) if stats["total_trades"]>0 else 0
                        hist = stats.get("history", [])[-5:]
                        hist_txt = "\n".join([f"{h.get('time','')} {h.get('type','')} {h.get('pnl',0):.2f}%" for h in hist]) if hist else "ยังไม่มี"
                        send_telegram(f"📊 BT {thai_time()}\nTrades:{stats['total_trades']} Wins:{stats['wins']} Loss:{stats['losses']}\nWR:{wr:.1f}% Total PnL:{stats.get('total_pnl_pct',0):.2f}%\n\n5 ล่าสุด:\n{hist_txt}", chat)
                    except Exception as e:
                        send_telegram(f"/bt err {e}", chat)
                elif text == "/stop":
                    send_telegram(f"🛑 STOP {thai_time()} - จะไม่เปิดใหม่จน Restart", chat)
                    cooldown_until = time.time() + 3600*24
        except Exception as e:
            print(f"Tele err {e}")
            time.sleep(10)

threading.Thread(target=telegram_check_loop, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
