import sqlite3
import json

DB_FILE = 'smarthome.db'

def find_door_sensors():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM devices")
    rows = c.fetchall()
    conn.close()
    
    found = []
    for r in rows:
        d = dict(r)
        name = d.get('name', '').lower()
        product_name = d.get('product_name', '').lower()
        category = d.get('category', '').lower()
        
        if 'door' in name or 'door' in product_name or \
           'cửa' in name or 'cửa' in product_name or \
           category == 'mcs' or category == 'ds':
            found.append(d)
            
    if found:
        print(f"Found {len(found)} potential door sensors:")
        for dev in found:
            print(f"- Name: {dev['name']}")
            print(f"  ID: {dev['id']}")
            print(f"  Category: {dev['category']}")
            print(f"  Product Name: {dev['product_name']}")
            print("-" * 20)
    else:
        print("No door sensors found.")

if __name__ == '__main__':
    find_door_sensors()
