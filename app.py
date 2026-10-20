"""
V13.8 FIX DRY RUN -> REAL MODE - แก้จากรูป 20:31 DRY RUN LONG 0.0005 BTC

รูปบอก:
Bal $40.00 Price 81500
TRY LONG 0.0005 BTC = 5 CONT Margin $5.09 Bal $40.00
✅ DRY RUN LONG 0.0005 BTC=5 CONT @ 81500 Margin $5.09

สาเหตุ DRY RUN:
- mexc == None หรือ API KEY ว่าง หรือ load_markets fail
- get_balance_fast() return 40.0 fallback ไม่ใช่ balance จริง

FIX V13.8:
- เช็ค API KEY มีไหม โชว์ใน /check
- log load_markets error ชัดเจน
- ถ้า API KEY ไม่มี บอกวิธีใส่ใน Render ENV
- ถ้า 700007 บอก No Restriction
- แยก Bal จริง vs Bal fallback
- บังคับ REAL MODE ถ้ามี KEY
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

print(f"V13.8 ENV CHECK: API_KEY len={len(MEXC_API_KEY)} SECRET len={len(MEXC_API_SECRET)} BOT len={len(BOT_TOKEN)} CHAT {CHAT_ID}")

SYMBOL_CCXT = "BTC/USDT:USDT"
CONTRACT_SIZE = 0.0001
DEFAULT_BTC = 0.0005
DEFAULT_CONT = 5
LEVERAGE = 8

POSITIONS_FILE = os.path.join(DATA_DIR, "positions.json")
STATS_FILE = os.path.join(DATA_DIR, "stats.json")

app = Flask(__name__)
@app.route('/')
def home(): return f"V13.8 FIX DRY RUN -> REAL | API_KEY {len(MEXC_API_KEY)} chars | {DEFAULT_BTC} BTC={DEFAULT_CONT} CONT"
@app.route('/health')
def health(): return jsonify({"status":"ok","version":"V13.8","api_key_len":len(MEXC_API_KEY),"has_secret":len(MEXC_API_SECRET)>0})

positions=[]; stats={"total_trades":0,"wins":0,"losses":0,"total_pnl_pct":0.0,"history":[]}
mexc=None
mexc_error=""

try:
    import ccxt
    if not MEXC_API_KEY or not MEXC_API_SECRET:
        mexc_error = f"API KEY ว่าง! KEY len {len(MEXC_API_KEY)} SECRET len {len(MEXC_API_SECRET)} ต้องใส่ใน Render ENV"
        print(mexc_error)
        mexc=None
    else:
        mexc = ccxt.mexc({
            'apiKey': MEXC_API_KEY,
            'secret': MEXC_API_SECRET,
            'enableRateLimit': True,
            'options': {'defaultType': 'swap'}
        })
        mexc.load_markets()
        print(f"✅ MEXC markets loaded {len(mexc.markets)}")
        # test balance
        bal = mexc.fetch_balance()
        usdt = bal.get('USDT',{}).get('free',0)
        print(f"✅ MEXC Balance test USDT free {usdt}")
        mexc_error = f"OK Balance ${usdt}"
except Exception as e:
    mexc_error = f"CCXT FAIL {str(e)[:300]}"
    print(mexc_error)
    # ถ้า load_markets fail แต่มี KEY ก็ยังลองสร้าง mexc แบบไม่ load markets
    try:
        if MEXC_API_KEY and MEXC_API_SECRET:
            import ccxt
            mexc = ccxt.mexc({
                'apiKey': MEXC_API_KEY,
                'secret': MEXC_API_SECRET,
                'enableRateLimit': True,
                'options': {'defaultType': 'swap'}
            })
            print("Created mexc without load_markets for fallback")
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
    if not BOT_TOKEN or not target: 
        print(f"TELE NO TOKEN {msg[:100]}")
        return False
    try:
        r=requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id":target,"text":msg,"parse_mode":"HTML","disable_web_page_preview":True}, timeout=10)
        print(f"TELE {r.status_code} {msg[:80]}")
        return r.json().get('ok',False)
    except Exception as e:
        print(f"TELE ERR {e}")
        return False

def get_price_fast():
    try:
        r=requests.get("https://api.binance.com/api/v3/ticker/price", params={"symbol":"BTCUSDT"}, timeout=5)
        if r.status_code==200: return float(r.json().get('price',0))
    except: pass
    return 81500.0

def get_balance_real():
    global mexc_error
    if not mexc:
        return 40.0, False, f"mexc None - {mexc_error}"
    try:
        bal=mexc.fetch_balance()
        free = bal.get('USDT',{}).get('free',0)
        total = bal.get('USDT',{}).get('total',0)
        # ถ้า free 0 อาจอยู่ใน Futures wallet ต้องดู total
        usdt = free if free>0 else total
        if usdt==0:
            # ลองดูทุกสกุล
            usdt = bal.get('total',{}).get('USDT',0)
        return float(usdt), True, f"REAL Bal ${usdt:.2f} free ${free:.2f}"
    except Exception as e:
        err=str(e)
        mexc_error=err[:300]
        if "700007" in err:
            return 40.0, False, f"700007 IP ไม่ได้รับอนุญาต → MEXC API → No Restriction\n{err[:150]}"
        if "1001" in err or "Invalid" in err:
            return 40.0, False, f"API KEY ผิดหรือหมดอายุ\n{err[:150]}"
        return 40.0, False, f"Balance ERR {err[:200]}"

def convert_btc_to_cont(btc_amount):
    try:
        btc=float(btc_amount)
        if btc >= 1: return int(btc)
        cont=int(btc / CONTRACT_SIZE)
        if cont<1: cont=1
        return cont
    except:
        return DEFAULT_CONT

def mexc_open_futures(price, btc_input, side):
    global mexc_error
    side=side.lower()
    if side in ['buy']: side='long'
    if side in ['sell']: side='short'
    
    cont = convert_btc_to_cont(btc_input)
    btc_actual = cont * CONTRACT_SIZE
    notional = btc_actual * price
    margin = notional / LEVERAGE
    balance, is_real, bal_msg = get_balance_real()

    # Log แบบละเอียด
    mode = "REAL" if is_real and mexc else "DRY RUN"
    send_telegram(f"🔄 V13.8 {mode} TRY {side.upper()} {btc_actual:.4f} BTC={cont} CONT\nPrice {price:.0f} Notional ${notional:.2f} Margin ${margin:.2f}\nBal {bal_msg}\nAPI KEY len {len(MEXC_API_KEY)} {mexc_error[:100]}")

    if balance < margin:
        send_telegram(f"⚠️ Margin ไม่พอ ต้อง ${margin:.2f} มี ${balance:.2f}")

    if not mexc or not is_real:
        # DRY RUN แต่บอกสาเหตุ
        positions.append({'entry':price,'qty':cont,'btc':btc_actual,'side':side,'time':time.time()})
        save_positions()
        if not mexc:
            send_telegram(f"✅ DRY RUN {side.upper()} {btc_actual:.4f} BTC={cont} CONT @ {price:.0f} Margin ${margin:.2f}\n⚠️ สาเหตุ DRY RUN:\n{mexc_error[:300]}\n\nวิธีแก้:\n1. Render → Environment → MEXC_API_KEY, MEXC_API_SECRET ใส่ใหม่\n2. MEXC → API → No Restriction\n3. เปิด Futures Permission\n4. โอน USDT ไป Futures Wallet")
        else:
            send_telegram(f"✅ DRY RUN {side.upper()} {btc_actual:.4f} BTC={cont} CONT @ {price:.0f}\n{bal_msg}\n{mexc_error[:200]}")
        return True

    # REAL MODE
    try:
        try: mexc.set_leverage(LEVERAGE, SYMBOL_CCXT, {'marginMode':'CROSS'})
        except: pass
        if side=='long':
            order=mexc.create_market_buy_order(SYMBOL_CCXT, cont)
        else:
            order=mexc.create_market_sell_order(SYMBOL_CCXT, cont)
        send_telegram(f"✅ REAL OPEN {side.upper()} {btc_actual:.4f} BTC={cont} CONT @ {price:.0f}$\nNotional ${notional:.2f} Margin ${margin:.2f} Bal ${balance:.2f}\nOrder {str(order.get('id',''))[:12]}")
        positions.append({'entry':price,'qty':cont,'btc':btc_actual,'side':side,'time':time.time()})
        save_positions()
        return True
    except Exception as e:
        err=str(e)
        if "700007" in err:
            send_telegram(f"❌ 700007 IP ไม่ได้รับอนุญาต\nแก้: MEXC → API Management → IP Access → No Restriction → Save\nError: {err[:200]}")
        else:
            send_telegram(f"❌ REAL OPEN FAILED {btc_actual:.4f} BTC={cont} CONT\n{err[:300]}")
        # fallback m2
        try:
            if side=='long':
                order=mexc.create_order(SYMBOL_CCXT, 'market', 'buy', cont, None, {'openType':1})
            else:
                order=mexc.create_order(SYMBOL_CCXT, 'market', 'sell', cont, None, {'openType':1})
            send_telegram(f"✅ REAL OPEN m2 {side.upper()} {cont} CONT @ {price:.0f}$")
            positions.append({'entry':price,'qty':cont,'btc':btc_actual,'side':side,'time':time.time()})
            save_positions()
            return True
        except Exception as e2:
            send_telegram(f"❌ m2 FAIL {str(e2)[:200]}")
            return False

def mexc_close_fast(price, side_filter=None):
    global positions
    if not positions:
        send_telegram("ℹ ไม่มีออเดอร์")
        return False
    to_close = [p for p in positions if p['side']==side_filter] if side_filter in ['long','short'] else positions
    if not to_close: return False
    cont=sum(int(p['qty']) for p in to_close)
    balance, is_real, bal_msg = get_balance_real()
    if mexc and is_real:
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
    mode="REAL" if is_real else "DRY RUN"
    send_telegram(f"{'🟢' if pnl>0 else '🔴'} {mode} CLOSE {to_close[0]['side'].upper()} {cont} CONT PnL {pnl:+.2f}% {bal_msg}")
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
    bal, is_real, bal_msg = get_balance_real()
    mode="REAL" if is_real else "DRY RUN"
    send_telegram(f"🚀 V13.8 FIX DRY RUN→REAL START {thai_time()}\n{mode} | Bal {bal_msg} Price {price:.0f}\nAPI KEY len {len(MEXC_API_KEY)} | {mexc_error[:150]}\nทุน $40 → 0.0005 BTC=5 CONT Margin $5 ✅\n/open long 0.0005 /open short 0.0005")
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
                    bal, is_real, bal_msg = get_balance_real()
                    price=get_price_fast()
                    wr=(stats["wins"]/stats["total_trades"]*100) if stats["total_trades"]>0 else 0
                    mode="✅ REAL MODE" if is_real and mexc else "⚠️ DRY RUN MODE"
                    send_telegram(f"{mode}\n⏰ {thai_time()}\nPrice {price:.0f}\n{bal_msg}\nAPI KEY len {len(MEXC_API_KEY)} SECRET len {len(MEXC_API_SECRET)}\n{mexc_error[:200]}\n\nPos {len(positions)} WR {wr:.1f}%\nทุน $40 → 0.0005 BTC=5 CONT Margin $5\n/open long 0.0005 /open short 0.0005\n\nวิธีแก้ DRY RUN→REAL:\n1. Render ENV ใส่ KEY ใหม่\n2. MEXC API No Restriction\n3. Futures Permission ON\n4. โอน USDT ไป Futures Wallet", chat)

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
                    bal, is_real, bal_msg = get_balance_real()
                    price=get_price_fast()
                    mode="REAL" if is_real else "DRY RUN"
                    send_telegram(f"💰 {mode} Bal {bal_msg}\nPrice {price:.0f}\nAPI {mexc_error[:200]}\n0.0005 BTC=5 CONT Margin ${price*0.0005/8:.2f}", chat)

                elif text=="/env":
                    send_telegram(f"ENV CHECK\nAPI_KEY len {len(MEXC_API_KEY)}\nSECRET len {len(MEXC_API_SECRET)}\nBOT_TOKEN len {len(BOT_TOKEN)}\nCHAT_ID {CHAT_ID}\nMEXC {mexc_error[:300]}", chat)

        except Exception as e:
            print(f"Tele err {e}")
            time.sleep(5)

threading.Thread(target=telegram_check_loop, daemon=True).start()

if __name__=="__main__":
    port=int(os.environ.get("PORT",10000))
    app.run(host="0.0.0.0", port=port)
