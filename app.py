"""
V16 AUTO PROFIT BTC - ได้กำไร ไม่ใช่ชนะ
ตามคำขอ: สำเร็จคือ ได้กำไร ไม่ใช่ชนะ

Concept:
- ชนะ 50% ก็กำไรได้ถ้า TP ใหญ่กว่า SL มาก
- ทุน $40: SL 3.4% = -$0.17 TP 18% = +$0.91 Ratio 5.3:1
- 100 ไม้ ชนะ 40 แพ้ 60 = กำไร +$26.2 (40*0.91 - 60*0.17)
- เน้นกำไรสุทธิ $ ไม่เน้น Win Rate %

V16 AUTO PROFIT:
- ราคา REAL MEXC เท่านั้น ไม่มี Binance
- Balance REAL
- SL/TP REAL บน MEXC Exchange
- Auto Signal EMA9/21 + RSI 14 15m BTC
- รันเองได้: /auto on แล้วปล่อย
- Max Daily Loss -$0.50 หยุดเทรดวันนั้น กันขาดทุนหนัก
- เก็บสถิติกำไร $ จริง ไม่ใช่แค่ชนะ
"""

import requests, os, time, threading, json
from flask import Flask, jsonify
from datetime import datetime
import pytz

DATA_DIR = "/mnt/data" if os.path.exists("/mnt/data") else "./data"
os.makedirs(DATA_DIR, exist_ok=True)

MEXC_API_KEY = os.environ.get("MEXC_API_KEY","").strip()
MEXC_API_SECRET = os.environ.get("MEXC_API_SECRET","").strip()
BOT_TOKEN = os.environ.get("BOT_TOKEN","").strip()
CHAT_ID = os.environ.get("CHAT_ID","").strip()

SYMBOL_CCXT = "BTC/USDT:USDT"
DEFAULT_BTC = 0.0005
DEFAULT_CONT = 5
LEVERAGE = 8
DEFAULT_SL_PCT = 3.4
DEFAULT_TP_PCT = 18.0

auto_enabled = False
ema_short = 9
ema_long = 21
rsi_low = 30
rsi_high = 70
rsi_period = 14
max_daily_loss = -0.50
max_positions = 2

POSITIONS_FILE = os.path.join(DATA_DIR, "positions_v16.json")
CONFIG_FILE = os.path.join(DATA_DIR, "config_v16.json")
STATS_FILE = os.path.join(DATA_DIR, "stats_v16.json")
DAILY_FILE = os.path.join(DATA_DIR, "daily_v16.json")

app = Flask(__name__)
@app.route('/')
def home(): return f"V16 AUTO PROFIT BTC | กำไร ไม่ใช่ชนะ | Ratio 5.3:1 SL ${0.17} TP ${0.91}"
@app.route('/health')
def health(): return jsonify({"version":"V16 AUTO PROFIT","concept":"ได้กำไร ไม่ใช่ชนะ","ratio":"5.3:1","sl_usd":"-0.17","tp_usd":"+0.91","auto":auto_enabled})

positions=[]; stats={"total_trades":0,"wins":0,"losses":0,"total_pnl_pct":0.0,"total_pnl_usd":0.0,"auto_trades":0,"profit_days":0,"loss_days":0}; daily={"date":"","pnl_usd":0.0,"trades":0}
mexc=None

try:
    import ccxt
    mexc = ccxt.mexc({'apiKey': MEXC_API_KEY, 'secret': MEXC_API_SECRET, 'enableRateLimit': True, 'options': {'defaultType': 'swap'}})
    print(f"✅ V16 AUTO PROFIT MEXC created")
except Exception as e:
    print(f"CREATE FAIL {e}")
    mexc=None

def thai_time(): return datetime.now(pytz.timezone('Asia/Bangkok')).strftime("%d/%m/%Y %H:%M ICT")
def today_str(): return datetime.now(pytz.timezone('Asia/Bangkok')).strftime("%Y-%m-%d")

def save_all():
    try:
        with open(POSITIONS_FILE,'w',encoding='utf-8') as f: json.dump({"positions":positions}, f)
        with open(STATS_FILE,'w',encoding='utf-8') as f: json.dump(stats,f)
        with open(CONFIG_FILE,'w',encoding='utf-8') as f: json.dump({"auto_enabled":auto_enabled,"ema_short":ema_short,"ema_long":ema_long,"rsi_low":rsi_low,"rsi_high":rsi_high,"max_daily_loss":max_daily_loss}, f)
        with open(DAILY_FILE,'w',encoding='utf-8') as f: json.dump(daily,f)
    except: pass
def load_all():
    global positions, stats, auto_enabled, daily
    try:
        if os.path.exists(POSITIONS_FILE):
            with open(POSITIONS_FILE,'r',encoding='utf-8') as f: positions=json.load(f).get("positions",[])
        if os.path.exists(STATS_FILE):
            with open(STATS_FILE,'r',encoding='utf-8') as f: stats.update(json.load(f))
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE,'r',encoding='utf-8') as f:
                c=json.load(f)
                auto_enabled=c.get("auto_enabled",False)
        if os.path.exists(DAILY_FILE):
            with open(DAILY_FILE,'r',encoding='utf-8') as f: daily.update(json.load(f))
    except: pass
load_all()

def reset_daily_if_new_day():
    global daily
    td = today_str()
    if daily.get("date") != td:
        # สรุปวันก่อน
        if daily.get("date"):
            if daily.get("pnl_usd",0) > 0:
                stats["profit_days"]+=1
            elif daily.get("pnl_usd",0) < 0:
                stats["loss_days"]+=1
        daily={"date":td,"pnl_usd":0.0,"trades":0}
        save_all()

def send_telegram(msg, chat_id=None):
    target=str(chat_id) if chat_id else CHAT_ID
    if not BOT_TOKEN or not target: return False
    try:
        r=requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id":target,"text":msg,"parse_mode":"HTML","disable_web_page_preview":True}, timeout=15)
        return r.json().get('ok',False)
    except: return False

def get_price_mexc_real():
    if mexc:
        try:
            ticker = mexc.fetch_ticker(SYMBOL_CCXT)
            price = ticker.get('last') or ticker.get('close')
            if price and price > 1000:
                return float(price), "MEXC REAL"
        except: pass
        try:
            r=requests.get("https://contract.mexc.com/api/v1/contract/ticker", params={"symbol":"BTC_USDT"}, timeout=5)
            if r.status_code==200:
                last = r.json().get('data',{}).get('lastPrice')
                if last and float(last) > 1000:
                    return float(last), "MEXC contract API"
        except: pass
    return None, "PRICE FAIL"

def get_klines_mexc(limit=100, interval="15m"):
    if mexc:
        try:
            ohlcv = mexc.fetch_ohlcv(SYMBOL_CCXT, interval, limit=limit)
            if ohlcv and len(ohlcv) >= 30:
                closes = [c[4] for c in ohlcv]
                return closes
        except Exception as e:
            print(f"ohlcv fail {e}")
    return []

def calc_ema(prices, period):
    if len(prices) < period: return None
    k = 2 / (period + 1)
    ema = sum(prices[:period]) / period
    for price in prices[period:]:
        ema = price * k + ema * (1 - k)
    return ema

def calc_rsi(prices, period=14):
    if len(prices) < period+1: return 50
    deltas = [prices[i]-prices[i-1] for i in range(1,len(prices))]
    gains = [d if d>0 else 0 for d in deltas]
    losses = [-d if d<0 else 0 for d in deltas]
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss==0: return 100
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

def get_balance_real():
    if not mexc:
        return None, False, "mexc None"
    try:
        bal=mexc.fetch_balance()
        free = bal.get('USDT',{}).get('free',0)
        total = bal.get('USDT',{}).get('total',0)
        usdt = free if free>0 else total
        return float(usdt), True, f"REAL Bal ${float(usdt):.2f}"
    except Exception as e:
        return None, False, f"ERR {str(e)[:150]}"

def convert_qty(qty_input):
    try:
        qty=float(qty_input)
        if qty >= 1: return int(qty)
        return max(1, int(qty / 0.0001))
    except: return DEFAULT_CONT

def calc_sl_tp(entry, side, sl_pct, tp_pct):
    if side=='long':
        sl_price = entry * (1 - sl_pct/100/LEVERAGE)
        tp_price = entry * (1 + tp_pct/100/LEVERAGE)
    else:
        sl_price = entry * (1 + sl_pct/100/LEVERAGE)
        tp_price = entry * (1 - tp_pct/100/LEVERAGE)
    return sl_price, tp_price

def set_real_sl_tp(side, qty, sl_price, tp_price):
    if not mexc:
        return False, False, "mexc None"
    sl_ok=False; tp_ok=False; details=[]
    close_side = 'sell' if side=='long' else 'buy'
    try:
        sl_type = 2 if side=='long' else 1
        tp_type = 1 if side=='long' else 2
        try:
            mexc.create_order(SYMBOL_CCXT, 'market', close_side, qty, None, {'triggerPrice': sl_price,'triggerType': sl_type,'reduceOnly': True,'openType': 1,})
            sl_ok=True
            details.append(f"SL REAL @ {sl_price:.0f}")
        except Exception as e1:
            details.append(f"SL fail {str(e1)[:100]}")
        try:
            mexc.create_order(SYMBOL_CCXT, 'market', close_side, qty, None, {'triggerPrice': tp_price,'triggerType': tp_type,'reduceOnly': True,'openType': 1,})
            tp_ok=True
            details.append(f"TP REAL @ {tp_price:.0f}")
        except Exception as e2:
            details.append(f"TP fail {str(e2)[:100]}")
    except Exception as e:
        details.append(f"fail {str(e)[:100]}")
    return sl_ok, tp_ok, " | ".join(details)

def open_btc_real(price, qty_input, side, sl_pct, tp_pct, source="Manual"):
    global daily
    reset_daily_if_new_day()
    # เช็ค Max Daily Loss
    if daily.get("pnl_usd",0) <= max_daily_loss:
        send_telegram(f"🛑 หยุดเทรดวันนี้ Daily PnL ${daily.get('pnl_usd',0):.2f} ถึง Max Loss ${max_daily_loss} แล้ว\nพรุ่งนี้ค่อยเทรดใหม่ กันขาดทุนหนัก - ได้กำไร ไม่ใช่ชนะ")
        return False

    side='long' if side.lower() in ['long','buy'] else 'short'
    cont = convert_qty(qty_input)
    btc_actual = cont * 0.0001
    notional = btc_actual * price
    margin = notional / LEVERAGE
    balance, is_real, bal_msg = get_balance_real()
    
    if balance is None:
        send_telegram(f"❌ เปิดไม่ได้ {bal_msg}")
        return False
    if balance < margin:
        send_telegram(f"❌ เงินไม่พอ Bal ${balance:.2f} ต้อง ${margin:.2f}")
        return False
    if len(positions) >= max_positions:
        send_telegram(f"ℹ มี {len(positions)} ออเดอร์แล้ว Max {max_positions} - รอปิดก่อน")
        return False

    sl_price, tp_price = calc_sl_tp(price, side, sl_pct, tp_pct)
    sl_usd = margin * sl_pct / 100
    tp_usd = margin * tp_pct / 100

    if not is_real:
        send_telegram(f"❌ Balance ไม่ REAL {bal_msg}")
        return False

    try:
        try: mexc.set_leverage(LEVERAGE, SYMBOL_CCXT, {'marginMode':'CROSS'})
        except: pass
        if side=='long': mexc.create_market_buy_order(SYMBOL_CCXT, cont)
        else: mexc.create_market_sell_order(SYMBOL_CCXT, cont)
        
        sl_ok, tp_ok, details = set_real_sl_tp(side, cont, sl_price, tp_price)
        
        pos={'entry':price,'qty':cont,'btc':btc_actual,'side':side,'time':time.time(),'symbol':SYMBOL_CCXT,'sl_pct':sl_pct,'tp_pct':tp_pct,'sl_price':sl_price,'tp_price':tp_price,'real_sl':sl_ok,'real_tp':tp_ok,'source':source,'sl_usd':sl_usd,'tp_usd':tp_usd}
        positions.append(pos)
        stats["auto_trades"]+=1 if "Auto" in source else 0
        save_all()

        send_telegram(f"✅ V16 AUTO PROFIT OPEN {side.upper()} BTC {cont} CONT @ {price:.0f}\nMargin ${margin:.2f} SL {sl_pct}% -${sl_usd:.2f} @ {sl_price:.0f} {'✅ REAL' if sl_ok else 'Bot'}\nTP {tp_pct}% +${tp_usd:.2f} @ {tp_price:.0f} {'✅ REAL' if tp_ok else 'Bot'} Ratio {tp_pct/sl_pct:.1f}:1\nBal ${balance:.2f} Daily PnL ${daily.get('pnl_usd',0):.2f}\nได้กำไร ไม่ใช่ชนะ - ไม้เล็กแต่ TP ใหญ่")
        return True
    except Exception as e:
        send_telegram(f"❌ OPEN FAILED {str(e)[:250]}")
        return False

def close_btc_real(price, side_filter=None, reason="Manual"):
    global positions, daily
    if not positions:
        return False
    to_close = [p for p in positions if p['side']==side_filter] if side_filter in ['long','short'] else positions
    if not to_close: return False
    cont=sum(int(p['qty']) for p in to_close)
    sym = to_close[0].get('symbol', SYMBOL_CCXT)
    balance, is_real, bal_msg = get_balance_real()
    if is_real:
        try:
            if to_close[0]['side']=='long': mexc.create_market_sell_order(sym, cont)
            else: mexc.create_market_buy_order(sym, cont)
        except:
            try:
                if to_close[0]['side']=='long': mexc.create_order(sym, 'market', 'sell', cont, None, {'openType':1})
                else: mexc.create_order(sym, 'market', 'buy', cont, None, {'openType':1})
            except: pass
        try:
            open_orders = mexc.fetch_open_orders(sym)
            for o in open_orders:
                try: mexc.cancel_order(o['id'], sym)
                except: pass
        except: pass

    avg=sum(p['entry']*p['qty'] for p in to_close)/sum(p['qty'] for p in to_close)
    pnl_pct=(price-avg)/avg*100*LEVERAGE if to_close[0]['side']=='long' else (avg-price)/avg*100*LEVERAGE
    # คำนวณ PnL USD จริง
    btc_total = sum(p['btc'] for p in to_close)
    notional = btc_total * price
    margin_total = notional / LEVERAGE
    pnl_usd = margin_total * pnl_pct / 100

    reset_daily_if_new_day()
    daily["pnl_usd"]+=pnl_usd
    daily["trades"]+=1

    positions=[p for p in positions if p not in to_close]
    stats["total_trades"]+=1
    if pnl_usd>0: stats["wins"]+=1
    else: stats["losses"]+=1
    stats["total_pnl_pct"]+=pnl_pct
    stats["total_pnl_usd"]+=pnl_usd
    save_all()

    emoji="🟢 กำไร" if pnl_usd>0 else "🔴 ขาดทุน"
    profit_factor = stats["total_pnl_usd"]
    send_telegram(f"{emoji} {reason} {to_close[0]['side'].upper()} BTC {cont} CONT @ {price:.0f}\nPnL {pnl_pct:+.2f}% = ${pnl_usd:+.2f}\nTotal PnL ${stats['total_pnl_usd']:+.2f} Daily ${daily['pnl_usd']:+.2f} {bal_msg}\nได้กำไร ไม่ใช่ชนะ - กำไรสุทธิคือเป้าหมาย")
    return True

prev_ema_short = None
prev_ema_long = None

def auto_profit_loop():
    global prev_ema_short, prev_ema_long, auto_enabled
    while True:
        try:
            if not auto_enabled:
                time.sleep(30)
                continue
            reset_daily_if_new_day()
            if daily.get("pnl_usd",0) <= max_daily_loss:
                time.sleep(300)
                continue

            closes = get_klines_mexc(100, "15m")
            if len(closes) < ema_long+5:
                time.sleep(60)
                continue
            ema_s = calc_ema(closes, ema_short)
            ema_l = calc_ema(closes, ema_long)
            rsi = calc_rsi(closes, rsi_period)
            price = closes[-1]
            
            if prev_ema_short is not None and prev_ema_long is not None:
                if prev_ema_short <= prev_ema_long and ema_s > ema_l and rsi < rsi_high:
                    has_long = any(p['side']=='long' for p in positions)
                    if not has_long and len(positions) < max_positions:
                        send_telegram(f"🤖 AUTO PROFIT SIGNAL GOLDEN CROSS\nEMA{ema_short} {ema_s:.0f} > EMA{ema_long} {ema_l:.0f} RSI {rsi:.0f} Price {price:.0f} MEXC REAL\nเปิด LONG เน้นกำไร TP ใหญ่ SL เล็ก Ratio 5.3:1")
                        open_btc_real(price, str(DEFAULT_BTC), 'long', DEFAULT_SL_PCT, DEFAULT_TP_PCT, "Auto PROFIT Golden Cross")
                
                if prev_ema_short >= prev_ema_long and ema_s < ema_l and rsi > rsi_low:
                    has_short = any(p['side']=='short' for p in positions)
                    if not has_short and len(positions) < max_positions:
                        send_telegram(f"🤖 AUTO PROFIT SIGNAL DEATH CROSS\nEMA{ema_short} {ema_s:.0f} < EMA{ema_long} {ema_l:.0f} RSI {rsi:.0f} Price {price:.0f} MEXC REAL\nเปิด SHORT เน้นกำไร TP ใหญ่ SL เล็ก")
                        open_btc_real(price, str(DEFAULT_BTC), 'short', DEFAULT_SL_PCT, DEFAULT_TP_PCT, "Auto PROFIT Death Cross")

            prev_ema_short = ema_s
            prev_ema_long = ema_l
            time.sleep(60)
        except Exception as e:
            print(f"Auto err {e}")
            time.sleep(60)

threading.Thread(target=auto_profit_loop, daemon=True).start()

def check_sl_tp_loop():
    while True:
        try:
            if not positions:
                time.sleep(10)
                continue
            price, src = get_price_mexc_real()
            if price is None:
                time.sleep(10)
                continue
            for pos in positions[:]:
                if pos.get('real_sl') and pos.get('real_tp'):
                    continue
                side = pos['side']
                sl_price = pos.get('sl_price', 0)
                tp_price = pos.get('tp_price', 0)
                hit = None
                if side=='long':
                    if price <= sl_price: hit = f"SL {pos.get('sl_pct',0)}% Bot"
                    elif price >= tp_price: hit = f"TP {pos.get('tp_pct',0)}% Bot"
                else:
                    if price >= sl_price: hit = f"SL {pos.get('sl_pct',0)}% Bot"
                    elif price <= tp_price: hit = f"TP {pos.get('tp_pct',0)}% Bot"
                if hit:
                    close_btc_real(price, reason=f"Auto {hit} @ {price:.0f}")
            time.sleep(10)
        except Exception as e:
            print(f"SL/TP err {e}")
            time.sleep(10)

threading.Thread(target=check_sl_tp_loop, daemon=True).start()

def trading_loop():
    price, src = get_price_mexc_real()
    bal, is_real, bal_msg = get_balance_real()
    reset_daily_if_new_day()
    mode="✅ REAL MODE" if is_real and price else "❌ FAIL"
    send_telegram(f"🚀 V16 AUTO PROFIT START {thai_time()}\n{mode} ได้กำไร ไม่ใช่ชนะ\nBTC {price if price else 'FAIL'} ({src}) {bal_msg}\nทุน $40 → {DEFAULT_BTC} BTC={DEFAULT_CONT} CONT SL -${0.17} TP +${0.91} Ratio 5.3:1\nAuto {'ON ✅' if auto_enabled else 'OFF'} Daily PnL ${daily.get('pnl_usd',0):.2f} Max Loss ${max_daily_loss}\n/auto on เพื่อรันเองทำกำไร")
    while True: time.sleep(60)

threading.Thread(target=trading_loop, daemon=True).start()

def telegram_check_loop():
    if not BOT_TOKEN: return
    try: requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook?drop_pending_updates=True", timeout=10)
    except: pass
    offset=0
    while True:
        try:
            r=requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?offset={offset+1}&timeout=25", timeout=30)
            data=r.json()
            if not data.get("ok"): time.sleep(5); continue
            for upd in data.get("result",[]):
                offset=upd["update_id"]
                msg=upd.get("message",{})
                text_raw=msg.get("text","").strip()
                if not text_raw: continue
                text=text_raw.lower()
                chat=str(msg.get("chat",{}).get("id",""))

                if text in ["/start","/help"]:
                    send_telegram(f"📖 V16 AUTO PROFIT - ได้กำไร ไม่ใช่ชนะ\n\nConcept:\nSL -${0.17} TP +${0.91} Ratio 5.3:1\nชนะ 40% ก็กำไร +$26 จาก 100 ไม้\nเน้นกำไรสุทธิ $ ไม่เน้นชนะ %\n\nคำสั่ง:\n/auto on → รันเองทำกำไร\n/auto off → หยุด\n/price → ราคา REAL\n/check → ดู Pos + กำไร $ + Daily PnL\n/open long {DEFAULT_BTC} sl 3.4 tp 18\n/close /balance\n\nMax Daily Loss ${max_daily_loss} กันขาดทุนหนัก", chat)

                elif text.startswith("/auto"):
                    parts=text_raw.split()
                    if len(parts)>=2 and parts[1].lower()=="on":
                        auto_enabled=True
                        save_all()
                        price, src = get_price_mexc_real()
                        send_telegram(f"✅ เปิด AUTO PROFIT\nBTC {price:.0f} ({src}) EMA{ema_short}/{ema_long} RSI {rsi_low}-{rsi_high}\nSL -${0.17} TP +${0.91} Ratio 5.3:1 Max Loss ${max_daily_loss}/วัน\nบอทรันเองได้ ทำกำไรสุทธิ ไม่เน้นชนะ", chat)
                    elif len(parts)>=2 and parts[1].lower()=="off":
                        auto_enabled=False
                        save_all()
                        send_telegram("❌ ปิด AUTO PROFIT", chat)
                    else:
                        send_telegram(f"🤖 AUTO {'ON ✅' if auto_enabled else 'OFF'} EMA{ema_short}/{ema_long} Daily PnL ${daily.get('pnl_usd',0):.2f}\nTotal PnL ${stats.get('total_pnl_usd',0):+.2f} WR {stats['wins']}/{stats['total_trades']}", chat)

                elif text=="/price":
                    price, src = get_price_mexc_real()
                    bal, is_real, bal_msg = get_balance_mexc_real()
                    if price:
                        send_telegram(f"💰 BTC REAL {price:.2f} ({src})\n{bal_msg}\nทุน $40 {DEFAULT_BTC} BTC={DEFAULT_CONT} CONT SL -${0.17} TP +${0.91}", chat)
                    else:
                        send_telegram(f"❌ {src}", chat)

                elif text in ["/check","/status"]:
                    price, src = get_price_mexc_real()
                    bal, is_real, bal_msg = get_balance_real()
                    reset_daily_if_new_day()
                    pos_txt=""
                    for p in positions:
                        pos_txt+=f"\n{p['side'].upper()} {p['qty']} CONT Entry {p['entry']:.0f} SL {p.get('sl_pct',0)}% -${p.get('sl_usd',0):.2f} TP {p.get('tp_pct',0)}% +${p.get('tp_usd',0):.2f}"
                    if not pos_txt: pos_txt="ว่าง"
                    total_pnl = stats.get('total_pnl_usd',0)
                    send_telegram(f"✅ V16 AUTO PROFIT {'ON' if auto_enabled else 'OFF'}\n⏰ {thai_time()}\nBTC {price if price else 'FAIL'} ({src})\n{bal_msg}\nDaily PnL ${daily.get('pnl_usd',0):+.2f} / Max Loss ${max_daily_loss}\nTotal PnL ${total_pnl:+.2f} Trades {stats['total_trades']} W {stats['wins']} L {stats['losses']}\nPos:{pos_txt}\nได้กำไร ไม่ใช่ชนะ", chat)

                elif text.startswith("/open"):
                    parts=text_raw.split()
                    side='long'; qty_str=None; sl_str=None; tp_str=None
                    i=1
                    while i < len(parts):
                        p=parts[i].lower()
                        if p in ['long','short','buy','sell']:
                            side='long' if p in ['long','buy'] else 'short'
                        elif p=='sl' and i+1 < len(parts):
                            sl_str=parts[i+1]; i+=1
                        elif p=='tp' and i+1 < len(parts):
                            tp_str=parts[i+1]; i+=1
                        else:
                            if qty_str is None:
                                try: float(p); qty_str=p
                                except: pass
                        i+=1
                    if not qty_str: qty_str=str(DEFAULT_BTC)
                    price, src = get_price_mexc_real()
                    if price is None:
                        send_telegram(f"❌ ราคา MEXC ดึงไม่ได้")
                        continue
                    send_telegram(f"⏳ เปิด {side} {qty_str} SL {sl_str or DEFAULT_SL_PCT}% TP {tp_str or DEFAULT_TP_PCT}%...", chat)
                    threading.Thread(target=lambda: open_btc_real(price, qty_str, side, sl_str or DEFAULT_SL_PCT, tp_str or DEFAULT_TP_PCT, "Manual"), daemon=True).start()

                elif text.startswith("/close"):
                    parts=text_raw.split()
                    f=None
                    if len(parts)>=2 and parts[1].lower() in ['long','short']: f=parts[1].lower()
                    price, src = get_price_mexc_real()
                    if price is None:
                        send_telegram("❌ ราคาดึงไม่ได้")
                        continue
                    send_telegram(f"⏳ ปิด {f or 'ALL'} @ {price:.0f}...", chat)
                    threading.Thread(target=lambda: close_btc_real(price, f, "Manual close"), daemon=True).start()

                elif text=="/balance":
                    bal, is_real, bal_msg = get_balance_real()
                    price, src = get_price_mexc_real()
                    send_telegram(f"💰 {bal_msg}\nBTC {price if price else 'FAIL'} ({src})\nTotal PnL ${stats.get('total_pnl_usd',0):+.2f} Daily ${daily.get('pnl_usd',0):+.2f}\nได้กำไร ไม่ใช่ชนะ", chat)

        except Exception as e:
            print(f"Tele err {e}")
            time.sleep(5)

threading.Thread(target=telegram_check_loop, daemon=True).start()

if __name__=="__main__":
    port=int(os.environ.get("PORT",10000))
    app.run(host="0.0.0.0", port=port)
