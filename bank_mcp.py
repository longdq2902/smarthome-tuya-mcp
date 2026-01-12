# FILE: bank_mcp.py
from flask import Flask, request, jsonify
import threading
import json
import os
import logging
from datetime import datetime

logger = logging.getLogger('bank_module')
BANK_DB_FILE = 'transactions.json'

app = Flask(__name__)

# --- WEBHOOK LOGIC ---
def save_transaction(data):
    history = []
    if os.path.exists(BANK_DB_FILE):
        try:
            with open(BANK_DB_FILE, 'r', encoding='utf-8') as f:
                history = json.load(f)
        except: pass
    
    # Format dữ liệu (SePay/Casso)
    amount = data.get("transferAmount", data.get("amount", 0))
    content = data.get("content", data.get("description", ""))
    
    new_record = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "bank": data.get("gateway", "Bank"),
        "amount": amount,
        "content": content
    }
    
    history.insert(0, new_record)
    history = history[:50]
    
    with open(BANK_DB_FILE, 'w', encoding='utf-8') as f:
        json.dump(history, f, indent=4, ensure_ascii=False)
    logger.info(f"💰 +{amount} | {content}")

@app.route('/webhook', methods=['POST'])
def receive_webhook():
    try:
        data = request.json
        if data:
            save_transaction(data)
            return jsonify({"success": True}), 200
        return jsonify({"success": False}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def start_webhook_server():
    """Hàm khởi động Flask Server chạy ngầm"""
    # Tắt log startup của Flask để đỡ rối
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    
    server_thread = threading.Thread(target=lambda: app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False), daemon=True)
    server_thread.start()
    print("🚀 Bank Webhook running at http://0.0.0.0:5000/webhook")

# --- CÁC HÀM CÔNG CỤ (TOOLS) ---
def check_latest_transactions(limit: int = 5) -> str:
    """Kiểm tra giao dịch ngân hàng mới nhất."""
    if not os.path.exists(BANK_DB_FILE): return "Chưa có giao dịch nào."
    try:
        with open(BANK_DB_FILE, 'r', encoding='utf-8') as f:
            history = json.load(f)
        if not history: return "Danh sách trống."
        
        report = f"💰 {limit} Giao dịch mới nhất:\n"
        for i, tx in enumerate(history[:limit], 1):
            amt = "{:,.0f}".format(float(tx['amount']))
            report += f"{i}. +{amt}đ ({tx['time']}) | {tx['content']}\n"
        return report
    except Exception as e: return f"Lỗi đọc file: {e}"