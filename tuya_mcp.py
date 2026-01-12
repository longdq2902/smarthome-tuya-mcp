# FILE: tuya_mcp.py
import tinytuya
import json
import os
import logging

# Setup Logger riêng
logger = logging.getLogger('tuya_module')
DEVICES_FILE = 'devices.json'

# Global variables
tuya_cache = {} 
device_lookup = {} 

def load_devices():
    """Nạp và xử lý danh sách thiết bị"""
    global tuya_cache, device_lookup
    if not os.path.exists(DEVICES_FILE):
        return

    try:
        with open(DEVICES_FILE, 'r', encoding='utf-8') as f:
            raw_devices = json.load(f)
            
        # Tìm Gateway
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
        logger.info(f"Loaded {len(tuya_cache)} devices.")

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

    is_on = (command.lower() == 'on')
    dp_id = target_info['dp']
    real_name = target_info['name']

    try:
        if dp_id:
            d.set_value(str(dp_id), is_on)
            return f"Đã {command} {real_name}."
        else:
            if is_on: d.turn_on()
            else: d.turn_off()
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
    d, err = get_tuya_obj(dev_config)
    if err: return "Mất kết nối."

    try:
        data = d.status()
        if not data or 'dps' not in data: return "Thiết bị Offline."
        dps = data['dps']
        
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