import json

def get_details():
    try:
        with open('devices.json', 'r', encoding='utf-8') as f:
            devices = json.load(f)
            
        for d in devices:
            if d.get('category') == 'mcs' or 'door' in d.get('name', '').lower() or 'cửa' in d.get('name', '').lower():
                print(json.dumps(d, indent=4, ensure_ascii=False))
                
    except Exception as e:
        print(f"Error: {e}")

if __name__ == '__main__':
    get_details()
