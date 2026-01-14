import sqlite3
import os
import db_manager

def verify():
    # 1. FILE EXISTENCE
    files = [
        'email_mcp.py', 
        'speaker_mcp.py', 
        'settings.html', 
        'main.py', 
        'master_mcp.py'
    ]
    print("--- 1. FILE CHECK ---")
    for f in files:
        if os.path.exists(f):
            print(f"[OK] Found {f}")
        else:
            print(f"[FAIL] Missing {f}")

    # 2. DB SCHEMA CHECK
    print("\n--- 2. DB SCHEMA CHECK ---")
    conn = sqlite3.connect('smarthome.db')
    c = conn.cursor()
    c.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [r[0] for r in c.fetchall()]
    conn.close()
    
    expected_tables = ['bql_emails', 'settings', 'devices']
    for t in expected_tables:
        if t in tables:
            print(f"[OK] Table '{t}' exists.")
        else:
            print(f"[FAIL] Table '{t}' MISSING. Did you run init_db()?")
    
    # 3. SETTINGS TEST
    print("\n--- 3. SETTINGS FUNC TEST ---")
    try:
        db_manager.set_setting('test_key', 'test_value')
        val = db_manager.get_setting('test_key')
        if val == 'test_value':
            print(f"[OK] Settings Set/Get working.")
        else:
            print(f"[FAIL] Settings Read Mismatch: {val}")
    except Exception as e:
        print(f"[FAIL] Settings Error: {e}")

if __name__ == '__main__':
    # Ensure updated init_db is run if needed
    try:
        db_manager.init_db()
    except Exception as e:
        print(f"init_db error: {e}")
        
    verify()
