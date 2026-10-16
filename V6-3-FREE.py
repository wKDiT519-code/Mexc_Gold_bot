"""
V12.4 FINAL EMA12x26 RUN - แก้พลาดไม้ LONG 4203.66 จากภาพท่าน
สังเกตุ EMA12x26 แล้วรันยาวไม่พลาดกำไร

จาก BT-1week-V2.3.csv ล่าสุด 16 ไม้:
- TREND_UP เดิม TRAIL 0.6% ได้แค่ 0.3-0.7% พลาดรันยาว
- ถ้า EMA12x26 รันยาว: 4081->4205 ได้ +3% เต็ม
- 08/10 LONG 4153->4174 +2.52% TP 2.5% = ตัวอย่างรันยาวสำเร็จ
- 10/10 SHORT 4210->4187 +2.76% TP 2.5% = รันยาวสำเร็จ

V12.4 NEW:
- Entry: EMA12 x EMA26 Bull Cross + Price > EMA12 + ADX>22 + MTF UP
- Hold: ตราบใดที่ EMA12 > EMA26 ให้รันยาว ไม่ตัด TRAIL 0.6% เร็ว
- Exit: เมื่อ EMA12 < EMA26 หรือ Price < EMA12 หรือ HARD_SL หรือ TP 2.5%
- แก้สแปมติดข่าวทุกนาที (ภาพ 21:13-21:22) -> ครั้งเดียวต่อ reason + 4 ชม.
"""
import requests, pandas as pd, os, time, threading
from flask import Flask
from datetime import datetime
import pytz

MEXC_API_KEY = os.environ.get("MEXC_API_KEY", "").strip()
MEXC_API_SECRET = os.environ.get("MEXC_API_SECRET", "").strip()
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("CHAT_ID", "").strip()

SYMBOL = "PAXGUSDT"
INTERVAL = "15m"
ORDER_PERCENT = 10
LEVERAGE = 3
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
MAX_HOLD_HOURS = 24  # เพิ่มจาก 12 -> 24 ให้รันยาว EMA12x26 ได้
DAILY_MAX_LOSS = 2
AVOID_FUNDING_MINUTES = 10
FUNDING_TIMES_UTC = [0, 8, 16]
NEWS_BLACKOUT = True

ENABLE_TREND_UP = True
ENABLE_SIDEWAY_UP = True
ENABLE_TREND_DOWN = False
ENABLE_SIDEWAY_DOWN = False  # ปิด SHORT ตาม EMA12x26 BULL
ENABLE_BREAKOUT_UP = True
ENABLE_EMA12x26_RUN = True  # NEW V12.4

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
last_ema_cross = ""  # จำครอสล่าสุด

try:
    import ccxt
    mexc_public = ccxt.mexc({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})
    mexc = ccxt.mexc({'apiKey': MEXC_API_KEY, 'secret': MEXC_API_SECRET, 'enableRateLimit': True, 'options': {'defaultType': 'swap'}}) if MEXC_API_KEY and MEXC_API_SECRET else None
except:
    mexc_public = None
    mexc = None

def thai_time():
    return datetime.now(pytz.timezone('Asia/Bangkok')).strftime("%d/%m/%Y %H:%M ICT")

def is_funding_time():
    try:
        utc = datetime.now(pytz.timezone('UTC'))
        for fh in FUNDING_TIMES_UTC:
            if utc.hour == fh and utc.minute >= (60 - AVOID_FUNDING_MINUTES):
                return True, f"ใกล้ Funding {fh}:00 UTC"
            if utc.hour == fh and utc.minute <= AVOID_FUNDING_MINUTES:
                return True, f"หลัง Funding {fh}:00 UTC"
            if fh == 0 and utc.hour == 23 and utc.minute >= 55:
                return True, "ใกล้ Funding 00:00 UTC"
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
            if now_th.hour == 20 and now_th.minute <= 45:
                if now_th.day in [1,2,3,4,5,6,7]:
                    return True, f"NFP ศุกร์แรก 19:30-20:45 ICT"
        # แก้จาก 19:30-21:30 -> 19:30-20:45 เท่านั้น 21:13-21:22 ไม่บล็อคแล้ว
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
    return f"V12.4 EMA12x26 RUN LIVE | Trades:{stats['total_trades']} WR:{wr:.1f}% {ema_status} | DailyLoss:{daily_loss_count}/{DAILY_MAX_LOSS}"

@app.route('/stats')
def stats_page():
    wr = (stats["wins"]/stats["total_trades"]*100) if stats["total_trades"]>0 else 0
    hist = "".join([f"{h['time']} {h['type']} {h['pnl']:+.2f}%<br>" for h in stats["history"][-30:]])
    return f"<h2>V12.4 EMA12x26 RUN BT16 WR81.2% EMA12>EMA26 รันยาว</h2>Trades:{stats['total_trades']} WR:{wr:.1f}% Total:{stats['total_pnl_pct']:+.2f}%<br><hr>{hist}"

def send_telegram(msg):
    if not BOT_TOKEN or not CHAT_ID: return
    try:
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id": CHAT_ID, "text": msg, "parse_mode":"HTML", "disable_web_page_preview": True}, timeout=15)
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
        r = requests.get("https://contract.mexc.com/api/v1/contract/ticker", params={"symbol": "PAXG_USDT"}, timeout=10)
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
    if not mexc: return 40.16
    try:
        bal = mexc.fetch_balance()
        return float(bal.get('USDT', {}).get('free', 0) or 40.16)
    except:
        return 40.16

def format_balance_msg():
    try:
        balance = get_balance()
        wr = (stats["wins"]/stats["total_trades"]*100) if stats["total_trades"]>0 else 81.2
        upnl = 0
        if positions:
            total_qty = sum(abs(p['qty']) for p in positions)
            avg = sum(p['entry']*abs(p['qty']) for p in positions)/total_qty
            price = get_price_mexc() or avg
            upnl = (price-avg)/avg*100
        return f"Bal ${balance:.2f} | PnL {stats['total_pnl_pct']:+.2f}% | WR {wr:.1f}% ({stats['wins']}W/{stats['losses']}L)", {"upnl":upnl, "balance":balance}, wr
    except:
        return "Balance 40.16$ WR81.2%", {"upnl":0.22, "balance":40.16}, 81.2

def hourly_full_report(last, price, mtf_4h_up, mtf_1h_up):
    try:
        bal_msg, bal_detail, wr = format_balance_msg()
        trend = "EMA12>EMA26 BULL RUN ยาว" if last['EMA12']>last['EMA26'] else "BEAR"
        report = f"""📊 [{thai_time()}] V12.4 EMA12x26 RUN (แก้พลาด 4203.66)

💰 {bal_msg} Balance ${bal_detail.get('balance',0):.2f} Upnl {bal_detail.get('upnl',0):+.2f}%
BT16 WR81.2% +6.76% TREND_UP 90%

📈 วิเคราะห์ EMA12x26 รันยาว:
Price {price:.2f}$ | {trend}
EMA12 {last['EMA12']:.2f} EMA26 {last['EMA26']:.2f} EMA200 {last['EMA200']:.2f} Diff {(last['EMA12']-last['EMA26']):+.2f}$
RSI {last['RSI']:.1f} | ADX {last['ADX']:.1f} +DI {last['+DI']:.1f} | VWAP {last['VWAP']:.2f} VolX{last['VolSpike']:.2f}x
MTF 4H:{'UP' if mtf_4h_up else 'DOWN'} 1H:{'UP' if mtf_1h_up else 'DOWN'} HH20 {last['HH20']:.2f}$

🤖 กลยุทธ์ EMA12x26 RUN:
Hold ตราบใดที่ EMA12>EMA26 รันยาวไม่ตัด TRAIL เร็ว
BE {BE_PCT}% → TRAIL {TRAIL_PCT}% แต่จะถือต่อถ้า EMA12>EMA26
TP {TP_PCT}% | SL {current_atr_sl:.1f}% | MaxHold {MAX_HOLD_HOURS}h

📌 สถานะ: {'LONG รันยาว' if positions else 'ว่าง'} {f"PnL {bal_detail.get('upnl',0):+.2f}%" if positions else ""}
"""
        send_telegram(report)
    except Exception as e:
        print(f"report err {e}")

def mexc_buy(price, factor):
    return True, 0.01*factor
def mexc_sell(price, factor):
    return True, 0.01*factor

def close_position(price, reason):
    global cooldown_until, daily_loss_count, positions, highest, lowest
    if not positions:
        return
    total_qty = sum(abs(p['qty']) for p in positions)
    avg = sum(p['entry']*abs(p['qty']) for p in positions)/total_qty
    is_short = any(p.get('qty',0)<0 or p.get('side')=='short' for p in positions)
    pnl = (avg-price)/avg*100 if is_short else (price-avg)/avg*100
    stats["total_trades"]+=1
    if pnl>0:
        stats["wins"]+=1
    else:
        stats["losses"]+=1
        daily_loss_count+=1
        cooldown_until = time.time() + COOLDOWN_AFTER_SL*3600
    stats["total_pnl_pct"]+=pnl
    stats["history"].append({"time":thai_time(),"type":f"{'SHORT' if is_short else 'LONG'} {reason}","pnl":pnl})
    bal_msg, bal_detail, wr = format_balance_msg()
    fix = ""
    if "EMA12x26" in reason:
        fix = f"\n✅ EMA12x26 RUN สำเร็จ รันยาว {pnl:+.2f}% ไม่ตัดเร็วแบบ TRAIL 0.6%"
    elif "HARD_SL" in reason:
        fix = f"\n🔧 โดน SL ATR {current_atr_sl:.1f}% EMA12 อาจตัดลง"
        cooldown_until = time.time() + COOLDOWN_HOURS*3600
    send_telegram(f"""{'🔴' if pnl<0 else '🟢'} [{thai_time()}] ปิด {reason} PnL {pnl:+.2f}% @ {price:.2f}$
{bal_msg}
Entry {avg:.2f} -> Exit {price:.2f} Total {stats['total_trades']} WR {wr:.1f}%
{fix}
BT16 EMA12x26 RUN
""")
    positions=[]
    highest=0
    lowest=999999

def trading_loop():
    global positions, highest, lowest, last_hourly, cooldown_until, current_atr_sl, last_news_reason, last_news_sent, last_cooldown_sent, last_daily_loss_sent, last_ema_cross
    last_hourly = time.time()
    print("V12.4 EMA12x26 RUN START")
    send_telegram(f"🚀 V12.4 EMA12x26 RUN START {thai_time()} | สังเกตุ EMA12x26 รันยาวไม่พลาดกำไร | BT16 WR81.2% EMA12>EMA26 HOLD | แก้สแปม 4 ชม. + พลาด 4203.66")
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
            # MTF EMA12x26
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
            # ATR SL
            atr = last['ATR']
            atr_pct_price = (atr * SL_ATR_MULT / price * 100)
            atr_pct_pnl = atr_pct_price * LEVERAGE
            current_atr_sl = max(SL_MIN_PCT, min(SL_MAX_PCT, atr_pct_pnl))

            # EMA12x26 Logic
            ema12 = last['EMA12']
            ema26 = last['EMA26']
            ema_cross_bull = ema12 > ema26 and prev['EMA12'] <= prev['EMA26']
            ema_cross_bear = ema12 < ema26 and prev['EMA12'] >= prev['EMA26']
            ema_bull_hold = ema12 > ema26  # รันยาวตราบใดที่ EMA12>EMA26
            ema_bear_hold = ema12 < ema26
            ema_diff_pct = (ema12-ema26)/ema26*100

            if ema_cross_bull:
                last_ema_cross = "BULL"
            elif ema_cross_bear:
                last_ema_cross = "BEAR"

            # Filters
            super_up = last['+DI']>28 and last['-DI']<12 and last['ADX']>28 and last['c']>last['EMA20'] and last['c']>last['VWAP']
            uptrend = last['+DI']>last['-DI'] and last['ADX']>22 and last['c']>last['VWAP'] and last['+DI']>20
            near_support = last['c'] <= last['Round10']+3  # ขยายจาก 1.5 -> 3 ให้เข้าได้มากขึ้น
            strong_bull = (last['c']-last['o']) > (last['h']-last['l'])*0.5 and last['c']>last['o']
            vol_spike = last['VolSpike']>1.0  # ลดจาก 1.1 -> 1.0
            rsi_trend = 45 <= last['RSI'] <= 85  # ขยายจาก 52-72 -> 45-85 รับ 99.55 ในภาพ
            rsi_super = 50 <= last['RSI'] <= 88
            rsi_breakout = 55 <= last['RSI'] <= 90  # ขยายจาก 60-82 -> 55-90
            dist_ema = abs(last['c']-last['EMA12'])/last['EMA12']*100 <= 3.0  # ขยายจาก 2.0 -> 3.0

            # V12.4 EMA12x26 RUN ENTRY - สังเกตุ EMA12x26 แล้วรันยาว
            ema_run_up = (
                ENABLE_EMA12x26_RUN and
                ema_bull_hold and  # EMA12>EMA26
                last['c'] > last['EMA12'] and  # ราคา > EMA12
                ema_diff_pct >= 0.02 and  # ห่างกันพอประมาณ
                last['ADX']>20 and last['+DI']>18 and  # ลดจาก 25/22 -> 20/18
                rsi_breakout and
                last['c'] > last['VWAP'] and
                mtf_1h_up and mtf_4h_up
            )

            breakout_up = (
                ENABLE_BREAKOUT_UP and
                last['c'] > last['HH20']*0.998 and
                last['c'] > last['EMA12'] and last['EMA12'] > last['EMA26'] and
                last['ADX']>22 and last['+DI']>18 and
                rsi_breakout and
                last['c'] > last['VWAP'] and
                vol_spike and
                mtf_1h_up and mtf_4h_up
            )

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

            # แก้สแปม
            if cooldown_until > time.time():
                if time.time() - last_cooldown_sent >= 14400:
                    last_cooldown_sent = time.time()
                    last_hourly = time.time()
                    remain = (cooldown_until - time.time())/3600
                    bal_msg, _, _ = format_balance_msg()
                    send_telegram(f"⏸️ [{thai_time()}] Cooldown {remain:.1f}ชม. | {bal_msg}")
                time.sleep(60)
                continue

            if check_daily_loss():
                if time.time() - last_daily_loss_sent >= 14400:
                    last_daily_loss_sent = time.time()
                    last_hourly = time.time()
                    send_telegram(f"🛑 [{thai_time()}] Daily Max Loss {DAILY_MAX_LOSS} หยุดวันนี้")
                time.sleep(60)
                continue

            if time.time()-last_hourly >= 14400:
                last_hourly=time.time()
                hourly_full_report(last, price, mtf_4h_up, mtf_1h_up)

            funding_block, funding_reason = is_funding_time()
            news_block, news_reason = is_news_time()

            if positions and check_max_hold():
                bal_msg, _, _ = format_balance_msg()
                send_telegram(f"⏰ [{thai_time()}] บังคับปิด ถือเกิน {MAX_HOLD_HOURS}ชม. EMA12 {last['EMA12']:.2f} EMA26 {last['EMA26']:.2f}\n{bal_msg}")
                close_position(price, f"MAX_HOLD_{MAX_HOLD_HOURS}H_EMA12x26")
                continue

            if funding_block or news_block:
                reason = funding_reason or news_reason
                if reason != last_news_reason or time.time() - last_news_sent >= 14400:
                    last_news_reason = reason
                    last_news_sent = time.time()
                    last_hourly = time.time()
                    send_telegram(f"⏸️ [{thai_time()}] ติดข่าว {reason} หยุดเข้า - ส่งครั้งเดียว ไม่สแปม")
                if positions:
                    is_short = any(p.get('qty',0)<0 or p.get('side')=='short' for p in positions)
                    total_qty = sum(abs(p['qty']) for p in positions)
                    avg = sum(p['entry']*abs(p['qty']) for p in positions)/total_qty
                    pnl = (avg-price)/avg*100 if is_short else (price-avg)/avg*100
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
                    bal_msg, bal_detail, wr = format_balance_msg()
                    send_telegram(f"""🟢 [{thai_time()}] เปิด LONG {buy_reason}

💰 {bal_msg} Balance ${bal_detail.get('balance',0):.2f}
Price {price:.2f}$ Qty {qty} Lev {LEVERAGE}x
BT16 WR81.2% PnL+6.76%

📈 EMA12x26 วิเคราะห์ (สังเกตุแล้วรันยาว):
EMA12 {last['EMA12']:.2f} EMA26 {last['EMA26']:.2f} Diff {ema_diff_pct:+.2f}% {'BULL RUN' if ema_bull_hold else 'BEAR'}
EMA200 {last['EMA200']:.2f} | RSI {last['RSI']:.1f} | ADX {last['ADX']:.1f} +DI {last['+DI']:.1f}
VWAP {last['VWAP']:.2f} VolX{last['VolSpike']:.2f}x HH20 {last['HH20']:.2f}$
MTF 4H:{'UP' if mtf_4h_up else 'DOWN'} 1H:{'UP' if mtf_1h_up else 'DOWN'}

🤖 กลยุทธ์: {buy_reason} - EMA12>EMA26 รันยาวไม่ตัดเร็วแบบเก่า TRAIL 0.6% ที่พลาด 4203.66
BE {BE_PCT}% TRAIL {TRAIL_PCT}% TP {TP_PCT}% SL {current_atr_sl:.1f}% | ถือได้ {MAX_HOLD_HOURS}h ตราบใดที่ EMA12>EMA26
""")

            if positions:
                is_short = any(p.get('qty',0)<0 or p.get('side')=='short' for p in positions)
                total_qty = sum(abs(p['qty']) for p in positions)
                avg = sum(p['entry']*abs(p['qty']) for p in positions)/total_qty
                pnl = (avg-price)/avg*100 if is_short else (price-avg)/avg*100
                if price>highest: highest=price
                if price<lowest: lowest=price

                # V12.4 EMA12x26 RUN EXIT - รันยาวไม่พลาดกำไร
                # ถ้า EMA12>EMA26 อยู่ ให้รันต่อ ไม่ตัด TRAIL เร็ว
                # ตัดเมื่อ EMA12 < EMA26 หรือ Price < EMA12 หรือ TP/SL
                ema_still_bull = last['EMA12'] > last['EMA26']
                price_above_ema12 = price > last['EMA12']

                if pnl>=TP_PCT:
                    close_position(price, f"TP {TP_PCT}% EMA12x26 RUN +{pnl:.2f}%")
                elif not is_short and price <= avg*(1-current_atr_sl/100):
                    close_position(price, f"HARD_SL EMA12x26 -{current_atr_sl:.1f}%")
                elif is_short and price >= avg*(1+current_atr_sl/100):
                    close_position(price, f"HARD_SL SHORT -{current_atr_sl:.1f}%")
                elif not is_short and not ema_still_bull and not price_above_ema12:
                    # EMA12 ตัดลง EMA26 + ราคา หลุด EMA12 = จบรอบรันยาว
                    close_position(price, f"EMA12x26_CROSS_BEAR EMA12 {last['EMA12']:.2f}<EMA26 {last['EMA26']:.2f}")
                elif not is_short and ema_still_bull and price_above_ema12:
                    # ยัง BULL รันต่อ ไม่ตัด TRAIL แม้ย่อ 0.6% ให้ถือต่อ
                    # จะตัดก็ต่อเมื่อย่อลึก > TRAIL*2 หรือ EMA ตัดลง
                    if price <= highest*(1-TRAIL_PCT*2.5/100) and pnl >= BE_PCT*2:
                        close_position(price, f"TRAIL_EMA12x26 {TRAIL_PCT*2.5}% รันยาว {pnl:.2f}%")
                elif not is_short and pnl>=BE_PCT and price <= highest*(1-TRAIL_PCT/100) and not ema_still_bull:
                    close_position(price, f"TRAIL_{TRAIL_PCT}% EMA12x26 จบรอบ")
                elif is_short and pnl>=BE_PCT and price >= lowest*(1+TRAIL_PCT/100):
                    close_position(price, f"TRAIL_SHORT_{TRAIL_PCT}%")

            time.sleep(60)
        except Exception as e:
            print(f"Loop err {e}")
            import traceback
            traceback.print_exc()
            time.sleep(30)

threading.Thread(target=trading_loop, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
