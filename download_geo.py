"""下載 Taiwan GeoJSON 並存到 static/ 資料夾"""
import os, requests, json

os.makedirs("static", exist_ok=True)

# twcounty2010.2.json = 精簡版縣市邊界 (ronnywang/twgeojson)
GEO_URL = (
    "https://raw.githubusercontent.com/ronnywang/twgeojson/"
    "master/twcounty2010.2.json"
)

print("[GEO] Downloading Taiwan county GeoJSON...")
r = requests.get(GEO_URL, timeout=30)
r.raise_for_status()

geo = r.json()
print(f"[GEO] Features: {len(geo['features'])}")
# 印出前幾個縣市的 property keys & name
for f in geo["features"][:3]:
    print(f"  props: {list(f['properties'].keys())}  name: {list(f['properties'].values())[:3]}")

with open("static/taiwan.geojson", "w", encoding="utf-8") as f:
    json.dump(geo, f, ensure_ascii=False)

print(f"[GEO] Saved to static/taiwan.geojson ({len(r.text)//1024} KB)")
