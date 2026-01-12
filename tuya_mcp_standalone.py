from mcp.server.fastmcp import FastMCP
import tinytuya
import json
import os
import time
import logging

# --- LOGGING SETUP ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger('tuya_mcp')

mcp = FastMCP("TuyaHomeControl")

DEVICES_FILE = 'devices.json'

# Cache
tuya_cache = {} 
device_lookup = {} 

def load_devices():
    """Nạp thiết bị và tạo chỉ mục tìm kiếm thông minh."""
    global tuya_cache, device_lookup
    if not os.path.exists(DEVICES_FILE):
        logger.error("File devices.json not found")
        return

    try:
        with open(DEVICES_FILE, 'r', encoding='utf-8') as f:
            raw_devices = json.load(f)
            
        # Tìm Gateway trước
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

            # Xử lý Gateway/Zigbee
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

            # --- TẠO CHỈ MỤC TÌM KIẾM ---
            
            # 1. Index tên thiết bị cha
            if parent_name:
                key = parent_name.lower().strip()
                temp_lookup[key] = {'id': dev_id, 'dp': None, 'name': parent_name}

            # 2. Index tên các nút con
            mapping = dev.get('mapping', {})
            for dp_id, dp_data in mapping.items():
                btn_name = dp_data.get('name', dp_data.get('code'))
                # Chỉ index nếu là công tắc (Boolean) và có tên
                if btn_name and dp_data.get('type') == 'Boolean':
                    key = btn_name.lower().strip()
                    temp_lookup[key] = {'id': dev_id, 'dp': dp_id, 'name': btn_name}

        tuya_cache = temp_cache
        device_lookup = temp_lookup
        logger.info(f"Loaded {len(tuya_cache)} devices, {len(device_lookup)} searchable names.")

    except Exception as e:
        logger.error(f"Error reading devices.json: {e}")

load_devices()

def get_device_obj(dev_info):
    try:
        dev_id = dev_info['id']
        ip = dev_info.get('ip')
        key = dev_info.get('key')
        ver = float(dev_info.get('version', 3.3) or 3.3)
        
        if not ip: return None, f"IP Missing for {dev_info.get('name')}"

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

# --- MCP TOOLS ---

@mcp.tool()
def list_devices() -> str:
    """Liệt kê danh sách tên thiết bị để kiểm tra."""
    load_devices()
    lines = []
    for name in sorted(device_lookup.keys()):
        info = device_lookup[name]
        type_str = "Nút con" if info['dp'] else "Thiết bị chính"
        lines.append(f"- {info['name']} ({type_str})")
    return "\n".join(lines)

@mcp.tool()
def control_device(device_name: str, command: str) -> str:
    """
    Bật hoặc tắt thiết bị.
    Args:
        device_name: Tên thiết bị hoặc tên nút (VD: 'Hút mùi', 'Đèn trần').
        command: 'on' hoặc 'off'.
    """
    load_devices()
    target_name = device_name.lower().strip()
    target_info = None

    if target_name in device_lookup:
        target_info = device_lookup[target_name]
    else:
        for key, info in device_lookup.items():
            if target_name in key:
                target_info = info
                break
    
    if not target_info:
        return f"Không tìm thấy thiết bị '{device_name}'."

    dev_config = tuya_cache.get(target_info['id'])
    if not dev_config: return "Lỗi cấu hình thiết bị."

    d, err = get_device_obj(dev_config)
    if err: return f"Lỗi kết nối: {err}"

    cmd = command.lower()
    is_on = (cmd == 'on')
    dp_id = target_info['dp'] # ID nút con (nếu có)
    real_name = target_info['name']

    try:
        if dp_id:
            logger.info(f"Control SUB: {real_name} (DP {dp_id}) -> {cmd}")
            d.set_value(str(dp_id), is_on)
            return f"Đã {cmd} {real_name}."
        else:
            logger.info(f"Control MAIN: {real_name} -> {cmd}")
            if is_on: d.turn_on()
            else: d.turn_off()
            return f"Đã {cmd} toàn bộ {real_name}."
    except Exception as e:
        logger.error(f"Failed: {e}")
        return f"Thất bại: {e}"

@mcp.tool()
def check_status(device_name: str) -> str:
    """
    Kiểm tra trạng thái Bật/Tắt của thiết bị hoặc các nút.
    
    Args:
        device_name: Tên thiết bị (VD: 'Công tắc vệ sinh') hoặc tên nút (VD: 'Hút mùi').
    """
    load_devices()
    target_name = device_name.lower().strip()
    target_info = None

    # Tìm kiếm (Chính xác hoặc Gần đúng)
    if target_name in device_lookup:
        target_info = device_lookup[target_name]
    else:
        for key, info in device_lookup.items():
            if target_name in key:
                target_info = info
                break
    
    if not target_info:
        return f"Không tìm thấy thiết bị '{device_name}' để kiểm tra."

    dev_config = tuya_cache.get(target_info['id'])
    d, err = get_device_obj(dev_config)
    if err: return f"Không thể kết nối đến {target_info['name']}: {err}"

    try:
        # Lấy dữ liệu thực tế từ thiết bị
        data = d.status()
        if not data or 'dps' not in data:
            return f"Thiết bị {target_info['name']} đang ngoại tuyến (Offline)."
        
        dps = data['dps']
        requested_dp = target_info['dp'] # ID cụ thể nếu người dùng hỏi nút con
        
        # TRƯỜNG HỢP 1: Hỏi trạng thái 1 nút cụ thể (VD: "Hút mùi có bật không?")
        if requested_dp:
            state = dps.get(str(requested_dp))
            status_str = "BẬT" if state else "TẮT"
            return f"{target_info['name']} đang {status_str}."

        # TRƯỜNG HỢP 2: Hỏi trạng thái thiết bị tổng (VD: "Công tắc vệ sinh thế nào?")
        else:
            mapping = dev_config.get('mapping', {})
            # Nếu thiết bị đơn (không có mapping nhiều nút), lấy trạng thái chung
            if not mapping:
                # Thường thiết bị đơn dùng DP '1' hoặc '20'
                is_on = dps.get('1') or dps.get('20') or False
                status_str = "BẬT" if is_on else "TẮT"
                return f"{target_info['name']} đang {status_str}."
            
            # Nếu là thiết bị nhiều nút -> Liệt kê hết ra
            report_parts = []
            sorted_dps = sorted(mapping.keys(), key=lambda x: int(x) if x.isdigit() else 99)
            
            for dp in sorted_dps:
                detail = mapping[dp]
                # Chỉ báo cáo các nút là công tắc (Boolean)
                if detail.get('type') == 'Boolean':
                    btn_name = detail.get('name', detail.get('code', dp))
                    state = dps.get(str(dp))
                    state_str = "BẬT" if state else "TẮT"
                    report_parts.append(f"{btn_name}: {state_str}")
            
            if not report_parts:
                return f"{target_info['name']} đang Online."
                
            return f"Trạng thái {target_info['name']}: " + ", ".join(report_parts)

    except Exception as e:
        logger.error(f"Status check failed: {e}")
        return f"Lỗi khi đọc trạng thái: {e}"

if __name__ == "__main__":
    mcp.run(transport="stdio")