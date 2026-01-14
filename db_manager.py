import sqlite3
import json
import threading

DB_FILE = 'smarthome.db'
db_lock = threading.Lock()

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # Table 'devices' will store both static config and dynamic state
    # We use a JSON column for 'mapping', 'dps', etc. for flexibility
    c.execute('''CREATE TABLE IF NOT EXISTS devices (
        id TEXT PRIMARY KEY,
        name TEXT,
        ip TEXT,
        key TEXT,
        version REAL,
        category TEXT,
        product_name TEXT,
        product_id TEXT,
        biz_type INTEGER,
        model TEXT,
        sub BOOLEAN,
        icon TEXT,
        node_id TEXT,
        parent TEXT,
        mapping TEXT, 
        dps TEXT,
        online BOOLEAN,
        last_update REAL,
        missing_ip BOOLEAN
    )''')

    # Table 'bql_emails' for storing notifications and bills
    c.execute('''CREATE TABLE IF NOT EXISTS bql_emails (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        received_at TEXT,
        subject TEXT,
        sender TEXT,
        content_type TEXT, -- 'BILL' or 'NOTICE'
        summary TEXT,      -- Text content or summary
        metadata TEXT,     -- JSON for extra details (e.g. amount, month)
        is_announced BOOLEAN DEFAULT 0
    )''')

    # Table 'settings' for key-value configuration
    c.execute('''CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )''')
    conn.commit()
    conn.close()

def upsert_device(dev_data):
    """Insert or Update device. fields not present in dev_data will be kept as is if updating."""
    with db_lock:
        conn = sqlite3.connect(DB_FILE)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        
        dev_id = dev_data.get('id')
        if not dev_id: return
        
        # Check existence
        c.execute("SELECT * FROM devices WHERE id = ?", (dev_id,))
        existing = c.fetchone()
        
        if existing:
            # Update mode: merge data
            current_data = dict(existing)
            # Deserialize JSON fields for merging if needed (though we might just overwrite complex fields)
            # For simplicity, we'll overwrite provided fields and keep others
            
            # Helper to safely serialize
            def safe_json(val):
                return json.dumps(val, ensure_ascii=False) if isinstance(val, (dict, list)) else val

            update_fields = []
            values = []
            
            for k, v in dev_data.items():
                if k == 'id': continue
                if k in ['mapping', 'dps']:
                     update_fields.append(f"{k} = ?")
                     values.append(safe_json(v))
                else:
                    update_fields.append(f"{k} = ?")
                    values.append(v)
            
            if update_fields:
                query = f"UPDATE devices SET {', '.join(update_fields)} WHERE id = ?"
                values.append(dev_id)
                c.execute(query, values)
        else:
            # Insert mode
            # Defaults
            row = {
                'id': dev_id,
                'name': dev_data.get('name', ''),
                'ip': dev_data.get('ip', ''),
                'key': dev_data.get('key', ''),
                'version': dev_data.get('version', 3.3),
                'category': dev_data.get('category', ''),
                'product_name': dev_data.get('product_name', ''),
                'product_id': dev_data.get('product_id', ''),
                'biz_type': dev_data.get('biz_type', 0),
                'model': dev_data.get('model', ''),
                'sub': dev_data.get('sub', False),
                'icon': dev_data.get('icon', ''),
                'node_id': dev_data.get('node_id', ''),
                'parent': dev_data.get('parent', ''),
                'mapping': json.dumps(dev_data.get('mapping', {}), ensure_ascii=False),
                'dps': json.dumps(dev_data.get('dps', {}), ensure_ascii=False),
                'online': dev_data.get('online', False),
                'last_update': dev_data.get('last_update', 0),
                'missing_ip': dev_data.get('missing_ip', True)
            }
            cols = ', '.join(row.keys())
            qmarks = ', '.join(['?'] * len(row))
            c.execute(f"INSERT INTO devices ({cols}) VALUES ({qmarks})", list(row.values()))
            
        conn.commit()
        conn.close()

def get_all_devices():
    """Return list of dicts with properly parsed JSON fields"""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM devices")
    rows = c.fetchall()
    conn.close()
    
    devices = []
    for r in rows:
        d = dict(r)
        # Parse JSON columns
        try: d['mapping'] = json.loads(d['mapping']) if d['mapping'] else {}
        except: d['mapping'] = {}
        
        try: d['dps'] = json.loads(d['dps']) if d['dps'] else {}
        except: d['dps'] = {}
        
        devices.append(d)
    return devices

def update_device_state(dev_id, dps_dict, is_online=True):
    with db_lock:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        # First get current dps to merge
        c.execute("SELECT dps FROM devices WHERE id = ?", (dev_id,))
        row = c.fetchone()
        if row:
            current_dps_str = row[0]
            try: current_dps = json.loads(current_dps_str) if current_dps_str else {}
            except: current_dps = {}
            
            current_dps.update(dps_dict)
            new_dps_str = json.dumps(current_dps, ensure_ascii=False)
            
            import time
            c.execute("UPDATE devices SET dps = ?, online = ?, last_update = ? WHERE id = ?", 
                      (new_dps_str, is_online, time.time(), dev_id))
            conn.commit()
        conn.close()

# --- SETTINGS HELPERS ---
def get_setting(key, default=None):
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = c.fetchone()
        conn.close()
        return row[0] if row else default
    except: return default

def set_setting(key, value):
    with db_lock:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
        conn.commit()
        conn.close()

def get_all_settings():
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT key, value FROM settings")
        rows = c.fetchall()
        conn.close()
        return {r[0]: r[1] for r in rows}
    except: return {}

# --- EMAIL/BILL HELPERS ---
def add_email(data):
    """
    data dict: received_at, subject, sender, content_type, summary, metadata (dict)
    """
    with db_lock:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        
        meta_str = json.dumps(data.get('metadata', {}), ensure_ascii=False)
        
        # Check duplicate (simple check based on subject and received time roughly, or just insert)
        # Here we just insert. The logic to avoid duplicate bills should be in email_mcp.py logic
        
        c.execute('''INSERT INTO bql_emails 
                     (received_at, subject, sender, content_type, summary, metadata, is_announced)
                     VALUES (?, ?, ?, ?, ?, ?, ?)''', 
                  (data.get('received_at'), data.get('subject'), data.get('sender'),
                   data.get('content_type'), data.get('summary'), meta_str, 0))
        conn.commit()
        conn.close()

def get_emails(limit=10, content_type=None):
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    query = "SELECT * FROM bql_emails"
    params = []
    
    if content_type:
        query += " WHERE content_type = ?"
        params.append(content_type)
        
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    
    c.execute(query, params)
    rows = c.fetchall()
    conn.close()
    
    results = []
    for r in rows:
        d = dict(r)
        try: d['metadata'] = json.loads(d['metadata']) if d['metadata'] else {}
        except: d['metadata'] = {}
        results.append(d)
    return results

def get_pending_bills():
    """Get BILLs that haven't been announced yet."""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    # Get all unannounced bills
    c.execute("SELECT * FROM bql_emails WHERE content_type = 'BILL' AND is_announced = 0")
    rows = c.fetchall()
    conn.close()
    
    results = []
    for r in rows:
        d = dict(r)
        try: d['metadata'] = json.loads(d['metadata']) if d['metadata'] else {}
        except: d['metadata'] = {}
        results.append(d)
    return results

def mark_as_announced(email_id):
    with db_lock:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("UPDATE bql_emails SET is_announced = 1 WHERE id = ?", (email_id,))
        conn.commit()
        conn.close()

def search_emails(keyword):
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    # Search in subject or summary
    pattern = f"%{keyword}%"
    c.execute("SELECT * FROM bql_emails WHERE subject LIKE ? OR summary LIKE ? ORDER BY id DESC LIMIT 5", (pattern, pattern))
    rows = c.fetchall()
    conn.close()
    
    results = []
    for r in rows:
        d = dict(r)
        try: d['metadata'] = json.loads(d['metadata']) if d['metadata'] else {}
        except: d['metadata'] = {}
        results.append(d)
    return results

def check_email_exists(sender, subject, received_at):
    """
    Check if email already exists in DB to avoid deduplication.
    Return True if exists, False otherwise.
    """
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        # Use a flexible check (exact subject + exact received time)
        # Note: received_at string format must match exactly
        c.execute("SELECT id FROM bql_emails WHERE sender = ? AND subject = ? AND received_at = ?", 
                  (sender, subject, received_at))
        row = c.fetchone()
        conn.close()
        return row is not None
    except:
        return False
