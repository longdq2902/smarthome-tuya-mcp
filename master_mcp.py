# FILE: master_mcp.py
from mcp.server.fastmcp import FastMCP
import logging
import sys
import threading
from datetime import datetime, timedelta # <--- MỚI: Cần cái này để tính giờ

# 1. IMPORT CÁC MODULE CON
import tuya_mcp
# import bank_mcp

# 2. IMPORT WEB SERVER
try:
    import main as web_server
except ImportError as e:
    sys.stderr.write(f"Loi import main.py: {e}\n")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', stream=sys.stderr)

mcp = FastMCP("XiaoZhi_Smart_Home")

# --- HÀM KHỞI CHẠY WEB SERVER ---
def start_flask():
    sys.stderr.write("--> Starting Flask Web Server on port 5000...\n")
    web_server.app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)

# --- [MỚI] HÀM TOOL HẸN GIỜ CHO AI ---
# --- [MỚI] HÀM TOOL HẸN GIỜ CHO AI ---
def set_timer_tool(device_name: str, minutes: int) -> str:
    """
    Hẹn giờ Bật hoặc Tắt thiết bị (Hỗ trợ cả công tắc nhiều nút).
    Args:
        device_name: Tên thiết bị (VD: 'Quạt', 'Đèn trần', 'Công tắc 1').
        minutes: Số phút đếm ngược (VD: 30). Nhập 0 để hủy hẹn giờ.
    """
    # 1. Cập nhật danh sách thiết bị mới nhất để tìm kiếm
    tuya_mcp.load_devices() 
    
    # 2. Tìm kiếm thiết bị theo tên
    target_name = device_name.lower().strip()
    target_info = None

    if target_name in tuya_mcp.device_lookup:
        target_info = tuya_mcp.device_lookup[target_name]
    else:
        # Tìm gần đúng
        for key, info in tuya_mcp.device_lookup.items():
            if target_name in key:
                target_info = info
                break
    
    if not target_info:
        return f"Không tìm thấy thiết bị tên là '{device_name}'."

    # 3. Lấy thông tin định danh
    dev_id = target_info['id']
    dp_id = target_info.get('dp') # Lấy ID nút con (nếu là switch nhiều nút)
    
    # Chuẩn hóa dp_id để khớp với key trong main.py (nếu None thì là chuỗi rỗng)
    safe_dp_id = str(dp_id) if dp_id else ""
    timer_key = f"{dev_id}_{safe_dp_id}"

    # 4. Xử lý Hủy Timer
    if minutes <= 0:
        if timer_key in web_server.active_timers:
            del web_server.active_timers[timer_key]
            return f"Đã hủy hẹn giờ cho {target_info['name']}."
        return f"Thiết bị {target_info['name']} hiện không có hẹn giờ nào."

    # 5. Xác định hành động (Bật hay Tắt?) dựa trên trạng thái hiện tại
    info = web_server.tuya_cache.get(dev_id)
    if not info: return "Không lấy được thông tin thiết bị."
    
    dps = info.get('dps', {})
    is_currently_on = False

    if dp_id and str(dp_id) in dps:
        # Nếu là nút con, lấy trạng thái nút con
        is_currently_on = dps[str(dp_id)]
    else:
        # Nếu là thiết bị đơn, lấy trạng thái chung (thường là 1 hoặc 20)
        is_currently_on = dps.get('1') or dps.get('20') or False

    # Logic đảo ngược: Đang Bật -> Hẹn Tắt, Đang Tắt -> Hẹn Bật
    action = 'off' if is_currently_on else 'on'
    end_time = datetime.now() + timedelta(minutes=minutes)

    # 6. Ghi vào bộ nhớ của Web Server
    web_server.active_timers[timer_key] = {
        'end_time': end_time,
        'action': action
    }

    action_vn = "TẮT" if action == 'off' else "BẬT"
    return f"Đã đặt lịch: {target_info['name']} sẽ {action_vn} sau {minutes} phút nữa."

# 3. ĐĂNG KÝ CÁC TOOLS
mcp.add_tool(tuya_mcp.list_devices, name="Danh_sach_thiet_bi", description="Liệt kê tên các thiết bị.")
mcp.add_tool(tuya_mcp.control_device, name="Dieu_khien_thiet_bi", description="Bật hoặc tắt ngay lập tức.")
mcp.add_tool(tuya_mcp.check_status, name="Kiem_tra_trang_thai", description="Kiểm tra xem thiết bị đang Bật hay Tắt.")

# Đăng ký tool mới
mcp.add_tool(set_timer_tool, name="Hen_gio_thiet_bi", description="Hẹn giờ bật hoặc tắt thiết bị sau một khoảng thời gian (phút).")

if __name__ == "__main__":
    flask_thread = threading.Thread(target=start_flask, daemon=True)
    flask_thread.start()

    sys.stderr.write("Master MCP is ready! Waiting for connection...\n")
    mcp.run(transport="stdio")