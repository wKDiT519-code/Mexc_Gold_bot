"""
V13 ULTRA MINIMAL - ทดสอบ Render ว่า PORT bind ได้มั้ย - ต้องไม่ exit early
ถ้าตัวนี้ยัง exit early แสดงว่า requirements หรือ start command ผิด
"""
import os
from flask import Flask, request

app = Flask(__name__)

@app.route('/', methods=['GET','HEAD'])
def home():
    if request.method == 'HEAD':
        return '', 200
    return 'V13 ULTRA MINIMAL OK - Render PORT bind OK', 200

@app.route('/health', methods=['GET','HEAD'])
def health():
    if request.method == 'HEAD':
        return '', 200
    return 'ok', 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    print(f"ULTRA MINIMAL starting on 0.0.0.0:{port}")
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
