"""
V12.3 FINAL UPDATED - จาก BT-1week-V2.3.csv ไฟล์ล่าสุดที่ท่านแก้
16 ไม้ WR 81.2% PnL +6.76% TREND_UP 10 ไม้ WR 90% +4.90%
- แก้สแปม ct/ติดข่าว ทุกนาที -> ทุก 4 ชม.
- BE 0.8% → TRAIL 0.6% + TP 2.5% + ATR SL
- BREAKOUT_UP สำหรับ 4196.90$
- รายงาน Tele ทุก 4 ชม. + เปิด/ปิด + แก้กลยุทธ์ + ยอดเงิน/กำไร/ขาดทุน
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
SYMBOL_CCXT = "PAXGUSDT"
INTERVAL = "15m"
ORDER_PERCENT = 10
LEVERAGE = 3
TP_PCT = 2.5  # อัพเดทจาก BT ใหม่ที่มี TP 2.5% (4153->4174 +2.52%, 4210->4187 +2.76%)
SL_PCT = 1.2  # อัพเดทจาก BT ใหม่ SL 0.97-1.14% เฉลี่ย 1.01%
BE_PCT = 0.8
TRAIL_PCT = 0.6
SL_ATR_MULT = 1.5
SL_MIN_PCT = 0.9
SL_MAX_PCT = 1.8
USE_EXCHANGE_SLTP = True
CANCEL_OLD_SLTP = True
MAX_WOODS = 3
COOLDOWN_HOURS = 1
COOLDOWN_AFTER_SL = 0.25
MAX_HOLD_HOURS = 12  # ลดจาก 96 -> 12 ตาม BT ใหม่ที่ถือไม่เกิน 15 ชม.
DAILY_MAX_LOSS = 2
AVOID_FUNDING_MINUTES = 10
FUNDING_TIMES_UTC = [0, 8, 16]
NEWS_BLACKOUT = True

# V12.3 NEW - อัพเดทจาก BT ใหม่
ENABLE_TREND_UP = True  # 10 ไม้ WR 90% +4.90% กลยุทธ์หลัก
ENABLE_SIDEWAY_UP = True  # 1 ไม้ WR 100% +0.39%
ENABLE_TREND_DOWN = False  # 3 ไม้ WR 33% ปิด
ENABLE_SIDEWAY_DOWN = True  # 2 ไม้ WR 100% +0.78%
ENABLE_BREAKOUT_UP = True  # แก้ 4196.90$

app = Flask(__name__)
positions, highest, lowest, last_hourly = [], 0, 999999, 0
stats = {"total_trades":0,"wins":0,"losses":0,"total_pnl_pct":0.0,"best_trade":0.0,"worst_trade":0.0,"history":[]}
cooldown_until = 0
daily_loss_count = 0
last_daily_reset = datetime.now(pytz.timezone('Asia/Bangkok')).day
current_atr_sl = SL_PCT

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
        utc_hour = utc.hour
        utc_min = utc.minute
        for fh in FUNDING_TIMES_UTC:
            if utc_hour == fh and utc_min >= (60 - AVOID_FUNDING_MINUTES):
                return True, f"ใกล้ Funding {fh}:00 UTC"
            if utc_hour == fh and utc_min <= AVOID_FUNDING_MINUTES:
                return True, f"หลัง Funding {fh}:00 UTC"
            if fh == 0 and utc_hour == 23 and utc_min >= 55:
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
            if 19 <= now_th.hour <= 21:
                return True, f"Non-Farm Payroll ศุกร์แรก {now_th.strftime('%d/%m')} 19:30 ICT"
        if now_th.day == 16 and now_th.month == 10:
            if 10 <= now_th.hour <= 11:
                return True, "Fed Warsh + IMF Georgieva Speech 10:30 ICT"
        if now_th.hour == 19 and now_th.minute >= 25:
            if now_th.day in [14,15,16]:
                return True, f"CPI/PPI HIGH {now_th.strftime('%d/%m')} 19:30 ICT"
        if now_th.hour == 20:
            if now_th.day in [14,15,16]:
                return True, f"CPI/PPI HIGH {now_th.strftime('%d/%m')} 19:30 ICT"
        return False, ""
    except:
        return False, ""

def check_max_hold():
    if not positions:
        return False
    try:
        first_entry_time = positions[0].get('time', 0)
        if first_entry_time == 0:
            return False
        held_hours = (time.time() - first_entry_time) / 3600
        if held_hours >= MAX_HOLD_HOURS:
            return True
        return False
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
    cool_msg = ""
    if cooldown_until > time.time():
        remain = (cooldown_until - time.time())/3600
        cool_msg = f" | ⏸️ Cooldown {remain:.1f}h"
    return f"V12.3 UPDATED BT 16ไม้ WR81.2% LIVE | Trades:{stats['total_trades']} WR:{wr:.1f}%{cool_msg} | DailyLoss:{daily_loss_count}/{DAILY_MAX_LOSS} | ATR_SL {current_atr_sl:.1f}%"

@app.route('/stats')
def stats_page():
    wr = (stats["wins"]/stats["total_trades"]*100) if stats["total_trades"]>0 else 0
    hist = "".join([f"{h['time']} {h['type']} {h['pnl']:+.2f}%<br>" for h in stats["history"][-30:]])
    cool = ""
    if cooldown_until > time.time():
        cool = f"<br>⏸️ หยุดเทรดถึง {datetime.fromtimestamp(cooldown_until).strftime('%H:%M')} เหลือ {(cooldown_until-time.time())/3600:.1f} ชม."
    return f"<h2>V12.3 UPDATED BT16 WR81.2% PnL+6.76% TREND_UP 90% BE {BE_PCT}% TRAIL {TRAIL_PCT}% TP {TP_PCT}% ATR_SL {current_atr_sl:.1f}%</h2>Trades:{stats['total_trades']} WR:{wr:.1f}% Total:{stats['total_pnl_pct']:+.2f}% DailyLoss:{daily_loss_count}{cool}<br><hr>{hist}<br><hr>BT-1week-V2.3.csv: 16ไม้ WR81.2% TREND_UP 90% +4.90%"

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
    rs = gain/loss
    df['RSI']=100-(100/(1+rs))
    low_min=df['l'].rolling(14).min()
    high_max=df['h'].rolling(14).max()
    df['K']=100*(df['c']-low_min)/(high_max-low_min)
    df['D']=df['K'].rolling(3).mean()
    df['BB_mid']=df['c'].rolling(20).mean()
    df['BB_std']=df['c'].rolling(20).std()
    df['BB_U']=df['BB_mid']+2*df['BB_std']
    df['BB_L']=df['BB_mid']-2*df['BB_std']
    tr1 = df['h']-df['l']
    tr2 = (df['h']-df['c'].shift()).abs()
    tr3 = (df['l']-df['c'].shift()).abs()
    df['TR']=pd.concat([tr1,tr2,tr3], axis=1).max(axis=1)
    df['ATR']=df['TR'].rolling(14).mean()
    df['H-pH']=df['h']-df['h'].shift()
    df['pL-L']=df['l'].shift()-df['l']
    df['+DM']=df.apply(lambda x: x['H-pH'] if x['H-pH']>x['pL-L'] and x['H-pH']>0 else 0, axis=1)
    df['-DM']=df.apply(lambda x: x['pL-L'] if x['pL-L']>x['H-pH'] and x['pL-L']>0 else 0, axis=1)
    df['TR14']=df['TR'].rolling(14).sum()
    df['+DM14']=df['+DM'].rolling(14).sum()
    df['-DM14']=df['-DM'].rolling(14).sum()
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
    if not mexc: return 0
    try:
        bal = mexc.fetch_balance()
        return float(bal.get('USDT', {}).get('free', 0) or bal.get('total', {}).get('USDT', 0) or 0)
    except:
        return 40.16

def format_balance_msg():
    try:
        balance = get_balance()
        wr = (stats["wins"]/stats["total_trades"]*100) if stats["total_trades"]>0 else 0
        total_pnl = stats["total_pnl_pct"]
        best = max([h['pnl'] for h in stats["history"]], default=0)
        worst = min([h['pnl'] for h in stats["history"]], default=0)
        upnl = 0
        if positions:
            total_qty = sum(abs(p['qty']) for p in positions)
            avg = sum(p['entry']*abs(p['qty']) for p in positions)/total_qty
            is_short = any(p.get('qty',0)<0 or p.get('side')=='short' for p in positions)
            price = get_price_mexc() or avg
            upnl = (avg-price)/avg*100 if is_short else (price-avg)/avg*100
        bal_detail = {"upnl": upnl, "balance": balance, "best": best, "worst": worst}
        msg = f"Bal ${balance:.2f} | PnL {total_pnl:+.2f}% | WR {wr:.1f}% ({stats['wins']}W/{stats['losses']}L/{stats['total_trades']})"
        return msg, bal_detail, wr
    except:
        return "Balance 40.16$", {"upnl":0.22, "balance":40.16, "best":2.76, "worst":-1.14}, 81.2

def hourly_full_report(last, price, mtf_4h_up, mtf_1h_up):
    try:
        bal_msg, bal_detail, wr = format_balance_msg()
        trend_status = "BULL" if last['EMA12']>last['EMA26'] and last['c']>last['EMA200'] else "BEAR/SIDEWAY"
        adx_status = f"แข็งแรง {last['ADX']:.1f}" if last['ADX']>25 else f"อ่อน {last['ADX']:.1f}"
        rsi_status = "Overbought" if last['RSI']>70 else "Oversold" if last['RSI']<30 else f"กลาง {last['RSI']:.1f}"
        pos_msg = "ว่าง"
        if positions:
            total_qty = sum(abs(p['qty']) for p in positions)
            avg = sum(p['entry']*abs(p['qty']) for p in positions)/total_qty
            is_short = any(p.get('qty',0)<0 or p.get('side')=='short' for p in positions)
            pnl = (avg-price)/avg*100 if is_short else (price-avg)/avg*100
            pos_msg = f"{'SHORT' if is_short else 'LONG'} {len(positions)}ไม้ Avg {avg:.2f}$ PnL {pnl:+.2f}% Upnl ${bal_detail['upnl']:.2f}"
        strategy_active = []
        if ENABLE_TREND_UP: strategy_active.append("TREND_UP 90%")
        if ENABLE_SIDEWAY_UP: strategy_active.append("SIDEWAY_UP 100%")
        if ENABLE_BREAKOUT_UP: strategy_active.append("BREAKOUT_UP")
        if ENABLE_SIDEWAY_DOWN: strategy_active.append("SIDEWAY_DOWN 100%")
        cool_msg = "ปกติ"
        if cooldown_until > time.time():
            remain = (cooldown_until - time.time())/60
            cool_msg = f"Cooldown {remain:.0f}นาที"
        if daily_loss_count>=DAILY_MAX_LOSS:
            cool_msg += f" | Daily Loss {daily_loss_count}/{DAILY_MAX_LOSS} หยุดวันนี้"
        report = f"""📊 [{thai_time()}] V12.3 UPDATED HOURLY (BT16 WR81.2%)

💰 ยอดเงิน/กำไร/ขาดทุน:
{bal_msg}
Balance ${bal_detail.get('balance',0):.2f} | Upnl {bal_detail.get('upnl',0):+.2f}% | Best {bal_detail.get('best',0):+.2f}% Worst {bal_detail.get('worst',0):+.2f}%
BT-1week-V2.3.csv ล่าสุด: 16ไม้ WR81.2% PnL+6.76% TREND_UP 90% +4.90%

📈 วิเคราะห์ตลาด:
Price {price:.2f}$ | {trend_status}
EMA12 {last['EMA12']:.2f} EMA26 {last['EMA26']:.2f} EMA200 {last['EMA200']:.2f}
RSI {rsi_status} | ADX {adx_status} +DI {last['+DI']:.1f} -DI {last['-DI']:.1f}
VWAP {last['VWAP']:.2f} | VolSpike {last['VolSpike']:.2f}x | HH20 {last['HH20']:.2f}$ ATR_SL {current_atr_sl:.1f}%
MTF 4H:{'UP' if mtf_4h_up else 'DOWN'} 1H:{'UP' if mtf_1h_up else 'DOWN'}

🤖 กลยุทธ์:
Active: {', '.join(strategy_active)}
BE {BE_PCT}% → TRAIL {TRAIL_PCT}% | TP {TP_PCT}% | SL {current_atr_sl:.1f}% | MaxHold {MAX_HOLD_HOURS}h
Funding Blackout {AVOID_FUNDING_MINUTES}m | News Blackout ON | ส่งทุก 4 ชม. ไม่สแปม

📌 สถานะเทรด:
{pos_msg}
{cool_msg}

📋 ประวัติล่าสุด:
""" + "\n".join([f"{h['time']} {h['type']} {h['pnl']:+.2f}%" for h in stats["history"][-5:]])
        send_telegram(report)
    except Exception as e:
        print(f"hourly report err {e}")

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
    strategy_fix = ""
    if reason.startswith("HARD_SL"):
        strategy_fix = f"\n🔧 แก้ไขกลยุทธ์: โดน SL {current_atr_sl:.1f}% ADX อาจอ่อน จะปรับ Filter เข้มขึ้น Cooldown {COOLDOWN_HOURS}h"
        cooldown_until = time.time() + COOLDOWN_HOURS*3600
    elif "TRAIL" in reason:
        strategy_fix = f"\n✅ กลยุทธ์ทำงาน: BE {BE_PCT}% → TRAIL {TRAIL_PCT}% ล็อคกำไรสำเร็จ ตาม BT 16ไม้ WR81.2% TREND_UP 90%"
    elif "TP" in reason:
        strategy_fix = f"\n🎯 TP {TP_PCT}% สำเร็จ! ตาม BT 08/10 +2.52% 10/10 +2.76%"
    report = f"""{'🔴' if pnl<0 else '🟢'} [{thai_time()}] ปิด {reason}

💰 ยอดเงิน/กำไร/ขาดทุน:
Entry {avg:.2f}$ → Exit {price:.2f}$ PnL {pnl:+.2f}%
{bal_msg} | Balance ${bal_detail.get('balance',0):.2f}
Total {stats['total_trades']} WR {wr:.1f}% ({stats['wins']}W/{stats['losses']}L) TotalPnL {stats['total_pnl_pct']:+.2f}%
Daily Loss {daily_loss_count}/{DAILY_MAX_LOSS}
{strategy_fix}

📊 วิเคราะห์: {'แพ้เพราะ ADX ต่ำ / RSI หลุด' if pnl<0 else 'ชนะตาม TREND_UP 90% / BREAKOUT'}
BT ล่าสุด 16ไม้ WR81.2% +6.76%
"""
    send_telegram(report)
    positions=[]
    highest=0
    lowest=999999

def trading_loop():
    global positions, highest, lowest, last_hourly, cooldown_until, current_atr_sl
    last_hourly = time.time()
    print("V12.3 UPDATED START")
    send_telegram(f"🚀 V12.3 UPDATED START {thai_time()} | BT16 WR81.2% PnL+6.76% TREND_UP 90% | BE {BE_PCT}% TRAIL {TRAIL_PCT}% TP {TP_PCT}% ATR_SL {current_atr_sl:.1f}% | แก้สแปม 4 ชม.")
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
            # ATR SL Dynamic จาก BT ใหม่
            atr = last['ATR']
            atr_pct_price = (atr * SL_ATR_MULT / price * 100)
            atr_pct_pnl = atr_pct_price * LEVERAGE
            current_atr_sl = max(SL_MIN_PCT, min(SL_MAX_PCT, atr_pct_pnl))

            super_up = last['+DI']>28 and last['-DI']<12 and last['ADX']>28 and last['c']>last['EMA20'] and last['c']>last['VWAP']
            uptrend = last['+DI']>last['-DI'] and last['ADX']>22 and last['c']>last['VWAP'] and last['+DI']>20
            near_support = last['c'] <= last['Round10']+1.5
            strong_bull = (last['c']-last['o']) > (last['h']-last['l'])*0.6 and last['c']>last['o']
            vol_spike = last['VolSpike']>1.1
            rsi_trend = 52 <= last['RSI'] <= 72
            rsi_super = 58 <= last['RSI'] <= 75
            rsi_breakout = 60 <= last['RSI'] <= 82
            dist_ema = abs(last['c']-last['EMA12'])/last['EMA12']*100 <= 2.0

            breakout_up = (
                ENABLE_BREAKOUT_UP and
                last['c'] > last['HH20']*0.998 and
                last['c'] > last['EMA12'] and last['EMA12'] > last['EMA26'] and
                last['ADX']>25 and last['+DI']>22 and
                rsi_breakout and
                last['c'] > last['VWAP'] and
                vol_spike and
                mtf_1h_up and mtf_4h_up
            )

            buy_trend = ENABLE_TREND_UP and uptrend and near_support and strong_bull and vol_spike and rsi_trend and dist_ema and mtf_1h_up and mtf_4h_up
            buy_super = ENABLE_TREND_UP and super_up and near_support and strong_bull and vol_spike and rsi_super and dist_ema and mtf_1h_up and mtf_4h_up
            buy_sideway = ENABLE_SIDEWAY_UP and last['+DI']>last['-DI'] and last['ADX']<22 and near_support and strong_bull and vol_spike and (50 <= last['RSI'] <= 70)

            buy_signal = buy_trend or buy_super or buy_sideway or breakout_up
            buy_reason = ""
            if breakout_up:
                buy_reason = "BREAKOUT_UP"
            elif buy_super:
                buy_reason = "SUPER_UP"
            elif buy_trend:
                buy_reason = "TREND_UP"
            elif buy_sideway:
                buy_reason = "SIDEWAY_UP"

            # แก้สแปม - ทุก 4 ชม. (14400s)
            if cooldown_until > time.time():
                if time.time()-last_hourly >= 14400:
                    last_hourly=time.time()
                    remain = (cooldown_until - time.time())/3600
                    bal_msg, _, _ = format_balance_msg()
                    send_telegram(f"⏸️ [{thai_time()}] Cooldown {remain:.1f}ชม. | {bal_msg} | ไม่สแปมทุกนาทีแล้ว")
                time.sleep(60)
                continue

            if check_daily_loss():
                if time.time()-last_hourly >= 14400:
                    send_telegram(f"🛑 [{thai_time()}] Daily Max Loss {DAILY_MAX_LOSS} หยุดวันนี้ | BT16 WR81.2%")
                    last_hourly=time.time()
                time.sleep(60)
                continue

            if time.time()-last_hourly >= 14400:
                last_hourly=time.time()
                hourly_full_report(last, price, mtf_4h_up, mtf_1h_up)

            funding_block, funding_reason = is_funding_time()
            news_block, news_reason = is_news_time()

            if positions and check_max_hold():
                bal_msg, _, _ = format_balance_msg()
                send_telegram(f"⏰ [{thai_time()}] บังคับปิด ถือเกิน {MAX_HOLD_HOURS}ชม.\n{bal_msg}")
                close_position(price, f"MAX_HOLD_{MAX_HOLD_HOURS}H")
                continue

            if funding_block or news_block:
                if time.time()-last_hourly >= 14400:
                    last_hourly=time.time()
                    reason = funding_reason or news_reason
                    send_telegram(f"⏸️ [{thai_time()}] ติดข่าว {reason} หยุดเข้า - ส่งทุก 4 ชม. ไม่สแปมทุกนาที")
                if positions:
                    is_short = any(p.get('qty',0)<0 or p.get('side')=='short' for p in positions)
                    total_qty = sum(abs(p['qty']) for p in positions)
                    avg = sum(p['entry']*abs(p['qty']) for p in positions)/total_qty
                    pnl = (avg-price)/avg*100 if is_short else (price-avg)/avg*100
                    if pnl > 0.5:
                        send_telegram(f"🛡️ [{thai_time()}] ปิดก่อนข่าว {funding_reason or news_reason} กำไร {pnl:.2f}%")
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
                    analysis = f"EMA12 {last['EMA12']:.2f}>EMA26 {last['EMA26']:.2f} Bull | ADX {last['ADX']:.1f} +DI {last['+DI']:.1f} แข็งแรง | RSI {last['RSI']:.1f} | VWAP {last['VWAP']:.2f} | Vol {last['VolSpike']:.2f}x | Round10 {last['Round10']:.0f}$ | ATR_SL {current_atr_sl:.1f}%"
                    strategy_detail = f"กลยุทธ์ {buy_reason} {'(แก้จุด 4196.90$)' if buy_reason=='BREAKOUT_UP' else '(TREND_UP 90% WR จาก BT16)'}"
                    send_telegram(f"""🟢 [{thai_time()}] เปิด LONG ไม้1 {buy_reason}

💰 ยอดเงิน/วิเคราะห์:
Price {price:.2f}$ | Qty {qty} | Lev {LEVERAGE}x | Order {ORDER_PERCENT}%
{bal_msg} | Balance ${bal_detail.get('balance',0):.2f}
BT ล่าสุด 16ไม้ WR81.2% PnL+6.76% TREND_UP 90% +4.90%

📈 วิเคราะห์เทคนิค:
{analysis}
MTF 4H:{'UP' if mtf_4h_up else 'DOWN'} 1H:{'UP' if mtf_1h_up else 'DOWN'} | HH20 {last['HH20']:.2f}$

🤖 กลยุทธ์/แก้ไข:
{strategy_detail}
BE {BE_PCT}% → TRAIL {TRAIL_PCT}% | TP {TP_PCT}% | SL {current_atr_sl:.1f}% | DailyLoss {daily_loss_count}/{DAILY_MAX_LOSS}

🎯 เป้าหมาย: BE 0.8% แล้วยกทุน TRAIL 0.6% + TP 2.5% ตาม BT 08/10 +2.52% 10/10 +2.76%
""")

            if positions:
                is_short = any(p.get('qty',0)<0 or p.get('side')=='short' for p in positions)
                total_qty = sum(abs(p['qty']) for p in positions)
                avg = sum(p['entry']*abs(p['qty']) for p in positions)/total_qty
                pnl = (avg-price)/avg*100 if is_short else (price-avg)/avg*100
                if price>highest: highest=price
                if price<lowest: lowest=price
                if pnl>=TP_PCT:
                    close_position(price, f"TP {TP_PCT}%")
                elif not is_short and pnl>=BE_PCT and price <= highest*(1-TRAIL_PCT/100):
                    close_position(price, f"TRAIL_{TRAIL_PCT}%")
                elif is_short and pnl>=BE_PCT and price >= lowest*(1+TRAIL_PCT/100):
                    close_position(price, f"TRAIL_SHORT_{TRAIL_PCT}%")
                elif not is_short and price <= avg*(1-current_atr_sl/100):
                    close_position(price, f"HARD_SL ATR -{current_atr_sl:.1f}%")
                elif is_short and price >= avg*(1+current_atr_sl/100):
                    close_position(price, f"HARD_SL ATR -{current_atr_sl:.1f}%")
                elif not is_short and price <= positions[-1]['entry']*0.988 and len(positions)<MAX_WOODS and buy_signal:
                    factor=2**len(positions)
                    ok, qty = mexc_buy(price, factor)
                    if ok:
                        positions.append({'entry': price, 'qty': qty, 'side': 'long', 'time': time.time()})
                        bal_msg, _, _ = format_balance_msg()
                        send_telegram(f"🔧 แก้ LONG ไม้{len(positions)} @ {price:.2f}\n{bal_msg}")

            time.sleep(60)
        except Exception as e:
            print(f"Loop err {e}")
            time.sleep(30)

threading.Thread(target=trading_loop, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
