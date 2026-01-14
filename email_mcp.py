import threading
import time
import imaplib
import email
from email.header import decode_header
import logging
import db_manager
import speaker_mcp
import requests
import io
import re
import traceback
from datetime import datetime, timedelta
import json

# Try import PyPDF2
try:
    import PyPDF2
except ImportError:
    PyPDF2 = None

logger = logging.getLogger('email_module')

class EmailMCP:
    def __init__(self):
        self.running = True
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.last_check_date = None
        self.last_announce_date = None

    def start(self):
        self.thread.start()
        logger.info("📩 Email MCP Started")

    def loop(self):
        logger.info("📩 Email Scheduler Loop Running...")
        while self.running:
            try:
                now = datetime.now()
                current_hm = now.strftime("%H:%M")
                current_date = now.strftime("%Y-%m-%d")

                # 1. READ CONFIG FROM DB
                settings = db_manager.get_all_settings()
                check_times = settings.get('check_schedule', '12:00, 18:00').replace(' ', '').split(',')
                announce_time = settings.get('announce_schedule', '20:00').strip()
                
                # 2. CHECK EMAIL LOGIC
                # Simple check: if current minute matches trigger time and haven't run this minute
                # (To avoid multiple runs, we can track last run time)
                
                # Check Email
                if current_hm in check_times:
                   # Prevent double check in same minute (sleep 60s handled effectively by outer sleep, 
                   # but explicit check is safer)
                   if self.last_check_date != f"{current_date}_{current_hm}":
                       logger.info(f"⏰ Triggering Email Check at {current_hm}")
                       self.check_mail()
                       self.last_check_date = f"{current_date}_{current_hm}"

                # 3. ANNOUNCE LOGIC
                if current_hm == announce_time:
                    if self.last_announce_date != current_date:
                        logger.info(f"⏰ Triggering Daily Announcement at {current_hm}")
                        self.daily_announcement()
                        self.last_announce_date = current_date

            except Exception as e:
                logger.error(f"Error in Email Loop: {e}")
            
            time.sleep(30) # Check every 30s

    def check_mail(self):
        settings = db_manager.get_all_settings()
        username = settings.get('email_account')
        password = settings.get('email_password')
        sender_filter = settings.get('email_sender')
        bill_keyword = settings.get('bill_subject_keyword', 'Thông báo phí')
        
        if not username or not password:
            logger.warning("⚠️ Email credentials missing in Settings.")
            return

        try:
            # Connect IMAP (Configurable)
            imap_host = settings.get('imap_host', 'imap.gmail.com')
            imap_port = int(settings.get('imap_port', 993))
            
            mail = imaplib.IMAP4_SSL(imap_host, imap_port)
            mail.login(username, password)
            mail.select("inbox")

            # Calculate date range
            scan_days = int(settings.get('email_scan_days', 30))
            if scan_days < 1: scan_days = 1
            
            today = datetime.now() 
            # Note: timedelta(days=0) is today. So subtract (scan_days - 1).
            # If scan_days=1 (Today) -> delta=0 -> SINCE Today
            # If scan_days=30 -> delta=29 -> SINCE 29 days ago
            days_ago_date = today - timedelta(days=scan_days - 1)
            since_str = days_ago_date.strftime("%d-%b-%Y")
            
            search_crit = f'(SINCE "{since_str}")'
            if sender_filter:
                search_crit = f'(SINCE "{since_str}" FROM "{sender_filter}")'
            
            status, messages = mail.search(None, search_crit)
            if status != "OK": return

            mail_ids = messages[0].split()
            # Process last 10 emails only to be safe
            mail_ids = mail_ids[-10:] if len(mail_ids) > 10 else mail_ids
            logger.info(f"📩 Found {len(mail_ids)} potential emails.")

            for mid in mail_ids:
                res, msg_data = mail.fetch(mid, "(RFC822)")
                for response_part in msg_data:
                    if isinstance(response_part, tuple):
                        msg = email.message_from_bytes(response_part[1])
                        
                        # Decorre Subject
                        subject, encoding = decode_header(msg["Subject"])[0]
                        if isinstance(subject, bytes):
                            subject = subject.decode(encoding if encoding else "utf-8")
                        
                        sender = msg.get("From")
                        
                        # Parse Date
                        date_str = msg.get("Date")
                        dt = datetime.now()
                        if date_str:
                            try:
                                dt = email.utils.parsedate_to_datetime(date_str)
                            except: pass
                        
                        received_at = dt.strftime("%Y-%m-%d %H:%M:%S")

                        # DEDUPLICATION CHECK
                        if db_manager.check_email_exists(sender, subject, received_at):
                            logger.info(f"   -> [Skip] Email already exists: {subject}")
                            continue

                        logger.info(f"Processing: {subject} | From: {sender} | Date: {received_at}")

                        # Determine Type
                        content_type = 'NOTICE'
                        is_bill = False
                        if bill_keyword.lower() in subject.lower():
                            content_type = 'BILL'
                            is_bill = True
                        
                        logger.info(f"   -> [Detection] Type: {content_type}, Is Bill: {is_bill} (Keyword: '{bill_keyword}')")

                        summary = ""
                        metadata = {}
                        
                        # Process Content & Attachments
                        if msg.is_multipart():
                            logger.info("   -> [Structure] Multipart email detected.")
                            for part in msg.walk():
                                c_type = part.get_content_type()
                                c_disp = str(part.get("Content-Disposition"))
                                logger.info(f"     -> [Part] Type: {c_type}, Disp: {c_disp}")

                                # 1. Get Text Content
                                if c_type == "text/plain" and "attachment" not in c_disp:
                                    try: 
                                        text_part = part.get_payload(decode=True).decode()
                                        summary += text_part
                                        logger.info(f"       -> Extracted text body: {len(text_part)} chars")
                                    except Exception as e:
                                        logger.error(f"       -> Error reading text part: {e}")
                                
                                # 2. Get PDF Attachment (Only for Bills)
                                if is_bill and "application/pdf" in c_type:
                                    filename = part.get_filename()
                                    if filename:
                                        file_data = part.get_payload(decode=True)
                                        text_content = self.extract_pdf_text(file_data)
                                        logger.info(f"       -> PDF Extracted Text Length: {len(text_content)}")
                                        # Log first 500 chars to debug regex
                                        logger.info(f"       -> [DEBUG PDF TEXT]: {text_content[:500].replace(chr(10), ' ')}")
                                        
                                        summary += "\n[PDF Content]: " + text_content
                                        
                                        # Parse Month/Amount
                                        meta = self.parse_bill_content(text_content, dt)
                                        logger.info(f"       -> Parsed Metadata: {meta}")
                                        metadata.update(meta)

                        else:
                            # Not multipart
                            logger.info("   -> [Structure] Single part email.")
                            try: 
                                summary = msg.get_payload(decode=True).decode()
                                logger.info(f"     -> Extracted body: {len(summary)} chars")
                            except: pass

                        # SAVE TO DB
                        email_data = {
                            "received_at": received_at,
                            "subject": subject,
                            "sender": sender,
                            "content_type": content_type,
                            "summary": summary[:2000], # Limit length
                            "metadata": metadata
                        }
                        db_manager.add_email(email_data)
                        logger.info(f"   -> [Action] Saved to DB as {content_type}")
                        
                        # SEND TELEGRAM
                        if is_bill:
                            logger.info("   -> [Action] Sending Telegram notification...")
                            self.send_telegram_notification(subject, metadata)
                        else:
                            # Optional: Notify for other important notices
                            logger.info("   -> [Action] Skipped Telegram (Not a bill)")
                            pass

            mail.close()
            mail.logout()
        except Exception as e:
            logger.error(f"IMAP Error: {e}")
            traceback.print_exc()

    def extract_pdf_text(self, data):
        if not PyPDF2: return "[PyPDF2 not installed]"
        try:
            reader = PyPDF2.PdfReader(io.BytesIO(data))
            text = ""
            for page in reader.pages:
                text += page.extract_text()
            return text
        except: return "[Error parsing PDF]"

    def parse_bill_content(self, text, email_date=None):
        settings = db_manager.get_all_settings()
        mode = settings.get('parser_mode', 'regex')

        if mode == 'llm':
            return self.parse_bill_with_llm(text, settings)
        else:
            return self.parse_bill_with_regex(text, email_date)

    def parse_bill_with_llm(self, text, settings):
        api_key = settings.get('llm_api_key')
        if not api_key:
            logger.warning("⚠️ LLM Mode enabled but API Key missing.")
            return {}

        logger.info("🧠 Sending PDF content to DeepSeek LLM...")
        
        prompt = f"""
        Bạn là một trợ lý AI phân tích hóa đơn. Nhiệm vụ của bạn là trích xuất thông tin từ nội dung file PDF bên dưới và trả về kết quả dạng JSON.
        
        Yêu Cầu:
        1. Tìm THÁNG của hóa đơn (Ví dụ: "Tháng 01", "Kỳ 1", "Ngày TB: 15/01/2025" -> month: 1).
        2. Tìm SỐ TIỀN phải thanh toán (Ví dụ: "Tổng cộng: 1.000.000", "Thanh toán: 500,000").
        
        Prompt Update:
        Output JSON Format:
        {{
            "month": <int>,
            "amount": <int> 
        }}
        Note: amount must be an integer number (no dots, no commas, no currency unit).
        Example: 1000000 instead of "1.000.000"
        """
        # Limit text to 8000 chars to avoid token limits

        try:
            url = "https://api.deepseek.com/v1/chat/completions" # DeepSeek Endpoint (Standard)
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}"
            }
            data = {
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1
            }
            
            response = requests.post(url, headers=headers, json=data, timeout=30)
            
            if response.status_code == 200:
                result = response.json()
                content = result['choices'][0]['message']['content']
                # Clean JSON string (remove markdown ```json ... ```)
                content = content.replace('```json', '').replace('```', '').strip()
                return json.loads(content)
            else:
                logger.error(f"DeepSeek API Error: {response.status_code} - {response.text}")
                return {}
        except Exception as e:
            logger.error(f"LLM Error: {e}")
            return {}

    def parse_bill_with_regex(self, text, email_date=None):
        meta = {}
        try:
            # 1. MONTH from Notice Date Pattern "NgàyTB:25/12/2025"
            # Pattern: Case insensitive, allow optional spaces
            date_match = re.search(r'Ngày\s*TB\s*[:]\s*(\d{1,2})[/-](\d{1,2})[/-](\d{4})', text, re.IGNORECASE)
            
            if date_match:
                # Group 2 is Month
                meta['month'] = int(date_match.group(2))
            else:
                # Fallback to email date if not found (optional, or just leave empty)
                if email_date:
                    meta['month'] = email_date.month
            
            # 2. AMOUNT from Specific Pattern
            # User Pattern: "TỔNGSỐTIỀNPHẢITHANHTOÁN=(D+E) 813.440"
            # We use a flexible regex that allows optional spaces
            key_pattern = r'TỔNG\s*SỐ\s*TIỀN\s*PHẢI\s*THANH\s*TOÁN\s*=\s*\(D\s*\+\s*E\)'
            
            amount_match = re.search(key_pattern + r'\s*([\d.,]+)', text, re.IGNORECASE)
            
            if amount_match:
                # Remove separators to get integer
                raw_amount = amount_match.group(1).replace('.', '').replace(',', '')
                meta['amount'] = int(raw_amount)
            else:
                logger.info("       -> [Regex Failed] Could not find specific amount pattern 'TỔNG SỐ TIỀN...=(D+E)'")
        except Exception as e:
             logger.error(f"Regex Error: {e}")
        return meta

    def _format_money(self, amount):
        """Format number to string with dots: 1000 -> 1.000"""
        try:
             return "{:,.0f}".format(int(amount)).replace(",", ".")
        except: return str(amount)

    def send_telegram_notification(self, subject, metadata):
        settings = db_manager.get_all_settings()
        if settings.get('telegram_enabled') != '1': return
        
        token = settings.get('telegram_token')
        chat_id = settings.get('telegram_chat_id')
        if not token or not chat_id: return

        # Format requested: "Phí dịch vụ của gia đình vào tháng xxx là yyy đồng"
        month = metadata.get('month', '...')
        amount = metadata.get('amount')
        
        amount_str = '...'
        if amount:
            amount_str = f"{self._format_money(amount)} đồng"

        msg = f"🔔 <b>Hóa đơn mới!</b>\n"
        msg += f"Phí dịch vụ của gia đình vào tháng {month} là {amount_str}.\n"
        msg += f"(Email: {subject})"

        try:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            requests.post(url, json={"chat_id": chat_id, "text": msg, "parse_mode": "HTML"}, timeout=5)
        except Exception as e:
            logger.error(f"Telegram Error: {e}")

    def daily_announcement(self):
        # 1. Tìm bill chưa announce
        bills = db_manager.get_pending_bills()
        if not bills: return # Không có gì để đọc

        # Đọc từng cái (hoặc cái mới nhất)
        for bill in bills:
            month = bill['metadata'].get('month', 'này')
            amount = bill['metadata'].get('amount')
            
            # Text for Speed: Don't use dots, let TTS handle or use specific format
            # "813440 đồng" reads better than "813.440 đồng" depending on engine
            # Safe bet: "813 nghìn 440 đồng" (Complex) or just raw number without dots
            if amount:
                text = f"Phí dịch vụ của gia đình vào tháng {month} là {amount} đồng. Vui lòng kiểm tra."
            else:
                text = f"Phí dịch vụ của gia đình vào tháng {month} đã có thông báo. Vui lòng kiểm tra."
            
            # Gửi ra loa
            success = speaker_mcp.speak(text)
            
            if success:
                # Mark as done
                db_manager.mark_as_announced(bill['id'])

