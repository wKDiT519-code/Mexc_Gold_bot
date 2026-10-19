"""
V12.17 NO-PANDAS TELE FIX + /data /bt RENDER FREE PERSIST - สำหรับคนคุ้นเคย Render.com ไม่อยากย้าย ไม่ต้องซื้อ Disk
แก้ปัญหา Render Free ที่เคยเจอวันนี้:
- Permission denied /mnt/data -> ใช้ ./data ได้ แต่หายตอน Restart
- Position History ว่าง เพราะ positions=[] หาย บอทเลยเปิด LONG ซ้ำ 9 นาที
- Order History มี แต่ PNL 0 เพราะยังไม่ได้ปิด

V12.9 NEW - RENDER FREE PERSIST:
- AUTO RECOVER จาก MEXC จริง: สตาร์ทบอทจะเรียก mexc.fetch_positions() ดึงไม้ LONG 0.001 ที่ค้างบน MEXC มาจำใหม่ ไม่เปิดซ้ำ
- SMART DATA_DIR: /mnt/data ถ้า Paid, ./data ถ้า Free, ถ้าไม่มีทั้งคู่ก็จำในหน่วยความจำ + Recover จาก MEXC
- LONG ONLY 5X Cross ตาม History จริง, EMA12x26 RUN รันยาวไม่ตัดเร็ว
- ใช้บน Render.com Free ได้ถาวร ไม่ต้องจ่าย $7.25
"""

import requests, os, time, threading, json, math
from flask import Flask, jsonify
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
print(f"V12.17 NO-PANDAS TELE FIX + /data /bt RENDER FREE PERSIST - DATA_DIR: {DATA_DIR} | Render.com Familiar Mode")

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

# routes defined later with HEAD support

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
    # keep mexc if already created
    if 'mexc_public' not in locals():
        mexc_public = None
    if 'mexc' not in locals():
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
    """
    RENDER FREE PERSIST - หัวใจสำคัญ
    ดึงไม้ที่ค้างบน MEXC จริงๆ มาเลย กันเปิดซ้ำ 4,154/4,159 แบบเมื่อวาน
    """
    global positions, highest, lowest
    if not mexc:
        print("mexc not configured, skip sync")
        return False
    try:
        print(f"Syncing positions from MEXC {SYMBOL}...")
        poss = mexc.fetch_positions(SYMBOL)
        # ccxt return list
        real_pos = []
        for p in poss:
            # หา position ที่ size !=0
            contracts = float(p.get('contracts', 0) or p.get('contractSize', 0) or 0)
            # บาง exchange ใช้ 'info' ข้างใน
            info = p.get('info', {})
            # MEXC format: holdVol, entry price
            entry_price = float(p.get('entryPrice') or info.get('openAvgPrice') or info.get('holdAvgPrice') or 0)
            qty = abs(contracts)
            if qty == 0:
                # ลองดู info.holdVol
                try:
                    qty = abs(float(info.get('holdVol', 0)))
                except:
                    qty = 0
            if qty > 0 and entry_price > 0:
                # เป็น LONG ถ้า side long
                side = p.get('side') or info.get('positionType', 1)
                is_long = True
                if isinstance(side, str):
                    is_long = 'long' in side.lower() or side == '1'
                else:
                    is_long = int(side) == 1
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
            print("No open positions on MEXC (Position History ว่างเพราะปิดหมดแล้ว)")
            return False
    except Exception as e:
        print(f"sync from MEXC err {e}")
        import traceback
        traceback.print_exc()
        return False

# Load sequence: file first, then MEXC if empty (Render Free)
load_stats()
has_file = load_positions()
if not has_file:
    print("No file positions, trying recover from MEXC (Render Free Persist)...")
    sync_positions_from_mexc()

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

@app.route('/', methods=['GET','HEAD'])
def home():
    if request.method == 'HEAD':
        return '', 200

    wr = (stats["wins"]/stats["total_trades"]*100) if stats["total_trades"]>0 else 0
    tier = "PAID /mnt/data" if DATA_DIR=="/mnt/data" else "FREE Render.com (Recover from MEXC)"
    pos_info = f"{len(positions)} LONG" if positions else "ว่าง"
    return f"V12.17 NO-PANDAS TELE FIX + /data /bt RENDER FREE PERSIST LIVE | {tier} | {DATA_DIR} | Pos:{pos_info} | Trades:{stats['total_trades']} WR:{wr:.1f}% | EMA12x26 RUN"

@app.route('/health', methods=['GET','HEAD'])
def health():
    if request.method == 'HEAD':
        return '', 200
    return jsonify({"status":"ok","version":"V12.9 FULL TELE CHECK","data_dir":DATA_DIR,"positions":len(positions),"trades":stats["total_trades"],"mexc_sync":True,"render":"RUNNING","uptime":time.time()})

@app.route('/stats')
def stats_page():
    wr = (stats["wins"]/stats["total_trades"]*100) if stats["total_trades"]>0 else 0
    hist = "".join([f"{h['time']} {h['type']} {h['pnl']:+.2f}%<br>" for h in stats["history"][-30:]])
    return f"<h2>V12.17 NO-PANDAS TELE FIX + /data /bt RENDER FREE PERSIST - 5X LONG ONLY - {DATA_DIR}</h2>Pos:{len(positions)} Trades:{stats['total_trades']} WR:{wr:.1f}%<br><hr>{hist}"

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
    try:
        if mexc_public:
            ohlcv = mexc_public.fetch_ohlcv(SYMBOL, timeframe=timeframe, limit=limit)
            return ohlcv
    except Exception as e:
        print(f"Kline err {e}")
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
    # ohlcv = list of [timestamp, open, high, low, close, volume]
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


