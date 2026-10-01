"""
Gate 1 — CWA API
Goal: 從 CWA Open Data API 取得真實 Forecast JSON
Dataset: F-C0032-001 (一般天氣預報-今明36小時天氣預報)
Endpoint: https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-C0032-001
"""

import os
import sys
import json
import requests
from dotenv import load_dotenv

# ── 載入 .env ─────────────────────────────────────────────
load_dotenv()
CWA_API_KEY = os.getenv("CWA_API_KEY")
if not CWA_API_KEY:
    print("[ERROR] CWA_API_KEY 未設定，請確認 .env 檔案")
    sys.exit(1)

# 只顯示 key 前 8 字元，不輸出完整 key
print(f"[INFO] API Key loaded: {CWA_API_KEY[:8]}...")

# ── 常數 ──────────────────────────────────────────────────
BASE_URL = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-C0032-001"

# 台灣所有縣市（F-C0032-001 涵蓋範圍）
ALL_LOCATIONS = [
    "臺北市", "新北市", "桃園市", "臺中市", "臺南市", "高雄市",
    "基隆市", "新竹市", "嘉義市",
    "新竹縣", "苗栗縣", "彰化縣", "南投縣", "雲林縣",
    "嘉義縣", "屏東縣", "宜蘭縣", "花蓮縣", "臺東縣",
    "澎湖縣", "金門縣", "連江縣",
]


def fetch_forecast(location_name: str) -> dict | None:
    """呼叫 CWA API，取得指定縣市的 36h 天氣預報"""
    params = {
        "Authorization": CWA_API_KEY,
        "format": "JSON",
        "locationName": location_name,
    }
    try:
        resp = requests.get(BASE_URL, params=params, timeout=15)
    except requests.exceptions.RequestException as e:
        print(f"[ERROR] HTTP request failed: {e}")
        return None

    # ── Step 4: 驗證 HTTP status ──
    print(f"[INFO] HTTP Status: {resp.status_code}")
    if resp.status_code != 200:
        print(f"[ERROR] Non-200 response: {resp.text[:200]}")
        return None

    # ── Step 5: 依實際 response 解析 JSON ──
    try:
        data = resp.json()
    except json.JSONDecodeError as e:
        print(f"[ERROR] JSON parse error: {e}")
        return None

    if data.get("success") != "true":
        print(f"[ERROR] API returned success=false: {data}")
        return None

    return data


def parse_location(location_data: dict) -> dict:
    """從單一 location 物件提取天氣要素"""
    loc_name = location_data.get("locationName", "Unknown")
    elements = {el["elementName"]: el for el in location_data.get("weatherElement", [])}

    # 取第一個時段（最近預報）
    def get_first(elem_name):
        el = elements.get(elem_name)
        if not el:
            return None
        times = el.get("time", [])
        if not times:
            return None
        return times[0]

    wx_slot   = get_first("Wx")
    mint_slot = get_first("MinT")
    maxt_slot = get_first("MaxT")
    pop_slot  = get_first("PoP")

    result = {
        "location": loc_name,
        "forecast_start": wx_slot["startTime"] if wx_slot else None,
        "forecast_end":   wx_slot["endTime"]   if wx_slot else None,
        "weather":        wx_slot["parameter"]["parameterName"] if wx_slot else None,
        "min_temp":       mint_slot["parameter"]["parameterName"] if mint_slot else None,
        "max_temp":       maxt_slot["parameter"]["parameterName"] if maxt_slot else None,
    }

    # PoP（降雨機率）—— 有才輸出
    if pop_slot:
        result["pop"] = pop_slot["parameter"]["parameterName"]

    return result


def verify_location(location_name: str) -> dict | None:
    """取得並驗證單一縣市資料"""
    print(f"\n{'='*55}")
    print(f"  驗證地區：{location_name}")
    print(f"{'='*55}")

    data = fetch_forecast(location_name)
    if data is None:
        return None

    locations = data.get("records", {}).get("location", [])
    if not locations:
        print(f"[ERROR] No location data in response")
        return None

    parsed = parse_location(locations[0])

    print(f"  Location       : {parsed['location']}")
    print(f"  Forecast Start : {parsed['forecast_start']}")
    print(f"  Forecast End   : {parsed['forecast_end']}")
    print(f"  Weather        : {parsed['weather']}")
    print(f"  Min Temp       : {parsed['min_temp']} °C")
    print(f"  Max Temp       : {parsed['max_temp']} °C")
    if "pop" in parsed:
        print(f"  PoP            : {parsed['pop']} %")

    return parsed


def run_gate1():
    print("\n" + "="*55)
    print("  GATE 1 — CWA API Verification")
    print("="*55)

    # ── Step 6: 先驗證臺中市 ──
    pilot = verify_location("臺中市")
    if pilot is None:
        print("\n[FAIL] 臺中市驗證失敗，請檢查 API Key 或網路")
        sys.exit(1)

    # ── Step 8: 確認其他地區也存在 ──
    print(f"\n{'='*55}")
    print(f"  驗證所有 {len(ALL_LOCATIONS)} 個縣市是否存在於 API...")
    print(f"{'='*55}")

    # 一次拉全台資料（不帶 locationName），再解析
    data_all = fetch_forecast("")  # 不帶地名 → 全台
    if data_all is None:
        print("[FAIL] 無法取得全台資料")
        sys.exit(1)

    all_locs_in_api = {
        loc["locationName"]
        for loc in data_all.get("records", {}).get("location", [])
    }

    missing = [l for l in ALL_LOCATIONS if l not in all_locs_in_api]
    found   = [l for l in ALL_LOCATIONS if l in all_locs_in_api]

    print(f"  API 回傳地區總數 : {len(all_locs_in_api)}")
    print(f"  目標地區找到     : {len(found)}/{len(ALL_LOCATIONS)}")
    if missing:
        print(f"  [WARN] 缺少地區  : {missing}")
    else:
        print("  [OK] All target locations found in API (PASS)")

    # ── 儲存完整 JSON 以供 Gate 2 參考 ──
    with open("cwa_sample.json", "w", encoding="utf-8") as f:
        json.dump(data_all, f, ensure_ascii=False, indent=2)
    print("\n  [INFO] Full data saved to cwa_sample.json")

    if missing:
        print("\n[RESULT] GATE 1 = PARTIAL (some locations missing)")
    else:
        print("\n" + "="*55)
        print("  GATE 1 = PASS")
        print("="*55)


if __name__ == "__main__":
    run_gate1()
