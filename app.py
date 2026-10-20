"""
V13.7 FINAL - QTY 0.0005 BTC = 5 CONT = $40 Notional Margin $5 แนะนำสุดสำหรับทุน $40
ตามที่คอนเฟิร์ม: 0.0005 BTC = 5 CONT = $40 Notional Margin $5 → แนะนำสุด ✅

ทุน $40:
- 0.001 BTC = 10 CONT = $81 Notional Margin $10 = 25% พอร์ต เสี่ยงไป
- 0.0005 BTC = 5 CONT = $40 Notional Margin $5 = 12.5% พอร์ต พอดีสุด ✅
- 0.0001 BTC = 1 CONT = $8 Notional Margin $1 = 2.5% พอร์ต ปลอดภัยสุดแต่กำไรน้อย

Risk V13.7:
SL 3.4% PnL = ราคาวิ่ง 0.425% = ขาดทุน $0.17/ไม้ = 0.4% พอร์ต
TP 18% PnL = ราคาวิ่ง 2.25% = กำไร $0.91/ไม้ = 2.2% พอร์ต
Ratio 5.3:1 แบบ Trader 85% WR
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
CONTRACT_SIZE = 0.0001  # 1 CONT = 0.0001 BTC
DEFAULT_BTC = 0.0005    # แนะนำสุด
DEFAULT_CONT = 5        # 0.0005 / 0.0001 = 5
LEVERAGE = 8
TP_PCT = 18.0
SL_PCT = 3.4

POSITIONS_FILE = os.path.join(DATA_DIR, "positions.json")
STATS_FILE = os.path.join(DATA_DIR, "stats.json")

app = Flask(__name__)
@app.route('/')
def home(): return f"V13.7 FINAL 0.0005 BTC=5 CONT $40 Margin $5 RECOMMENDED"
@app.route('/health')
def health(): return jsonify({"status":"ok","version":"V13.7","default_btc":DEFAULT_BTC,"default_cont":DEFAULT_CONT,"margin":"$5"})

positions=[]; stats={"total_trades":0,"wins":0,"losses":0,"total_pnl_pct":0.0,"history":[]}

try:
    import ccxt
    mexc = ccxt.mexc({'apiKey': MEXC_API_KEY, 'secret': MEXC_API_SECRET, 'enableRateLimit': True, 'options': {'defaultType': 'swap'}})
    mexc.load_markets()
except:
    mexc=None

def thai_time(): return datetime.now(pytz.timezone('Asia/Bangkok')).strftime("%d/%m/%Y %H:%M ICT")
def save_positions():
    try:
        with open(POSITIONS_FILE,'w',encoding='utf-8') as f: json.dump({"positions":positions}, f)
    except: pass
def load_positions():
    global positions
    try:
        if os.path.exists(POSITIONS_FILE):
            with open(POSITIONS_FILE,'r',encoding='utf-8') as f: positions=json.load(f).get("positions",[])
    except: pass
def save_stats():
    try:
        with open(STATS_FILE,'w',encoding='utf-8') as f: json.dump(stats,f)
    except: pass
def load_stats():
    global stats
    try:
        if os.path.exists(STATS_FILE):
            with open(STATS_FILE,'r',encoding='utf-8') as f: stats.update(json.load(f))
    except: pass
load_stats(); load_positions()

def send_telegram(msg, chat_id=None):
    target=str(chat_id) if chat_id else CHAT_ID
    if not BOT_TOKEN or not target: return False
    try:
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id":target,"text":msg,"parse_mode":"HTML","disable_web_page_preview":True}, timeout=10)
        return True
    except: return False

def get_price_fast():
    try:
        r=requests.get("https://api.binance.com/api/v3/ticker/price", params={"symbol":"BTCUSDT"}, timeout=5)
        if r.status_code==200: return float(r.json().get('price',0))
    except: pass
    return 81500.0

def get_balance_fast():
    if not mexc: return 40.0
    try:
        bal=mexc.fetch_balance()
        return float(bal.get('USDT',{}).get('free',0) or bal.get('total',{}).get('USDT',0) or 40.0)
    except: return 40.0

def convert_btc_to_cont(btc_amount):
    try:
        btc=float(btc_amount)
        if btc >= 1:  # พิมพ์เป็น CONT อยู่แล้ว
            return int(btc)
        # แปลง BTC -> CONT
        cont=int(btc / CONTRACT_SIZE)
        if cont<1: cont=1
        return cont
    except:
        return DEFAULT_CONT

def mexc_open_futures(price, btc_input, side):
    side=side.lower()
    if side in ['buy']: side='long'
    if side in ['sell']: side='short'
    
    cont = convert_btc_to_cont(btc_input)
    btc_actual = cont * CONTRACT_SIZE
    notional = btc_actual * price
    margin = notional / LEVERAGE
    balance = get_balance_fast()

    send_telegram(f"🔄 V13.7 TRY {side.upper()} {btc_actual:.4f} BTC = {cont} CONT\nPrice {price:.0f} Notional ${notional:.2f} Margin ${margin:.2f} Bal ${balance:.2f}\nทุน $40 → 0.0005 BTC=5 CONT Margin $5 แนะนำสุด ✅")

    if balance < margin:
        send_telegram(f"⚠️ Margin ไม่พอ ต้อง ${margin:.2f} มี ${balance:.2f} ลด QTY ลง")
        if cont>1:
            cont=1
            btc_actual=cont*CONTRACT_SIZE
            notional=btc_actual*price
            margin=notional/LEVERAGE

    if not mexc:
        positions.append({'entry':price,'qty':cont,'btc':btc_actual,'side':side,'time':time.time()})
        save_positions()
        send_telegram(f"✅ DRY RUN {side.upper()} {btc_actual:.4f} BTC={cont} CONT @ {price:.0f} Margin ${margin:.2f}")
        return True

    try:
        try: mexc.set_leverage(LEVERAGE, SYMBOL_CCXT, {'marginMode':'CROSS'})
        except: pass
        if side=='long':
            order=mexc.create_market_buy_order(SYMBOL_CCXT, cont)
        else:
            order=mexc.create_market_sell_order(SYMBOL_CCXT, cont)
        send_telegram(f"✅ V13.7 OPEN {side.upper()} {btc_actual:.4f} BTC={cont} CONT @ {price:.0f}$\nNotional ${notional:.2f} Margin ${margin:.2f} Bal ${balance:.2f}\nSL ${margin*0.034:.2f} TP ${margin*0.18:.2f} Ratio 5.3:1")
        positions.append({'entry':price,'qty':cont,'btc':btc_actual,'side':side,'time':time.time()})
        save_positions()
        return True
    except Exception as e:
        try:
            if side=='long':
                order=mexc.create_order(SYMBOL_CCXT, 'market', 'buy', cont, None, {'openType':1})
            else:
                order=mexc.create_order(SYMBOL_CCXT, 'market', 'sell', cont, None, {'openType':1})
            send_telegram(f"✅ V13.7 OPEN m2 {side.upper()} {cont} CONT @ {price:.0f}$")
            positions.append({'entry':price,'qty':cont,'btc':btc_actual,'side':side,'time':time.time()})
            save_positions()
            return True
        except Exception as e2:
            send_telegram(f"❌ FAILED {btc_actual:.4f} BTC={cont} CONT\n{str(e)[:200]}")
            return False

def mexc_close_fast(price, side_filter=None):
    global positions
    if not positions:
        send_telegram("ℹ ไม่มีออเดอร์")
        return False
    to_close = [p for p in positions if p['side']==side_filter] if side_filter in ['long','short'] else positions
    if not to_close: return False
    cont=sum(int(p['qty']) for p in to_close)
    if mexc:
        try:
            if to_close[0]['side']=='long': mexc.create_market_sell_order(SYMBOL_CCXT, cont)
            else: mexc.create_market_buy_order(SYMBOL_CCXT, cont)
        except:
            try:
                if to_close[0]['side']=='long': mexc.create_order(SYMBOL_CCXT, 'market', 'sell', cont, None, {'openType':1})
                else: mexc.create_order(SYMBOL_CCXT, 'market', 'buy', cont, None, {'openType':1})
            except: pass
    avg=sum(p['entry']*p['qty'] for p in to_close)/sum(p['qty'] for p in to_close)
    pnl=(price-avg)/avg*100*LEVERAGE if to_close[0]['side']=='long' else (avg-price)/avg*100*LEVERAGE
    positions=[p for p in positions if p not in to_close]
    save_positions()
    stats["total_trades"]+=1
    if pnl>0: stats["wins"]+=1
    else: stats["losses"]+=1
    stats["total_pnl_pct"]+=pnl
    save_stats()
    btc_total=sum(p['btc'] for p in to_close)
    send_telegram(f"{'🟢' if pnl>0 else '🔴'} CLOSE {to_close[0]['side'].upper()} {btc_total:.4f} BTC={cont} CONT PnL {pnl:+.2f}%")
    return True

def manual_open(side='long', qty_str=None):
    try:
        price=get_price_fast()
        btc_input = qty_str if qty_str else str(DEFAULT_BTC)
        mexc_open_futures(price, btc_input, side=side)
    except Exception as e:
        send_telegram(f"❌ open crash {e}")

def trading_loop():
    price=get_price_fast()
    bal=get_balance_fast()
    send_telegram(f"🚀 V13.7 FINAL 0.0005 BTC=5 CONT START {thai_time()}\nBal ${bal:.2f} Price {price:.0f}\nทุน $40 → 0.0005 BTC=5 CONT Margin $5 แนะนำสุด ✅\nNotional ${price*0.0005:.2f} SL $0.17 TP $0.91 Ratio 5.3:1\n/open long 0.0005 /open short 0.0005 /close")
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

                if text in ["/start","/check","/status"]:
                    bal=get_balance_fast()
                    price=get_price_fast()
                    wr=(stats["wins"]/stats["total_trades"]*100) if stats["total_trades"]>0 else 0
                    notional=price*DEFAULT_BTC
                    margin=notional/LEVERAGE
                    send_telegram(f"✅ V13.7 FINAL 0.0005 BTC=5 CONT\n⏰ {thai_time()}\nPrice {price:.0f} Bal ${bal:.2f}\nPos {len(positions)} WR {wr:.1f}%\n\nทุน $40 แนะนำสุด ✅\n0.0005 BTC=5 CONT\nNotional ${notional:.2f} Margin ${margin:.2f}\nSL $0.17 (0.4%) TP $0.91 (2.2%) Ratio 5.3:1\n\n0.001 BTC=10 CONT Margin $10\n0.0005 BTC=5 CONT Margin $5 ✅\n0.0001 BTC=1 CONT Margin $1\n\n/open long 0.0005\n/open short 0.0005", chat)

                elif text.startswith("/open"):
                    parts=text_raw.split()
                    side='long'
                    qty_str=str(DEFAULT_BTC)
                    if len(parts)>=2:
                        if parts[1].lower() in ['long','short','buy','sell']:
                            side='long' if parts[1].lower() in ['long','buy'] else 'short'
                            if len(parts)>=3: qty_str=parts[2]
                        else:
                            qty_str=parts[1]
                    send_telegram(f"⏳ รับคำสั่ง /open {side} {qty_str} BTC...", chat)
                    threading.Thread(target=manual_open, args=(side, qty_str), daemon=True).start()

                elif text in ["/long","/short"]:
                    side='long' if text=="/long" else 'short'
                    send_telegram(f"⏳ รับคำสั่ง {side} 0.0005 BTC=5 CONT...", chat)
                    threading.Thread(target=manual_open, args=(side, str(DEFAULT_BTC)), daemon=True).start()

                elif text.startswith("/close"):
                    parts=text_raw.split()
                    f=None
                    if len(parts)>=2 and parts[1].lower() in ['long','short']: f=parts[1].lower()
                    send_telegram(f"⏳ รับคำสั่ง /close {f or 'ALL'}...", chat)
                    threading.Thread(target=lambda: mexc_close_fast(get_price_fast(), f), daemon=True).start()

                elif text=="/balance":
                    bal=get_balance_fast()
                    price=get_price_fast()
                    send_telegram(f"💰 Bal ${bal:.2f} Price {price:.0f}\n0.0005 BTC=5 CONT Notional ${price*0.0005:.2f} Margin ${price*0.0005/8:.2f} ✅ทุน $40\n0.001 BTC=10 CONT Margin ${price*0.001/8:.2f}\nSL 0.17 TP 0.91", chat)

        except Exception as e:
            print(f"Tele err {e}")
            time.sleep(5)

threading.Thread(target=telegram_check_loop, daemon=True).start()

if __name__=="__main__":
    port=int(os.environ.get("PORT",10000))
    app.run(host="0.0.0.0", port=port)
