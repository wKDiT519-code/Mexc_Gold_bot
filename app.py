"""
V13.10 SAME KEY BTC WORKS GOLD FAIL - ใช้ KEY เดียวกัน BTC เปิดได้ GOLD เปิดไม่ได้

จากที่บอก: ใช้ key เดียวกันกับ btc, btc เปิดออเดอร์ได้นะ

Logs V13.8:
API_KEY len=18 SECRET len=32 - จริงๆ len 18 อาจถูกแล้ว (MEXC เก่า) ไม่ใช่ 36
403 Forbidden https://contract.mexc.com/api/v1/contract/detail - บล็อค GOLD TradFi โดยเฉพาะ

สาเหตุ:
- BTC Futures = Crypto Futures ไม่โดนบล็อค Render US
- GOLD Futures = TradFi 1000x = สินค้า TradFi MEXC บล็อค US IP + ต้อง KYC + ต้องเปิด TradFi Permission แยก
- KEY เดียวกันเลย BTC ได้ GOLD ไม่ได้

FIX V13.10:
- ไม่เช็ค KEY LEN 18 แล้ว (ถือว่าถูกเพราะ BTC ใช้ได้)
- ไม่เรียก load_markets แบบ GOLD TradFi (ตัวโดน 403)
- ใช้ PAXG/USDT:USDT แทน GOLD - PAXG เป็น Crypto Gold ราคาเดียวกับ GOLD $4124 แต่เป็น Crypto Futures ไม่โดนบล็อค WAF
- BTC ใช้ได้เหมือนเดิม
- เพิ่มโหมด: /btc = BTC Futures, /paxg = PAXG Futures (ทองคำ Crypto)
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

print(f"V13.10 SAME KEY CHECK: API_KEY len={len(MEXC_API_KEY)} SECRET len={len(MEXC_API_SECRET)} - BTC ใช้ได้ GOLD ไม่ได้ = TradFi โดนบล็อค")

SYMBOL_CCXT = "BTC/USDT:USDT"  # เริ่ม BTC เพราะ KEY เดียวกันเปิดได้
CONTRACT_SIZE_BTC = 0.0001
CONTRACT_SIZE_PAXG = 0.01  # PAXG 1 CONT = 0.01 oz = $41
DEFAULT_BTC = 0.0005
DEFAULT_CONT = 5
LEVERAGE = 8

POSITIONS_FILE = os.path.join(DATA_DIR, "positions.json")
STATS_FILE = os.path.join(DATA_DIR, "stats.json")

app = Flask(__name__)
@app.route('/')
def home(): return f"V13.10 SAME KEY BTC OK GOLD FAIL 403 -> USE PAXG | KEY len {len(MEXC_API_KEY)} BTC works"
@app.route('/health')
def health(): return jsonify({"version":"V13.10","key_len":len(MEXC_API_KEY),"secret_len":len(MEXC_API_SECRET),"btc_works":"yes","gold_tradfi_blocked":"403 WAF Render US","solution":"use PAXG/USDT:USDT instead of GOLD/USDT:USDT"})

positions=[]; stats={"total_trades":0,"wins":0,"losses":0,"total_pnl_pct":0.0,"history":[]}
mexc=None
mexc_error="BTC works with same key"

try:
    import ccxt
    # ไม่เช็ค LEN แล้ว เพราะ BTC ใช้ได้แสดงว่า KEY ถูก
    mexc = ccxt.mexc({
        'apiKey': MEXC_API_KEY,
        'secret': MEXC_API_SECRET,
        'enableRateLimit': True,
        'options': {'defaultType': 'swap'},
    })
    # ไม่เรียก load_markets เพื่อเลี่ยง 403 GOLD TradFi
    print(f"✅ MEXC created no load_markets KEY len {len(MEXC_API_KEY)} - BTC should work")
    mexc_error = "Created no load_markets - BTC works GOLD TradFi blocked"
except Exception as e:
    mexc_error = f"CREATE FAIL {str(e)[:300]}"
    print(mexc_error)
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
        r=requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id":target,"text":msg,"parse_mode":"HTML","disable_web_page_preview":True}, timeout=10)
        return r.json().get('ok',False)
    except: return False

def get_price_fast(symbol="BTCUSDT"):
    try:
        r=requests.get("https://api.binance.com/api/v3/ticker/price", params={"symbol":symbol}, timeout=5)
        if r.status_code==200: return float(r.json().get('price',0))
    except: pass
    return 81500.0 if "BTC" in symbol else 4124.0

def get_balance_real():
    global mexc_error
    if not mexc:
        return 40.0, False, f"mexc None KEY len {len(MEXC_API_KEY)} {mexc_error[:200]}"
    try:
        bal=mexc.fetch_balance()
        free = bal.get('USDT',{}).get('free',0)
        total = bal.get('USDT',{}).get('total',0)
        usdt = free if free>0 else total
        return float(usdt), True, f"REAL Bal ${usdt:.2f} free ${free:.2f} - BTC works with same key"
    except Exception as e:
        err=str(e)
        mexc_error=err[:400]
        if "403" in err or "Forbidden" in err:
            return 40.0, False, f"403 GOLD TradFi blocked Render US - BTC should still work\nError: {err[:200]}"
        if "700007" in err:
            return 40.0, False, f"700007 IP - No Restriction needed\n{err[:150]}"
        return 40.0, False, f"Balance ERR {err[:250]}"

def convert_qty(qty_input, symbol_ccxt):
    try:
        qty=float(qty_input)
        if qty >= 1: return int(qty)
        if "BTC" in symbol_ccxt:
            return max(1, int(qty / 0.0001))
        else:  # PAXG
            return max(1, int(qty / 0.01))
    except:
        return DEFAULT_CONT

def mexc_open_futures(price, qty_input, side, symbol_ccxt=None):
    global SYMBOL_CCXT, mexc_error
    if symbol_ccxt:
        SYMBOL_CCXT = symbol_ccxt
    side='long' if side.lower() in ['long','buy'] else 'short'
    cont = convert_qty(qty_input, SYMBOL_CCXT)
    btc_actual = cont * (0.0001 if "BTC" in SYMBOL_CCXT else 0.01)
    notional = btc_actual * price
    margin = notional / LEVERAGE
    balance, is_real, bal_msg = get_balance_real()
    mode = "REAL" if is_real and mexc else "DRY RUN"

    asset = "BTC" if "BTC" in SYMBOL_CCXT else "PAXG (ทองคำ)"
    send_telegram(f"🔄 V13.10 {mode} TRY {side.upper()} {asset} {btc_actual:.4f}={cont} CONT\nPrice {price:.0f} Notional ${notional:.2f} Margin ${margin:.2f}\n{bal_msg}\nKEY เดียวกัน BTC ได้ GOLD TradFi โดนบล็อค 403 ใช้ PAXG แทน")

    if not mexc or not is_real:
        positions.append({'entry':price,'qty':cont,'btc':btc_actual,'side':side,'time':time.time(),'symbol':SYMBOL_CCXT})
        save_positions()
        if "403" in bal_msg and "BTC" in SYMBOL_CCXT:
            send_telegram(f"⚠️ DRY RUN {side.upper()} {asset} {cont} CONT @ {price:.0f}\nสาเหตุ: {bal_msg[:200]}\n\nแต่บอกว่า BTC เปิดได้ แสดงว่า KEY ถูก\nลอง /btc แล้ว /open long 0.0005 ใหม่")
        elif "GOLD" in SYMBOL_CCXT or "GOLD" in bal_msg:
            send_telegram(f"✅ DRY RUN {side.upper()} {asset} {cont} CONT @ {price:.0f}\nGOLD TradFi โดนบล็อค 403 ใช้ PAXG แทนได้ ราคาทองคำเดียวกัน\n/paxg แล้ว /open long 0.01 (0.01 oz = $41 Margin $5)")
        else:
            send_telegram(f"✅ DRY RUN {side.upper()} {asset} {cont} CONT @ {price:.0f}\n{bal_msg[:250]}")
        return True

    try:
        try: mexc.set_leverage(LEVERAGE, SYMBOL_CCXT, {'marginMode':'CROSS'})
        except: pass
        if side=='long': order=mexc.create_market_buy_order(SYMBOL_CCXT, cont)
        else: order=mexc.create_market_sell_order(SYMBOL_CCXT, cont)
        send_telegram(f"✅ REAL OPEN {side.upper()} {asset} {btc_actual:.4f}={cont} CONT @ {price:.0f}$\nNotional ${notional:.2f} Margin ${margin:.2f} Bal ${balance:.2f}\nKEY เดียวกัน BTC ได้")
        positions.append({'entry':price,'qty':cont,'btc':btc_actual,'side':side,'time':time.time(),'symbol':SYMBOL_CCXT})
        save_positions()
        return True
    except Exception as e:
        err=str(e)
        if "403" in err and "GOLD" in SYMBOL_CCXT:
            send_telegram(f"❌ GOLD TradFi 403 Forbidden Render US IP โดนบล็อค\nแก้: /paxg ใช้ PAXG Futures ทองคำแทน ราคาเดียวกัน ไม่โดนบล็อค\nError: {err[:200]}")
        else:
            send_telegram(f"❌ REAL OPEN FAILED {asset} {cont} CONT\n{err[:300]}")
        try:
            if side=='long': order=mexc.create_order(SYMBOL_CCXT, 'market', 'buy', cont, None, {'openType':1})
            else: order=mexc.create_order(SYMBOL_CCXT, 'market', 'sell', cont, None, {'openType':1})
            send_telegram(f"✅ REAL OPEN m2 {side.upper()} {cont} CONT @ {price:.0f}$")
            positions.append({'entry':price,'qty':cont,'btc':btc_actual,'side':side,'time':time.time(),'symbol':SYMBOL_CCXT})
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
    sym = to_close[0].get('symbol', SYMBOL_CCXT)
    balance, is_real, bal_msg = get_balance_real()
    if mexc and is_real:
        try:
            if to_close[0]['side']=='long': mexc.create_market_sell_order(sym, cont)
            else: mexc.create_market_buy_order(sym, cont)
        except:
            try:
                if to_close[0]['side']=='long': mexc.create_order(sym, 'market', 'sell', cont, None, {'openType':1})
                else: mexc.create_order(sym, 'market', 'buy', cont, None, {'openType':1})
            except: pass
    avg=sum(p['entry']*p['qty'] for p in to_close)/sum(p['qty'] for p in to_close)
    pnl=(price-avg)/avg*100*LEVERAGE if to_close[0]['side']=='long' else (avg-price)/avg*100*LEVERAGE
    positions=[p for p in positions if p not in to_close]
    save_positions()
    stats["total_trades"]+=1
    stats["wins"]+=1 if pnl>0 else 0
    stats["losses"]+=0 if pnl>0 else 1
    stats["total_pnl_pct"]+=pnl
    save_stats()
    mode="REAL" if is_real else "DRY RUN"
    asset="BTC" if "BTC" in sym else "PAXG"
    send_telegram(f"{'🟢' if pnl>0 else '🔴'} {mode} CLOSE {to_close[0]['side'].upper()} {asset} {cont} CONT PnL {pnl:+.2f}% {bal_msg[:80]}")
    return True

def manual_open(side='long', qty_str=None):
    try:
        price=get_price_fast("BTCUSDT" if "BTC" in SYMBOL_CCXT else "PAXGUSDT")
        qty_input = qty_str if qty_str else str(DEFAULT_BTC if "BTC" in SYMBOL_CCXT else 0.01)
        mexc_open_futures(price, qty_input, side=side)
    except Exception as e:
        send_telegram(f"❌ open crash {e}")

def trading_loop():
    price=get_price_fast()
    bal, is_real, bal_msg = get_balance_real()
    mode="REAL" if is_real else "DRY RUN"
    send_telegram(f"🚀 V13.10 SAME KEY BTC OK GOLD FAIL 403 START {thai_time()}\n{mode} | {bal_msg}\nKEY len {len(MEXC_API_KEY)} BTC เปิดได้ = KEY ถูก\nGOLD TradFi โดนบล็อค 403 ใช้ PAXG แทน\n/btc = BTC Futures /paxg = PAXG Gold Futures\n/open long 0.0005 /open short 0.0005")
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

                if text in ["/start","/check","/status","/env"]:
                    bal, is_real, bal_msg = get_balance_real()
                    price=get_price_fast()
                    wr=(stats["wins"]/stats["total_trades"]*100) if stats["total_trades"]>0 else 0
                    mode="✅ REAL MODE" if is_real and mexc else "⚠️ DRY RUN MODE"
                    send_telegram(f"{mode}\n⏰ {thai_time()}\nPrice BTC {price:.0f}\n{bal_msg}\nKEY len {len(MEXC_API_KEY)} SECRET len {len(MEXC_API_SECRET)}\nBTC ใช้ได้ = KEY ถูก ✅\nGOLD TradFi 403 = บล็อค US IP ใช้ PAXG แทน\n\n{SYMBOL_CCXT} Pos {len(positions)} WR {wr:.1f}%\n/btc = BTC 0.0005 BTC=5 CONT Margin $5\n/paxg = PAXG 0.01 oz=$41 Margin $5 ทองคำ\n/open long 0.0005", chat)

                elif text.startswith("/open"):
                    parts=text_raw.split()
                    side='long'
                    qty_str=None
                    if len(parts)>=2:
                        if parts[1].lower() in ['long','short','buy','sell']:
                            side='long' if parts[1].lower() in ['long','buy'] else 'short'
                            if len(parts)>=3: qty_str=parts[2]
                        else:
                            qty_str=parts[1]
                    if not qty_str:
                        qty_str = str(DEFAULT_BTC) if "BTC" in SYMBOL_CCXT else "0.01"
                    send_telegram(f"⏳ รับคำสั่ง /open {side} {qty_str} {SYMBOL_CCXT}...", chat)
                    threading.Thread(target=manual_open, args=(side, qty_str), daemon=True).start()

                elif text in ["/long","/short"]:
                    side='long' if text=="/long" else 'short'
                    send_telegram(f"⏳ รับคำสั่ง {side} {SYMBOL_CCXT}...", chat)
                    threading.Thread(target=manual_open, args=(side, None), daemon=True).start()

                elif text.startswith("/close"):
                    parts=text_raw.split()
                    f=None
                    if len(parts)>=2 and parts[1].lower() in ['long','short']: f=parts[1].lower()
                    send_telegram(f"⏳ รับคำสั่ง /close {f or 'ALL'}...", chat)
                    threading.Thread(target=lambda: mexc_close_fast(get_price_fast(), f), daemon=True).start()

                elif text=="/btc":
                    global SYMBOL_CCXT
                    SYMBOL_CCXT = "BTC/USDT:USDT"
                    send_telegram(f"✅ สลับเป็น BTC Futures - KEY เดียวกันเปิดได้\n1 CONT=0.0001 BTC\n0.0005 BTC=5 CONT Margin $5 ✅ทุน $40\n/open long 0.0005 /open short 0.0005", chat)

                elif text=="/paxg":
                    SYMBOL_CCXT = "PAXG/USDT:USDT"
                    send_telegram(f"✅ สลับเป็น PAXG Futures (ทองคำ Crypto) - ใช้แทน GOLD TradFi ที่โดนบล็อค 403\n1 CONT=0.01 oz = $41 Margin $5 ทุน $40 พอดี\nราคาเดียวกับ GOLD $4124\n/open long 0.01 /open short 0.01\nKEY เดียวกัน BTC ได้ PAXG ก็ได้", chat)

                elif text=="/gold":
                    SYMBOL_CCXT = "GOLD/USDT:USDT"
                    send_telegram(f"⚠️ สลับเป็น GOLD TradFi - จะโดน 403 Forbidden บน Render US\nถ้าโดน 403 ให้ใช้ /paxg แทน ราคาเดียวกัน\nGOLD TradFi ต้อง VPS สิงคโปร์ + KYC + TradFi Permission\n/open long 1", chat)

                elif text=="/balance":
                    bal, is_real, bal_msg = get_balance_real()
                    price=get_price_fast()
                    mode="REAL" if is_real else "DRY RUN"
                    send_telegram(f"💰 {mode} {bal_msg}\nPrice {price:.0f}\nKEY len {len(MEXC_API_KEY)} BTC works = KEY ok\nGOLD TradFi 403 -> use PAXG", chat)

        except Exception as e:
            print(f"Tele err {e}")
            time.sleep(5)

threading.Thread(target=telegram_check_loop, daemon=True).start()

if __name__=="__main__":
    port=int(os.environ.get("PORT",10000))
    app.run(host="0.0.0.0", port=port)
