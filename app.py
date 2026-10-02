"""
app.py — Flask backend for Taiwan Weather GIS
Gate 3: Local Taiwan GIS
"""

import os
import sqlite3
from flask import Flask, jsonify, render_template, send_from_directory
from dotenv import load_dotenv
import requests

load_dotenv()

app = Flask(__name__)
DB_PATH = "weather.db"
CWA_API_KEY = os.getenv("CWA_API_KEY")
STATION_API_URL = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/O-A0001-001"


def query_db(sql: str, params=()) -> list[dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(sql, params)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


# ── Routes ────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/weather")
def api_weather():
    """回傳全台所有縣市最近一筆 + 完整時段的天氣資料"""
    rows = query_db("""
        SELECT location_name,
               forecast_start, forecast_end,
               weather, min_temp, max_temp, pop,
               fetched_at
        FROM forecasts
        ORDER BY location_name, forecast_start
    """)

    # 以 location_name 分組
    result = {}
    for r in rows:
        name = r["location_name"]
        if name not in result:
            result[name] = {
                "location_name": name,
                "fetched_at": r["fetched_at"],
                "forecasts": []
            }
        result[name]["forecasts"].append({
            "start":   r["forecast_start"],
            "end":     r["forecast_end"],
            "weather": r["weather"],
            "min_temp": r["min_temp"],
            "max_temp": r["max_temp"],
            "pop":     r["pop"],
        })

    return jsonify(list(result.values()))


@app.route("/api/weather/refresh", methods=["POST"])
def api_refresh():
    """觸發 ETL，從 CWA API 重新抓取並存入 DB"""
    try:
        from database import run_gate2
        run_gate2()
        return jsonify({"status": "ok", "message": "Weather data refreshed"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


def _safe_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _extract_wgs84_coordinates(station):
    geo = station.get("GeoInfo", {})
    for coord in geo.get("Coordinates", []):
        if coord.get("CoordinateName") == "WGS84":
            lat = _safe_float(coord.get("StationLatitude"))
            lon = _safe_float(coord.get("StationLongitude"))
            if lat is not None and lon is not None:
                return lat, lon
    return None, None


def normalize_station(station):
    geo = station.get("GeoInfo", {})
    lat, lon = _extract_wgs84_coordinates(station)
    weather_elem = station.get("WeatherElement", {})
    now = weather_elem.get("Now", {})

    return {
        "station_name": station.get("StationName"),
        "station_id": station.get("StationId"),
        "county": geo.get("CountyName"),
        "town": geo.get("TownName"),
        "lat": lat,
        "lon": lon,
        "weather": weather_elem.get("Weather"),
        "temperature": _safe_float(weather_elem.get("AirTemperature")),
        "humidity": _safe_int(weather_elem.get("RelativeHumidity")),
        "wind_speed": _safe_float(weather_elem.get("WindSpeed")),
        "wind_direction": _safe_float(weather_elem.get("WindDirection")),
        "rain_1h": _safe_float(now.get("Precipitation")),
        "obs_time": station.get("ObsTime", {}).get("DateTime"),
    }


@app.route("/api/stations")
def api_stations():
    """回傳所有自動氣象站即時觀測資料，作為地圖疊層，不影響既有縣市預報功能。"""
    if not CWA_API_KEY:
        return jsonify({"status": "error", "message": "CWA_API_KEY not configured"}), 500

    try:
        resp = requests.get(
            STATION_API_URL,
            params={"Authorization": CWA_API_KEY, "format": "JSON"},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        stations = data.get("records", {}).get("Station", [])

        normalized = []
        for station in stations:
            item = normalize_station(station)
            if item["lat"] is not None and item["lon"] is not None:
                normalized.append(item)
        return jsonify(normalized)
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/static/<path:filename>")
def static_files(filename):
    return send_from_directory("static", filename)


if __name__ == "__main__":
    print("[APP] Starting Taiwan Weather GIS on http://localhost:5000")
    app.run(debug=True, port=5000)
