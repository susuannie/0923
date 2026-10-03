"""
Gate 2 — Database
Goal: 將真實 CWA response 做 ETL 並存入 SQLite
- Schema 建立
- 資料驗證
- Duplicate strategy (UPSERT)
- SQL SELECT 驗證
"""

import os
import sqlite3
import json
import requests
from datetime import datetime
from dotenv import load_dotenv

try:
    import psycopg
except ImportError:  # pragma: no cover
    psycopg = None

# ── 載入 .env ─────────────────────────────────────────────
load_dotenv()
CWA_API_KEY = os.getenv("CWA_API_KEY")
if not CWA_API_KEY:
    raise RuntimeError("[ERROR] CWA_API_KEY 未設定")


def resolve_db_path() -> str:
    db_path = os.getenv("DB_PATH")
    if db_path:
        return db_path
    if os.getenv("VERCEL"):
        return "/tmp/weather.db"
    return "weather.db"


DB_PATH = resolve_db_path()
DATABASE_URL = os.getenv("DATABASE_URL")
DB_BACKEND = "postgres" if DATABASE_URL and psycopg is not None else "sqlite"
BASE_URL = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-C0032-001"


def ensure_db_dir(db_path: str) -> str:
    directory = os.path.dirname(db_path)
    if directory and directory not in (".", ""):
        os.makedirs(directory, exist_ok=True)
    return db_path


def get_db_connection():
    if DB_BACKEND == "postgres":
        return psycopg.connect(DATABASE_URL)
    return sqlite3.connect(ensure_db_dir(DB_PATH))

# ═══════════════════════════════════════════════════════════
# 1. SCHEMA
# ═══════════════════════════════════════════════════════════

CREATE_LOCATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS locations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    location_name TEXT    NOT NULL UNIQUE,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);
""" if DB_BACKEND == "sqlite" else """
CREATE TABLE IF NOT EXISTS locations (
    id            SERIAL PRIMARY KEY,
    location_name TEXT    NOT NULL UNIQUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

CREATE_FORECASTS_TABLE = """
CREATE TABLE IF NOT EXISTS forecasts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    location_name  TEXT    NOT NULL,
    forecast_start TEXT    NOT NULL,
    forecast_end   TEXT    NOT NULL,
    weather        TEXT,
    min_temp       INTEGER,
    max_temp       INTEGER,
    pop            INTEGER,
    fetched_at     TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(location_name, forecast_start, forecast_end)
        ON CONFLICT REPLACE
);
""" if DB_BACKEND == "sqlite" else """
CREATE TABLE IF NOT EXISTS forecasts (
    id             SERIAL PRIMARY KEY,
    location_name  TEXT    NOT NULL,
    forecast_start TEXT    NOT NULL,
    forecast_end   TEXT    NOT NULL,
    weather        TEXT,
    min_temp       INTEGER,
    max_temp       INTEGER,
    pop            INTEGER,
    fetched_at     TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(location_name, forecast_start, forecast_end)
);
"""

CREATE_IDX = """
CREATE INDEX IF NOT EXISTS idx_forecasts_location
    ON forecasts (location_name);
"""


def init_db(conn):
    """建立 schema"""
    cur = conn.cursor()
    if DB_BACKEND == "sqlite":
        cur.executescript(
            CREATE_LOCATIONS_TABLE +
            CREATE_FORECASTS_TABLE +
            CREATE_IDX
        )
    else:
        cur.execute(CREATE_LOCATIONS_TABLE)
        cur.execute(CREATE_FORECASTS_TABLE)
        cur.execute(CREATE_IDX)
    conn.commit()
    print("[DB] Schema initialised")


# ═══════════════════════════════════════════════════════════
# 2. EXTRACT  (CWA API → raw JSON)
# ═══════════════════════════════════════════════════════════

def extract() -> list[dict]:
    """呼叫 CWA API，回傳所有 location 的原始資料"""
    print("[EXTRACT] Fetching all-Taiwan forecast from CWA API...")
    resp = requests.get(
        BASE_URL,
        params={"Authorization": CWA_API_KEY, "format": "JSON"},
        timeout=15,
    )
    print(f"[EXTRACT] HTTP {resp.status_code}")
    resp.raise_for_status()

    data = resp.json()
    assert data.get("success") == "true", f"API error: {data}"

    locations = data["records"]["location"]
    print(f"[EXTRACT] Got {len(locations)} locations")
    return locations


# ═══════════════════════════════════════════════════════════
# 3. TRANSFORM  (raw location → list of forecast rows)
# ═══════════════════════════════════════════════════════════

def _safe_int(value: str | None) -> int | None:
    """字串轉整數，失敗回傳 None"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def transform(location: dict) -> tuple[str, list[dict]]:
    """
    從單一 location 物件提取所有時段的天氣資料。
    回傳 (location_name, [forecast_row, ...])
    """
    name = location["locationName"]
    elements = {el["elementName"]: el["time"] for el in location["weatherElement"]}

    # 對齊各要素的時段 (以 Wx 為主軸)
    wx_times   = elements.get("Wx",   [])
    mint_times = elements.get("MinT", [])
    maxt_times = elements.get("MaxT", [])
    pop_times  = elements.get("PoP",  [])

    rows = []
    for i, wx in enumerate(wx_times):
        def get_param(time_list, idx):
            if idx < len(time_list):
                return time_list[idx]["parameter"]["parameterName"]
            return None

        row = {
            "location_name":  name,
            "forecast_start": wx["startTime"],
            "forecast_end":   wx["endTime"],
            "weather":        wx["parameter"]["parameterName"],
            "min_temp":       _safe_int(get_param(mint_times, i)),
            "max_temp":       _safe_int(get_param(maxt_times, i)),
            "pop":            _safe_int(get_param(pop_times,  i)),
        }

        # ── 資料驗證 ──
        assert row["location_name"],  "Missing location_name"
        assert row["forecast_start"], "Missing forecast_start"
        assert row["forecast_end"],   "Missing forecast_end"

        rows.append(row)

    return name, rows


# ═══════════════════════════════════════════════════════════
# 4. LOAD  (rows → SQLite)
# ═══════════════════════════════════════════════════════════

INSERT_LOCATION = """
INSERT OR IGNORE INTO locations (location_name) VALUES (?);
""" if DB_BACKEND == "sqlite" else """
INSERT INTO locations (location_name)
VALUES (%s)
ON CONFLICT (location_name) DO NOTHING;
"""

INSERT_FORECAST = """
INSERT INTO forecasts
    (location_name, forecast_start, forecast_end,
     weather, min_temp, max_temp, pop)
VALUES
    (:location_name, :forecast_start, :forecast_end,
     :weather, :min_temp, :max_temp, :pop)
ON CONFLICT(location_name, forecast_start, forecast_end)
    DO UPDATE SET
        weather    = excluded.weather,
        min_temp   = excluded.min_temp,
        max_temp   = excluded.max_temp,
        pop        = excluded.pop,
        fetched_at = datetime('now','localtime');
""" if DB_BACKEND == "sqlite" else """
INSERT INTO forecasts
    (location_name, forecast_start, forecast_end,
     weather, min_temp, max_temp, pop)
VALUES
    (%s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (location_name, forecast_start, forecast_end)
    DO UPDATE SET
        weather    = EXCLUDED.weather,
        min_temp   = EXCLUDED.min_temp,
        max_temp   = EXCLUDED.max_temp,
        pop        = EXCLUDED.pop,
        fetched_at = CURRENT_TIMESTAMP;
"""


def load(conn, location_name: str, rows: list[dict]):
    cur = conn.cursor()
    if DB_BACKEND == "sqlite":
        cur.execute(INSERT_LOCATION, (location_name,))
        cur.executemany(INSERT_FORECAST, rows)
    else:
        cur.execute(INSERT_LOCATION, (location_name,))
        for row in rows:
            cur.execute(
                INSERT_FORECAST,
                (
                    row["location_name"],
                    row["forecast_start"],
                    row["forecast_end"],
                    row["weather"],
                    row["min_temp"],
                    row["max_temp"],
                    row["pop"],
                ),
            )
    conn.commit()


# ═══════════════════════════════════════════════════════════
# 5. VERIFY  (SQL SELECT)
# ═══════════════════════════════════════════════════════════

def verify(conn: sqlite3.Connection):
    cur = conn.cursor()
    sep = "=" * 55

    # 5A — 指定地區：臺中市
    print(f"\n{sep}")
    print("  VERIFY A: 臺中市 最新預報")
    print(sep)
    cur.execute("""
        SELECT location_name, forecast_start, forecast_end,
               weather, min_temp, max_temp, pop
        FROM forecasts
        WHERE location_name = '臺中市'
        ORDER BY forecast_start;
    """)
    rows = cur.fetchall()
    assert rows, "[FAIL] 臺中市查無資料"
    for r in rows:
        print(f"  {r[0]:6} | {r[1]} ~ {r[2]} | {r[3]:10} | "
              f"MinT={r[4]}°C MaxT={r[5]}°C PoP={r[6]}%")

    # 5B — 多地區：全台統計
    print(f"\n{sep}")
    print("  VERIFY B: 全台縣市筆數統計")
    print(sep)
    cur.execute("""
        SELECT location_name, COUNT(*) as cnt
        FROM forecasts
        GROUP BY location_name
        ORDER BY location_name;
    """)
    rows = cur.fetchall()
    for r in rows:
        print(f"  {r[0]:6}  {r[1]} 筆")
    print(f"\n  [OK] 共 {len(rows)} 個縣市，每縣市 {rows[0][1]} 個時段")

    # 5C — locations table
    print(f"\n{sep}")
    print("  VERIFY C: locations table")
    print(sep)
    cur.execute("SELECT COUNT(*) FROM locations;")
    loc_count = cur.fetchone()[0]
    print(f"  locations 共 {loc_count} 筆")
    assert loc_count == 22, f"[FAIL] 應有 22 縣市，實得 {loc_count}"
    print("  [OK] 22 縣市全部存在")

    # 5D — 總筆數
    cur.execute("SELECT COUNT(*) FROM forecasts;")
    total = cur.fetchone()[0]
    print(f"\n  Total forecast rows: {total}")


# ═══════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════

def run_gate2():
    print("\n" + "=" * 55)
    print("  GATE 2 — Database ETL")
    print("=" * 55)

    conn = get_db_connection()

    # Step 1 — Schema
    init_db(conn)

    # Step 2 — Extract
    raw_locations = extract()

    # Step 3 & 4 — Transform + Load (all locations)
    print(f"\n[ETL] Processing {len(raw_locations)} locations...")
    total_rows = 0
    for loc in raw_locations:
        name, rows = transform(loc)
        load(conn, name, rows)
        total_rows += len(rows)
        print(f"  {name}: {len(rows)} rows loaded")

    print(f"\n[ETL] Done. Total rows inserted/upserted: {total_rows}")

    # Step 5 — Verify
    verify(conn)

    conn.close()
    print("\n" + "=" * 55)
    print("  GATE 2 = PASS")
    print("=" * 55)


if __name__ == "__main__":
    run_gate2()
