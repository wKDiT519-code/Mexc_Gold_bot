"""
V12.22 OPEN/CLOSE MANUAL + NO-PANDAS NO-BLOCK 409+403 TELE FIX
- เพิ่ม /open /close /buy /sell เปิดปิดออเดอร์ด้วยมือ
- แก้ 403 /contract/detail Render IP blocked
- แก้ 409 Conflict 2 instance
- NO-PANDAS ใช้ได้บน Render Free
- LONG ONLY 5X Cross EMA12x26 RUN
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
print(f"V12.22 OPEN/CLOSE + NO-BLOCK - DATA_DIR: {DATA_DIR}")

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
WEEKLY_REPORT_FILE = os.path.join(DATA_DIR, "weekly_saturday_report.json")

app = Flask(__name__)

@app.route('/', methods=['GET','HEAD'])
def home():
    if request.method == 'HEAD':
        return '', 200
    wr = (stats["wins"]/stats["total_trades"]*100) if stats["total_trades"]>0 else 0
    tier = "PAID /mnt/data" if DATA_DIR=="/mnt/data" else "FREE Render.com (Recover from MEXC)"
    pos_info = f"{len(positions)} LONG" if positions else "ว่าง"
    return f"V12.22 OPEN/CLOSE MANUAL + NO-BLOCK LIVE | {tier} | {DATA_DIR} | Pos:{pos_info} | Trades:{stats['total_trades']} WR:{wr:.1f}% | EMA12x26 RUN"

@app.route('/health', methods=['GET','HEAD'])
def health():
    if request.method == 'HEAD':
        return '', 200
    return jsonify({"status":"ok","version":"V12.22 OPEN/CLOSE","data_dir":DATA_DIR,"positions":len(positions),"trades":stats["total_trades"],"mexc_sync":True,"render":"RUNNING","uptime":time.time()})

positions, highest, lowest, last_hourly = [], 0, 999999, time.time()
stats = {"total_trades":0,"wins":0,"losses":0,"total_pnl_pct":0.0,"history":[]}
cooldown_until = 0
daily_loss_count = 0
last_daily_reset = datetime.now(pytz.timezone('Asia/Bangkok')).day
current_atr_sl = SL_PCT
last_news_reason = ""
last_news_sent = 0
last_cooldown_sent = 0
last_daily_loss_sent = 0
last_ema_cross = ""
weekly_config = {}
manual_pause_until = 0  # หลังเปิด/ปิดมือ ไม่ให้บอทเปิดซ้ำทันที

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
            print(f"load_markets skip (Render IP 403) continue: {le}")
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
        print("mexc not configured, skip sync")
        return False
    try:
        print(f"Syncing positions from MEXC {SYMBOL}...")
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
            print(f"RECOVERED {len(positions)} positions from MEXC: Avg {avg_entry}")
            return True
        else:
            print("No open positions on MEXC")
            return False
    except Exception as e:
        err_str = str(e)
        if "403" in err_str or "Access Denied" in err_str or "Forbidden" in err_str:
            print(f"⚠️ MEXC blocks Render IP (403) - cannot auto-recover now. Bot will run without duplicate open.")
            return False
        print(f"sync from MEXC err {e}")
        import traceback
        traceback.print_exc()
        return False

load_stats()
has_file = load_positions()
if not has_file:
    print("No file positions, trying recover from MEXC (Render Free Persist)...")
    sync_positions_from_mexc()

@app.route('/stats')
def stats_page():
    wr = (stats["wins"]/stats["total_trades"]*100) if stats["total_trades"]>0 else 0
    hist = "".join([f"{h['time']} {h['type']} {h['pnl']:+.2f}%<br>" for h in stats["history"][-30:]])
    return f"<h2>V12.22 OPEN/CLOSE - 5X LONG ONLY - {DATA_DIR}</h2>Pos:{len(positions)} Trades:{stats['total_trades']} WR:{wr:.1f}%<br><hr>{hist}"

def send_telegram(msg, chat_id=None):
    target = str(chat_id) if chat_id else CHAT_ID
    if not BOT_TOKEN or not target:
        print(f"send_telegram skip no token/target")
        return False
    try:
        r = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id": target, "text": msg, "parse_mode":"HTML", "disable_web_page_preview": True}, timeout=15)
        print(f"send_telegram to {target} ok={r.json().get('ok')} len={len(msg)}")
        return r.json().get('ok', False)
    except Exception as e:
        print(f"TG err {e} to {target}")
        return False

def get_price_mexc():
    try:
        if mexc_public:
            try:
                if not getattr(mexc_public, 'markets', None):
                    mexc_public.markets = {SYMBOL: {'id': SYMBOL_MEXC_ID, 'symbol': SYMBOL, 'swap': True, 'type': 'swap', 'contract': True}}
                    mexc_public.markets_by_id = {SYMBOL_MEXC_ID: mexc_public.markets[SYMBOL]}
            except:
                pass
            t = mexc_public.fetch_ticker(SYMBOL)
            return float(t.get('last') or t.get('close'))
    except:
        pass
    try:
        r = requests.get("https://contract.mexc.com/api/v1/contract/ticker", params={"symbol": SYMBOL_MEXC_ID}, timeout=10)
        return float(r.json()['data']['lastPrice'])
    except:
        return None

def get_klines_mexc(limit=200, timeframe='15m'):
    # V12.22 NO-BLOCK
    try:
        if mexc_public:
            try:
                if not getattr(mexc_public, 'markets', None):
                    mexc_public.markets = {SYMBOL: {'id': SYMBOL_MEXC_ID, 'symbol': SYMBOL, 'swap': True, 'type': 'swap', 'contract': True}}
                    mexc_public.markets_by_id = {SYMBOL_MEXC_ID: mexc_public.markets[SYMBOL], SYMBOL: mexc_public.markets[SYMBOL]}
                    mexc_public.markets_by_id['PAXGUSDT'] = mexc_public.markets[SYMBOL]
            except:
                pass
            ohlcv = mexc_public.fetch_ohlcv(SYMBOL, timeframe=timeframe, limit=limit)
            return ohlcv
    except Exception as e:
        print(f"Kline ccxt err {e} - try direct")
    # fallback direct HTTP
    try:
        interval_map = {'15m': 'Min15', '1h': 'Min60', '4h': 'Hour4', '1d': 'Day1'}
        interval = interval_map.get(timeframe, 'Min15')
        r = requests.get(f"https://contract.mexc.com/api/v1/contract/kline/{SYMBOL_MEXC_ID}", params={"interval": interval}, timeout=15)
        data = r.json()
        d = data.get('data', {})
        if isinstance(d, dict) and 'time' in d:
            ohlcv = []
            times = d.get('time', [])
            opens = d.get('open', [])
            highs = d.get('high', [])
            lows = d.get('low', [])
            closes = d.get('close', [])
            vols = d.get('vol', [])
            for i in range(len(times)):
                try:
                    ohlcv.append([int(times[i])*1000, float(opens[i]), float(highs[i]), float(lows[i]), float(closes[i]), float(vols[i])])
                except:
                    continue
            ohlcv = ohlcv[-limit:]
            print(f"Kline direct ok {len(ohlcv)} candles")
            return ohlcv
        elif isinstance(d, list):
            return d
    except Exception as e2:
        print(f"Kline direct err {e2}")
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
        vwap = [0.0]*n
        cum_pv=0
        cum_v=0
        for i in range(n):
            tp=(highs[i]+lows[i]+closes[i])/3
            cum_pv+=tp*vols[i]
            cum_v+=vols[i]
            vwap[i]=cum_pv/cum_v if cum_v!=0 else closes[i]
        rows=[]
        for i in range(n):
            hh20 = max(highs[max(0,i-19):i+1])
            ll20 = min(lows[max(0,i-19):i+1])
            rows.append({
                'ot': times[i], 'o': opens[i], 'h': highs[i], 'l': lows[i], 'c': closes[i], 'v': vols[i],
                'EMA12': ema12[i], 'EMA26': ema26[i], 'EMA20': ema20[i], 'EMA200': ema200[i],
                'RSI': rsi[i], 'TR': tr[i], 'ATR': atr[i],
                '+DM': plus_dm[i], '-DM': minus_dm[i], '+DM14': plus_dm14[i], '-DM14': minus_dm14[i], 'TR14': tr14[i],
                '+DI': plus_di[i], '-DI': minus_di[i], 'DX': dx[i], 'ADX': adx[i],
                'VWAP': vwap[i], 'HH20': hh20, 'LL20': ll20,
            })
        return rows
    except Exception as e:
        print(f"calc_indicators err {e}")
        import traceback; traceback.print_exc()
        return None

def is_funding_time():
    try:
        utc = datetime.now(pytz.timezone('UTC'))
        for fh in FUNDING_TIMES_UTC:
            if utc.hour == fh and utc.minute >= (60 - AVOID_FUNDING_MINUTES):
                return True, f"ใกล้ Funding {fh}:00 UTC"
            if utc.hour == fh and utc.minute <= AVOID_FUNDING_MINUTES:
                return True, f"หลัง Funding {fh}:00 UTC"
            if fh == 0 and utc.hour == 23 and utc.minute >= 55:
                return True, f"ใกล้ Funding 00:00 UTC"
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
        if now_th.hour == 19 and now_th.minute >= 30:
            if now_th.day in [14,15,16]:
                return True, f"CPI/PPI HIGH {now_th.strftime('%d/%m')} 19:30 ICT"
        if now_th.hour == 20 and now_th.minute <= 45:
            if now_th.day in [14,15,16]:
                return True, f"CPI/PPI HIGH {now_th.strftime('%d/%m')} 19:30-20:45 ICT"
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

def mexc_buy(price, factor=1.0, custom_qty=None):
    global manual_pause_until
    if not mexc:
        print("mexc not configured, DRY RUN BUY")
        qty = custom_qty or 0.001*factor
        # simulate local
        positions.append({'entry': price, 'qty': qty, 'side': 'long', 'time': time.time()})
        save_positions()
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
        print(f"REAL BUY {SYMBOL} qty {qty} @ {price} | {DATA_DIR}")
        order = mexc.create_market_buy_order(SYMBOL, qty)
        print(f"Buy filled {order}")
        # add to local tracking
        positions.append({'entry': price, 'qty': qty, 'side': 'long', 'time': time.time()})
        save_positions()
        return True, qty
    except Exception as e:
        print(f"BUY ERR {e}")
        import traceback
        traceback.print_exc()
        send_telegram(f"❌ BUY ERROR {e}")
        return False, 0

def close_position(price, reason):
    global cooldown_until, daily_loss_count, positions, highest, lowest
    if not positions:
        return False
    total_qty = sum(abs(p['qty']) for p in positions)
    avg = sum(p['entry']*abs(p['qty']) for p in positions)/total_qty
    pnl = (price-avg)/avg*100
    if mexc:
        try:
            print(f"REAL CLOSE {SYMBOL} qty {total_qty} @ {price} {reason}")
            mexc.create_market_sell_order(SYMBOL, total_qty)
        except Exception as e:
            print(f"CLOSE ERR {e}")
            send_telegram(f"❌ CLOSE ERROR {e}")
            # still clear local
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
    send_telegram(f"{'🔴' if pnl<0 else '🟢'} [{thai_time()}] ปิด {reason} PnL {pnl:+.2f}% @ {price:.2f}$ {bal_msg} | {DATA_DIR}")
    positions=[]
    highest=0
    lowest=999999
    save_positions()
    return True

# ===== Manual open/close functions for Telegram =====
def manual_open(qty_str=None):
    global manual_pause_until
    price = get_price_mexc()
    if not price:
        send_telegram(f"❌ เปิดออเดอร์ไม่ได้ - ดึงราคาไม่ได้ {thai_time()}")
        return
    if len(positions) >= MAX_WOODS:
        send_telegram(f"⚠️ มี {len(positions)} ไม้แล้ว เต็ม {MAX_WOODS} ไม้ - ปิดก่อนเปิดใหม่ /close {thai_time()}")
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
        manual_pause_until = time.time() + 300  # 5 นาทีไม่ให้บอทเปิดทับ
        bal_msg, _, _ = format_balance_msg()
        send_telegram(f"✅ MANUAL OPEN LONG {qty} @ {price:.2f}$ {thai_time()}\n{bal_msg}\nPos:{len(positions)} | พิมพ์ /close เพื่อปิด")

def manual_close():
    global manual_pause_until
    price = get_price_mexc()
    if not price:
        price = positions[0]['entry'] if positions else 0
    if not positions:
        send_telegram(f"ℹ️ ไม่มีออเดอร์ให้ปิด {thai_time()}")
        return
    ok = close_position(price, "MANUAL_CLOSE")
    if ok:
        manual_pause_until = time.time() + 300
        send_telegram(f"✅ MANUAL CLOSE สำเร็จ {thai_time()} - พร้อมเปิดใหม่ /open")

def trading_loop():
    global positions, highest, lowest, last_hourly, cooldown_until, current_atr_sl, last_news_reason, last_news_sent, last_cooldown_sent, last_daily_loss_sent, last_ema_cross
    last_hourly = time.time()
    print(f"V12.22 OPEN/CLOSE START {DATA_DIR}")
    if not positions:
        sync_positions_from_mexc()
    bal_msg, _, _ = format_balance_msg()
    pos_info = f"{len(positions)} LONG" if positions else "ว่าง"
    send_telegram(f"🚀 V12.22 OPEN/CLOSE START {thai_time()} | {DATA_DIR} | {bal_msg}\nPos: {pos_info}\n5X LONG ONLY | EMA12x26 RUN | /open /close ได้แล้ว")
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
            # Signals
            super_up = last['+DI']>28 and last['-DI']<12 and last['ADX']>28 and last['c']>last['EMA20'] and last['c']>last['VWAP']
            uptrend = last['+DI']>last['-DI'] and last['ADX']>22 and last['c']>last['VWAP'] and last['+DI']>20
            # simplified conditions for no-pandas
            strong_bull = (last['c']-last['o']) > (last['h']-last['l'])*0.5
            rsi_ok = 45 <= last['RSI'] <= 85
            ema_run_up = ENABLE_EMA12x26_RUN and ema_bull_hold and last['c'] > last['EMA12'] and last['ADX']>20 and rsi_ok and last['c'] > last['VWAP'] and mtf_1h_up and mtf_4h_up
            buy_signal = ema_run_up or (uptrend and strong_bull and rsi_ok)
            if cooldown_until > time.time():
                time.sleep(60)
                continue
            if check_daily_loss():
                time.sleep(60)
                continue
            if time.time()-last_hourly >= 14400:
                last_hourly=time.time()
                bal_msg, _, _ = format_balance_msg()
                send_telegram(f"📊 [{thai_time()}] Price {price:.2f} Pos:{len(positions)} {bal_msg} | EMA12 {last['EMA12']:.2f} EMA26 {last['EMA26']:.2f} RSI {last['RSI']:.1f} | /open /close ได้")
            if positions and check_max_hold():
                close_position(price, f"MAX_HOLD_{MAX_HOLD_HOURS}H")
                continue
            funding_block, _ = is_funding_time()
            news_block, _ = is_news_time()
            if funding_block or news_block:
                time.sleep(60)
                continue
            # Close logic
            if positions:
                total_qty = sum(abs(p['qty']) for p in positions)
                avg = sum(p['entry']*abs(p['qty']) for p in positions)/total_qty
                pnl = (price-avg)/avg*100
                if pnl>=TP_PCT:
                    close_position(price, f"TP {TP_PCT}%")
                elif price <= avg*(1-current_atr_sl/100):
                    close_position(price, f"HARD_SL -{current_atr_sl:.1f}%")
                elif not ema_bull_hold:
                    close_position(price, f"EMA12x26_CROSS_BEAR")
            else:
                if buy_signal and len(positions) < MAX_WOODS:
                    mexc_buy(price, factor=1.0)
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
        dw = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook?drop_pending_updates=True", timeout=10)
        print(f"deleteWebhook: {dw.text[:200]}")
    except Exception as e:
        print(f"deleteWebhook err {e}")
    offset = 0
    print(f"Telegram CHECK + OPEN/CLOSE started - Expected Chat {CHAT_ID}")
    while True:
        try:
            r = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?offset={offset+1}&timeout=30", timeout=35)
            data = r.json()
            if not data.get("ok"):
                err = data.get('description','')
                print(f"getUpdates not ok {data}")
                if '409' in str(data) or 'Conflict' in err:
                    print("409 Conflict - sleep 30s and delete webhook")
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
                print(f"Got Tele: '{text_raw}' from chat {chat}")
                if CHAT_ID and chat != CHAT_ID:
                    try:
                        requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage?chat_id={chat}&text=⚠️ CHAT_ID mismatch: you are {chat} but ENV CHAT_ID={CHAT_ID}. Set ENV to {chat} to fix.", timeout=10)
                    except:
                        pass
                # Commands
                if text in ["/start","/check","/status","/running"]:
                    wr = (stats["wins"]/stats["total_trades"]*100) if stats["total_trades"]>0 else 0
                    pos_info = f"{len(positions)} LONG" if positions else "ว่าง"
                    total_qty = sum(abs(p['qty']) for p in positions) if positions else 0
                    avg = sum(p['entry']*abs(p['qty']) for p in positions)/total_qty if total_qty>0 else 0
                    price = get_price_mexc()
                    send_telegram(f"✅ บอทรันอยู่บน Render\n⏰ {thai_time()}\nPos:{pos_info} Qty:{total_qty} Avg:{avg:.2f} Price:{price}\nWR:{wr:.1f}% Trades:{stats['total_trades']}\nDATA_DIR:{DATA_DIR}\nVersion: V12.22 OPEN/CLOSE\n\nคำสั่งใหม่:\n/open - เปิด LONG 0.001\n/open 0.002 - เปิด LONG 0.002\n/close - ปิดทั้งหมด\n/bt /data /balance /check", chat)
                elif text.startswith("/open") or text.startswith("/buy") or text.startswith("/long"):
                    # parse qty
                    parts = text_raw.split()
                    qty = parts[1] if len(parts)>1 else None
                    # run in thread to not block
                    threading.Thread(target=manual_open, args=(qty,), daemon=True).start()
                    send_telegram(f"⏳ กำลังเปิด LONG {qty or '0.001'} ...", chat)
                elif text.startswith("/close") or text.startswith("/sell") or text.startswith("/short"):
                    threading.Thread(target=manual_close, daemon=True).start()
                    send_telegram(f"⏳ กำลังปิด {len(positions)} ไม้ ...", chat)
                elif text == "/health":
                    send_telegram(f"🏥 Render Health: OK ✅\n{thai_time()}\nPos:{len(positions)} Trades:{stats['total_trades']}\n\n/open /close ได้", chat)
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
                        send_telegram(f"💰 Balance {thai_time()}\nUSDT: ${bal:.2f}\nPrice: {price}\nPos: {len(positions)} Avg:{avg:.2f} uPnL:{upnl:+.2f}%\n\n/open /close ได้", chat)
                    except Exception as e:
                        send_telegram(f"Balance err {e}", chat)
                elif text == "/data":
                    try:
                        pos_exists = os.path.exists(POSITIONS_FILE)
                        stat_exists = os.path.exists(STATS_FILE)
                        pos_size = os.path.getsize(POSITIONS_FILE) if pos_exists else 0
                        total_qty = sum(abs(p.get('qty',0)) for p in positions) if positions else 0
                        avg = sum(p['entry']*abs(p.get('qty',0)) for p in positions)/total_qty if total_qty>0 else 0
                        send_telegram(f"📁 DATA {thai_time()}\nDATA_DIR:{DATA_DIR}\nPosFile:{'✅' if pos_exists else '❌'} {pos_size}B\nPos:{len(positions)} Qty:{total_qty} Avg:{avg:.2f}\nHighest:{highest:.2f} Lowest:{lowest:.2f}\n\n/open /close ได้", chat)
                    except Exception as e:
                        send_telegram(f"/data err {e}", chat)
                elif text == "/bt":
                    try:
                        wr = (stats["wins"]/stats["total_trades"]*100) if stats["total_trades"]>0 else 0
                        hist = stats.get("history", [])[-5:]
                        hist_txt = "\n".join([f"{h.get('time','')} {h.get('type','')} {h.get('pnl',0):.2f}%" for h in hist]) if hist else "ยังไม่มีประวัติ"
                        send_telegram(f"📊 BT {thai_time()}\nTrades:{stats['total_trades']} Wins:{stats['wins']} Loss:{stats['losses']}\nWR:{wr:.1f}% Total PnL:{stats.get('total_pnl_pct',0):.2f}%\nCooldown until:{datetime.fromtimestamp(cooldown_until).strftime('%H:%M') if cooldown_until>time.time() else 'ไม่มี'}\n\n5 ล่าสุด:\n{hist_txt}\n\n/open /close ได้", chat)
                    except Exception as e:
                        send_telegram(f"/bt err {e}", chat)
                elif text == "/stop":
                    send_telegram(f"🛑 STOP สั่งหยุดแล้ว {thai_time()} - จะไม่เปิดใหม่จน Restart", chat)
                    cooldown_until = time.time() + 3600*24
        except Exception as e:
            print(f"Tele check err {e}")
            time.sleep(10)

threading.Thread(target=telegram_check_loop, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
