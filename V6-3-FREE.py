
import requests, pandas as pd, os, time, threading, ccxt, traceback, hmac, hashlib, json, urllib.parse
from flask import Flask
from datetime import datetime, timezone
import pytz

MEXC_API_KEY = os.environ.get("MEXC_API_KEY", "").strip()
MEXC_API_SECRET = os.environ.get("MEXC_API_SECRET", "").strip()
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("CHAT_ID", "").strip()

SYMBOL = "GOLD_USDT"
SYMBOL_CCXT = "GOLD/USDT:USDT"
SYMBOL_FALLBACKS = ["GOLD/USDT:USDT", "GOLD_USDT"]
LEVERAGE = 5
TP_PCT = 2.5
SL_PCT = 0.6
FAIL_COOLDOWN_MIN = 2
ADX_THRESHOLD = 20
AVOID_FUNDING_MINUTES = 10
FUNDING_TIMES_UTC = [0, 8, 16]
COOLDOWN_HOURS = 2
MAX_HOLD_HOURS = 48
TRAIL_START_PCT = 0.4
TRAIL_PCT = 0.7
TRAIL_SUPER_PCT = 1.2
BE_PCT = 0.45

app = Flask(__name__)
positions, highest, lowest = [], 0, 999999
stats = {"total_trades":0,"wins":0,"losses":0,"total_pnl_pct":0.0,"daily_pnl":0.0,"last_reset_day":0}
cooldown_until = 0
trail_sl = 0
be_done = False
last_hourly = 0
last_hourly_report_minute = -1
fail_count = 0
last_error_detail = "ยังไม่เคยลอง"

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

@app.route('/')
def home():
    return f"V6 FINAL V2 Fail:{fail_count} Last:{last_error_detail[:500]}"
@app.route('/stats')
def stats_page():
    return f"<h2>FINAL V2</h2>Fail:{fail_count}<br><pre>{last_error_detail}</pre>"

def send_telegram(msg):
    if not BOT_TOKEN or not CHAT_ID: return False
    try:
        r = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id": CHAT_ID, "text": msg, "parse_mode":"HTML", "disable_web_page_preview": True}, timeout=15)
        print(f"TELE {r.status_code}")
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
    return f"💰 {total:.2f}$ Free {free:.2f}$", total, 0

def patch_mexc_markets():
    global last_error_detail
    if not mexc: return False
    gold_market = {'id': 'GOLD_USDT','symbol': 'GOLD/USDT:USDT','base': 'GOLD','quote': 'USDT','settle': 'USDT','baseId': 'GOLD','quoteId': 'USDT','type': 'swap','spot': False,'swap': True,'future': False,'contract': True,'linear': True,'inverse': False,'active': True,'precision': {'amount': 0.0001, 'price': 0.01},'limits': {'amount': {'min': 0.001, 'max': 1000}, 'price': {'min': 0.01, 'max': 100000}}}
    try:
        mexc.load_markets()
        golds = [k for k in mexc.markets.keys() if 'GOLD' in k]
        print(f"[PATCH] Before inject GOLD: {golds}")
        # บังคับ inject เสมอ แม้ load สำเร็จ
        for sym in SYMBOL_FALLBACKS:
            mexc.markets[sym] = gold_market.copy()
            mexc.markets[sym]['symbol'] = sym
        try:
            mexc.markets_by_id['GOLD_USDT'] = gold_market
            mexc.symbols = list(mexc.markets.keys())
        except: pass
        golds_after = [k for k in mexc.markets.keys() if 'GOLD' in k and 'USDT' in k][:10]
        last_error_detail = f"GOLD before={golds} after={golds_after}"
        print(f"[PATCH] After inject {golds_after}")
        return True
    except Exception as e:
        print(f"load fail {e}")
        last_error_detail = f"load fail {e}"
        try:
            for sym in SYMBOL_FALLBACKS:
                mexc.markets[sym] = gold_market.copy()
                mexc.markets[sym]['symbol'] = sym
            mexc.markets_by_id['GOLD_USDT'] = gold_market
            mexc.symbols = list(mexc.markets.keys())
        except: pass
        return True

def get_price_mexc():
    for sym in SYMBOL_FALLBACKS:
        try:
            t = mexc_public.fetch_ticker(sym)
            price = float(t.get('last') or t.get('close') or 0)
            if price > 100: return price
        except Exception as e:
            print(f"price fail {sym} {e}")
            time.sleep(0.3)
    try:
        r = requests.get("https://contract.mexc.com/api/v1/contract/ticker", params={"symbol": "GOLD_USDT"}, timeout=10)
        j = r.json()
        price = float(j.get('data', {}).get('lastPrice') or j.get('data', {}).get('last') or 0)
        if price > 100: return price
    except Exception as e:
        print(f"price REST fail {e}")
    # fallback to Binance XAUT price as approx
    try:
        r = requests.get("https://api.binance.com/api/v3/ticker/price", params={"symbol": "PAXGUSDT"}, timeout=5)
        paxg = float(r.json().get('price') or 0)
        if paxg > 100: return paxg
    except: pass
    return None

def get_klines_mexc(limit=200):
    for sym in SYMBOL_FALLBACKS:
        try:
            ohlcv = mexc_public.fetch_ohlcv(sym, timeframe='15m', limit=limit)
            df = pd.DataFrame(ohlcv, columns=['ot','o','h','l','c','v'])
            if len(df) >= 50: return df
        except: time.sleep(0.3)
    return None

def calc_lite(df):
    df['hlc3'] = (df['h'] + df['l'] + df['c']) / 3
    df['VWAP'] = df['hlc3'].ewm(span=96).mean()
    df['EMA20'] = df['c'].ewm(span=20).mean()
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

# ===== DIRECT MEXC RAW ORDER - ไม่ผ่าน CCXT market check =====
def mexc_raw_order(side, vol=0.01, price=0):
    global last_error_detail
    # side: 1=open long, 3=open short
    try:
        # ใช้ CCXT sign แต่ยิงเองด้วย requests เพื่อไม่ให้โดน market check
        params = {
            'symbol': 'GOLD_USDT',
            'vol': float(vol),
            'side': int(side),
            'type': 5,  # market
            'openType': 1,
            'leverage': LEVERAGE,
        }
        if price and price > 0:
            params['price'] = float(price)
            params['type'] = 1  # limit if price given

        print(f"[RAW] Trying order {params}")
        # ลองใช้ mexc private method ที่ไม่เช็ค market
        # วิธีที่เวิร์คสุด: ใช้ mexc's contractPrivatePostOrderSubmit ถ้ามี
        if hasattr(mexc, 'contract_private_post_order_submit'):
            order = mexc.contract_private_post_order_submit(params)
            print(f"[RAW OK] contract_private_post {order}")
            return True, order
        
        # อีกวิธี: ใช้ mexc.request แต่ต้องแน่ใจว่า params ไม่มี None
        # ลบ None ออก
        clean_params = {k: v for k, v in params.items() if v is not None}
        order = mexc.request('order/submit', 'contractPrivate', 'POST', clean_params)
        print(f"[RAW OK] request {order}")
        return True, order
    except Exception as e:
        err = f"RAW FAIL side {side}: {e}"
        print(err)
        traceback.print_exc()
        last_error_detail = err + "\n" + traceback.format_exc()[:1500]
        return False, err

def mexc_buy(price,factor):
    global fail_count, cooldown_until, last_error_detail
    if not mexc: return True, 0.01
    qty_gold = 0.01
    all_errors = []

    # METHOD 1: CCXT - แต่ตอนนี้ patch แล้ว ต้องมี market
    for sym in SYMBOL_FALLBACKS:
        try:
            print(f"[BUY TRY1] {sym} {qty_gold}")
            # เช็คว่ามี market จริง
            if sym not in mexc.markets:
                raise Exception(f"market {sym} not in mexc.markets after patch")
            try: 
                mexc.set_leverage(LEVERAGE, sym)
            except Exception as le:
                print(f"set lev fail {le}")
            order = mexc.create_market_buy_order(sym, qty_gold)
            print(f"[BUY OK1] {order}")
            fail_count = 0
            last_error_detail = f"OK1 {sym} {order}"
            return True, qty_gold
        except Exception as e:
            err = f"TRY1 {sym}: {str(e)[:300]}"
            all_errors.append(err)
            last_error_detail = err + "\n" + traceback.format_exc()[:800]
            print(err)

    # METHOD 2: RAW - ไม่ผ่าน market check
    ok, res = mexc_raw_order(side=1, vol=qty_gold, price=0)
    if ok:
        fail_count = 0
        last_error_detail = f"OK RAW {res}"
        return True, qty_gold
    else:
        all_errors.append(f"TRY2 RAW: {res}")

    # METHOD 3: RAW with price
    ok, res = mexc_raw_order(side=1, vol=qty_gold, price=price)
    if ok:
        fail_count = 0
        last_error_detail = f"OK RAW price {res}"
        return True, qty_gold
    else:
        all_errors.append(f"TRY3 RAW price {price}: {res}")

    # METHOD 4: Try set position mode then retry
    try:
        print("[BUY TRY4] set position mode")
        for mode in [1,2]:
            try:
                mexc.request('position/mode', 'contractPrivate', 'POST', {'positionMode': mode})
                print(f"set mode {mode} OK")
                ok, res = mexc_raw_order(side=1, vol=qty_gold, price=0)
                if ok:
                    fail_count = 0
                    last_error_detail = f"OK after mode {mode} {res}"
                    return True, qty_gold
            except Exception as me:
                print(f"mode {mode} fail {me}")
    except Exception as e:
        all_errors.append(f"TRY4 mode: {e}")

    fail_count += 1
    cooldown_until = time.time() + FAIL_COOLDOWN_MIN*60
    combined = "\n".join(all_errors)
    last_error_detail = combined[:3000]
    send_telegram(f"⚠️ BUY FAIL V2 {fail_count}ครั้ง\n💰 {get_balance()[0]:.2f}$\n{combined[:2000]}")
    return False, 0

def mexc_sell(price,factor):
    global fail_count, cooldown_until, last_error_detail
    if not mexc: return True, 0.01
    qty_gold = 0.01
    for sym in SYMBOL_FALLBACKS:
        try:
            if sym not in mexc.markets: continue
            try: mexc.set_leverage(LEVERAGE, sym)
            except: pass
            order = mexc.create_market_sell_order(sym, qty_gold)
            fail_count = 0
            return True, qty_gold
        except: time.sleep(0.3)
    ok, res = mexc_raw_order(side=3, vol=qty_gold, price=0)
    if ok:
        fail_count = 0
        return True, qty_gold
    fail_count += 1
    cooldown_until = time.time() + FAIL_COOLDOWN_MIN*60
    return False, 0

def mexc_close_all():
    if not mexc: return True
    for sym in SYMBOL_FALLBACKS:
        try:
            if sym in mexc.markets:
                mexc.create_order(sym, 'market', 'sell', 0, None, {'reduceOnly': True, 'closePosition': True})
                return True
        except:
            try:
                mexc.create_order(sym, 'market', 'buy', 0, None, {'reduceOnly': True, 'closePosition': True})
                return True
            except: continue
    # raw close
    try:
        mexc.request('order/submit', 'contractPrivate', 'POST', {'symbol': 'GOLD_USDT','vol': 0.01,'side': 2,'type': 5,'openType': 1})
        mexc.request('order/submit', 'contractPrivate', 'POST', {'symbol': 'GOLD_USDT','vol': 0.01,'side': 4,'type': 5,'openType': 1})
    except: pass
    return True

def close_position(price,typ):
    global positions,stats,trail_sl,be_done,cooldown_until
    if not positions: return
    avg = sum(p['entry']*abs(p['qty']) for p in positions)/sum(abs(p['qty']) for p in positions) if positions else price
    is_short = positions[0].get('side') == 'short'
    pnl = (avg - price)/avg*100*LEVERAGE if is_short else (price-avg)/avg*100*LEVERAGE
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
    mexc_close_all()
    bal_msg,_ ,_ = format_balance_msg()
    send_telegram(f"✅ [{thai_time()}] {typ} ปิด {pnl:+.2f}%\n{bal_msg}")
    positions.clear()
    global highest, lowest
    highest=0; lowest=999999; trail_sl=0; be_done=False

def should_send_hourly():
    global last_hourly, last_hourly_report_minute
    now = thai_now()
    return (time.time() - last_hourly) >= 3600 or (now.minute == 0 and now.second < 20 and last_hourly_report_minute != now.hour)

def trading_loop():
    global positions,highest,lowest,last_hourly,cooldown_until,last_hourly_report_minute,trail_sl,be_done,last_error_detail
    time.sleep(3)
    patch_mexc_markets()
    bal_msg,total,_=format_balance_msg()
    last_hourly = time.time()
    last_hourly_report_minute = thai_now().hour
    try:
        golds = [k for k in mexc.markets.keys() if 'GOLD' in k and 'USDT' in k][:10] if mexc and mexc.markets else []
        send_telegram(f"🚀 V6 FINAL V2 LIVE {SYMBOL_CCXT} 0.01 GOLD 5X\n{bal_msg}\nGOLD: {golds}\n✅ FINAL V2 force inject + RAW")
    except Exception as e:
        send_telegram(f"🚀 V6 FINAL V2 LIVE {bal_msg}\nErr: {e}")
    time.sleep(2)
    price_test = get_price_mexc()
    if not price_test:
        price_test = 4200.0
        send_telegram(f"⚠️ ดึงราคาไม่ได้ ใช้ {price_test}$ ทดสอบ BUY")
    else:
        send_telegram(f"🧪 ทดสอบ BUY {price_test:.2f}$...")
    ok,qty = mexc_buy(price_test,1)
    if ok:
        send_telegram(f"✅ TEST BUY สำเร็จ! {price_test:.2f}$")
        time.sleep(2)
        mexc_close_all()
        positions.clear()
        send_telegram(f"✅ ปิด TEST แล้ว พร้อมเทรด")
    else:
        send_telegram(f"❌ TEST BUY ล้มเหลว\n{last_error_detail[:1500]}")
    while True:
        try:
            if time.time() < cooldown_until: time.sleep(15); continue
            if is_funding_time(): time.sleep(60); continue
            price=get_price_mexc()
            if not price: time.sleep(15); continue
            df=get_klines_mexc(150)
            if df is None or len(df)<80: time.sleep(15); continue
            df=calc_lite(df)
            if len(df)<30: time.sleep(15); continue
            last=df.iloc[-1]
            ema20=last['EMA20']; vwap=last['VWAP']; rsi=last['RSI']; adx=last['ADX']; plus_di=last['+DI']; minus_di=last['-DI']; vol_spike=last['VolSpike']
            dist_ema = (last['c']-ema20)/ema20*100
            super_up = plus_di > 25 and minus_di < 15 and adx > 25 and last['c']>ema20 and last['c']>vwap
            super_down = minus_di > 25 and plus_di < 15 and adx > 25 and last['c']<ema20 and last['c']<vwap
            uptrend = plus_di > minus_di and adx > ADX_THRESHOLD and last['c']>vwap
            downtrend = minus_di > plus_di and adx > ADX_THRESHOLD and last['c']<vwap
            near_support = last['c'] <= last['Round10'] + 2
            near_resistance = last['c'] >= last['Round10'] - 2
            touch_ema_buy = abs(dist_ema) < 0.5
            touch_ema_sell = abs(dist_ema) < 0.5
            bull_candle = last['c'] > last['o'] and (last['c']-last['o']) > (last['h']-last['l'])*0.5
            bear_candle = last['c'] < last['o'] and (last['o']-last['c']) > (last['h']-last['l'])*0.5
            candle_name = "BULL" if bull_candle else "BEAR" if bear_candle else "DOJI"
            buy=False; sell=False; buy_reason=""; sell_reason=""
            if super_up and last['c']>ema20 and last['c']>vwap and 55<=rsi<=78 and bull_candle and vol_spike:
                buy=True; buy_reason=f"SUPER_UP {candle_name} ADX{adx:.0f}"
            elif uptrend and last['c']>vwap and (touch_ema_buy or 0<=dist_ema<=8) and 50<=rsi<=78 and near_support and bull_candle:
                buy=True; buy_reason=f"TREND_UP {candle_name} ADX{adx:.0f}"
            if super_down and last['c']<ema20 and last['c']<vwap and 20<=rsi<=45 and bear_candle and vol_spike:
                sell=True; sell_reason=f"SUPER_DOWN {candle_name} ADX{adx:.0f}"
            elif downtrend and last['c']<vwap and (touch_ema_sell or -8<=dist_ema<=0) and 22<=rsi<=50 and near_resistance and bear_candle:
                sell=True; sell_reason=f"TREND_DOWN {candle_name} ADX{adx:.0f}"
            if should_send_hourly():
                last_hourly=time.time()
                last_hourly_report_minute = thai_now().hour
                total,_,_=get_balance()
                status = f"ว่าง ADX{adx:.0f} {candle_name}" if len(positions)==0 else f"ถือ {positions[0]['side']}"
                send_telegram(f"⏰ [{thai_time()}] {SYMBOL_CCXT} {price:.2f}$ {status}\n💰 {total:.2f}$ Fail:{fail_count}")
            if len(positions)==0:
                if buy:
                    ok,qty=mexc_buy(price,1)
                    if ok:
                        positions.append({'entry':price,'qty':qty,'side':'long','time':time.time()})
                        highest=price; lowest=999999; trail_sl=price*(1-SL_PCT/100); be_done=False
                        bal_msg,_,_=format_balance_msg()
                        send_telegram(f"🟢 LONG {price:.2f}$ {buy_reason}\nSL {trail_sl:.2f}\n{bal_msg}")
                elif sell:
                    ok,qty=mexc_sell(price,1)
                    if ok:
                        positions.append({'entry':price,'qty':-qty,'side':'short','time':time.time()})
                        lowest=price; highest=0; trail_sl=price*(1+SL_PCT/100); be_done=False
                        bal_msg,_,_=format_balance_msg()
                        send_telegram(f"🔴 SHORT {price:.2f}$ {sell_reason}\nSL {trail_sl:.2f}\n{bal_msg}")
            if positions:
                avg=positions[0]['entry']
                is_short = positions[0]['side']=='short'
                pnl=(avg-price)/avg*100*LEVERAGE if is_short else (price-avg)/avg*100*LEVERAGE
                if is_short:
                    if price<lowest: lowest=price
                else:
                    if price>highest: highest=price
                should_exit=False; reason=""
                if pnl>=TP_PCT: should_exit=True; reason=f"TP {TP_PCT}%"
                if check_max_hold(): should_exit=True; reason=f"MAX_HOLD"
                if pnl <= -SL_PCT*1.5: should_exit=True; reason=f"HARD_SL {pnl:.2f}%"
                if should_exit: close_position(price, reason)
            time.sleep(15)
        except Exception as e:
            print(f"Loop err {e}")
            traceback.print_exc()
            last_error_detail = f"Loop err {e}\n{traceback.format_exc()[:1000]}"
            time.sleep(15)
threading.Thread(target=trading_loop, daemon=True).start()
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
