import json
import os
import db_manager

DEVICES_FILE = 'devices.json'
SNAPSHOT_FILE = 'snapshot.json'

def migrate():
    print("--> Bắt đầu chuyển đổi dữ liệu sang SQLite...")
    
    # Init DB schema
    db_manager.init_db()
    
    devices_data = {}
    
    # 1. Load config gốc
    if os.path.exists(DEVICES_FILE):
        try:
            with open(DEVICES_FILE, 'r', encoding='utf-8') as f:
                raw_list = json.load(f)
                print(f"    Tìm thấy {len(raw_list)} thiết bị trong {DEVICES_FILE}")
                for d in raw_list:
                    devices_data[d['id']] = d
        except Exception as e:
            print(f"!!! Lỗi đọc {DEVICES_FILE}: {e}")
            
    # 2. Load snapshot (trạng thái + thông tin phụ)
    if os.path.exists(SNAPSHOT_FILE):
        try:
            with open(SNAPSHOT_FILE, 'r', encoding='utf-8') as f:
                sn_data = json.load(f)
                sn_list = sn_data.get('devices', []) if isinstance(sn_data, dict) else sn_data
                print(f"    Tìm thấy {len(sn_list)} bản ghi trong {SNAPSHOT_FILE}")
                
                for s in sn_list:
                    did = s.get('id')
                    if did not in devices_data:
                        # Nếu thiết bị có trong snapshot nhưng ko có trong config -> Vẫn thêm vào
                        devices_data[did] = s
                    else:
                        # Merge thông tin: Ưu tiên DPS mới nhất từ snapshot
                        if 'dps' in s:
                            dps = s['dps']
                            if 'dps' in dps: dps = dps['dps'] # Xử lý trường hợp lồng nhau
                            
                            if 'dps' not in devices_data[did]: devices_data[did]['dps'] = {}
                            devices_data[did]['dps'].update(dps)
                            
                        # Merge version nếu config = 0
                        if devices_data[did].get('version') in [0, 0.0, "0.0"]:
                             devices_data[did]['version'] = s.get('ver', 3.3)
                             
        except Exception as e:
            print(f"!!! Lỗi đọc {SNAPSHOT_FILE}: {e}")

    # 3. Ghi vào DB
    count = 0
    for did, data in devices_data.items():
        try:
            db_manager.upsert_device(data)
            count += 1
        except Exception as e:
            print(f"!!! Lỗi lưu thiết bị {did}: {e}")

    print(f"--> Hoàn tất! Đã lưu {count} thiết bị vào 'smarthome.db'.")

if __name__ == "__main__":
    migrate()
