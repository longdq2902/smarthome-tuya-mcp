import json

def list_categories():
    try:
        with open('devices.json', 'r', encoding='utf-8') as f:
            devices = json.load(f)
            
        categories = {}
        for d in devices:
            cat = d.get('category', 'unknown')
            name = d.get('name', 'unknown')
            if cat not in categories:
                categories[cat] = []
            categories[cat].append(name)
            
        print(f"Total devices: {len(devices)}")
        print("Categories found:")
        for cat, names in categories.items():
            print(f"Category: {cat}")
            # print first 3 names to avoid spam
            for n in names[:3]:
                print(f"  - {n}")
            if len(names) > 3:
                print(f"  ... and {len(names)-3} more")
            print("-" * 20)
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == '__main__':
    list_categories()
