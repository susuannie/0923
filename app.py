"""
app.py — Flask backend for Taiwan Weather GIS
Gate 3: Local Taiwan GIS
"""

import os
import sqlite3
from flask import Flask, jsonify, render_template, send_from_directory
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
DB_PATH = "weather.db"


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


@app.route("/static/<path:filename>")
def static_files(filename):
    return send_from_directory("static", filename)


if __name__ == "__main__":
    print("[APP] Starting Taiwan Weather GIS on http://localhost:5000")
    app.run(debug=True, port=5000)
