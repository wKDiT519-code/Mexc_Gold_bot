import requests, pandas as pd, os, time, threading, ccxt, hmac, hashlib, json
from flask import Flask
from datetime import datetime, timezone
import pytz

# === ENV ===
MEXC_API_KEY = os.environ.get("MEXC_API_KEY", "").strip()
MEXC_API_SECRET = os.environ.get("MEXC_API_SECRET", "").strip()
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("CHAT_ID", "").strip()

# === CONFIG V2.3 2-WAY 5X FIX SYMBOL REST ===
SYMBOL = "GOLD_USDT"
SYMBOL_CCXT = "GOLD/USDT:USDT"
SYMBOL_FALLBACKS = ["GOLD/USDT:USDT", "GOLD_USDT", "GOLD/USDT", "XAU/USDT:USDT", "XAUUSDT"]
SYMBOL_REST = "GOLD_USDT"  # สำหรับ REST API
RENDER_URL = "https://v6-cloud-bot-1.onrender.com"
INTERVAL = "15m"

ORDER_PERCENT = 6
LEVERAGE = 5
TP_PCT = 2.5
SL_PCT = 0.6
TRAIL_START_PCT = 0.4
TRAIL_PCT = 0.7
TRAIL_SUPER_PCT = 1.2
BE_PCT = 0.45

MAX_WOODS = 1
WOOD_DISTANCE_PCT = 0.9
COOLDOWN_HOURS = 2
MAX_HOLD_HOURS = 48
MAX_DAILY_LOSS_PCT = 3.0

AVOID_FUNDING_MINUTES = 10
FUNDING_TIMES_UTC = [0, 8, 16]
ADX_THRESHOLD = 20
VWAP_DIST_MAX = 6

app = Flask(__name__)
positions, highest, lowest = [], 0, 999999
stats = {"total_trades":0,"wins":0,"losses":0,"total_pnl_pct":0.0,"history":[],"daily_pnl":0.0,"last_reset_day":0}
cooldown_until = 0
trail_sl = 0
be_done = False
last_hourly = 0
last_hourly_report_minute = -1
last_price_source = "N/A"

mexc_public = ccxt.mexc({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})
mexc = ccxt.mexc({
    'apiKey': MEXC_API_KEY, 
    'secret': MEXC_API_SECRET, 
    'enableRateLimit': True, 
    'options': {'defaultType': 'swap'}
}) if MEXC_API_KEY and MEXC_API_SECRET else None

def thai_time():
    return datetime.now(pytz.timezone('Asia/Bangkok')).strftime("%d/%m/%Y %H:%M ICT")
def thai_now():
    return datetime.now(pytz.timezone('Asia/Bangkok'))

def is_funding_time():
    try:
        now_utc = datetime.now(timezone.utc)
        for ft in FUNDING_TIMES_UTC:
            if now_utc.hour == ft and now_utc.minute < AVOID_FUNDING_MINUTES:
                return True
            if now_utc.hour == ft-1 and now_utc.minute >= (60-AVOID_FUNDING_MINUTES):
                return True
        return False
    except:
        return False

def check_max_hold():
    if not positions: return False
    try:
        ft = positions[0].get('time',0)
        return (time.time()-ft)/3600 >= MAX_HOLD_HOURS if ft else False
    except: return False

@app.route('/')
def home():
    wr = (stats["wins"]/stats["total_trades"]*100) if stats["total_trades"]>0 else 0
    side = positions[0]['side'] if positions else "ว่าง"
    return f"V2.3 2WAY GOLD FIXED-REST WR:{wr:.1f}% PnL:{stats['total_pnl_pct']:+.2f}% Pos:{len(positions)} {side} Src:{last_price_source}"

@app.route('/stats')
def stats_page():
    wr = (stats["wins"]/stats["total_trades"]*100) if stats["total_trades"]>0 else 0
    hist = "".join([f"{h['time']} {h['type']} {h['pnl']:+.2f}%<br>" for h in stats["history"][-15:]])
    side = positions[0]['side'] if positions else "ว่าง"
    return f"<h2>V2.3 2WAY LONG/SHORT FIXED-REST TP{TP_PCT}% SL{SL_PCT}% ADX>{ADX_THRESHOLD} {side} Trail:{trail_sl:.1f}</h2>Trades:{stats['total_trades']} WR:{wr:.1f}% Total:{stats['total_pnl_pct']:+.2f}% Daily:{stats['daily_pnl']:+.2f}% Src:{last_price_source}<br><hr>{hist}"

def send_telegram(msg):
    if not BOT_TOKEN or not CHAT_ID: return False
    try:
        r = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", 
                          json={"chat_id": CHAT_ID, "text": msg, "parse_mode":"HTML", "disable_web_page_preview": True}, timeout=12)
        return r.status_code==200
    except Exception as e:
        print(f"Telegram err {e}")
        return False

def get_price_mexc():
    global last_price_source
    sources = [
        (SYMBOL_CCXT, "ccxt"),
        ("GOLD/USDT:USDT", "ccxt_GOLD/USDT:USDT"),
        ("GOLD_USDT", "ccxt_GOLD_USDT"),
        ("XAUUSDT", "ccxt_XAUUSDT"),
        ("GOLD/USDT", "ccxt_GOLD/USDT"),
        ("XAU/USDT:USDT", "ccxt_XAU/USDT:USDT"),
    ]
    for sym, name in sources:
        try:
            t = mexc_public.fetch_ticker(sym)
            price = float(t.get('last') or t.get('close') or t.get('bid') or 0)
            if price > 100:
                last_price_source = f"{sym}-{name} {price:.2f}"
                print(f"[PRICE OK] {sym} = {price}")
                return price
        except Exception as e:
            print(f"[PRICE FAIL] {sym} err {e}")
            continue
    try:
        r = requests.get("https://contract.mexc.com/api/v1/contract/ticker", params={"symbol": "GOLD_USDT"}, timeout=10)
        j = r.json()
        price = float(j.get('data', {}).get('lastPrice') or j.get('data', {}).get('last') or 0)
        if price > 100:
            last_price_source = f"GOLD_USDT-contract REST {price:.2f}"
            return price
    except: pass
    return None

def get_klines_mexc(limit=200):
    symbols_try = [SYMBOL_CCXT, "GOLD/USDT:USDT", "GOLD_USDT", "XAUUSDT", "GOLD/USDT", "XAU/USDT:USDT"]
    for sym in symbols_try:
        try:
            ohlcv = mexc_public.fetch_ohlcv(sym, timeframe='15m', limit=limit)
            df = pd.DataFrame(ohlcv, columns=['ot','o','h','l','c','v'])
            if len(df) >= 50:
                return df
        except: continue
    return None

def calc_lite(df):
    df['hlc3'] = (df['h'] + df['l'] + df['c']) / 3
    df['VWAP'] = df['hlc3'].ewm(span=96).mean()
    df['EMA20'] = df['c'].ewm(span=20).mean()
    df['EMA50'] = df['c'].ewm(span=50).mean()
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
    low_min=df['l'].rolling(14).min()
    high_max=df['h'].rolling(14).max()
    df['K']=100*(df['c']-low_min)/(high_max-low_min).replace(0,0.001)
    df['D']=df['K'].rolling(3).mean()
    delta=df['c'].diff()
    gain=delta.where(delta>0,0).rolling(14).mean()
    loss=-delta.where(delta<0,0).rolling(14).mean()
    df['RSI']=100-(100/(1+gain/(loss.replace(0,0.001))))
    df['VolEMA'] = df['v'].ewm(span=20).mean()
    df['VolSpike'] = df['v'] > df['VolEMA'] * 1.3
    df['Round10'] = (df['c'] / 10).round() * 10
    df['DistRound10'] = abs(df['c'] - df['Round10'])
    return df.dropna()

def detect_candle_lite(df, i):
    if i < 2: return False, False, ""
    o, c, h, l = df.iloc[i]['o'], df.iloc[i]['c'], df.iloc[i]['h'], df.iloc[i]['l']
    prev_o, prev_c = df.iloc[i-1]['o'], df.iloc[i-1]['c']
    body = abs(c-o)
    lower_wick = min(o,c) - l
    upper_wick = h - max(o,c)
    total_range = h-l if (h-l)>0 else 0.01
    bull_pin = lower_wick > body*1.8 and lower_wick > total_range*0.5 and body < total_range*0.4
    bull_eng = prev_c < prev_o and c > o and c > prev_o and o <= prev_c and body > abs(prev_c-prev_o)
    bear_pin = upper_wick > body*1.8 and upper_wick > total_range*0.5
    bear_eng = prev_c > prev_o and c < o and c < prev_o and o >= prev_c and body > abs(prev_c-prev_o)
    bull = bull_pin or bull_eng
    bear = bear_pin or bear_eng
    name = "HAMMER" if bull_pin else ("BULL_ENGULF" if bull_eng else ("SHOOTING" if bear_pin else ("BEAR_ENGULF" if bear_eng else "")))
    return bull, bear, name

def get_balance():
    if not mexc: return 0.0, 0.0, 0.0
    try:
        bal=mexc.fetch_balance()
        total = float(bal.get('USDT',{}).get('total') or bal.get('total',{}).get('USDT') or 0)
        free = float(bal.get('USDT',{}).get('free') or bal.get('USDT',{}).get('available') or 0)
        return total, free, 0.0
    except Exception as e:
        print(f"Balance err {e}")
        return 10.29, 10.29, 0.0  # fallback from screenshot

def format_balance_msg():
    total, free, _ = get_balance()
    wr = (stats["wins"]/stats["total_trades"]*100) if stats["total_trades"]>0 else 0
    return f"💰 {total:.2f}$ Free {free:.2f}$ | WR {wr:.1f}% PnL {stats['total_pnl_pct']:+.2f}% Daily {stats['daily_pnl']:+.2f}%", total, wr

# === MEXC CONTRACT REST API DIRECT ===
def mexc_rest_sign(params_str=""):
    """สร้าง signature สำหรับ MEXC contract"""
    if not MEXC_API_KEY or not MEXC_API_SECRET:
        return None, None
    req_time = str(int(time.time()*1000))
    # MEXC contract sign = HMAC SHA256 of (apiKey + reqTime + params)
    msg = MEXC_API_KEY + req_time + params_str
    signature = hmac.new(MEXC_API_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return req_time, signature

def mexc_rest_order(side, vol=1, price=None, leverage=5):
    """
    side: 1 open long, 3 open short, 2 close long, 4 close short
    vol: จำนวน contract (GOLD 1 = 0.01 oz ~ $41)
    """
    if not MEXC_API_KEY or not MEXC_API_SECRET:
        print("[REST] No API key")
        return False, "no api key"
    
    # สำหรับ GOLD_USDT, ใช้ isolated margin openType=1
    body = {
        "symbol": SYMBOL_REST,
        "price": float(price) if price else 0,
        "vol": int(vol),
        "side": side,
        "type": 5,  # 5 = market order
        "openType": 1,  # 1=isolated, 2=cross
        "positionId": 0,
        "leverage": int(leverage)
    }
    if price is None or side in [1,3]:  # market open, price ไม่ต้อง
        body["price"] = 0  # market
    
    params_str = json.dumps(body)
    req_time, signature = mexc_rest_sign(params_str)
    
    headers = {
        "ApiKey": MEXC_API_KEY,
        "Request-Time": req_time,
        "Signature": signature,
        "Content-Type": "application/json"
    }
    
    try:
        url = "https://contract.mexc.com/api/v1/private/order/submit"
        r = requests.post(url, headers=headers, data=params_str, timeout=10)
        print(f"[REST ORDER] {body} -> {r.status_code} {r.text}")
        j = r.json()
        if j.get('success') or j.get('code')==0 or j.get('data'):
            return True, j
        else:
            return False, j
    except Exception as e:
        print(f"[REST ORDER ERR] {e}")
        return False, str(e)

def mexc_buy(price,factor):
    if not mexc: 
        print(f"[DEMO] BUY {price}")
        return True, 0.001
    
    # คำนวณ vol: ทุน $10.29 *6% = $0.61, leverage 5 = $3 position
    # GOLD 1 contract = 0.01 oz = ~$41.7 ต้องใช้ vol=1 ขั้นต่ำ
    # ถ้าทุนน้อย ให้ vol=1 ไปเลย
    total, free, _ = get_balance()
    bal = total if total>0 else free
    if bal < 5:
        print(f"Balance too low {bal}, but try vol 1")
    
    # ลอง CCXT ก่อน (ทุก symbol)
    for sym in SYMBOL_FALLBACKS:
        try:
            usdt_amount = bal*(ORDER_PERCENT/100)*factor
            qty = round(max(usdt_amount/price, 0.001), 4)
            try:
                mexc.set_leverage(LEVERAGE, sym)
            except: pass
            order = mexc.create_market_buy_order(sym, qty)
            print(f"BUY OK {sym} qty {qty}")
            return True, qty
        except Exception as e:
            print(f"BUY FAIL {sym} err {e}")
            continue
    
    # ถ้า CCXT ล้มเหลวหมด ใช้ REST API โดยตรง
    print("[BUY] CCXT failed all, trying REST API")
    ok, res = mexc_rest_order(side=1, vol=1, price=price, leverage=LEVERAGE)
    if ok:
        print(f"[BUY REST OK] {res}")
        return True, 1
    else:
        # ลอง side 1 vol 1 แบบ market จริงๆ
        ok2, res2 = mexc_rest_order(side=1, vol=1, leverage=LEVERAGE)
        if ok2:
            return True, 1
        err_msg = f"BUY REST ล้มเหลว {res}"
        print(err_msg)
        send_telegram(f"❌ BUY ล้มเหลว {err_msg}")
        return False, 0

def mexc_sell(price,factor):
    if not mexc: 
        print(f"[DEMO] SELL {price}")
        return True, 0.001
    
    total, free, _ = get_balance()
    
    for sym in SYMBOL_FALLBACKS:
        try:
            usdt_amount = (total if total>0 else free)*(ORDER_PERCENT/100)*factor
            qty = round(max(usdt_amount/price, 0.001), 4)
            try:
                mexc.set_leverage(LEVERAGE, sym)
            except: pass
            order = mexc.create_market_sell_order(sym, qty)
            print(f"SELL OK {sym} qty {qty}")
            return True, qty
        except Exception as e:
            print(f"SELL FAIL {sym} err {e}")
            continue
    
    print("[SELL] CCXT failed all, trying REST API")
    ok, res = mexc_rest_order(side=3, vol=1, price=price, leverage=LEVERAGE)
    if ok:
        print(f"[SELL REST OK] {res}")
        return True, 1
    else:
        ok2, res2 = mexc_rest_order(side=3, vol=1, leverage=LEVERAGE)
        if ok2:
            return True, 1
        err_msg = f"SELL REST ล้มเหลว {res}"
        print(err_msg)
        send_telegram(f"❌ SELL ล้มเหลว {err_msg} | ทุก symbol ล้มเหลว {SYMBOL_FALLBACKS}")
        return False, 0

def mexc_close_all():
    if not mexc: return True
    for sym in SYMBOL_FALLBACKS:
        try:
            mexc.create_order(sym, 'market', 'sell', 0, None, {'reduceOnly': True, 'closePosition': True})
            return True
        except:
            try:
                mexc.create_order(sym, 'market', 'buy', 0, None, {'reduceOnly': True, 'closePosition': True})
                return True
            except:
                continue
    # REST close
    mexc_rest_order(side=2, vol=1, leverage=LEVERAGE)  # close long
    mexc_rest_order(side=4, vol=1, leverage=LEVERAGE)  # close short
    return True

def close_position(price,typ):
    global positions,stats,trail_sl,be_done,cooldown_until
    if not positions: return
    avg = sum(p['entry']*abs(p['qty']) for p in positions)/sum(abs(p['qty']) for p in positions) if positions else price
    is_short = positions[0].get('side') == 'short'
    if is_short:
        pnl = (avg - price)/avg*100*LEVERAGE
    else:
        pnl = (price-avg)/avg*100*LEVERAGE
    today = thai_now().day
    if stats["last_reset_day"] != today:
        stats["daily_pnl"] = 0
        stats["last_reset_day"] = today
    stats["daily_pnl"] += pnl
    stats["total_trades"]+=1
    stats["total_pnl_pct"]+=pnl
    if pnl>0: stats["wins"]+=1
    else: 
        stats["losses"]+=1
        cooldown_until = time.time() + COOLDOWN_HOURS*3600
    stats["history"].append({"time":thai_time(),"type":typ,"pnl":pnl})
    mexc_close_all()
    bal_msg,_,wr=format_balance_msg()
    send_telegram(f"✅ [{thai_time()}] {typ} ปิด {pnl:+.2f}% {positions[0]['side']} WR {wr:.1f}%\n{bal_msg}")
    positions.clear()
    global highest, lowest
    highest=0
    lowest=999999
    trail_sl=0
    be_done=False

def should_send_hourly():
    global last_hourly, last_hourly_report_minute
    now = thai_now()
    time_ok = (time.time() - last_hourly) >= 3600
    hour_top = (now.minute == 0 and now.second < 20 and last_hourly_report_minute != now.hour)
    return time_ok or hour_top

def trading_loop():
    global positions,highest,lowest,last_hourly,cooldown_until,last_hourly_report_minute,trail_sl,be_done
    time.sleep(3)
    bal_msg,_,_=format_balance_msg()
    last_hourly = time.time()
    last_hourly_report_minute = thai_now().hour
    send_telegram(f"🚀 V2.3 2WAY LIVE {SYMBOL_CCXT} TP{TP_PCT}% SL{SL_PCT}% ADX>{ADX_THRESHOLD} Wood:{MAX_WOODS} {ORDER_PERCENT}% LONG+SHORT FIXED-REST")
    while True:
        try:
            if time.time() < cooldown_until:
                time.sleep(30)
                continue
            if is_funding_time():
                time.sleep(60)
                continue
            if stats["daily_pnl"] <= -MAX_DAILY_LOSS_PCT:
                time.sleep(3600)
                continue
            price=get_price_mexc()
            if not price:
                time.sleep(15)
                continue
            df=get_klines_mexc(150)
            if df is None or len(df)<80:
                time.sleep(15)
                continue
            df=calc_lite(df)
            if len(df)<30:
                time.sleep(15)
                continue
            last=df.iloc[-1]
            prev=df.iloc[-2]
            ema20=last['EMA20']
            vwap=last['VWAP']
            rsi=last['RSI']
            adx=last['ADX']
            plus_di=last['+DI']
            minus_di=last['-DI']
            dist_vwap=last['c']-vwap
            dist_ema=last['c']-ema20
            near_support=(abs(last['DistRound10'])<2.5) or (0<=dist_vwap<=6) or (0<=dist_ema<=6)
            near_resistance=(abs(last['DistRound10'])<2.5) or (-6<=dist_vwap<=0) or (-6<=dist_ema<=0)
            touch_ema_buy=last['l']<=ema20 and last['c']>ema20
            touch_ema_sell=last['h']>=ema20 and last['c']<ema20
            bull_candle,bear_candle,candle_name=detect_candle_lite(df, len(df)-1)
            vol_spike=last['VolSpike']
            has_trend=adx>ADX_THRESHOLD
            uptrend=plus_di>minus_di and has_trend
            downtrend=minus_di>plus_di and has_trend
            super_up=adx>25 and (plus_di-minus_di)>10
            super_down=adx>25 and (minus_di-plus_di)>10
            buy=False
            sell=False
            buy_reason=""
            sell_reason=""
            if super_up and last['c']>ema20 and last['c']>vwap and 55<=rsi<=80 and bull_candle and vol_spike:
                buy=True
                buy_reason=f"SUPER_UP {candle_name} ADX{adx:.0f}"
            elif uptrend and last['c']>vwap and (touch_ema_buy or 0<=dist_ema<=8) and 50<=rsi<=78 and near_support and bull_candle:
                buy=True
                buy_reason=f"TREND_UP {candle_name} ADX{adx:.0f}"
            elif not has_trend and touch_ema_buy and 50<=rsi<=70 and near_support and bull_candle and vol_spike:
                buy=True
                buy_reason=f"SIDEWAY_UP {candle_name} ADX{adx:.0f}"
            if super_down and last['c']<ema20 and last['c']<vwap and 20<=rsi<=45 and bear_candle and vol_spike:
                sell=True
                sell_reason=f"SUPER_DOWN {candle_name} ADX{adx:.0f}"
            elif downtrend and last['c']<vwap and (touch_ema_sell or -8<=dist_ema<=0) and 22<=rsi<=50 and near_resistance and bear_candle:
                sell=True
                sell_reason=f"TREND_DOWN {candle_name} ADX{adx:.0f}"
            elif not has_trend and touch_ema_sell and 30<=rsi<=50 and near_resistance and bear_candle and vol_spike:
                sell=True
                sell_reason=f"SIDEWAY_DOWN {candle_name} ADX{adx:.0f}"
            if should_send_hourly():
                last_hourly=time.time()
                last_hourly_report_minute = thai_now().hour
                total,_,_=get_balance()
                if len(positions)==0:
                    status = f"ว่าง ADX{adx:.0f} {candle_name} VWAP{dist_vwap:+.1f}"
                else:
                    avg = positions[0]['entry']
                    is_s = positions[0]['side']=='short'
                    pnl = (avg-price)/avg*100*LEVERAGE if is_s else (price-avg)/avg*100*LEVERAGE
                    status = f"ถือ {positions[0]['side']} {len(positions)}ไม้ PnL {pnl:+.2f}% Trail {trail_sl:.1f}"
                send_telegram(f"⏰ [{thai_time()}] {SYMBOL_CCXT} {price:.2f}$ {status}\n💰 {total:.2f}$ Daily {stats['daily_pnl']:+.2f}%")
            if len(positions)==0:
                if buy:
                    ok,qty=mexc_buy(price,1)
                    if ok:
                        positions.append({'entry':price,'qty':qty,'side':'long','time':time.time()})
                        highest=price
                        lowest=999999
                        trail_sl=price*(1-SL_PCT/100)
                        be_done=False
                        bal_msg,_,_=format_balance_msg()
                        send_telegram(f"🟢 LONG {price:.2f}$ {buy_reason}\nSL {trail_sl:.2f} RSI{rsi:.0f}\n{bal_msg}")
                elif sell:
                    ok,qty=mexc_sell(price,1)
                    if ok:
                        positions.append({'entry':price,'qty':-qty,'side':'short','time':time.time()})
                        lowest=price
                        highest=0
                        trail_sl=price*(1+SL_PCT/100)
                        be_done=False
                        bal_msg,_,_=format_balance_msg()
                        send_telegram(f"🔴 SHORT {price:.2f}$ {sell_reason}\nSL {trail_sl:.2f} RSI{rsi:.0f}\n{bal_msg}")
            if positions:
                avg=positions[0]['entry']
                is_short = positions[0]['side']=='short'
                if is_short:
                    pnl=(avg-price)/avg*100*LEVERAGE
                    if price<lowest: lowest=price
                else:
                    pnl=(price-avg)/avg*100*LEVERAGE
                    if price>highest: highest=price
                if pnl>=BE_PCT and not be_done:
                    if is_short:
                        trail_sl=min(trail_sl, avg*0.999)
                    else:
                        trail_sl=max(trail_sl, avg*1.001)
                    be_done=True
                    send_telegram(f"🔒 ยกทุน {trail_sl:.2f} PnL {pnl:+.2f}% {positions[0]['side']}")
                if pnl>=TRAIL_START_PCT:
                    if is_short:
                        cur_pct = TRAIL_SUPER_PCT if super_down else TRAIL_PCT
                        new_trail=lowest*(1+cur_pct/100)
                        if new_trail < trail_sl or trail_sl==0:
                            trail_sl=new_trail
                            if pnl>=1.0:
                                send_telegram(f"📉 Trail SHORT {trail_sl:.2f} Low {lowest:.2f} PnL {pnl:+.2f}% ADX{adx:.0f}")
                    else:
                        cur_pct = TRAIL_SUPER_PCT if super_up else TRAIL_PCT
                        new_trail=highest*(1-cur_pct/100)
                        if new_trail>trail_sl:
                            trail_sl=new_trail
                            if pnl>=1.0:
                                send_telegram(f"📈 Trail LONG {trail_sl:.2f} High {highest:.2f} PnL {pnl:+.2f}% ADX{adx:.0f}")
                should_exit=False
                reason=""
                if is_short:
                    if price>=trail_sl and trail_sl>0:
                        if not (super_down and last['c']<vwap and last['c']<ema20):
                            should_exit=True
                            reason=f"TRAIL_SHORT {trail_sl:.1f}"
                    if (last['c']>vwap and last['c']>ema20 and rsi>55 and plus_di>minus_di and pnl>0.2):
                        should_exit=True
                        reason="TREND_BREAK_UP"
                    if bull_candle and pnl>0.5 and rsi<30:
                        should_exit=True
                        reason=f"BULL_CANDLE {candle_name}"
                else:
                    if price<=trail_sl and trail_sl>0:
                        if not (super_up and last['c']>vwap and last['c']>ema20):
                            should_exit=True
                            reason=f"TRAIL_LONG {trail_sl:.1f}"
                    if (last['c']<vwap and last['c']<ema20 and rsi<45 and minus_di>plus_di and pnl>0.2):
                        should_exit=True
                        reason="TREND_BREAK_DOWN"
                    if bear_candle and pnl>0.5 and rsi>70:
                        should_exit=True
                        reason=f"BEAR_CANDLE {candle_name}"
                if pnl>=TP_PCT:
                    should_exit=True
                    reason=f"TP {TP_PCT}%"
                if check_max_hold():
                    should_exit=True
                    reason=f"MAX_HOLD {MAX_HOLD_HOURS}h"
                if pnl <= -SL_PCT*1.5:
                    should_exit=True
                    reason=f"HARD_SL {pnl:.2f}%"
                if should_exit:
                    close_position(price, reason)
            time.sleep(15)
        except Exception as e:
            print(f"Loop err {e}")
            import traceback; traceback.print_exc()
            time.sleep(15)

threading.Thread(target=trading_loop, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
