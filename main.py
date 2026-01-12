from flask import Flask, jsonify, request, send_from_directory
import tinytuya
import json
import time
import os
import threading
from datetime import datetime, timedelta # <--- THÊM DÒNG NÀY

app = Flask(__name__)

DEVICES_FILE = 'devices.json'
SNAPSHOT_FILE = 'snapshot.json'

# Cache lưu trạng thái thiết bị
tuya_cache = {}
devices_config_list = [] 
active_timers = {}

# Lock để tránh xung đột khi nhiều luồng cùng ghi dữ liệu
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

def load_snapshot():
    global tuya_cache
    if not os.path.exists(SNAPSHOT_FILE): return
    try:
        with open(SNAPSHOT_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        dev_list = data.get('devices', []) if isinstance(data, dict) else data
        for s in dev_list:
            did = s.get('id')
            if not did: continue
            if did not in tuya_cache: 
                tuya_cache[did] = get_default_info(did)
            
            dps = s.get('dps', {})
            if 'dps' in dps: dps = dps['dps']
            if dps: tuya_cache[did]['dps'].update(dps)
            
            if 'ver' in s:
                tuya_cache[did]['snapshot_ver'] = safe_float_version(s['ver'])
    except Exception as e:
        print(f"⚠️ Lỗi đọc snapshot: {e}")

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
    snapshot_ver = 0.0
    if dev_id in tuya_cache:
        snapshot_ver = tuya_cache[dev_id].get('snapshot_ver', 0.0)
    
    if parent:
        ip = parent.get('ip')
        key = parent.get('key')
        ver = safe_float_version(parent.get('version'))
        if ver == 0.0: ver = 3.3
    else:
        ip = dev.get('ip')
        key = dev_key
        if config_ver > 0: ver = config_ver
        elif snapshot_ver > 0: ver = snapshot_ver
        else: ver = 3.3 

    if dev_id not in tuya_cache:
        tuya_cache[dev_id] = get_default_info(dev_id)

    tuya_cache[dev_id].update({
        "name": dev.get('name', 'Unknown'),
        "type": determine_device_type(dev),
        "category": dev.get('category', ''),
        "mapping": dev.get('mapping', {}),
        "ip": ip if ip and ip != "0.0.0.0" else None,
        "real_ip": dev.get('ip', ''),
        "version": ver,
        "via": parent.get('name') if parent else None,
        "is_sub": True if parent else False,
        "missing_ip": False
    })

    if not ip or ip == "0.0.0.0":
        tuya_cache[dev_id]["missing_ip"] = True
        return

    try:
        # Tái sử dụng object cũ nếu có để tránh tạo socket liên tục
        if not tuya_cache[dev_id].get("obj"):
            if tuya_cache[dev_id]['type'] == 'light':
                d = tinytuya.BulbDevice(dev_id, ip, key)
            else:
                d = tinytuya.OutletDevice(dev_id, ip, key)
            
            d.set_version(ver)
            d.set_socketPersistent(True) # Giữ kết nối để poll nhanh hơn
            d.set_socketRetryLimit(1)
            d.set_socketTimeout(2)
            if parent: d.cid = dev.get('node_id', dev_id)
            
            tuya_cache[dev_id]["obj"] = d
        else:
            # Update lại thông tin nếu config đổi
            d = tuya_cache[dev_id]["obj"]
            d.set_version(ver)
            d.address = ip
            d.local_key = key
            if parent: d.cid = dev.get('node_id', dev_id)

    except: pass

def load_system():
    global devices_config_list
    if os.path.exists(DEVICES_FILE):
        with open(DEVICES_FILE, 'r', encoding='utf-8') as f:
            devices_config_list = json.load(f)
    
    load_snapshot() 
    
    gateways = {d['id']: d for d in devices_config_list if 'wg' in d.get('category', '') or (d.get('ip') and not d.get('parent'))}

    for dev in devices_config_list:
        parent = None
        pid = dev.get('parent')
        if pid and pid in gateways: parent = gateways[pid]
        init_device(dev, parent)
    print(f"--> Đã nạp {len(tuya_cache)} thiết bị.")

# --- LUỒNG CẬP NHẬT TRẠNG THÁI (POLLING THREAD) ---
def background_polling():
    while True:
        
        now = datetime.now()
        # Duyệt qua danh sách timer (Key bây giờ sẽ có dạng "deviceid_dpid")
        for key, timer in list(active_timers.items()):
            if now >= timer['end_time']:
                try:
                    # Tách key để lấy ID thiết bị và ID nút
                    # Key định dạng: "devID_dpID" (VD: "bf82xx..._1")
                    parts = key.rsplit('_', 1) 
                    did = parts[0]
                    dp_id = parts[1] if len(parts) > 1 else None

                    print(f"⏰ Timer kích hoạt: {did} (DP {dp_id}) -> {timer['action']}")
                    
                    info = tuya_cache.get(did)
                    if info and info.get('obj'):
                        dev_obj = info['obj']
                        is_on = (timer['action'] == 'on')
                        
                        # LOGIC ĐIỀU KHIỂN CHÍNH XÁC TỪNG NÚT
                        if dp_id and dp_id != 'None':
                            dev_obj.set_value(str(dp_id), is_on)
                            with data_lock:
                                info['dps'][str(dp_id)] = is_on
                        else:
                            # Fallback cho thiết bị đơn (không có DP cụ thể)
                            if is_on: dev_obj.turn_on()
                            else: dev_obj.turn_off()
                            # Cập nhật tạm cache (để UI phản hồi ngay)
                            with data_lock:
                                if '1' in info['dps']: info['dps']['1'] = is_on
                                if '20' in info['dps']: info['dps']['20'] = is_on
                    
                    # Xóa timer sau khi xong
                    del active_timers[key]
                except Exception as e:
                    print(f"Lỗi Timer: {e}")
        # -------------------------

        # Lặp qua tất cả thiết bị để lấy trạng thái mới nhất
        # Copy keys để tránh lỗi runtime khi dict thay đổi size
        device_ids = list(tuya_cache.keys())
        
        for dev_id in device_ids:
            info = tuya_cache.get(dev_id)
            if not info or info.get('missing_ip') or not info.get('obj'):
                continue
            
            try:
                dev = info['obj']
                # Lấy status
                data = dev.status()
                
                if data and 'dps' in data:
                    with data_lock:
                        info['dps'].update(data['dps'])
                        info['online'] = True
                        info['last_update'] = time.time()
                elif 'Error' in str(data):
                    info['online'] = False
            except:
                info['online'] = False
            
            # Nghỉ nhẹ giữa các thiết bị để không làm nghẽn mạng
            time.sleep(0.1)
        
        # Đợi 5 giây trước khi quét lại vòng mới
        time.sleep(5)

# Khởi chạy hệ thống
load_system()

# Bắt đầu luồng chạy ngầm
poll_thread = threading.Thread(target=background_polling, daemon=True)
poll_thread.start()

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

@app.route('/api/devices', methods=['GET'])
def get_devices():
    response_list = []
    # Trả về cache ngay lập tức (Cache luôn được update bởi luồng ngầm)
    with data_lock:
        for dev_id, info in tuya_cache.items():
            
            # Fix lỗi tên mapping (như code trước)
            mapping = info.get('mapping', {})
            display_dps = {}
            
            if mapping:
                for dp, val in mapping.items():
                    pass # Chỉ để đảm bảo code mapping tồn tại
            timers_info = {}
            
            # Quét qua tất cả active_timers xem có cái nào thuộc về device này không
            for key, val in active_timers.items():
                if key.startswith(dev_id):
                    # Tách lại dp_id từ key
                    parts = key.rsplit('_', 1)
                    t_dp = parts[1] if len(parts) > 1 else 'main'
                    
                    remaining = val['end_time'] - datetime.now()
                    total_seconds = int(remaining.total_seconds())
                    if total_seconds > 0:
                        mins = total_seconds // 60
                        secs = total_seconds % 60
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

# --- SỬA API NÀY TRONG main.py ---
@app.route('/api/set_timer', methods=['POST'])
def set_timer():
    data = request.json
    dev_id = data.get('id')
    # Nhận thêm dp_id (nếu là thiết bị đơn thì dp_id có thể là null hoặc '1')
    dp_id = str(data.get('dp_id', '')) 
    minutes = int(data.get('minutes', 0))
    
    # Tạo Key duy nhất cho từng nút: "deviceID_dpID"
    timer_key = f"{dev_id}_{dp_id}"

    # Hủy Timer
    if minutes <= 0:
        if timer_key in active_timers:
            del active_timers[timer_key]
        return jsonify({"success": True, "message": "Đã hủy hẹn giờ."})

    info = tuya_cache.get(dev_id)
    if not info: return jsonify({"success": False}), 404
    
    # Lấy trạng thái hiện tại CỦA ĐÚNG NÚT ĐÓ
    dps = info.get('dps', {})
    is_currently_on = False
    
    if dp_id and dp_id in dps:
        is_currently_on = dps[dp_id]
    else:
        # Fallback cho thiết bị đơn
        is_currently_on = dps.get('1') or dps.get('20') or False
    
    action = 'off' if is_currently_on else 'on'
    end_time = datetime.now() + timedelta(minutes=minutes)
    
    # Lưu timer với key mới
    active_timers[timer_key] = {
        'end_time': end_time,
        'action': action
    }
    
    action_vn = "TẮT" if action == 'off' else "BẬT"
    target_name = info['name']
    # Nếu có tên nút con, hiển thị cho rõ
    if dp_id and 'mapping' in info and dp_id in info['mapping']:
        target_name += " (" + info['mapping'][dp_id].get('name', dp_id) + ")"

    return jsonify({"success": True, "message": f"Sẽ {action_vn} {target_name} sau {minutes} phút."})

@app.route('/api/update_config', methods=['POST'])
def update_config():
    data = request.json
    dev_id = data.get('id')
    new_ip = data.get('ip')
    new_ver = data.get('version')
    new_dev_name = data.get('device_name')
    
    # Rename DP (Nút hoặc Cảm biến)
    dp_id_to_rename = data.get('dp_id')
    new_dp_name = data.get('name')

    if not dev_id: return jsonify({"success": False}), 400

    changed = False
    with data_lock:
        for dev in devices_config_list:
            if dev['id'] == dev_id:
                if new_ip is not None: 
                    dev['ip'] = new_ip
                    changed = True
                if new_ver is not None:
                    dev['version'] = str(new_ver)
                    changed = True
                if new_dev_name is not None:
                    dev['name'] = new_dev_name.strip()
                    changed = True
                
                # Logic đổi tên DP (Dùng chung cho cả Switch và Sensor)
                if dp_id_to_rename is not None and new_dp_name is not None:
                    if 'mapping' not in dev: dev['mapping'] = {}
                    str_dp = str(dp_id_to_rename)
                    if str_dp not in dev['mapping']:
                        # Nếu chưa có mapping, tạo mới mặc định
                        dev['mapping'][str_dp] = {"code": f"DP {str_dp}", "type": "String"}
                    
                    dev['mapping'][str_dp]['name'] = new_dp_name 
                    changed = True
                break
            
    if changed:
        with open(DEVICES_FILE, 'w', encoding='utf-8') as f:
            json.dump(devices_config_list, f, indent=4, ensure_ascii=False)
        load_system() 
        return jsonify({"success": True, "message": "Đã lưu cấu hình."})
    
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
            else:
                if is_on: dev_obj.turn_on()
                else: dev_obj.turn_off()
                with data_lock:
                    if '1' in info['dps']: info['dps']['1'] = is_on
                    if '20' in info['dps']: info['dps']['20'] = is_on

        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

if __name__ == '__main__':
    print("--> Server running: http://localhost:5000")
    app.run(host='0.0.0.0', port=5000, debug=True)