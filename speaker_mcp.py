import logging
import requests
import db_manager

# Setup Logger
logger = logging.getLogger('speaker_module')

def speak(text, volume=None):
    """
    Gửi lệnh TTS tới thiết bị loa.
    Hiện tại là placeholder gửi tới xiaozhi.me hoặc log ra console.
    """
    try:
        if volume is None:
            # Lấy volume từ settings, mặc định 4
            volume = int(db_manager.get_setting('speaker_volume', 4))
        
        logger.info(f"📢 [SPEAK] Vol={volume}: {text}")
        print(f"📢 [LOA] Đang đọc: {text}")

        # --- PLACEHOLDER FOR PHICOMM R1 / XIAOZHI ---
        # TODO: Thay thế URL và Payload bên dưới bằng API thực tế
        # url = "http://xiaozhi.me/api/tts"
        # payload = {"text": text, "volume": volume, "device": "phicomm_r1"}
        # requests.post(url, json=payload, timeout=5)
        
        return True
    except Exception as e:
        logger.error(f"Error speaking: {e}")
        return False
