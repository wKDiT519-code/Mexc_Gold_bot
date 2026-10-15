
import requests, pandas as pd, os, time, threading, ccxt, traceback, json
from flask import Flask, request
from datetime import datetime, timezone
import pytz

MEXC_API_KEY = os.environ.get("MEXC_API_KEY", "").strip()
MEXC_API_SECRET = os.environ.get("MEXC_API_SECRET", "").strip()
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("CHAT_ID", "").strip()

# ===== V12.2 FINAL - ATR SL + MTF 4H/1H/15m + RE-ENTRY =====
# สรุปวิเคราะห์:
# - SHORT 4170 ถูก แต่ SL 0.18% ราคา = 7.5$ โดนไส้เทียนก่อนลง 4081
# - BUY 4110 ถูก ได้ 38$
# - ต้อง SL แบบ ATR 15-20$ + MTF 4H/1H ต้องเห็นด้วย + Re-entry
SYMBOL = "XAU_USDT"
SYMBOL_CCXT = "XAU/USDT:USDT"
SYMBOL_FALLBACKS = ["XAU/USDT:USDT", "XAU_USDT"]

# === SETTINGS V12.2 FINAL ===
ENABLE_TREND_UP = True
ENABLE_SIDEWAY_DOWN = True
ENABLE_TREND_DOWN = False
ENABLE_SIDEWAY_UP = False
ENABLE_SHORT = True

LEVERAGE = 5
TP_PCT = 2.2  # 2.2% PnL = 0.44% ราคา ~18$ เหมาะกับแท่งใหญ่ 4081->4148
SL_PCT = 1.8  # Base 1.8% PnL = 0.36% ราคา ~15$ - จะถูกปรับด้วย ATR
SL_ATR_MULT = 1.5  # ATR * 1.5
SL_MIN_PCT = 1.2
SL_MAX_PCT = 2.8
FAIL_COOLDOWN_MIN = 5
ADX_THRESHOLD = 22
AVOID_FUNDING_MINUTES = 10
FUNDING_TIMES_UTC = [0, 8, 16]
COOLDOWN_HOURS = 1  # ลดจาก 3h -> 1h เพื่อ Re-entry เร็ว (แก้ปัญหาโดน SL แล้วอดกำไร)
COOLDOWN_AFTER_SL_HOURS = 0.25  # หลังโดน SL 15 นาทีเข้าใหม่ได้ถ้าเทรนด์ยังถูก
MAX_HOLD_HOURS = 12
TRAIL_START_PCT = 0.7  # เริ่ม Trail ที่ 0.7% (ให้มีกำไรก่อน)
TRAIL_PCT = 0.6  # Trail กว้าง 0.6% ไม่โดนสลัด
TRAIL_SUPER_PCT = 1.2
BE_PCT = 0.8  # BE ที่ 0.8% ให้ room หายใจ
DAILY_MAX_LOSS = 2
MAX_DIST_EMA = 2.0

NEWS_BLACKOUT_HOURS = [
    {"day": "all", "start": "19:30", "end": "21:30", "reason": "US CPI/PPI 20:30 ICT"},
    {"day": "all", "start": "00:30", "end": "02:00", "reason": "FOMC 01:00 ICT"},
    {"day": "Friday", "start": "19:00", "end": "20:30", "reason": "NFP 19:30 ICT Friday"},
]
ENABLE_NEWS_FILTER = True
ENABLE_DAILY_BRIEF = True
DAILY_BRIEF_HOUR_ICT = 6

app = Flask(__name__)
positions, highest, lowest = [], 0, 999999
stats = {"total_trades":0,"wins":0,"losses":0,"total_pnl_pct":0.0}
cooldown_until = 0
trail_sl = 0
be_done = False
last_hourly = 0
last_hourly_report_minute = -1
fail_count = 0
last_error_detail = "ยังไม่เคยลอง"
daily_losses = 0
last_daily_reset = 0
last_brief_day = -1
current_bias = "NEUTRAL"
news_today_cache = []
last_sl_time = 0
last_sl_side = ""
current_atr_sl = SL_PCT

mexc_public = ccxt.mexc({'enableRateLimit': True, 'rateLimit': 1000, 'options': {'defaultType': 'swap'}})
mexc = ccxt.mexc({'apiKey': MEXC_API_KEY, 'secret': MEXC_API_SECRET, 'enableRateLimit': True, 'rateLimit': 1500, 'options': {'defaultType': 'swap'}}) if MEXC_API_KEY and MEXC_API_SECRET else None

def thai_time():
    return datetime.now(pytz.timezone('Asia/Bangkok')).strftime("%d/%m/%Y %H:%M ICT")
def thai_now():
    return datetime.now(pytz.timezone('Asia/Bangkok'))
def is_funding_time():
    try:
        now_utc = datetime.now(timezone.utc)
        for ft in FUNDING_TIMES_UTC:
            if now_utc.hour == ft and now_utc.minute < AVOID_FUNDING_MINUTES: return True
            if now_utc.hour == ft-1 and now_utc.minute >= (60-AVOID_FUNDING_MINUTES): return True
        return False
    except: return False
def check_max_hold():
    if not positions: return False
    try:
        ft = positions[0].get('time',0)
        return (time.time()-ft)/3600 >= MAX_HOLD_HOURS if ft else False
    except: return False

def is_news_blackout():
    if not ENABLE_NEWS_FILTER: return False, ""
    try:
        now = thai_now()
        now_str = now.strftime("%H:%M")
        weekday = now.strftime("%A")
        for nb in NEWS_BLACKOUT_HOURS:
            if nb["day"] != "all" and nb["day"] != weekday: continue
            if nb["start"] <= now_str <= nb["end"]: return True, nb["reason"]
        return False, ""
    except: return False, ""

def get_daily_news_brief():
    global current_bias, news_today_cache
    try:
        now = thai_now()
        weekday = now.strftime("%A")
        news = []
        if 12 <= now.day <= 14: news.append({"time": "20:30 ICT", "event": "US CPI", "impact": "HIGH"})
        if 14 <= now.day <= 15: news.append({"time": "20:30 ICT", "event": "US PPI", "impact": "HIGH"})
        if weekday == "Friday":
            news.append({"time": "19:30 ICT", "event": "US NFP", "impact": "HIGH"})
        if now.day in [1,2,15,16,29,30]: news.append({"time": "01:00 ICT", "event": "FOMC Speech", "impact": "MEDIUM"})
        has_high = any(n['impact']=='HIGH' for n in news)
        bias = "AVOID_BEFORE_NEWS" if has_high else "LONG_BIAS"
        reasons = [f"ข่าว HIGH {len([n for n in news if n['impact']=='HIGH'])} ตัว - หลบ 19:30-21:30"] if has_high else ["ไม่มีข่าว HIGH - เปิด LONG ตาม Q4 bullish"]
        current_bias = bias
        news_today_cache = news
        return news, bias, reasons
    except Exception as e:
        return [], "NEUTRAL", [f"Error {e}"]

def send_daily_brief():
    global last_brief_day
    try:
        now = thai_now()
        if now.hour != DAILY_BRIEF_HOUR_ICT or last_brief_day == now.day: return
        if not ENABLE_DAILY_BRIEF: return
        price = get_price_mexc() or 4160
        news, bias, reasons = get_daily_news_brief()
        msg = f"📰 <b>DAILY GOLD BRIEF {thai_time()}</b>\n💰 XAU: {price:.2f}$\n\n<b>📅 ข่าววันนี้:</b>\n"
        if news:
            for n in news:
                icon = "🔴" if n['impact']=='HIGH' else "🟡"
                msg += f"{icon} {n['time']} {n['event']} ({n['impact']})\n"
        else:
            msg += "🟢 ไม่มีข่าว HIGH วันนี้\n"
        msg += f"\n<b>🎯 BIAS: {bias}</b>\n"
        for r in reasons: msg += f"{r}\n"
        msg += f"\n<b>📊 BT-1week-V2.3.csv:</b>\nTREND_UP 76.9% +2.59%\nSIDEWAY_DOWN 100% +1.73%\n"
        msg += f"\n<b>🤖 V12.2:</b> ATR SL {current_atr_sl:.1f}% + MTF 4H/1H/15m + Re-entry 15m"
        send_telegram(msg)
        last_brief_day = now.day
    except Exception as e:
        print(f"brief err {e}")

def check_daily_loss():
    global daily_losses, last_daily_reset
    today = thai_now().day
    if last_daily_reset != today:
        daily_losses = 0
        last_daily_reset = today
    return daily_losses >= DAILY_MAX_LOSS

@app.route('/')
def home():
    env_ok = f"BOT:{'OK' if BOT_TOKEN else 'EMPTY'} CHAT:{'OK' if CHAT_ID else 'EMPTY'} MEXC:{'OK' if MEXC_API_KEY else 'EMPTY'}"
    news, bias, _ = get_daily_news_brief()
    return f"V12.2 ATR+MTF Live {env_ok} Bias:{bias} SL:{current_atr_sl:.1f}% Fail:{fail_count}<br><a href='/test_tele'>/test_tele</a> | <a href='/stats'>/stats</a> | <a href='/brief'>/brief</a>"
@app.route('/stats')
def stats_page():
    env = f"BOT_TOKEN len={len(BOT_TOKEN)} CHAT_ID={CHAT_ID} MEXC_KEY len={len(MEXC_API_KEY)}"
    news, bias, _ = get_daily_news_brief()
    return f"<h2>V12.2 ATR SL {current_atr_sl:.1f}% Bias {bias}</h2>{env}<br>Fail:{fail_count}<br><pre>{last_error_detail}</pre>"
@app.route('/test_tele')
def test_tele():
    ok = send_telegram(f"TEST V12.2 [{thai_time()}] Bias:{current_bias} ATR SL {current_atr_sl:.1f}%")
    return f"Telegram {ok}"
@app.route('/ping')
def ping():
    is_blackout, reason = is_news_blackout()
    return f"pong {thai_time()} pos={len(positions)} bias={current_bias} atr_sl={current_atr_sl:.1f}% blackout={is_blackout} {reason}"
@app.route('/brief')
def brief_route():
    send_daily_brief()
    return f"Brief sent {thai_time()} bias {current_bias}"
@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json(force=True, silent=True) or {}
        msg = data.get('message') or str(data)
        is_blackout, reason = is_news_blackout()
        if is_blackout:
            send_telegram(f"⏸️ Webhook {msg} ติดข่าว {reason}")
            return {"status": "blackout", "reason": reason}
        send_telegram(f"📡 TV Signal: {msg} Bias:{current_bias}")
        return {"status": "ok", "bias": current_bias}
    except Exception as e:
        return {"status": "error", "msg": str(e)}

def send_telegram(msg):
    if not BOT_TOKEN or not CHAT_ID: return False
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        r = requests.post(url, json={"chat_id": CHAT_ID, "text": msg, "parse_mode":"HTML", "disable_web_page_preview": True}, timeout=15)
        return r.status_code==200
    except: return False

def get_balance():
    if not mexc: return 0,0,0
    try:
        bal = mexc.fetch_balance()
        usdt = bal.get('USDT',{})
        return float(usdt.get('total') or 0), float(usdt.get('free') or 0), 0
    except: return 0.0,0.0,0.0
def format_balance_msg():
    total, free, _ = get_balance()
    wr = (stats["wins"]/stats["total_trades"]*100) if stats["total_trades"]>0 else 0
    return f"Free {free:.2f}$ Total {total:.2f}$ WR {wr:.1f}% ATR_SL {current_atr_sl:.1f}%", total

def patch_mexc_markets():
    global last_error_detail
    if not mexc: return False
    gold_market = {'id': 'XAU_USDT','symbol': 'XAU/USDT:USDT','base': 'XAU','quote': 'USDT','settle': 'USDT','baseId': 'XAU','quoteId': 'USDT','type': 'swap','spot': False,'swap': True,'future': False,'contract': True,'linear': True,'inverse': False,'active': True,'precision': {'amount': 0.01, 'price': 0.01},'limits': {'amount': {'min': 0.01, 'max': 1000}, 'price': {'min': 0.01, 'max': 100000}}}
    try:
        mexc.load_markets()
        for sym in SYMBOL_FALLBACKS:
            if sym not in mexc.markets:
                mexc.markets[sym] = gold_market.copy()
                mexc.markets[sym]['symbol'] = sym
        return True
    except Exception as e:
        last_error_detail = f"load fail {e}"
        for sym in SYMBOL_FALLBACKS:
            mexc.markets[sym] = gold_market.copy()
            mexc.markets[sym]['symbol'] = sym
        return True

def get_price_mexc():
    for sym in ["XAU/USDT:USDT", "XAU_USDT"]:
        try:
            t = mexc_public.fetch_ticker(sym)
            price = float(t.get('last') or t.get('close') or 0)
            if price > 100: return price
        except: time.sleep(0.3)
    try:
        r = requests.get("https://contract.mexc.com/api/v1/contract/ticker", params={"symbol": "XAU_USDT"}, timeout=10)
        j = r.json()
        price = float(j.get('data', {}).get('lastPrice') or 0)
        if price > 100: return price
    except: pass
    return None

def get_klines_mexc(limit=200, timeframe='15m'):
    for sym in ["XAU/USDT:USDT", "XAU_USDT"]:
        try:
            ohlcv = mexc_public.fetch_ohlcv(sym, timeframe=timeframe, limit=limit)
            df = pd.DataFrame(ohlcv, columns=['ot','o','h','l','c','v'])
            if len(df) >= 50: return df
        except: time.sleep(0.3)
    return None

def calc_lite(df):
    df['hlc3'] = (df['h'] + df['l'] + df['c']) / 3
    df['VWAP'] = df['hlc3'].ewm(span=96).mean()
    df['EMA20'] = df['c'].ewm(span=20).mean()
    df['EMA200'] = df['c'].ewm(span=200).mean() if len(df)>=200 else df['c'].ewm(span=50).mean()
    df['H-pH'] = df['h'].diff()
    df['pL-L'] = df['l'].shift(1) - df['l']
    df['+DM'] = df['H-pH'].where((df['H-pH'] > df['pL-L']) & (df['H-pH'] > 0), 0.0)
    df['-DM'] = df['pL-L'].where((df['pL-L'] > df['H-pH']) & (df['pL-L'] > 0), 0.0)
    df['TR'] = pd.concat([df['h']-df['l'], (df['h']-df['c'].shift()).abs(), (df['l']-df['c'].shift()).abs()], axis=1).max(axis=1)
    df['ATR'] = df['TR'].rolling(14).mean()
    df['+DM14'] = df['+DM'].rolling(14).sum()
    df['-DM14'] = df['-DM'].rolling(14).sum()
    atr_sum = df['ATR'].rolling(14).sum().replace(0, 0.001)
    df['+DI'] = 100 * (df['+DM14'] / atr_sum)
    df['-DI'] = 100 * (df['-DM14'] / atr_sum)
    df['DX'] = 100 * (df['+DI'] - df['-DI']).abs() / (df['+DI'] + df['-DI']).replace(0, 0.001)
    df['ADX'] = df['DX'].rolling(14).mean()
    delta=df['c'].diff()
    gain=delta.where(delta>0,0).rolling(14).mean()
    loss=-delta.where(delta<0,0).rolling(14).mean()
    df['RSI']=100-(100/(1+gain/(loss.replace(0,0.001))))
    df['VolEMA'] = df['v'].ewm(span=20).mean()
    df['VolSpike'] = df['v'] > df['VolEMA'] * 1.3
    df['Round10'] = (df['c'] / 10).round() * 10
    return df

def get_mtf_trend():
    try:
        df_4h = get_klines_mexc(100, '4h')
        df_1h = get_klines_mexc(100, '1h')
        if df_4h is None or df_1h is None or len(df_4h)<30 or len(df_1h)<30:
            return {"4h_up": True, "1h_up": True, "4h_adx": 0, "1h_adx": 0, "4h_rsi": 50, "1h_rsi": 50, "atr_1h": 5, "ok": False}
        df_4h = calc_lite(df_4h)
        df_1h = calc_lite(df_1h)
        last_4h = df_4h.iloc[-1]
        last_1h = df_1h.iloc[-1]
        up_4h = last_4h['c'] > last_4h['EMA20'] and last_4h['c'] > last_4h['VWAP'] and last_4h['+DI'] > last_4h['-DI'] and last_4h['ADX'] > 15
        down_4h = last_4h['c'] < last_4h['EMA20'] and last_4h['c'] < last_4h['VWAP'] and last_4h['-DI'] > last_4h['+DI'] and last_4h['ADX'] > 15
        up_1h = last_1h['c'] > last_1h['EMA20'] and last_1h['c'] > last_1h['VWAP'] and last_1h['+DI'] > last_1h['-DI']
        down_1h = last_1h['c'] < last_1h['EMA20'] and last_1h['c'] < last_1h['VWAP'] and last_1h['-DI'] > last_1h['+DI']
        return {
            "4h_up": up_4h, "4h_down": down_4h,
            "1h_up": up_1h, "1h_down": down_1h,
            "4h_adx": float(last_4h['ADX']), "1h_adx": float(last_1h['ADX']),
            "4h_rsi": float(last_4h['RSI']), "1h_rsi": float(last_1h['RSI']),
            "atr_1h": float(last_1h['ATR']),
            "4h_price": float(last_4h['c']), "1h_price": float(last_1h['c']),
            "ok": True
        }
    except Exception as e:
        print(f"MTF err {e}")
        return {"4h_up": True, "1h_up": True, "4h_down": False, "1h_down": False, "4h_adx": 0, "1h_adx": 0, "4h_rsi": 50, "1h_rsi": 50, "atr_1h": 5, "ok": False}

def mexc_buy(price, qty):
    global last_error_detail, fail_count
    try:
        if not mexc: return False,0
        mexc.create_order(SYMBOL_CCXT, 'market', 'buy', qty)
        return True, qty
    except Exception as e:
        last_error_detail = f"buy err {e}"
        fail_count+=1
        return False,0
def mexc_sell(price, qty):
    global last_error_detail, fail_count
    try:
        if not mexc: return False,0
        mexc.create_order(SYMBOL_CCXT, 'market', 'sell', qty)
        return True, qty
    except Exception as e:
        last_error_detail = f"sell err {e}"
        fail_count+=1
        return False,0
def mexc_close_all():
    try:
        if not mexc: return
        mexc.create_order(SYMBOL_CCXT, 'market', 'buy', 1) if positions and positions[0]['side']=='short' else mexc.create_order(SYMBOL_CCXT, 'market', 'sell', 1) if positions else None
    except Exception as e:
        print(f"close err {e}")
def close_position(price, reason):
    global positions, stats, daily_losses, cooldown_until, last_sl_time, last_sl_side
    try:
        mexc_close_all()
        entry = positions[0]['entry']
        side = positions[0]['side']
        is_short = side=='short'
        pnl = (entry-price)/entry*100*LEVERAGE if is_short else (price-entry)/entry*100*LEVERAGE
        stats["total_trades"]+=1
        if pnl>0:
            stats["wins"]+=1
            cooldown_until = time.time() + 300  # ชนะพัก 5 นาที
        else:
            stats["losses"]+=1
            daily_losses+=1
            last_sl_time = time.time()
            last_sl_side = side
            # ถ้าโดน SL ให้ cooldown สั้นลงเพื่อ Re-entry
            if "HARD_SL" in reason:
                cooldown_until = time.time() + COOLDOWN_AFTER_SL_HOURS*3600
            else:
                cooldown_until = time.time() + COOLDOWN_HOURS*3600
        stats["total_pnl_pct"]+=pnl
        bal_msg,_=format_balance_msg()
        send_telegram(f"✅ [{thai_time()}] {reason} {pnl:.2f}% ATR_SL {current_atr_sl:.1f}%\n{bal_msg}")
        positions.clear()
    except Exception as e:
        print(f"close pos err {e}")

def should_send_hourly():
    global last_hourly, last_hourly_report_minute
    now = thai_now()
    return (time.time() - last_hourly) >= 3600 or (now.minute == 0 and now.second < 20 and last_hourly_report_minute != now.hour)

def trading_loop():
    global positions,highest,lowest,last_hourly,cooldown_until,last_hourly_report_minute,trail_sl,be_done,last_error_detail,current_atr_sl
    print("[LOOP] V12.2 STARTED")
    send_telegram(f"V12.2 STARTED [{thai_time()}] ATR SL + MTF 4H/1H + Re-entry")
    time.sleep(3)
    try: patch_mexc_markets()
    except Exception as e: send_telegram(f"patch fail {e}")
    try: bal_msg,total=format_balance_msg()
    except: bal_msg="Balance err"; total=0.0
    last_hourly = time.time()
    last_hourly_report_minute = thai_now().hour
    try: mexc_close_all(); send_telegram(f"Closed old positions for V12.2")
    except: pass
    try: send_daily_brief()
    except: pass
    price_test = get_price_mexc() or 4160
    send_telegram(f"ทดสอบ BUY XAU {price_test:.2f}$ V12.2 ATR...")
    ok,qty = mexc_buy(price_test,1)
    if ok:
        send_telegram(f"TEST BUY สำเร็จ! {price_test:.2f}$")
        time.sleep(2); mexc_close_all(); positions.clear()
        send_telegram(f"ปิด TEST แล้ว พร้อมเทรด V12.2 FINAL")
    else:
        send_telegram(f"TEST BUY ล้มเหลว {last_error_detail[:1500]}")
    while True:
        try:
            send_daily_brief()
            is_blackout, reason = is_news_blackout()
            if is_blackout:
                if should_send_hourly(): send_telegram(f"⏸️ [{thai_time()}] ติดข่าว {reason} หยุดเทรด")
                time.sleep(60); continue
            if time.time() < cooldown_until: time.sleep(15); continue
            if is_funding_time(): time.sleep(60); continue
            if check_daily_loss():
                if should_send_hourly(): send_telegram(f"🛑 หยุดวันนี้ เสีย {daily_losses} ไม้")
                time.sleep(60); continue
            price=get_price_mexc()
            if not price: time.sleep(15); continue
            mtf = get_mtf_trend()
            # ATR SL Dynamic
            atr = mtf.get('atr_1h', 5)
            atr_pct_price = (atr * SL_ATR_MULT / price * 100)  # ATR เป็นราคา แปลงเป็น %
            atr_pct_pnl = atr_pct_price * LEVERAGE  # แปลงเป็น PnL %
            current_atr_sl = max(SL_MIN_PCT, min(SL_MAX_PCT, atr_pct_pnl))
            
            df=get_klines_mexc(150, '15m')
            if df is None or len(df)<80: time.sleep(15); continue
            df=calc_lite(df)
            if len(df)<30: time.sleep(15); continue
            last=df.iloc[-1]
            ema20=last['EMA20']; vwap=last['VWAP']; rsi=last['RSI']; adx=last['ADX']; plus_di=last['+DI']; minus_di=last['-DI']; vol_spike=last['VolSpike']
            dist_ema = (last['c']-ema20)/ema20*100
            super_up = plus_di > 28 and minus_di < 12 and adx > 28 and last['c']>ema20 and last['c']>vwap
            uptrend = plus_di > minus_di and adx > ADX_THRESHOLD and last['c']>vwap and plus_di > 20
            downtrend = minus_di > plus_di and adx > 35 and last['c']<vwap and minus_di > 25 and rsi < 35
            near_support = last['c'] <= last['Round10'] + 1.5
            near_resistance = last['c'] >= last['Round10'] - 1.5
            strong_bull = last['c'] > last['o'] and (last['c']-last['o']) > (last['h']-last['l'])*0.6
            strong_bear = last['c'] < last['o'] and (last['o']-last['c']) > (last['h']-last['l'])*0.6
            too_far = abs(dist_ema) > MAX_DIST_EMA
            
            # Re-entry check: ถ้าเพิ่งโดน SL 15 นาทีที่แล้ว และเทรนด์ยังถูก ให้เข้าใหม่ได้
            can_reentry = (time.time() - last_sl_time) < 1800  # 30 นาที
            
            buy=False; sell=False; buy_reason=""; sell_reason=""
            mtf_4h_up = mtf.get('4h_up', True)
            mtf_1h_up = mtf.get('1h_up', True)
            mtf_4h_down = mtf.get('4h_down', False)
            mtf_1h_down = mtf.get('1h_down', False)
            
            if ENABLE_TREND_UP and not too_far:
                if mtf_4h_up and mtf_1h_up:
                    if super_up and last['c']>ema20 and last['c']>vwap and 58<=rsi<=75 and strong_bull and vol_spike and near_support:
                        buy=True; buy_reason=f"SUPER_UP ADX{adx:.0f} MTF 4H-UP 1H-UP ATR_SL {current_atr_sl:.1f}%"
                    elif uptrend and last['c']>vwap and dist_ema>=0 and dist_ema<=MAX_DIST_EMA and 52<=rsi<=72 and near_support and strong_bull and vol_spike:
                        buy=True; buy_reason=f"TREND_UP ADX{adx:.0f} MTF UP ATR {current_atr_sl:.1f}%"
            
            if ENABLE_SHORT and not too_far:
                # SHORT ต้อง 4H DOWN + 1H DOWN เท่านั้น - ถ้า 4H ยัง UP จะไม่ SHORT
                if ENABLE_SIDEWAY_DOWN and mtf_4h_down and mtf_1h_down and downtrend and last['c']<vwap and dist_ema<=0 and dist_ema>=-MAX_DIST_EMA and 25<=rsi<=38 and near_resistance and strong_bear and vol_spike:
                    sell=True; sell_reason=f"SIDEWAY_DOWN MTF 4H-DOWN 1H-DOWN ATR {current_atr_sl:.1f}%"
            
            if should_send_hourly():
                last_hourly=time.time()
                last_hourly_report_minute = thai_now().hour
                total,_=get_balance()
                status = f"ว่าง ADX{adx:.0f}" if len(positions)==0 else f"ถือ {positions[0]['side']}"
                is_blackout, b_reason = is_news_blackout()
                news_info = f" Blackout {b_reason}" if is_blackout else f" Bias {current_bias}"
                mtf_info = f" MTF 4H {'UP' if mtf_4h_up else 'DOWN' if mtf_4h_down else '-'} 1H {'UP' if mtf_1h_up else 'DOWN' if mtf_1h_down else '-'}"
                atr_info = f" ATR {atr:.1f}$ SL {current_atr_sl:.1f}%"
                send_telegram(f"[{thai_time()}] {SYMBOL_CCXT} {price:.2f}$ {status}{news_info}{mtf_info}{atr_info}\nFree {total:.2f}$ Fail:{fail_count}")
            
            if len(positions)==0:
                if buy:
                    ok,qty=mexc_buy(price,1)
                    if ok:
                        positions.append({'entry':price,'qty':qty,'side':'long','time':time.time()})
                        highest=price; lowest=999999; trail_sl=price*(1-current_atr_sl/100/LEVERAGE); be_done=False
                        bal_msg,_=format_balance_msg()
                        send_telegram(f"🟢 LONG XAU {price:.2f}$ {buy_reason}\n{bal_msg}")
                elif sell:
                    ok,qty=mexc_sell(price,1)
                    if ok:
                        positions.append({'entry':price,'qty':-qty,'side':'short','time':time.time()})
                        lowest=price; highest=0; trail_sl=price*(1+current_atr_sl/100/LEVERAGE); be_done=False
                        bal_msg,_=format_balance_msg()
                        send_telegram(f"🔴 SHORT XAU {price:.2f}$ {sell_reason}\n{bal_msg}")
            
            if positions:
                avg=positions[0]['entry']
                is_short = positions[0]['side']=='short'
                pnl=(avg-price)/avg*100*LEVERAGE if is_short else (price-avg)/avg*100*LEVERAGE
                if is_short:
                    if price<lowest: lowest=price
                else:
                    if price>highest: highest=price
                should_exit=False; reason=""
                if not be_done and pnl >= BE_PCT:
                    trail_sl = avg
                    be_done = True
                    send_telegram(f"🔒 BE {pnl:.2f}% ATR SL {current_atr_sl:.1f}%")
                if be_done and pnl >= TRAIL_START_PCT:
                    if is_short:
                        new_sl = price * (1 + TRAIL_PCT/100/LEVERAGE)
                        if new_sl < trail_sl: trail_sl = new_sl
                        if price >= trail_sl: should_exit=True; reason=f"TRAIL {pnl:.2f}%"
                    else:
                        new_sl = price * (1 - TRAIL_PCT/100/LEVERAGE)
                        if new_sl > trail_sl: trail_sl = new_sl
                        if price <= trail_sl: should_exit=True; reason=f"TRAIL {pnl:.2f}%"
                if pnl>=TP_PCT: should_exit=True; reason=f"TP {TP_PCT}%"
                if check_max_hold(): should_exit=True; reason=f"MAX_HOLD {MAX_HOLD_HOURS}h"
                if pnl <= -current_atr_sl*1.1: should_exit=True; reason=f"HARD_SL ATR {pnl:.2f}% SL {current_atr_sl:.1f}%"
                if should_exit: close_position(price, reason)
            time.sleep(15)
        except Exception as e:
            print(f"Loop err {e}")
            traceback.print_exc()
            last_error_detail = f"Loop err {e}"
            time.sleep(15)

threading.Thread(target=trading_loop, daemon=True).start()
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
