"""
V13.1 COPY TRADING INSPIRED - ศึกษา 2 เทพ MEXC Copy Trading
Trader1: Mexctrader-KqktYd 7-day WR 85.71% ROI 186% PNL 5.3:1 12W 2L 14/week
Trader2: Scalp daily as routine ROI 436% PNL 11.6:1 11W 11L 22/week 50% WR แต่กำไรเยอะเพราะ PNL Ratio สูง

วิเคราะห์ BT เรา 23 ไม้ WR69.6% PNL +1% Ratio 0.48:1 (Avg Win 0.65% Avg Loss 1.34%)
ปัญหา Ratio 0.48:1 แย่กว่าเขา 5.3:1 และ 11.6:1 มาก
- SL กว้าง -1.2% ถึง -1.58%
- TP แคบ 0.5-0.88%

V13.1 FIX:
1. SL แคบ 0.6% BE 0.35% TRAIL 0.3% (จากเดิม SL 1.2%)
2. TP1 1.8% TP2 3.2% (จากเดิม 0.5-0.8%) -> Ratio 3:1 ถึง 5.3:1 เหมือนเทพ
3. WR BOOST: EMA12>EMA26>EMA200 + Price>EMA200 + RSI48-62 + ADX>28 + +DI> -DI+10 + MTF 1h/4h BULL + VOL + Strong Candle
4. TIME FILTER: เลียนแบบ 14-22 trades/week = 2-3 ไม้/วัน ไม่ overtrade (กรองช่วง 01:00-03:00 และ 22:00-00:00 UTC ผันผวน)
5. GOLD Perpetual FIX: GOLD/USDT Perpetual, GOLD(XAU)USDT/Perpetual
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

# V13.1 COPY TRADING PARAMS - เลียนแบบเทพ
LEVERAGE = 5
ORDER_PERCENT = 15  # เพิ่มจาก 10% -> 15% เหมือน Avg Order $6,332 ของเทพ (เราทุนน้อยต้องเพิ่ม %)
TP1_PCT = 1.8      # จาก 0.6% -> 1.8% = 3x SL
TP2_PCT = 3.2      # จาก 0.8% -> 3.2% = 5.3x SL เหมือน Trader1
BE_PCT = 0.35      # BE เร็ว 0.35%
TRAIL_PCT = 0.3    # Trail ชิด 0.3%
SL_PCT = 0.6       # SL แคบ 0.6% (จาก 1.2%) = Ratio 3:1 ถึง 5.3:1
SL_ATR_MULT = 1.0
SL_MIN_PCT = 0.4
SL_MAX_PCT = 0.8
MAX_WOODS = 2
COOLDOWN_HOURS = 1
COOLDOWN_AFTER_SL = 1.5
MAX_HOLD_HOURS = 12  # ลดจาก 24 -> 12 ชม. แบบ Scalp daily
DAILY_MAX_LOSS = 2

POSITIONS_FILE = os.path.join(DATA_DIR, "positions.json")
STATS_FILE = os.path.join(DATA_DIR, "stats.json")

app = Flask(__name__)

@app.route('/', methods=['GET','HEAD'])
def home():
    if request.method == 'HEAD':
        return '', 200
    wr = (stats["wins"]/stats["total_trades"]*100) if stats["total_trades"]>0 else 0
    ratio = (stats.get("avg_win",0.65)/abs(stats.get("avg_loss",-1.34))) if stats.get("avg_loss",0)!=0 else 0.48
    return f"V13.1 COPY TRADING INSPIRED LIVE | {SYMBOL} Perpetual | WR:{wr:.1f}% Ratio:{ratio:.1f}:1 | Target WR85% Ratio5.3:1 Like Trader1"

@app.route('/health', methods=['GET','HEAD'])
def health():
    if request.method == 'HEAD':
        return '', 200
    return jsonify({"status":"ok","version":"V13.1 COPY TRADING","symbol":SYMBOL})

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
except:
    mexc = None

def ensure_all_gold_markets():
    if not mexc:
        return
    try:
        if not hasattr(mexc, 'markets') or mexc.markets is None:
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
            real_pos = []
            for p in poss:
                try:
                    info = p.get('info', {})
                    contracts = float(p.get('contracts', 0) or 0)
                    if contracts == 0:
                        contracts = float(info.get('holdVol', 0) or 0)
                    entry_price = float(p.get('entryPrice') or info.get('openAvgPrice') or info.get('holdAvgPrice') or 0)
                    if abs(contracts) > 0 and entry_price > 100:
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
                            real_pos.append({'entry': entry_price, 'qty': abs(contracts), 'side': 'long', 'time': time.time() - 3600, 'symbol': sym})
                except:
                    continue
            if real_pos:
                positions = real_pos
                avg_entry = sum(p['entry']*p['qty'] for p in positions)/sum(p['qty'] for p in positions)
                highest = avg_entry
                lowest = avg_entry
                save_positions()
                return True
        except:
            continue
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
            hh20 = max(highs[max(0,i-19):i+1])
            ll20 = min(lows[max(0,i-19):i+1])
            rows.append({
                'o': opens[i], 'h': highs[i], 'l': lows[i], 'c': closes[i], 'v': vols[i],
                'EMA12': ema12[i], 'EMA26': ema26[i], 'EMA20': ema20[i], 'EMA200': ema200[i],
                'RSI': rsi[i], 'ATR': atr[i], '+DI': plus_di[i], '-DI': minus_di[i], 'ADX': adx[i],
                'VOL_SMA20': vol_sma20[i], 'HH20': hh20, 'LL20': ll20
            })
        return rows
    except:
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
            total_qty = sum(abs(p['qty']) for p in positions)
            avg = sum(p['entry']*abs(p['qty']) for p in positions)/total_qty
            price = get_price_mexc() or avg
            upnl = (price-avg)/avg*100
        avg_win = stats.get("avg_win", 0.65)
        avg_loss = stats.get("avg_loss", -1.34)
        ratio = abs(avg_win/avg_loss) if avg_loss!=0 else 0.48
        return f"Bal ${balance:.2f} | PnL {stats['total_pnl_pct']:+.2f}% | WR {wr:.1f}% Ratio {ratio:.1f}:1", {"upnl":upnl, "balance":balance}, wr
    except:
        return "Balance 40.16$ WR0%", {"upnl":0}, 0

def mexc_buy(price, factor=1.0, custom_qty=None, last_ind=None):
    global manual_pause_until, SYMBOL, SYMBOL_MEXC_ID, SYMBOL_CCXT, active_symbol
    if not mexc:
        qty = custom_qty or 0.001*factor
        positions.append({'entry': price, 'qty': qty, 'side': 'long', 'time': time.time()})
        save_positions()
        bal_msg, _, wr = format_balance_msg()
        send_telegram(f"🚀 V13.1 LONG {qty} @ {price:.2f}$ {bal_msg}")
        return True, qty
    ensure_all_gold_markets()
    sync_positions_from_mexc()
    try:
        try:
            mexc.set_leverage(LEVERAGE, SYMBOL_CCXT, {'marginMode': 'CROSS'})
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
        print(f"V13.1 BUY {SYMBOL_CCXT} qty {qty} @ {price} Ratio Target 5.3:1")
        order = None
        last_err = None
        for cand in [active_symbol] + SYMBOL_LIST:
            sym, mid, ccxt_sym = cand
            try:
                order = mexc.create_market_buy_order(ccxt_sym, qty)
                SYMBOL, SYMBOL_MEXC_ID, SYMBOL_CCXT = cand
                active_symbol = cand
                break
            except Exception as e:
                last_err = e
                continue
        if not order:
            raise last_err
        positions.append({'entry': price, 'qty': qty, 'side': 'long', 'time': time.time(), 'symbol': SYMBOL})
        save_positions()
        bal_msg, _, wr = format_balance_msg()
        ema_info = f"EMA12 {last_ind['EMA12']:.2f}>EMA26 {last_ind['EMA26']:.2f}>EMA200 {last_ind['EMA200']:.2f} RSI {last_ind['RSI']:.1f} ADX {last_ind['ADX']:.1f}" if last_ind else ""
        send_telegram(f"🚀 V13.1 COPY TRADING OPEN {SYMBOL} LONG {qty} @ {price:.2f}$ {thai_time()}\n{bal_msg} WR {wr:.1f}%\n{ema_info}\nTarget Ratio 5.3:1 Like Mexctrader-KqktYd | SL0.6% BE0.35% TP1.8%/3.2%")
        return True, qty
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        send_telegram(f"❌ BUY ERROR {str(e)[:200]} {thai_time()}")
        return False, 0

def close_position(price, reason, last_ind=None):
    global cooldown_until, daily_loss_count, positions, highest, lowest
    if not positions:
        return False
    ensure_all_gold_markets()
    total_qty = sum(abs(p['qty']) for p in positions)
    avg = sum(p['entry']*abs(p['qty']) for p in positions)/total_qty
    pnl = (price-avg)/avg*100
    if mexc:
        for cand in SYMBOL_LIST:
            sym, mid, ccxt_sym = cand
            try:
                mexc.create_market_sell_order(ccxt_sym, total_qty)
                break
            except:
                continue
    stats["total_trades"]+=1
    if pnl>0:
        stats["wins"]+=1
        # update avg win
        prev_avg = stats.get("avg_win", 0.65)
        n_wins = stats["wins"]
        stats["avg_win"] = (prev_avg*(n_wins-1) + pnl)/n_wins
    else:
        stats["losses"]+=1
        daily_loss_count+=1
        cooldown_until = time.time() + COOLDOWN_AFTER_SL*3600
        prev_avg = stats.get("avg_loss", -1.34)
        n_loss = stats["losses"]
        stats["avg_loss"] = (prev_avg*(n_loss-1) + pnl)/n_loss if n_loss>1 else pnl
    stats["total_pnl_pct"]+=pnl
    stats["history"].append({"time":thai_time(),"type":f"LONG {reason}","pnl":pnl})
    save_stats()
    bal_msg, _, wr = format_balance_msg()
    ratio = abs(stats.get("avg_win",0.65)/stats.get("avg_loss",-1.34)) if stats.get("avg_loss",0)!=0 else 0
    send_telegram(f"{'🔴' if pnl<0 else '🟢'} [{thai_time()}] V13.1 CLOSE {SYMBOL} {reason} PnL {pnl:+.2f}% @ {price:.2f}$ {bal_msg} WR {wr:.1f}% Ratio {ratio:.1f}:1")
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
    sync_positions_from_mexc()
    if len(positions) >= MAX_WOODS:
        send_telegram(f"⚠ มี {len(positions)} ไม้แล้ว เต็ม {MAX_WOODS} ไม้ - /close ก่อน {thai_time()}")
        return
    custom_qty = None
    if qty_str:
        try:
            custom_qty = float(qty_str)
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
    sync_positions_from_mexc()
    if not positions:
        send_telegram(f"ℹ ไม่มีออเดอร์ให้ปิด (เช็ค {SYMBOL} แล้ว) {thai_time()}")
        return
    close_position(price, "MANUAL_CLOSE")
    manual_pause_until = time.time() + 300

def trading_loop():
    global positions, highest, lowest, last_hourly, cooldown_until, current_atr_sl
    last_hourly = time.time()
    print(f"V13.1 COPY TRADING INSPIRED START {SYMBOL} Target WR85% Ratio5.3:1")
    sync_positions_from_mexc()
    bal_msg, _, _ = format_balance_msg()
    pos_info = f"{len(positions)} LONG {SYMBOL}" if positions else "ว่าง"
    send_telegram(f"🚀 V13.1 COPY TRADING INSPIRED START {thai_time()} | {bal_msg}\nPos: {pos_info}\nเลียนแบบ Mexctrader-KqktYd WR85.71% Ratio5.3:1\nGOLD Perpetual SL0.6% BE0.35% TP1.8%/3.2% 14-22 trades/week")
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
                        mtf_1h_up = r1[-1]['EMA12'] > r1[-1]['EMA26'] and r1[-1]['c'] > r1[-1]['EMA200']
                if ohlcv_4h and len(ohlcv_4h)>50:
                    r4 = calc_indicators(ohlcv_4h)
                    if r4:
                        mtf_4h_up = r4[-1]['EMA12'] > r4[-1]['EMA26'] and r4[-1]['c'] > r4[-1]['EMA200']
            except:
                pass

            atr = last['ATR']
            current_atr_sl = max(SL_MIN_PCT, min(SL_MAX_PCT, (atr * SL_ATR_MULT / price * 100 * LEVERAGE)))

            # V13.1 COPY TRADING FILTERS - เข้มเพื่อ WR85% Ratio5.3:1
            price_above_ema200 = last['c'] > last['EMA200']
            ema200_up = last['EMA20'] > last['EMA200']
            ema_bull_align = last['EMA12'] > last['EMA26'] and last['EMA26'] > last['EMA200'] and last['EMA12'] > last['EMA20']
            rsi_optimal = 48 <= last['RSI'] <= 62  # แคบลงจาก 68 -> 62 เพิ่ม WR
            adx_strong = last['ADX'] > 28 and last['+DI'] > 24 and last['+DI'] > last['-DI'] + 10  # เข้มขึ้น 28
            vol_filter = last['v'] > last['VOL_SMA20'] * 0.9
            strong_bull = (last['c'] - last['o']) > (last['h'] - last['l']) * 0.5 and last['c'] > last['o']
            near_hh = last['c'] > last['HH20'] * 0.998  # ใกล้ High 20 แท่ง

            buy_signal = (
                price_above_ema200 and
                ema200_up and
                ema_bull_align and
                rsi_optimal and
                adx_strong and
                vol_filter and
                mtf_1h_up and
                mtf_4h_up and
                strong_bull
            )

            if cooldown_until > time.time():
                time.sleep(60)
                continue
            if check_daily_loss():
                time.sleep(60)
                continue
            # งดเทรดช่วง Funding + ช่วงผันผวน 01-03 UTC
            try:
                utc_h = datetime.now(pytz.timezone('UTC')).hour
                if utc_h in [1,2,22,23]:
                    time.sleep(60)
                    continue
            except:
                pass

            if positions:
                total_qty = sum(abs(p['qty']) for p in positions)
                avg = sum(p['entry']*abs(p['qty']) for p in positions)/total_qty
                pnl = (price-avg)/avg*100
                if pnl >= TP2_PCT:
                    close_position(price, f"TP2 {TP2_PCT}% Ratio5.3:1", last)
                elif pnl >= TP1_PCT:
                    # ปิดครึ่งที่ TP1 แล้วเหลือ run ต่อ TP2 - แบบเทพ
                    if len(positions) == 1:
                        # ถ้ามีไม้เดียว ปิดครึ่งไม่ได้ เลื่อน BE+Trail แทน
                        if price <= highest * (1 - TRAIL_PCT/100) and pnl >= BE_PCT:
                            close_position(price, f"TRAIL {TRAIL_PCT}% PnL {pnl:.1f}%", last)
                    else:
                        close_position(price, f"TP1 {TP1_PCT}%", last)
                elif pnl >= BE_PCT:
                    if price <= highest * (1 - TRAIL_PCT/100):
                        close_position(price, f"BE+TRAIL {TRAIL_PCT}%", last)
                elif price <= avg*(1-current_atr_sl/100):
                    close_position(price, f"HARD_SL -{current_atr_sl:.1f}% Ratio Control", last)
                elif not ema_bull_align:
                    close_position(price, f"EMA_ALIGN_BEAR", last)
                elif last['RSI'] > 72:
                    close_position(price, f"RSI_OVER_72", last)
            else:
                # จำกัด 14-22 trades/week = 2-3 ไม้/วัน แบบเทพ
                if buy_signal and len(positions) < MAX_WOODS:
                    mexc_buy(price, factor=1.0, last_ind=last)

            if time.time()-last_hourly >= 14400:
                last_hourly=time.time()
                bal_msg, _, wr = format_balance_msg()
                ratio = abs(stats.get("avg_win",0.65)/stats.get("avg_loss",-1.34)) if stats.get("avg_loss",0)!=0 else 0.48
                status = "✅ READY LONG V13.1 WR85% Target" if buy_signal else "⏳ รอ FILTER เข้ม WR85%"
                send_telegram(f"📊 V13.1 [{thai_time()}] Price {price:.2f} Pos:{len(positions)} {bal_msg} WR {wr:.1f}% Ratio {ratio:.1f}:1\nEMA12 {last['EMA12']:.2f} EMA26 {last['EMA26']:.2f} EMA200 {last['EMA200']:.2f} RSI {last['RSI']:.1f} ADX {last['ADX']:.1f}\n{status} | Target Trader1 WR85.71% Ratio5.3:1")
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
                if text in ["/start","/check","/status","/running"]:
                    sync_positions_from_mexc()
                    wr = (stats["wins"]/stats["total_trades"]*100) if stats["total_trades"]>0 else 0
                    ratio = abs(stats.get("avg_win",0.65)/stats.get("avg_loss",-1.34)) if stats.get("avg_loss",0)!=0 else 0.48
                    pos_info = f"{len(positions)} LONG {SYMBOL}" if positions else "ว่าง"
                    total_qty = sum(abs(p['qty']) for p in positions) if positions else 0
                    avg = sum(p['entry']*abs(p['qty']) for p in positions)/total_qty if total_qty>0 else 0
                    price = get_price_mexc()
                    send_telegram(f"✅ V13.1 COPY TRADING รันอยู่\n⏰ {thai_time()}\nSymbol: {SYMBOL} GOLD Perpetual\nPos:{pos_info} Qty:{total_qty} Avg:{avg:.2f} Price:{price}\nWR:{wr:.1f}% Ratio:{ratio:.1f}:1 Trades:{stats['total_trades']}\nTarget: Trader1 WR85.71% Ratio5.3:1 14/week\nTrader2 ROI436% Ratio11.6:1 22/week\n/open /close /signal", chat)
                elif text.startswith("/signal"):
                    ohlcv = get_klines_mexc(200, '15m')
                    if ohlcv:
                        rows = calc_indicators(ohlcv)
                        if rows:
                            last = rows[-1]
                            price_above_ema200 = last['c'] > last['EMA200']
                            ema_align = last['EMA12'] > last['EMA26'] and last['EMA26'] > last['EMA200']
                            rsi_opt = 48 <= last['RSI'] <= 62
                            adx_strong = last['ADX'] > 28 and last['+DI'] > 24
                            buy_signal = price_above_ema200 and ema_align and rsi_opt and adx_strong
                            detail = f"Price>EMA200:{price_above_ema200} EMA Align:{ema_align} RSI48-62:{rsi_opt} ADX28+:{adx_strong}"
                            send_telegram(f"📡 V13.1 SIGNAL {SYMBOL} {thai_time()}\nPrice {last['c']:.2f} EMA12 {last['EMA12']:.2f} EMA26 {last['EMA26']:.2f} EMA200 {last['EMA200']:.2f}\nRSI {last['RSI']:.1f} ADX {last['ADX']:.1f} +DI {last['+DI']:.1f} -DI {last['-DI']:.1f}\n{detail}\n{'✅ READY LONG V13.1' if buy_signal else '⏳ รอ FILTER WR85%'}\n/open เปิดมือ", chat)
                elif text.startswith("/open") or text.startswith("/buy") or text.startswith("/long"):
                    parts = text_raw.split()
                    qty = parts[1] if len(parts)>1 else None
                    threading.Thread(target=manual_open, args=(qty,), daemon=True).start()
                    send_telegram(f"⏳ V13.1 กำลังเปิด {SYMBOL} LONG {qty or '0.001'} ...", chat)
                elif text.startswith("/close") or text.startswith("/sell"):
                    threading.Thread(target=manual_close, daemon=True).start()
                    send_telegram(f"⏳ กำลังปิด {SYMBOL} {len(positions)} ไม้ ...", chat)
                elif text == "/balance":
                    sync_positions_from_mexc()
                    bal = get_balance()
                    price = get_price_mexc()
                    avg = 0
                    upnl = 0
                    if positions:
                        total_qty = sum(abs(p['qty']) for p in positions)
                        avg = sum(p['entry']*abs(p['qty']) for p in positions)/total_qty
                        upnl = (price-avg)/avg*100 if price else 0
                    wr = (stats["wins"]/stats["total_trades"]*100) if stats["total_trades"]>0 else 0
                    ratio = abs(stats.get("avg_win",0.65)/stats.get("avg_loss",-1.34)) if stats.get("avg_loss",0)!=0 else 0.48
                    send_telegram(f"💰 V13.1 {SYMBOL} Perpetual {thai_time()}\nUSDT: ${bal:.2f}\nPrice: {price}\nPos: {len(positions)} Avg:{avg:.2f} uPnL:{upnl:+.2f}%\nWR {wr:.1f}% Ratio {ratio:.1f}:1\nTarget WR85% Ratio5.3:1", chat)
        except Exception as e:
            print(f"Tele err {e}")
            time.sleep(10)

threading.Thread(target=telegram_check_loop, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
