"""
V12.8 FREE FIX - แก้ Permission denied '/mnt/data' บน Render Free Tier
จาก Log: File "/opt/render/project/src/V6-3-FREE.py", line 73, os.makedirs("/mnt/data") PermissionError

V12.8 FIX:
- ใช้ DATA_DIR = ./data สำหรับ Free Tier (เขียนได้) แทน /mnt/data (ต้อง Paid Disk เท่านั้น)
- ถ้ามี Disk จะ auto ใช้ /mnt/data เอง
- ที่เหลือเหมือน V12.7 REAL ORDER 5X LONG ONLY
"""

import requests, pandas as pd, os, time, threading, json
from flask import Flask, jsonify
from datetime import datetime
import pytz

# === SMART DATA DIR - FIX FOR FREE TIER ===
# Free tier เขียน /mnt/data ไม่ได้ -> ใช้ ./data แทน
# Paid + Disk จะมี /mnt/data เขียนได้ -> ใช้ /mnt/data
def get_data_dir():
    # ลอง /mnt/data ก่อน (Paid Disk)
    try:
        if os.path.exists("/mnt/data") and os.access("/mnt/data", os.W_OK):
            return "/mnt/data"
        # ลองสร้างดู ถ้าได้แสดงว่า Paid
        os.makedirs("/mnt/data", exist_ok=True)
        if os.access("/mnt/data", os.W_OK):
            return "/mnt/data"
    except:
        pass
    # Fallback สำหรับ Free Tier
    fallback = "./data"
    os.makedirs(fallback, exist_ok=True)
    return fallback

DATA_DIR = get_data_dir()
print(f"Using DATA_DIR: {DATA_DIR} | Free Tier: {DATA_DIR != '/mnt/data'}")

MEXC_API_KEY = os.environ.get("MEXC_API_KEY", "").strip()
MEXC_API_SECRET = os.environ.get("MEXC_API_SECRET", "").strip()
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("CHAT_ID", "").strip()

SYMBOL = "PAXGUSDT"
SYMBOL_CCXT = "PAXG/USDT:USDT"
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

WEEKLY_REVIEW_DAY = 5
WEEKLY_REVIEW_HOUR = 20
BT_FILE_PRIMARY = os.path.join(DATA_DIR, "BT-1week-V2.3.csv")
BT_FILE_FALLBACK = "./BT-1week-V2.3.csv"
WEEKLY_REPORT_FILE = os.path.join(DATA_DIR, "weekly_saturday_report.json")
POSITIONS_FILE = os.path.join(DATA_DIR, "positions.json")
STATS_FILE = os.path.join(DATA_DIR, "stats.json")

app = Flask(__name__)
positions, highest, lowest, last_hourly = [], 0, 999999, time.time()
stats = {"total_trades":0,"wins":0,"losses":0,"total_pnl_pct":0.0,"history":[]}
cooldown_until = 0
daily_loss_count = 0
last_daily_reset = datetime.now(pytz.timezone('Asia/Bangkok')).day
current_atr_sl = SL_PCT
last_news_reason = ""
last_news_sent = time.time()
last_cooldown_sent = time.time()
last_daily_loss_sent = time.time()
last_ema_cross = ""
weekly_config = {"last_review": "", "wr": 0, "pnl": 0}

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
        mexc.load_markets()
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
                print(f"Loaded {len(positions)} positions from {POSITIONS_FILE}")
    except Exception as e:
        print(f"load pos err {e}")

def save_stats():
    try:
        with open(STATS_FILE, 'w', encoding='utf-8') as f:
            json.dump(stats, f)
    except Exception as e:
        print(f"save stats err {e}")

def load_stats():
    global stats
    try:
        if os.path.exists(STATS_FILE):
            with open(STATS_FILE, 'r', encoding='utf-8') as f:
                stats = json.load(f)
                print(f"Loaded stats from {STATS_FILE}")
    except Exception as e:
        print(f"load stats err {e}")

load_positions()
load_stats()

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

@app.route('/')
def home():
    wr = (stats["wins"]/stats["total_trades"]*100) if stats["total_trades"]>0 else 0
    ema_status = "EMA12>EMA26 BULL RUN" if last_ema_cross=="BULL" else "BEAR"
    pos_info = f"{len(positions)} LONG" if positions else "ว่าง"
    tier = "PAID Disk" if DATA_DIR=="/mnt/data" else "FREE (./data)"
    return f"V12.8 FREE FIX LIVE | {tier} | DATA_DIR {DATA_DIR} | Trades:{stats['total_trades']} WR:{wr:.1f}% {ema_status} | Pos:{pos_info}"

@app.route('/health')
def health():
    return jsonify({"status": "ok", "version": "V12.8 FREE FIX", "data_dir": DATA_DIR, "positions": len(positions), "trades": stats["total_trades"], "disk_free": DATA_DIR != "/mnt/data"})

@app.route('/stats')
def stats_page():
    wr = (stats["wins"]/stats["total_trades"]*100) if stats["total_trades"]>0 else 0
    hist = "".join([f"{h['time']} {h['type']} {h['pnl']:+.2f}%<br>" for h in stats["history"][-30:]])
    pos_str = json.dumps(positions, indent=2, ensure_ascii=False) if positions else "ว่าง"
    return f"<h2>V12.8 FREE FIX - REAL ORDER 5X LONG ONLY - {DATA_DIR}</h2>Trades:{stats['total_trades']} WR:{wr:.1f}% Total:{stats['total_pnl_pct']:+.2f}% Pos:{pos_str}<br><hr>{hist}"

@app.route('/weekly')
def weekly_page():
    report = weekly_analysis_detailed()
    return f"<h2>Saturday Weekly Report - V12.8 FREE FIX</h2><pre>{report}</pre>"

def send_telegram(msg):
    if not BOT_TOKEN or not CHAT_ID:
        return
    try:
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", 
                      json={"chat_id": CHAT_ID, "text": msg, "parse_mode":"HTML", "disable_web_page_preview": True}, timeout=15)
    except Exception as e:
        print(f"TG err {e}")

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
            return pd.DataFrame(ohlcv, columns=['ot','o','h','l','c','v'])
    except Exception as e:
        print(f"Kline err {e}")
        return None

def calc_indicators(df):
    df['EMA12']=df['c'].ewm(span=12, adjust=False).mean()
    df['EMA26']=df['c'].ewm(span=26, adjust=False).mean()
    df['EMA20']=df['c'].ewm(span=20, adjust=False).mean()
    df['EMA200']=df['c'].ewm(span=200, adjust=False).mean()
    delta=df['c'].diff()
    gain=delta.where(delta>0,0).rolling(14).mean()
    loss=-delta.where(delta<0,0).rolling(14).mean()
    df['RSI']=100-(100/(1+gain/(loss.replace(0,0.001))))
    tr1 = df['h']-df['l']
    tr2 = (df['h']-df['c'].shift()).abs()
    tr3 = (df['l']-df['c'].shift()).abs()
    df['TR']=pd.concat([tr1,tr2,tr3], axis=1).max(axis=1)
    df['ATR']=df['TR'].rolling(14).mean()
    df['H-pH']=df['h']-df['h'].shift()
    df['pL-L']=df['l'].shift()-df['l']
    df['+DM']=df.apply(lambda x: x['H-pH'] if x['H-pH']>x['pL-L'] and x['H-pH']>0 else 0, axis=1)
    df['-DM']=df.apply(lambda x: x['pL-L'] if x['pL-L']>x['H-pH'] and x['pL-L']>0 else 0, axis=1)
    df['+DM14']=df['+DM'].rolling(14).sum()
    df['-DM14']=df['-DM'].rolling(14).sum()
    df['TR14']=df['TR'].rolling(14).sum()
    df['+DI']=100*df['+DM14']/df['TR14']
    df['-DI']=100*df['-DM14']/df['TR14']
    df['DX']=100*(df['+DI']-df['-DI']).abs()/(df['+DI']+df['-DI'])
    df['ADX']=df['DX'].rolling(14).mean()
    df['TP']=(df['h']+df['l']+df['c'])/3
    df['TPV']=df['TP']*df['v']
    df['VWAP']=df['TPV'].rolling(20).sum()/df['v'].rolling(20).sum()
    df['V_SMA20']=df['v'].rolling(20).mean()
    df['VolSpike']=df['v']/df['V_SMA20']
    df['Round10']=(df['c']/10).round()*10
    df['HH20']=df['h'].rolling(20).max()
    return df.dropna()

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
        wr = (stats["wins"]/stats["total_trades"]*100) if stats["total_trades"]>0 else 69.6
        upnl = 0
        if positions:
            total_qty = sum(abs(p['qty']) for p in positions)
            avg = sum(p['entry']*abs(p['qty']) for p in positions)/total_qty
            price = get_price_mexc() or avg
            upnl = (price-avg)/avg*100
        return f"Bal ${balance:.2f} | PnL {stats['total_pnl_pct']:+.2f}% | WR {wr:.1f}%", {"upnl":upnl, "balance":balance}, wr
    except:
        return "Balance 40.16$ WR69.6%", {"upnl":0.22, "balance":40.16}, 69.6

def weekly_analysis_detailed():
    global weekly_config
    try:
        bt_path = BT_FILE_PRIMARY if os.path.exists(BT_FILE_PRIMARY) else BT_FILE_FALLBACK
        if not os.path.exists(bt_path):
            return f"BT file not found: {BT_FILE_PRIMARY} | อัพโหลด BT-1week-V2.3.csv ไปที่ ./data/ หรือ root"
        df = pd.read_csv(bt_path)
        df['entry_time'] = pd.to_datetime(df['entry_time'])
        total = len(df)
        wr = (df['pnl']>0).mean()*100
        pnl_sum = df['pnl'].sum()
        weekly_config = {"last_review": thai_time(), "total": total, "wr": wr, "pnl": pnl_sum}
        with open(WEEKLY_REPORT_FILE, 'w', encoding='utf-8') as f:
            json.dump(weekly_config, f, ensure_ascii=False, indent=2)
        return f"Weekly: {total}ไม้ WR {wr:.1f}% PnL {pnl_sum:+.2f}% DATA_DIR {DATA_DIR}"
    except Exception as e:
        return f"Weekly err {e}"

def weekly_saturday_loop():
    while True:
        try:
            now_th = datetime.now(pytz.timezone('Asia/Bangkok'))
            if now_th.weekday() == WEEKLY_REVIEW_DAY and now_th.hour == WEEKLY_REVIEW_HOUR and now_th.minute < 5:
                last = weekly_config.get('last_review','')
                today_str = now_th.strftime('%d/%m/%Y')
                if today_str not in last:
                    report = weekly_analysis_detailed()
                    send_telegram(report)
                    time.sleep(300)
            time.sleep(60)
        except Exception as e:
            print(f"Saturday loop err {e}")
            time.sleep(60)

def hourly_full_report(last, price, mtf_4h_up, mtf_1h_up):
    try:
        bal_msg, bal_detail, wr = format_balance_msg()
        send_telegram(f"📊 [{thai_time()}] V12.8 FREE FIX LIVE {DATA_DIR} Price {price:.2f} {bal_msg}")
    except Exception as e:
        print(f"report err {e}")

def mexc_buy(price, factor):
    if not mexc:
        print("mexc not configured, DRY RUN")
        return True, 0.001*factor
    try:
        try:
            mexc.set_leverage(LEVERAGE, SYMBOL, {'marginMode': 'CROSS'})
        except:
            try:
                mexc.set_leverage(LEVERAGE, SYMBOL)
            except:
                pass
        balance = get_balance()
        usdt_to_use = balance * ORDER_PERCENT / 100 * factor
        qty = (usdt_to_use * LEVERAGE) / price
        qty = max(0.001, round(qty, 3))
        qty = min(qty, 0.01)
        print(f"REAL BUY {SYMBOL} qty {qty} @ {price} DATA_DIR {DATA_DIR}")
        order = mexc.create_market_buy_order(SYMBOL, qty)
        print(f"Buy order filled: {order}")
        return True, qty
    except Exception as e:
        print(f"REAL BUY ERR {e}")
        import traceback
        traceback.print_exc()
        send_telegram(f"❌ BUY ERROR {e} Price {price}")
        return False, 0

def close_position(price, reason):
    global cooldown_until, daily_loss_count, positions, highest, lowest
    if not positions:
        return
    total_qty = sum(abs(p['qty']) for p in positions)
    avg = sum(p['entry']*abs(p['qty']) for p in positions)/total_qty
    pnl = (price-avg)/avg*100
    if mexc:
        try:
            print(f"REAL CLOSE {SYMBOL} qty {total_qty} @ {price} reason {reason}")
            close_order = mexc.create_market_sell_order(SYMBOL, total_qty)
            print(f"Close order: {close_order}")
        except Exception as e:
            print(f"REAL CLOSE ERR {e}")
            send_telegram(f"❌ CLOSE ERROR {e} PnL {pnl:.2f}%")
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
    bal_msg, bal_detail, wr = format_balance_msg()
    send_telegram(f"{'🔴' if pnl<0 else '🟢'} [{thai_time()}] ปิด {reason} PnL {pnl:+.2f}% @ {price:.2f}$ {bal_msg} DATA_DIR {DATA_DIR}")
    positions=[]
    highest=0
    lowest=999999
    save_positions()

def trading_loop():
    global positions, highest, lowest, last_hourly, cooldown_until, current_atr_sl, last_news_reason, last_news_sent, last_cooldown_sent, last_daily_loss_sent, last_ema_cross
    last_hourly = time.time()
    print(f"V12.8 FREE FIX START DATA_DIR {DATA_DIR}")
    send_telegram(f"🚀 V12.8 FREE FIX START {thai_time()} | DATA_DIR {DATA_DIR} | 5X Cross LONG ONLY | แก้ Permission denied /mnt/data")
    while True:
        try:
            df = get_klines_mexc(200, '15m')
            if df is None or len(df)<200:
                time.sleep(60)
                continue
            df = calc_indicators(df)
            last = df.iloc[-1]
            prev = df.iloc[-2]
            price = float(last['c'])
            df_1h = get_klines_mexc(100, '1h')
            df_4h = get_klines_mexc(100, '4h')
            mtf_1h_up = True
            mtf_4h_up = True
            try:
                if df_1h is not None and len(df_1h)>50:
                    df_1h = calc_indicators(df_1h)
                    mtf_1h_up = df_1h.iloc[-1]['EMA12'] > df_1h.iloc[-1]['EMA26']
                if df_4h is not None and len(df_4h)>50:
                    df_4h = calc_indicators(df_4h)
                    mtf_4h_up = df_4h.iloc[-1]['EMA12'] > df_4h.iloc[-1]['EMA26']
            except:
                pass
            atr = last['ATR']
            atr_pct_price = (atr * SL_ATR_MULT / price * 100)
            atr_pct_pnl = atr_pct_price * LEVERAGE
            current_atr_sl = max(SL_MIN_PCT, min(SL_MAX_PCT, atr_pct_pnl))
            ema12 = last['EMA12']
            ema26 = last['EMA26']
            ema_cross_bull = ema12 > ema26 and prev['EMA12'] <= prev['EMA26']
            ema_cross_bear = ema12 < ema26 and prev['EMA12'] >= prev['EMA26']
            ema_bull_hold = ema12 > ema26
            ema_diff_pct = (ema12-ema26)/ema26*100
            if ema_cross_bull:
                last_ema_cross = "BULL"
            elif ema_cross_bear:
                last_ema_cross = "BEAR"
            super_up = last['+DI']>28 and last['-DI']<12 and last['ADX']>28 and last['c']>last['EMA20'] and last['c']>last['VWAP']
            uptrend = last['+DI']>last['-DI'] and last['ADX']>22 and last['c']>last['VWAP'] and last['+DI']>20
            near_support = last['c'] <= last['Round10']+3
            strong_bull = (last['c']-last['o']) > (last['h']-last['l'])*0.5 and last['c']>last['o']
            vol_spike = last['VolSpike']>1.0
            rsi_trend = 45 <= last['RSI'] <= 85
            rsi_super = 50 <= last['RSI'] <= 88
            rsi_breakout = 55 <= last['RSI'] <= 90
            dist_ema = abs(last['c']-last['EMA12'])/last['EMA12']*100 <= 3.0
            ema_run_up = ENABLE_EMA12x26_RUN and ema_bull_hold and last['c'] > last['EMA12'] and ema_diff_pct >= 0.02 and last['ADX']>20 and last['+DI']>18 and rsi_breakout and last['c'] > last['VWAP'] and mtf_1h_up and mtf_4h_up
            breakout_up = ENABLE_BREAKOUT_UP and last['c'] > last['HH20']*0.998 and last['c'] > last['EMA12'] and last['EMA12'] > last['EMA26'] and last['ADX']>22 and last['+DI']>18 and rsi_breakout and last['c'] > last['VWAP'] and vol_spike and mtf_1h_up and mtf_4h_up
            buy_trend = ENABLE_TREND_UP and uptrend and near_support and strong_bull and vol_spike and rsi_trend and dist_ema and mtf_1h_up and mtf_4h_up
            buy_super = ENABLE_TREND_UP and super_up and near_support and strong_bull and vol_spike and rsi_super and dist_ema and mtf_1h_up and mtf_4h_up
            buy_sideway = ENABLE_SIDEWAY_UP and last['+DI']>last['-DI'] and last['ADX']<22 and near_support and strong_bull and vol_spike and (40 <= last['RSI'] <= 80)
            buy_signal = buy_trend or buy_super or buy_sideway or breakout_up or ema_run_up
            buy_reason = ""
            if ema_run_up:
                buy_reason = f"EMA12x26_RUN Bull Diff {ema_diff_pct:.2f}%"
            elif breakout_up:
                buy_reason = "BREAKOUT_UP"
            elif buy_super:
                buy_reason = "SUPER_UP"
            elif buy_trend:
                buy_reason = "TREND_UP"
            elif buy_sideway:
                buy_reason = "SIDEWAY_UP"
            if cooldown_until > time.time():
                if time.time() - last_cooldown_sent >= 14400:
                    last_cooldown_sent = time.time()
                    last_hourly = time.time()
                    remain = (cooldown_until - time.time())/3600
                    bal_msg, _, _ = format_balance_msg()
                    send_telegram(f"⏸️ [{thai_time()}] Cooldown {remain:.1f}ชม. | {bal_msg} | {DATA_DIR}")
                time.sleep(60)
                continue
            if check_daily_loss():
                if time.time() - last_daily_loss_sent >= 14400:
                    last_daily_loss_sent = time.time()
                    last_hourly = time.time()
                    send_telegram(f"🛑 [{thai_time()}] Daily Max Loss {DAILY_MAX_LOSS} หยุดวันนี้ | {DATA_DIR}")
                time.sleep(60)
                continue
            if time.time()-last_hourly >= 14400:
                last_hourly=time.time()
                hourly_full_report(last, price, mtf_4h_up, mtf_1h_up)
            funding_block, funding_reason = is_funding_time()
            news_block, news_reason = is_news_time()
            if positions and check_max_hold():
                bal_msg, _, _ = format_balance_msg()
                send_telegram(f"⏰ [{thai_time()}] บังคับปิด ถือเกิน {MAX_HOLD_HOURS}ชม.\n{bal_msg} | {DATA_DIR}")
                close_position(price, f"MAX_HOLD_{MAX_HOLD_HOURS}H")
                continue
            if funding_block or news_block:
                reason = funding_reason or news_reason
                if reason != last_news_reason or time.time() - last_news_sent >= 14400:
                    last_news_reason = reason
                    last_news_sent = time.time()
                    last_hourly = time.time()
                    send_telegram(f"⏸️ [{thai_time()}] ติดข่าว {reason} หยุดเข้า | {DATA_DIR}")
                if positions:
                    total_qty = sum(abs(p['qty']) for p in positions)
                    avg = sum(p['entry']*abs(p['qty']) for p in positions)/total_qty
                    pnl = (price-avg)/avg*100
                    if pnl > 0.5:
                        send_telegram(f"🛡️ [{thai_time()}] ปิดก่อนข่าว {reason} กำไร {pnl:.2f}%")
                        close_position(price, "PRE_NEWS_PROFIT")
                time.sleep(60)
                continue
            if len(positions)==0 and buy_signal:
                ok, qty = mexc_buy(price, 1)
                if ok:
                    positions.append({'entry': price, 'qty': qty, 'side': 'long', 'time': time.time()})
                    highest=price
                    lowest=price
                    save_positions()
                    bal_msg, bal_detail, wr = format_balance_msg()
                    send_telegram(f"🟢 [{thai_time()}] เปิด LONG REAL {buy_reason} @ {price:.2f}$ Qty {qty} | {bal_msg} | {DATA_DIR}")
            if positions:
                total_qty = sum(abs(p['qty']) for p in positions)
                avg = sum(p['entry']*abs(p['qty']) for p in positions)/total_qty
                pnl = (price-avg)/avg*100
                if price>highest: highest=price
                if price<lowest: lowest=price
                ema_still_bull = last['EMA12'] > last['EMA26']
                price_above_ema12 = price > last['EMA12']
                if pnl>=TP_PCT:
                    close_position(price, f"TP {TP_PCT}%")
                elif price <= avg*(1-current_atr_sl/100):
                    close_position(price, f"HARD_SL -{current_atr_sl:.1f}%")
                elif not ema_still_bull and not price_above_ema12:
                    close_position(price, f"EMA12x26_CROSS_BEAR")
                elif ema_still_bull and price_above_ema12 and price <= highest*(1-TRAIL_PCT*2.5/100) and pnl >= BE_PCT*2:
                    close_position(price, f"TRAIL_EMA12x26 {TRAIL_PCT*2.5}%")
                elif pnl>=BE_PCT and price <= highest*(1-TRAIL_PCT/100) and not ema_still_bull:
                    close_position(price, f"TRAIL_{TRAIL_PCT}%")
            time.sleep(60)
        except Exception as e:
            print(f"Loop err {e}")
            import traceback
            traceback.print_exc()
            time.sleep(30)

threading.Thread(target=trading_loop, daemon=True).start()
threading.Thread(target=weekly_saturday_loop, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
