"""GET /api/monthly - Energi och kostnad aggregerat per kalendermånad från jan 2025.

Notering om tidszoner:
  - Energimätningar migrerades från SQLite (lokal svensk tid, ingen tz) med Z-suffix →
    lagrade i Supabase som "UTC" men face-value är egentligen lokal tid.
  - Spotpriser har korrekt UTC i Supabase (konverterade från +01:00/+02:00 vid migrering).
  Lösning: konvertera spotprisernas UTC → lokal svensk tid (CET/CEST) och matcha mot
  energimätningarnas face-value (= lokal tid) på 15-minutersnivå.
"""
from http.server import BaseHTTPRequestHandler
from datetime import datetime, timezone, timedelta
import calendar
import json
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))
from _db import get_public_db

PAGE_SIZE = 1000
START_DATE = "2025-01-01T00:00:00+00:00"
# Spotpriser hämtas från lite tidigare för att täcka UTC-offset (max +2h)
PRICE_START_DATE = "2024-12-31T22:00:00+00:00"


# ── Hjälpfunktioner för svensk tid (CET/CEST) ──────────────────────────────

def _last_sunday(year, month):
    """Dag-nummer för sista söndagen i månaden."""
    last_day = calendar.monthrange(year, month)[1]
    # weekday(): Mon=0, Sun=6
    days_back = (datetime(year, month, last_day).weekday() + 1) % 7
    return last_day - days_back


def _swedish_offset(dt_utc):
    """Returnerar 2 (CEST, sommartid) eller 1 (CET, normaltid).
    CEST: sista söndagen i mars kl 01:00 UTC → sista söndagen i oktober kl 01:00 UTC."""
    year = dt_utc.year
    cest_start = datetime(year, 3, _last_sunday(year, 3), 1, 0, tzinfo=timezone.utc)
    cest_end   = datetime(year, 10, _last_sunday(year, 10), 1, 0, tzinfo=timezone.utc)
    return 2 if cest_start <= dt_utc < cest_end else 1


def price_to_local_15min_key(ts_utc_str):
    """Konverterar spotprisens UTC-timestamp (Supabase) → lokal tid 15-min nyckel.
    Ex: '2025-01-01T00:00:00+00:00' (= 01:00 CET) → '2025-01-01T01:00'"""
    ts = ts_utc_str[:19].replace(' ', 'T')
    dt_utc = datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)
    dt_local = dt_utc + timedelta(hours=_swedish_offset(dt_utc))
    m_floor = (dt_local.minute // 15) * 15
    return dt_local.strftime(f"%Y-%m-%dT%H:{m_floor:02d}")


def energy_to_15min_key(ts_utc_str):
    """Extraherar 15-min nyckel ur energimätningens timestamp (face-value = lokal tid).
    Ex: '2025-01-15T14:21:47+00:00' → '2025-01-15T14:15'"""
    ts = ts_utc_str[:19]  # ignorera timezone-suffix, face-value är lokal tid
    minute = int(ts[14:16])
    m_floor = (minute // 15) * 15
    return f"{ts[:13]}:{m_floor:02d}"


def month_key_from_local_ts(ts_utc_str):
    """Hämtar YYYY-MM ur energimätningens lokala tidsvärde."""
    return ts_utc_str[:7]


# ── Månadshjälp ─────────────────────────────────────────────────────────────

def all_months_since_start():
    """Returnerar lista med alla YYYY-MM från jan 2025 t.o.m. aktuell månad."""
    months = []
    now = datetime.now(timezone.utc)
    cur = datetime(2025, 1, 1, tzinfo=timezone.utc)
    while cur.year < now.year or (cur.year == now.year and cur.month <= now.month):
        months.append(cur.strftime("%Y-%m"))
        cur = cur.replace(month=cur.month + 1) if cur.month < 12 \
              else cur.replace(year=cur.year + 1, month=1)
    return months


# ── Handler ─────────────────────────────────────────────────────────────────

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            db = get_public_db()

            # Hämta energidata med paginering
            energy_rows = []
            offset = 0
            while True:
                res = (
                    db.table("energy_readings")
                    .select("device_name, timestamp, current_value")
                    .gte("timestamp", START_DATE)
                    .order("timestamp", desc=False)
                    .range(offset, offset + PAGE_SIZE - 1)
                    .execute()
                )
                energy_rows.extend(res.data)
                if len(res.data) < PAGE_SIZE:
                    break
                offset += PAGE_SIZE

            # Hämta spotpriser med paginering (från lite tidigare för tz-täckning)
            price_rows = []
            offset = 0
            while True:
                res = (
                    db.table("spot_prices")
                    .select("timestamp, price_sek")
                    .gte("timestamp", PRICE_START_DATE)
                    .order("timestamp", desc=False)
                    .range(offset, offset + PAGE_SIZE - 1)
                    .execute()
                )
                price_rows.extend(res.data)
                if len(res.data) < PAGE_SIZE:
                    break
                offset += PAGE_SIZE

            # Bygg 15-min → öre/kWh lookup i lokal svensk tid.
            # Spotpriser jan-sep 2025 är timvisa (744/mån) → pre-fyll de 3 övriga
            # kvarterna per timme. Om 15-min priser finns (okt 2025+) skrivs de över.
            price_by_15min = {}
            for p in price_rows:
                ore = p["price_sek"]
                key = price_to_local_15min_key(p["timestamp"])
                price_by_15min[key] = ore
                # Pre-fyll :15/:30/:45 för timvisa priser (minut-del = "00")
                if key[14:16] == "00":
                    h = key[:13]
                    for m in (15, 30, 45):
                        qk = f"{h}:{m:02d}"
                        if qk not in price_by_15min:
                            price_by_15min[qk] = ore

            # Aggregera per månad och enhet
            monthly = {}           # {YYYY-MM: {device: {kwh, cost, kwh_priced, weighted_price}}}
            readings_by_month = {} # {YYYY-MM: int}

            for r in energy_rows:
                ts      = r["timestamp"]
                mon     = month_key_from_local_ts(ts)
                e_key   = energy_to_15min_key(ts)
                device  = r["device_name"]
                watts   = r["current_value"] or 0
                kwh     = watts * 0.25 / 1000   # W × 0.25 h / 1000 = kWh

                price_ore = price_by_15min.get(e_key)
                cost = kwh * price_ore / 100 if price_ore is not None else None

                if mon not in monthly:
                    monthly[mon] = {}
                    readings_by_month[mon] = 0
                if device not in monthly[mon]:
                    monthly[mon][device] = {
                        "kwh": 0.0, "cost": 0.0,
                        "kwh_priced": 0.0, "weighted_price": 0.0
                    }

                d = monthly[mon][device]
                d["kwh"] += kwh
                if cost is not None:
                    d["cost"]           += cost
                    d["kwh_priced"]     += kwh
                    d["weighted_price"] += kwh * price_ore
                readings_by_month[mon] += 1

            # Bygg svar – alla månader sedan jan 2025, inkl. månader utan data
            now_month = datetime.now(timezone.utc).strftime("%Y-%m")
            result_list = []

            for month in all_months_since_start():
                if month not in monthly:
                    result_list.append({
                        "month": month, "is_current": month == now_month,
                        "no_data": True, "total_kwh": None,
                        "total_cost": None, "avg_price_ore": None,
                        "readings": 0, "devices": {}
                    })
                    continue

                devs = monthly[month]
                total_kwh           = sum(d["kwh"] for d in devs.values())
                total_cost          = sum(d["cost"] for d in devs.values())
                total_kwh_priced    = sum(d["kwh_priced"] for d in devs.values())
                total_weighted_price = sum(d["weighted_price"] for d in devs.values())

                avg_price = (total_weighted_price / total_kwh_priced
                             if total_kwh_priced > 0 else None)

                # Completeness-check: flagga månader med <50% förväntade mätningar
                yr, mo = int(month[:4]), int(month[5:7])
                days = calendar.monthrange(yr, mo)[1]
                expected = 4 * 24 * days * len(devs)
                partial = readings_by_month[month] / max(expected, 1) < 0.5

                result_list.append({
                    "month": month,
                    "is_current": month == now_month,
                    "no_data": False,
                    "partial": partial,
                    "total_kwh":      round(total_kwh, 1),
                    "total_cost":     round(total_cost, 0),
                    "avg_price_ore":  round(avg_price, 1) if avg_price is not None else None,
                    "readings":       readings_by_month[month],
                    "devices": {
                        name: {
                            "kwh":  round(d["kwh"], 1),
                            "cost": round(d["cost"], 0),
                        }
                        for name, d in devs.items()
                    }
                })

            result_list.reverse()  # Nyast först

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(result_list).encode())

        except Exception as e:
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    def log_message(self, format, *args):
        pass
