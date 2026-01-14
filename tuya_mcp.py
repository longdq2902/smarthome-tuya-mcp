# FILE: tuya_mcp.py
import tinytuya
import json
import os
import logging
import db_manager # <--- MỚI

# Setup Logger riêng
logger = logging.getLogger('tuya_module')

# Global variables
tuya_cache = {} 
device_lookup = {} 

def load_devices():
    """Nạp và xử lý danh sách thiết bị từ SQLite"""
    global tuya_cache, device_lookup
    
    try:
        # 1. Lấy dữ liệu từ DB
        raw_devices = db_manager.get_all_devices()
            
        # 2. Tìm Gateway
        gateways = {}
        for d in raw_devices:
            if d.get('ip') and (not d.get('parent') or 'wg' in d.get('category', '')):
                gateways[d['id']] = d

        temp_cache = {}
        temp_lookup = {}

        for dev in raw_devices:
            dev_id = dev.get('id')
            parent_name = dev.get('name', 'Unknown')
            if not dev_id: continue

            # Xử lý kết nối (Zigbee/WiFi)
            processed_dev = dev.copy()
            parent_id = dev.get('parent')
            
            # Logic thừa kế IP từ Gateway
            if parent_id and parent_id in gateways:
                gateway = gateways[parent_id]
                processed_dev['ip'] = gateway.get('ip')
                processed_dev['key'] = gateway.get('key')
                processed_dev['version'] = gateway.get('version', 3.3)
                processed_dev['is_sub'] = True
                if not processed_dev.get('node_id'):
                    processed_dev['node_id'] = dev_id
            
            temp_cache[dev_id] = processed_dev

            # Index thiết bị cha
            if parent_name:
                temp_lookup[parent_name.lower().strip()] = {'id': dev_id, 'dp': None, 'name': parent_name}

            # Index nút con
            mapping = dev.get('mapping', {})
            for dp_id, dp_data in mapping.items():
                btn_name = dp_data.get('name', dp_data.get('code'))
                if btn_name and dp_data.get('type') == 'Boolean':
                    temp_lookup[btn_name.lower().strip()] = {'id': dev_id, 'dp': dp_id, 'name': btn_name}

        tuya_cache = temp_cache
        device_lookup = temp_lookup
        logger.info(f"Loaded {len(tuya_cache)} devices from DB.")

    except Exception as e:
        logger.error(f"Error loading devices: {e}")

def get_tuya_obj(dev_info):
    try:
        dev_id = dev_info['id']
        ip = dev_info.get('ip')
        key = dev_info.get('key')
        ver = float(dev_info.get('version', 3.3) or 3.3)
        
        if not ip: return None, "Missing IP"

        d = tinytuya.OutletDevice(dev_id, ip, key)
        if dev_info.get('is_sub'):
            d.cid = dev_info.get('node_id', dev_id)
        
        d.set_version(ver)
        d.set_socketPersistent(False)
        d.set_socketRetryLimit(2)
        d.set_socketTimeout(3)
        return d, None
    except Exception as e:
        return None, str(e)

# --- CÁC HÀM CÔNG CỤ (TOOLS) ---
# Lưu ý: Không dùng @mcp.tool() ở đây, ta chỉ định nghĩa hàm thuần Python.

def list_devices() -> str:
    """Liệt kê các thiết bị trong nhà."""
    load_devices()
    lines = []
    for name in sorted(device_lookup.keys()):
        info = device_lookup[name]
        type_str = "Nút" if info['dp'] else "Thiết bị"
        lines.append(f"- {info['name']} ({type_str})")
    return "\n".join(lines)

def control_device(device_name: str, command: str) -> str:
    """Bật/Tắt thiết bị điện."""
    load_devices()
    target_name = device_name.lower().strip()
    target_info = None

    if target_name in device_lookup:
        target_info = device_lookup[target_name]
    else:
        for key, info in device_lookup.items():
            if target_name in key:
                target_info = info; break
    
    if not target_info: return f"Không tìm thấy '{device_name}'."

    dev_config = tuya_cache.get(target_info['id'])
    d, err = get_tuya_obj(dev_config)
    if err: return f"Lỗi kết nối: {err}"

    cmd = command.lower().strip()
    is_on = (cmd == 'on' or cmd == 'bật' or cmd == 'true' or cmd == '1' or 'bật' in cmd)
    dp_id = target_info['dp']
    real_name = target_info['name']

    try:
        if dp_id:
            d.set_value(str(dp_id), is_on)
            # Update DB (Optional nhưng nên làm để đồng bộ với Web)
            db_manager.update_device_state(target_info['id'], {str(dp_id): is_on})
            return f"Đã {command} {real_name}."
        else:
            if is_on: d.turn_on()
            else: d.turn_off()
            # Update DB
            # Note: Thiết bị đơn thường trả về dps '1' hoặc '20'
            # Ở đây ta set tạm giả định, thực tế nên chờ polling cập nhật
            return f"Đã {command} toàn bộ {real_name}."
    except Exception as e: return f"Thất bại: {e}"

def check_status(device_name: str) -> str:
    """Kiểm tra trạng thái thiết bị."""
    load_devices()
    target_name = device_name.lower().strip()
    target_info = None
    
    if target_name in device_lookup: target_info = device_lookup[target_name]
    else:
        for key, info in device_lookup.items():
            if target_name in key: target_info = info; break
    
    if not target_info: return "Không tìm thấy thiết bị."

    dev_config = tuya_cache.get(target_info['id'])
    # Ưu tiên lấy từ Cache DB nếu có (để phản hồi nhanh và tránh connect nhiều)
    # Tuy nhiên tool này yêu cầu check thực tế nên ta vẫn kết nối
    d, err = get_tuya_obj(dev_config)
    if err: return "Mất kết nối."

    try:
        data = d.status()
        if not data or 'dps' not in data: return "Thiết bị Offline."
        dps = data['dps']
        
        # Update ngược lại DB
        db_manager.update_device_state(target_info['id'], dps)
        
        if target_info['dp']:
            st = "BẬT" if dps.get(str(target_info['dp'])) else "TẮT"
            return f"{target_info['name']} đang {st}."
        else:
            mapping = dev_config.get('mapping', {})
            if not mapping:
                st = "BẬT" if (dps.get('1') or dps.get('20')) else "TẮT"
                return f"{target_info['name']} đang {st}."
            
            parts = []
            for dp, detail in mapping.items():
                if detail.get('type') == 'Boolean':
                    n = detail.get('name', detail.get('code'))
                    s = "BẬT" if dps.get(str(dp)) else "TẮT"
                    parts.append(f"{n}: {s}")
            return f"Trạng thái {target_info['name']}: {', '.join(parts)}"
    except Exception as e: return f"Lỗi: {e}"

# Load lần đầu
load_devices()
