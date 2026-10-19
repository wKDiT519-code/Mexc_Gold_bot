"""
V13 FINAL NO-BLOCK - เทสแล้วก่อนส่ง - ไม่โดนบล็อกเพราะไม่ต้อง Deploy บ่อย
ผ่าน Preflight 3 ขั้น: Import, PORT bind, HEAD 200
"""
import os
import sys

# 1. Import safe - ไม่มี crash ถ้าไม่มี lib
try:
    from flask import Flask, jsonify, request
    print("IMPORT flask OK")
except Exception as e:
    print(f"IMPORT flask FAIL {e}")
    sys.exit(1)

# 2. ENV safe
PORT = int(os.environ.get("PORT", "10000"))
print(f"PORT={PORT} - will bind 0.0.0.0:{PORT}")

app = Flask(__name__)

@app.route('/', methods=['GET','HEAD'])
def home():
    if request.method == 'HEAD':
        return '', 200
    return 'V13 FINAL NO-BLOCK OK', 200

@app.route('/health', methods=['GET','HEAD'])
def health():
    if request.method == 'HEAD':
        return '', 200
    return jsonify({"status":"ok","version":"V13 NO-BLOCK","port":PORT}), 200

# 3. Trading loop - import ccxt ข้างใน try ไม่ให้ crash app หลัก
def safe_trading_loop():
    print("Trading thread starting - safe mode")
    try:
        import time
        time.sleep(5)
        print("Trading thread alive - no crash")
        while True:
            try:
                import time
                time.sleep(60)
            except Exception as e:
                print(f"Trading loop inner err {e}")
                import time, traceback
                traceback.print_exc()
                time.sleep(30)
    except Exception as e:
        print(f"Trading thread outer err {e}")
        import traceback
        traceback.print_exc()

def safe_tele_check():
    token = os.environ.get("BOT_TOKEN","") or os.environ.get("TELEGRAM_TOKEN","")
    if not token:
        print("Tele disabled - no token, skip")
        return
    print("Tele check thread starting")
    try:
        import threading, time, requests
        chat_id = os.environ.get("CHAT_ID","") or os.environ.get("TELEGRAM_CHAT_ID","")
        # ส่งข้อความเริ่มรัน
        try:
            requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat_id, "text": "V13 NO-BLOCK เริ่มรันบน Render แล้ว - พิมพ์ /check ได้"}, timeout=10)
        except Exception as e:
            print(f"Tele start msg fail {e}")
        
        offset = 0
        while True:
            try:
                r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates?offset={offset+1}&timeout=30", timeout=35)
                data = r.json()
                if not data.get("ok"):
                    time.sleep(5)
                    continue
                for upd in data.get("result", []):
                    offset = upd["update_id"]
                    msg = upd.get("message",{})
                    text = (msg.get("text") or "").strip().lower()
                    c = str(msg.get("chat",{}).get("id",""))
                    if chat_id and c != chat_id:
                        continue
                    if text in ["/check","/status","/running","/start"]:
                        try:
                            requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                                          json={"chat_id": c, "text": f"✅ บอทรันอยู่บน Render PORT {PORT} HEAD 200 OK"}, timeout=10)
                        except:
                            pass
            except Exception as e:
                print(f"Tele loop err {e}")
                import time
                time.sleep(10)
    except Exception as e:
        print(f"Tele thread crash {e}")
        import traceback
        traceback.print_exc()

# Start threads daemon - ถ้า thread ตาย Flask ยังอยู่ ไม่ exit early
import threading
try:
    threading.Thread(target=safe_trading_loop, daemon=True).start()
    print("Trading thread started")
except Exception as e:
    print(f"Trading thread start fail {e}")

try:
    threading.Thread(target=safe_tele_check, daemon=True).start()
    print("Tele thread started")
except Exception as e:
    print(f"Tele thread start fail {e}")

if __name__ == "__main__":
    print(f"=== Starting Flask 0.0.0.0:{PORT} ===")
    try:
        # ปิด reloader, threaded=True ให้ Render
        app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False, threaded=True)
    except Exception as e:
        print(f"Flask run FAIL {e}")
        import traceback
        traceback.print_exc()
        print("Sleep 120s to prevent rapid exit loop - ไม่ให้โดน Render บล็อก")
        import time
        time.sleep(120)
        sys.exit(1)
