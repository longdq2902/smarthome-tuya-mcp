from flask import Flask, jsonify, request, send_from_directory
import tinytuya
import json
import time
import os
import threading
import sys
import io

# FORCE UTF-8 ENCODING FOR WINDOWS
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

from datetime import datetime, timedelta
import db_manager # <--- MỚI: Module quản lý DB

app = Flask(__name__)

# Cache lưu trạng thái thiết bị (Vẫn giữ Cache trên RAM để phản hồi nhanh)
tuya_cache = {}
active_timers = {}

# Lock để tránh xung đột
data_lock = threading.Lock()

def safe_float_version(val):
    try: return float(val)
    except: return 0.0

def get_default_info(did):
    return {
        "id": did,
        "name": f"Device {did[-6:]}",
        "type": "unknown",
        "category": "",
        "mapping": {},
        "ip": None,
        "real_ip": "",
        "version": 0.0,
        "via": None,
        "is_sub": False,
        "obj": None,
        "dps": {},
        "online": False,
        "missing_ip": True,
        "snapshot_ver": 0.0,
        "last_update": 0
    }

def determine_device_type(dev_config):
    cat = dev_config.get('category', '').lower()
    mapping = str(dev_config.get('mapping', {})).lower()
    
    if 'switch' in mapping: return 'switch'
    if 'led' in mapping or 'light' in mapping or 'colour' in mapping or 'dj' in cat: return 'light'
    if cat in ['cz', 'kg', 'cl', 'qjdt', 'dc', 'dd', 'fs', 'ws', 'qt']: return 'switch'
    if cat in ['hjjcy', 'wsdcg', 'pir', 'mcs', 'ywbj', 'door', 'sgl', 'ms']: return 'sensor'
    if 'wg' in cat: return 'gateway'
    if 'infrared' in cat or 'wnykq' in cat: return 'ir_remote'
    return 'sensor'

def init_device(dev, parent=None):
    dev_id = dev.get('id')
    dev_key = dev.get('key')
    
    config_ver = safe_float_version(dev.get('version'))
    
    # Logic xác định version và IP từ cha (nếu có)
    if parent:
        ip = parent.get('ip')
        key = parent.get('key')
        ver = safe_float_version(parent.get('version'))
        if ver == 0.0: ver = 3.3
    else:
        ip = dev.get('ip')
        key = dev_key
        ver = config_ver if config_ver > 0 else 3.3

    if dev_id not in tuya_cache:
        tuya_cache[dev_id] = get_default_info(dev_id)

    # Cập nhật thông tin static từ DB vào Cache
    tuya_cache[dev_id].update({
        "name": dev.get('name', 'Unknown'),
        "type": determine_device_type(dev),
        "category": dev.get('category', ''),
        "mapping": dev.get('mapping', {}),
        "ip": ip if ip and ip != "0.0.0.0" else None,
        "real_ip": dev.get('ip', ''), # IP lưu trong config chính chủ
        "version": ver,
        "via": parent.get('name') if parent else None,
        "is_sub": True if parent else False,
        "missing_ip": False
    })

    # Restore trạng thái cũ từ DB (nếu có) để UI không bị trống lúc mới khởi động
    if 'dps' in dev and dev['dps']:
        tuya_cache[dev_id]['dps'].update(dev['dps'])

    if not ip or ip == "0.0.0.0":
        tuya_cache[dev_id]["missing_ip"] = True
        return

    try:
        # Tái sử dụng object connection
        if not tuya_cache[dev_id].get("obj"):
            if tuya_cache[dev_id]['type'] == 'light':
                d = tinytuya.BulbDevice(dev_id, ip, key)
            else:
                d = tinytuya.OutletDevice(dev_id, ip, key)
            
            d.set_version(ver)
            d.set_socketPersistent(True) 
            d.set_socketRetryLimit(1)
            d.set_socketTimeout(2)
            if parent: d.cid = dev.get('node_id', dev_id)
            
            tuya_cache[dev_id]["obj"] = d
        else:
            # Update lại thông tin kết nối nếu config đổi
            d = tuya_cache[dev_id]["obj"]
            d.set_version(ver)
            d.address = ip
            d.local_key = key
            if parent: d.cid = dev.get('node_id', dev_id)

    except: pass

def load_system():
    print("--> Đang nạp danh sách thiết bị từ SQLite...")
    
    # 1. Lấy tất cả thiết bị từ DB
    all_devices = db_manager.get_all_devices()
    
    # 2. Lọc ra Gateway để xử lý thiết bị con
    gateways = {d['id']: d for d in all_devices if 'wg' in d.get('category', '') or (d.get('ip') and not d.get('parent'))}

    # 3. Khởi tạo từng thiết bị
    for dev in all_devices:
        parent = None
        pid = dev.get('parent')
        if pid and pid in gateways: parent = gateways[pid]
        init_device(dev, parent)
        
    print(f"--> Đã nạp {len(tuya_cache)} thiết bị từ DB.")

# --- LUỒNG CẬP NHẬT TRẠNG THÁI (POLLING THREAD) ---
def background_polling():
    # Load lần đầu
    load_system()
    
    while True:
        now = datetime.now()
        
        # 1. XỬ LÝ HẸN GIỜ (Giữ nguyên logic cũ)
        for key, timer in list(active_timers.items()):
            if now >= timer['end_time']:
                try:
                    parts = key.rsplit('_', 1) 
                    did = parts[0]
                    dp_id = parts[1] if len(parts) > 1 else None

                    print(f"⏰ Timer kích hoạt: {did} (DP {dp_id}) -> {timer['action']}")
                    
                    info = tuya_cache.get(did)
                    if info and info.get('obj'):
                        dev_obj = info['obj']
                        is_on = (timer['action'] == 'on')
                        
                        if dp_id and dp_id != 'None':
                            dev_obj.set_value(str(dp_id), is_on)
                            with data_lock:
                                info['dps'][str(dp_id)] = is_on
                                # Cập nhật DB khi Timer chạy
                                db_manager.update_device_state(did, {str(dp_id): is_on}) 
                        else:
                            if is_on: dev_obj.turn_on()
                            else: dev_obj.turn_off()
                            with data_lock:
                                if '1' in info['dps']: info['dps']['1'] = is_on
                                if '20' in info['dps']: info['dps']['20'] = is_on
                                # Cập nhật DB
                                db_manager.update_device_state(did, info['dps'])
                    
                    del active_timers[key]
                except Exception as e:
                    print(f"Lỗi Timer: {e}")
        
        # 2. QUÉT TRẠNG THÁI THIẾT BỊ
        device_ids = list(tuya_cache.keys())
        
        for dev_id in device_ids:
            info = tuya_cache.get(dev_id)
            if not info or info.get('missing_ip') or not info.get('obj'):
                continue
            
            try:
                dev = info['obj']
                data = dev.status()
                
                if data and 'dps' in data:
                    is_changed = False
                    with data_lock:
                        # Kiểm tra xem có gì mới không
                        old_dps = info.get('dps', {})
                        new_dps = data['dps']
                        
                        # Chỉ update DB nếu có thay đổi giá trị hoặc thiết bị vừa online lại
                        if info.get('online') == False: 
                            is_changed = True
                        else:
                            # So sánh đơn giản
                            for k, v in new_dps.items():
                                if str(k) not in old_dps or old_dps[str(k)] != v:
                                    is_changed = True
                                    break
                        
                        info['dps'].update(new_dps)
                        info['online'] = True
                        info['last_update'] = time.time()
                    
                    if is_changed:
                        # Ghi trạng thái mới xuống DB
                        # Chạy trong background thread nên không lo block UI chính
                        db_manager.update_device_state(dev_id, new_dps, is_online=True)
                        
                elif 'Error' in str(data):
                    if info.get('online'):
                        info['online'] = False
                        db_manager.update_device_state(dev_id, {}, is_online=False)
            except:
                info['online'] = False
                
            time.sleep(0.1)
        
        time.sleep(5)

# Bắt đầu luồng chạy ngầm ngay khi import (hoặc khi chạy main)
# Lưu ý: Flask khi chạy debug mode có thể load file 2 lần -> tạo 2 thread. 
# Cần kiểm tra biến môi trường hoặc dùng lock file nếu cần thiết. 
# Ở đây đơn giản hóa.
if not os.environ.get("WERKZEUG_RUN_MAIN") == "true":
    poll_thread = threading.Thread(target=background_polling, daemon=True)
    poll_thread.start()

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

@app.route('/api/devices', methods=['GET'])
def get_devices():
    response_list = []
    with data_lock:
        for dev_id, info in tuya_cache.items():
            
            mapping = info.get('mapping', {})
            timers_info = {}
            
            for key, val in active_timers.items():
                if key.startswith(dev_id):
                    parts = key.rsplit('_', 1)
                    t_dp = parts[1] if len(parts) > 1 else 'main'
                    remaining = val['end_time'] - datetime.now()
                    total_seconds = int(remaining.total_seconds())
                    if total_seconds > 0:
                        mins = total_seconds // 60
                        ac = "BẬT" if val['action'] == 'on' else "TẮT"
                        timers_info[t_dp] = f"{ac} sau {mins}p"
                        
            response_list.append({
                "id": dev_id,
                "name": info.get('name', f'Device {dev_id}'),
                "type": info.get('type', 'unknown'),
                "category": info.get('category', ''),
                "ip": info.get('ip'),
                "real_ip": info.get('real_ip', ''),
                "version": info.get('version', 0.0),
                "online": info.get('online', False), 
                "missing_ip": info.get('missing_ip', True),
                "via": info.get('via'),
                "mapping": mapping, 
                "dps": info.get('dps', {}) ,
                "timers": timers_info 
            })
    return jsonify(response_list)

@app.route('/api/set_timer', methods=['POST'])
def set_timer():
    data = request.json
    dev_id = data.get('id')
    dp_id = str(data.get('dp_id', '')) 
    minutes = int(data.get('minutes', 0))
    timer_key = f"{dev_id}_{dp_id}"

    if minutes <= 0:
        if timer_key in active_timers:
            del active_timers[timer_key]
        return jsonify({"success": True, "message": "Đã hủy hẹn giờ."})

    info = tuya_cache.get(dev_id)
    if not info: return jsonify({"success": False}), 404
    
    dps = info.get('dps', {})
    is_currently_on = False
    
    if dp_id and dp_id in dps:
        is_currently_on = dps[dp_id]
    else:
        is_currently_on = dps.get('1') or dps.get('20') or False
    
    action = 'off' if is_currently_on else 'on'
    end_time = datetime.now() + timedelta(minutes=minutes)
    
    active_timers[timer_key] = {
        'end_time': end_time,
        'action': action
    }
    
    action_vn = "TẮT" if action == 'off' else "BẬT"
    target_name = info['name']
    if dp_id and 'mapping' in info and dp_id in info['mapping']:
        target_name += " (" + info['mapping'][dp_id].get('name', dp_id) + ")"

    return jsonify({"success": True, "message": f"Sẽ {action_vn} {target_name} sau {minutes} phút."})

@app.route('/api/update_config', methods=['POST'])
def update_config():
    data = request.json
    dev_id = data.get('id')
    
    # Chỉ định các trường cho phép update
    allowed_fields = ['ip', 'version', 'name'] # name ở đây là device_name
    
    update_data = {'id': dev_id}
    has_change = False
    
    if 'device_name' in data:
        update_data['name'] = data['device_name']
        has_change = True
        
    if 'ip' in data:
        update_data['ip'] = data['ip']
        has_change = True
        
    if 'version' in data:
        update_data['version'] = data['version']
        has_change = True
        
    # Xử lý đổi tên DP
    dp_id = data.get('dp_id')
    dp_name = data.get('name')
    
    if dp_id and dp_name:
        # Cần get mapping cũ ra để update
        with data_lock:
            info = tuya_cache.get(dev_id)
            if info:
                current_mapping = info.get('mapping', {})
                str_dp = str(dp_id)
                if str_dp not in current_mapping:
                    current_mapping[str_dp] = {"code": f"DP {str_dp}", "type": "String"}
                
                current_mapping[str_dp]['name'] = dp_name
                update_data['mapping'] = current_mapping
                has_change = True

    if has_change:
        # Ghi vào DB
        db_manager.upsert_device(update_data)
        
        # Load lại để cập nhật Cache
        load_system()
        return jsonify({"success": True, "message": "Đã lưu vào DB."})
    
    return jsonify({"success": False, "message": "Không có gì thay đổi."})

@app.route('/api/control', methods=['POST'])
def control_device():
    data = request.json
    dev_id = data.get('id')
    action = data.get('action') 
    dps_id = data.get('dps_id') 
    
    info = tuya_cache.get(dev_id)
    if not info or not info.get('obj'):
        return jsonify({"success": False, "message": "Chưa có kết nối"}), 400

    try:
        dev_obj = info['obj']
        if action in ['on', 'off']:
            is_on = (action == 'on')
            if dps_id:
                dev_obj.set_value(str(dps_id), is_on)
                with data_lock: info['dps'][str(dps_id)] = is_on 
                # Cập nhật DB ngay sau khi điều khiển thành công
                db_manager.update_device_state(dev_id, {str(dps_id): is_on})
            else:
                if is_on: dev_obj.turn_on()
                else: dev_obj.turn_off()
                with data_lock:
                    if '1' in info['dps']: info['dps']['1'] = is_on
                    if '20' in info['dps']: info['dps']['20'] = is_on
                    # Cập nhật DB
                    db_manager.update_device_state(dev_id, info['dps'])

        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/settings')
def settings_page():
    return send_from_directory('.', 'settings.html')

@app.route('/api/settings', methods=['GET'])
def get_settings():
    try:
        data = db_manager.get_all_settings()
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/settings', methods=['POST'])
def save_settings():
    try:
        data = request.json
        for k, v in data.items():
            db_manager.set_setting(k, v)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

if __name__ == '__main__':
    print("--> Server running: http://localhost:5000")
    app.run(host='0.0.0.0', port=5000, debug=True)