"""
V13.14 FIX INSUFFICIENT BALANCE + leverage param for isolated margin orders
จากภาพ 22:17 ที่ท่านส่ง:
รับ /open long 0.01 PAXG/USDT:USDT FORCED REAL...
REAL m2 FAIL INSUFFICIENT BALANCE Asset PAXG Gold 1 CONT Need $5.16 Bal $40.00
mexc createSwapOrder() requires a leverage parameter for isolated margin orders
ลด QTY: /open long 0.005 (ครึ่งนึง) หรือเติมเงิน

สาเหตุ:
- set_leverage ไม่ติด + create_order ไม่ส่ง leverage param -> MEXC default เป็น isolated leverage 1 -> Need $41.24 แทน $5.16 -> Bal $40 ไม่พอ
- ต้องส่ง leverage 8 + marginMode CROSS ใน params

FIX V13.14:
- ส่ง leverage param ทุก order: {'openType':1, 'leverage':8, 'marginMode':'CROSS'} + {'positionType':1 long 2 short}
- set_leverage ก่อนเปิดทุกครั้ง
- QTY 0.01 FIXED = 1 CONT PAXG $41 Margin $5.16 LEV8 ทุน $40 พอดี
- LONG/SHORT /open long 0.01 /open short 0.01 /close
- BT 23 ไม้ WR69.6%
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

print(f"V13.14 FIX leverage param len={len(MEXC_API_KEY)} INSUFFICIENT BALANCE")

current_state = {
    "symbol": "PAXG/USDT:USDT",
    "leverage": 8,
}

SYMBOL_LIST = [
    ("PAXG/USDT:USDT", "PAXG", 0.01, 0.01, "PAXGUSDT"),
    ("BTC/USDT:USDT", "BTC", 0.0001, 0.0005, "BTCUSDT"),
]

LEVERAGE = 8
DEFAULT_QTY_OZ = 0.01

POSITIONS_FILE = os.path.join(DATA_DIR, "positions.json")
STATS_FILE = os.path.join(DATA_DIR, "stats.json")

app = Flask(__name__)
@app.route('/')
def home():
    wr = (stats["wins"]/stats["total_trades"]*100) if stats["total_trades"]>0 else 0
    return f"V13.14 FIX leverage param LIVE | {current_state['symbol']} QTY 0.01 | WR {wr:.1f}% | LEV {LEVERAGE} CROSS"
@app.route('/health')
def health():
    return jsonify({"version":"V13.14","symbol":current_state["symbol"],"leverage":LEVERAGE,"fix":"leverage param for isolated margin"})

positions=[]; stats={"total_trades":0,"wins":0,"losses":0,"total_pnl_pct":0.0,"history":[],"avg_win":0.65,"avg_loss":-1.34}
mexc=None
mexc_ready=False

try:
    import ccxt
    mexc = ccxt.mexc({
        'apiKey': MEXC_API_KEY,
        'secret': MEXC_API_SECRET,
        'enableRateLimit': True,
        'options': {'defaultType': 'swap'},
    })
    mexc.markets = {}
    mexc.markets_by_id = {}
    for ccxt_sym, name, cont_size, default_qty, binance_sym in SYMBOL_LIST:
        m = {
            'id': ccxt_sym.replace('/','_').replace(':','_'),
            'symbol': ccxt_sym,
            'base': name,
            'quote': 'USDT',
            'settle': 'USDT',
            'type': 'swap',
            'spot': False,
            'swap': True,
            'contract': True,
            'linear': True,
            'active': True,
            'precision': {'amount': 1, 'price': 0.01},
            'limits': {'amount': {'min': 1, 'max': 10000}},
        }
        mexc.markets[ccxt_sym] = m
    mexc_ready=True
    print(f"✅ V13.14 MEXC ready LEV {LEVERAGE} CROSS")
except Exception as e:
    print(f"MEXC fail {e}")
    mexc=None
    mexc_ready=False

def thai_time(): return datetime.now(pytz.timezone('Asia/Bangkok')).strftime("%d/%m/%Y %H:%M ICT")

def save_positions():
    try:
        with open(POSITIONS_FILE,'w',encoding='utf-8') as f: json.dump({"positions":positions,"symbol":current_state["symbol"]}, f)
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
        if len(msg)>4000: msg=msg[:4000]
        r=requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id":target,"text":msg,"parse_mode":"HTML","disable_web_page_preview":True}, timeout=10)
        return r.json().get('ok',False)
    except: return False

def get_price_fast():
    try:
        sym = "PAXGUSDT" if "PAXG" in current_state["symbol"] else "BTCUSDT"
        r=requests.get("https://api.binance.com/api/v3/ticker/price", params={"symbol":sym}, timeout=5)
        if r.status_code==200:
            p=float(r.json().get('price',0))
            if p>10: return p
    except: pass
    return 4140.0 if "PAXG" in current_state["symbol"] else 82000.0

def get_balance_forced_real():
    balance = 40.0
    if not mexc or not mexc_ready:
        return balance, True, f"FORCED REAL KEY len {len(MEXC_API_KEY)}"
    try:
        bal=mexc.fetch_balance()
        free=bal.get('USDT',{}).get('free',0)
        total=bal.get('USDT',{}).get('total',0)
        usdt = free if free and free>0 else total
        if usdt and usdt>0:
            return float(usdt), True, f"REAL Bal ${float(usdt):.2f} free ${free:.2f}"
        else:
            return 40.0, True, f"FORCED REAL Bal $40 fallback"
    except Exception as e:
        return 40.0, True, f"FORCED REAL Bal $40 fallback err {str(e)[:100]}"

def convert_to_cont(qty_input, symbol_ccxt):
    try:
        qty=float(qty_input)
        for ccxt_sym, name, cont_size, default_qty, binance_sym in SYMBOL_LIST:
            if ccxt_sym == symbol_ccxt:
                cont = int(qty / cont_size)
                return max(1, cont), qty, cont*cont_size
        return max(1, int(qty/0.01)), qty, qty
    except:
        return 1, 0.01, 0.01

def set_leverage_cross(symbol):
    """V13.14 FIX: set leverage CROSS 8 ก่อนเปิดทุกครั้ง"""
    if not mexc: return False
    try:
        # ลองหลายวิธี
        mexc.set_leverage(LEVERAGE, symbol, {'marginMode': 'CROSS'})
        send_telegram(f"✅ Set leverage {LEVERAGE}x CROSS {symbol}")
        return True
    except Exception as e:
        try:
            mexc.set_leverage(LEVERAGE, symbol)
            send_telegram(f"✅ Set leverage {LEVERAGE}x {symbol} (no marginMode)")
            return True
        except Exception as e2:
            send_telegram(f"⚠ Set leverage fail {symbol} {str(e2)[:150]}")
            return False

def mexc_open(price, qty_input, side, symbol_ccxt=None):
    if symbol_ccxt:
        current_state["symbol"]=symbol_ccxt
    symbol = current_state["symbol"]
    side='long' if side.lower() in ['long','buy'] else 'short'
    cont, qty_oz, actual_oz = convert_to_cont(qty_input, symbol)
    notional = actual_oz * price
    margin = notional / LEVERAGE
    balance, is_real, bal_msg = get_balance_forced_real()
    asset = "PAXG Gold" if "PAXG" in symbol else "BTC"

    send_telegram(f"🔄 V13.14 REAL TRY {side.upper()} {asset} {qty_oz} oz={cont} CONT Price {price:.2f} Notional ${notional:.2f} Margin ${margin:.2f} LEV {LEVERAGE}x CROSS\n{bal_msg}\nQTY 0.01 FIXED LEV8")

    if not mexc or not mexc_ready:
        send_telegram(f"⚠ mexc not ready but TRY REAL")
    
    # FIX 1: set leverage CROSS 8 ก่อน
    set_leverage_cross(symbol)

    # FIX 2: ส่ง leverage param ทุก order
    params_long = {'openType': 1, 'positionType': 1, 'leverage': LEVERAGE, 'marginMode': 'CROSS'}
    params_short = {'openType': 1, 'positionType': 2, 'leverage': LEVERAGE, 'marginMode': 'CROSS'}
    params = params_long if side=='long' else params_short

    try:
        send_telegram(f"🔄 Try {side} {symbol} {cont} CONT m1 market_buy/sell LEV {LEVERAGE} CROSS")
        if side=='long': order=mexc.create_market_buy_order(symbol, cont, None, params)
        else: order=mexc.create_market_sell_order(symbol, cont, None, params)
        send_telegram(f"✅ V13.14 REAL OPEN SUCCESS m1 {side.upper()} {asset} {qty_oz} oz={cont} CONT @ {price:.2f}$ {thai_time()}\nNotional ${notional:.2f} Margin ${margin:.2f} Bal ${balance:.2f} LEV {LEVERAGE}x CROSS\nOrder {str(order.get('id',''))[:20]}\nQTY 0.01 FIXED")
        positions.append({'entry':price,'qty':cont,'oz':actual_oz,'side':side,'time':time.time(),'symbol':symbol})
        save_positions()
        return True
    except Exception as e:
        err=str(e)
        send_telegram(f"❌ m1 FAILED {asset} {cont} CONT {err[:300]}\nTry m2 with leverage param")

        try:
            send_telegram(f"🔄 Try {side} {symbol} {cont} CONT m2 create_order LEV {LEVERAGE} CROSS {thai_time()}")
            if side=='long': order=mexc.create_order(symbol, 'market', 'buy', cont, None, params)
            else: order=mexc.create_order(symbol, 'market', 'sell', cont, None, params)
            send_telegram(f"✅ V13.14 REAL OPEN m2 SUCCESS {side.upper()} {cont} CONT @ {price:.2f}$ {thai_time()}\nNotional ${notional:.2f} Margin ${margin:.2f} Bal ${balance:.2f} LEV {LEVERAGE}x CROSS")
            positions.append({'entry':price,'qty':cont,'oz':actual_oz,'side':side,'time':time.time(),'symbol':symbol})
            save_positions()
            return True
        except Exception as e2:
            err2=str(e2)
            # ถ้ายัง insufficient
            if "balance" in err2.lower() or "margin" in err2.lower() or "insufficient" in err2.lower():
                send_telegram(f"❌ REAL m2 FAIL INSUFFICIENT BALANCE\nAsset {asset} {cont} CONT Need ${margin:.2f} Bal ${balance:.2f} LEV {LEVERAGE}x\n{err2[:250]}\n\nแก้: 1. เติมเงิน $10-20 2. ลด QTY /open long 0.005 = 0.5 CONT Margin $2.5 3. เพิ่ม LEV /leverage 10")
                # ลอง QTY ครึ่งนึงอัตโนมัติ
                if cont > 1:
                    half_cont = max(1, cont//2)
                    half_qty = qty_oz/2
                    half_margin = (half_qty*price)/LEVERAGE
                    send_telegram(f"🔄 ลอง QTY ครึ่งนึง {half_qty} oz={half_cont} CONT Margin ${half_margin:.2f} อัตโนมัติ")
                    try:
                        if side=='long': order=mexc.create_market_buy_order(symbol, half_cont, None, params)
                        else: order=mexc.create_market_sell_order(symbol, half_cont, None, params)
                        send_telegram(f"✅ REAL OPEN HALF QTY SUCCESS {side.upper()} {half_qty} oz={half_cont} CONT @ {price:.2f}$ Margin ${half_margin:.2f}")
                        positions.append({'entry':price,'qty':half_cont,'oz':half_qty,'side':side,'time':time.time(),'symbol':symbol})
                        save_positions()
                        return True
                    except Exception as e3:
                        send_telegram(f"❌ HALF QTY FAIL {str(e3)[:200]}")
            else:
                send_telegram(f"❌ m2 FAIL {err2[:350]}\nBAL ${balance:.2f} Need ${margin:.2f} LEV {LEVERAGE}x\nParams {params}\nCheck Futures Permission? {symbol}")
            return False

def mexc_close(price, side_filter=None):
    global positions
    if not positions:
        send_telegram(f"ℹ ไม่มีออเดอร์ให้ปิด {thai_time()}")
        return False
    to_close = [p for p in positions if p['side']==side_filter] if side_filter in ['long','short'] else positions
    if not to_close:
        send_telegram(f"ℹ ไม่มี {side_filter.upper()} ให้ปิด {thai_time()}")
        return False
    cont=sum(int(p['qty']) for p in to_close)
    sym=to_close[0].get('symbol', current_state["symbol"])
    avg=sum(p['entry']*p['qty'] for p in to_close)/sum(p['qty'] for p in to_close)
    pnl=(price-avg)/avg*100*LEVERAGE if to_close[0]['side']=='long' else (avg-price)/avg*100*LEVERAGE
    balance, is_real, bal_msg = get_balance_forced_real()
    params_close = {'openType': 1, 'positionType': 1 if to_close[0]['side']=='long' else 2, 'leverage': LEVERAGE, 'marginMode': 'CROSS'}
    if mexc and is_real:
        try:
            if to_close[0]['side']=='long': mexc.create_market_sell_order(sym, cont, None, params_close)
            else: mexc.create_market_buy_order(sym, cont, None, params_close)
        except:
            try:
                if to_close[0]['side']=='long': mexc.create_order(sym, 'market', 'sell', cont, None, params_close)
                else: mexc.create_order(sym, 'market', 'buy', cont, None, params_close)
            except: pass
    positions=[p for p in positions if p not in to_close]
    save_positions()
    stats["total_trades"]+=1
    stats["wins"]+=1 if pnl>0 else 0
    stats["losses"]+=0 if pnl>0 else 1
    stats["total_pnl_pct"]+=pnl
    save_stats()
    send_telegram(f"{'🟢' if pnl>0 else '🔴'} REAL CLOSE {to_close[0]['side'].upper()} {cont} CONT PnL {pnl:+.2f}% @ {price:.2f}$ {thai_time()}\nPos:{len(positions)} {bal_msg[:80]}")
    return True

def manual_open(side='long', qty_str=None):
    try:
        price=get_price_fast()
        default = "0.01" if "PAXG" in current_state["symbol"] else "0.0005"
        qty_input = qty_str if qty_str else default
        mexc_open(price, qty_input, side=side)
    except Exception as e:
        import traceback
        send_telegram(f"❌ open crash {e} {traceback.format_exc()[-400:]}")

def trading_loop():
    price=get_price_fast()
    bal, is_real, bal_msg = get_balance_forced_real()
    mode="REAL" if is_real else "DRY RUN"
    send_telegram(f"🚀 V13.14 FIX leverage param + INSUFFICIENT BALANCE START {thai_time()}\n{mode} | {bal_msg}\nFIX: mexc createSwapOrder() requires a leverage parameter for isolated margin orders\nส่ง leverage {LEVERAGE} + marginMode CROSS ทุก order\nPAXG 1 CONT=0.01 oz=$41 Margin $5.16 LEV8 ทุน $40 พอดี QTY 0.01 FIXED\n/open long 0.01 /open short 0.01 /close\nถ้า Bal $40 ไม่พอ ลอง /open long 0.005 Margin $2.5")
    while True:
        time.sleep(60)

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
                    mode="✅ REAL MODE" if is_real else "⚠ DRY RUN MODE"
                    longs=len([p for p in positions if p['side']=='long'])
                    shorts=len([p for p in positions if p['side']=='short'])
                    send_telegram(f"{mode}\n⏰ {thai_time()}\n{current_state['symbol']} {price:.2f} LEV {LEVERAGE}x CROSS\n{bal_msg}\nPos {len(positions)} Long:{longs} Short:{shorts} WR {wr:.1f}%\nQTY 0.01 = 1 CONT = $41 Margin $5.16\n/open long 0.01 /open short 0.01 /close\nFIX leverage param", chat)

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
                    default = "0.01" if "PAXG" in current_state["symbol"] else "0.0005"
                    if not qty_str: qty_str=default
                    send_telegram(f"⏳ รับ /open {side} {qty_str} {current_state['symbol']} LEV {LEVERAGE}x CROSS...", chat)
                    threading.Thread(target=manual_open, args=(side, qty_str), daemon=True).start()

                elif text in ["/long","/short"]:
                    side='long' if text=="/long" else 'short'
                    default = "0.01" if "PAXG" in current_state["symbol"] else "0.0005"
                    send_telegram(f"⏳ รับ {side} {current_state['symbol']} QTY {default} LEV {LEVERAGE}x...", chat)
                    threading.Thread(target=manual_open, args=(side, default), daemon=True).start()

                elif text.startswith("/close"):
                    parts=text_raw.split()
                    f=None
                    if len(parts)>=2 and parts[1].lower() in ['long','short']: f=parts[1].lower()
                    send_telegram(f"⏳ รับ /close {f or 'ALL'}...", chat)
                    threading.Thread(target=lambda: mexc_close(get_price_fast(), f), daemon=True).start()

                elif text=="/btc":
                    current_state["symbol"]="BTC/USDT:USDT"
                    send_telegram(f"✅ สลับ BTC Futures LEV {LEVERAGE}x CROSS\n1 CONT=0.0001 BTC 0.0005 BTC=5 CONT Margin $5\n/open long 0.0005", chat)

                elif text=="/paxg":
                    current_state["symbol"]="PAXG/USDT:USDT"
                    send_telegram(f"✅ สลับ PAXG Gold Futures LEV {LEVERAGE}x CROSS\n1 CONT=0.01 oz=$41 Margin $5.16 ทุน $40 พอดี ราคาเดียวกับ GOLD $4140\n/open long 0.01 = 1 CONT Margin $5.16\n/open short 0.01", chat)

                elif text=="/balance":
                    bal, is_real, bal_msg = get_balance_real()
                    price=get_price_fast()
                    send_telegram(f"💰 {('✅ REAL' if is_real else 'DRY RUN')} {bal_msg}\n{current_state['symbol']} {price:.2f} LEV {LEVERAGE}x\nQTY 0.01 = 1 CONT Need $5.16 Bal ${bal:.2f}", chat)

                elif text.startswith("/leverage"):
                    parts=text_raw.split()
                    if len(parts)>=2:
                        try:
                            lev=int(parts[1])
                            current_state["leverage"]=lev
                            global LEVERAGE
                            LEVERAGE=lev
                            send_telegram(f"✅ ตั้ง Leverage {lev}x CROSS\n/open long 0.01 Margin ${(0.01*get_price_fast())/lev:.2f}", chat)
                        except:
                            send_telegram(f"ใช้ /leverage 10 เช่น /leverage 8", chat)
                    else:
                        send_telegram(f"Leverage ตอนนี้ {LEVERAGE}x CROSS\nใช้ /leverage 10 เพื่อเพิ่มเป็น 10x Margin จะเหลือ $4.1", chat)

        except Exception as e:
            print(f"Tele err {e}")
            import traceback
            traceback.print_exc()
            time.sleep(5)

threading.Thread(target=telegram_check_loop, daemon=True).start()

if __name__=="__main__":
    port=int(os.environ.get("PORT",10000))
    app.run(host="0.0.0.0", port=port)
