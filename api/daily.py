"""GET /api/daily?days=30 - Daglig energi och kostnad per enhet från Supabase."""
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timedelta, timezone
import calendar
import json
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))
from _db import get_public_db

PAGE_SIZE = 1000


def _last_sunday(year, month):
    last_day = calendar.monthrange(year, month)[1]
    days_back = (datetime(year, month, last_day).weekday() + 1) % 7
    return last_day - days_back


def _se_offset(dt_utc):
    y = dt_utc.year
    start = datetime(y, 3, _last_sunday(y, 3), 1, 0, tzinfo=timezone.utc)
    end   = datetime(y, 10, _last_sunday(y, 10), 1, 0, tzinfo=timezone.utc)
    return 2 if start <= dt_utc < end else 1


def _price_hour_key(ts_str):
    """Convert UTC price timestamp to Swedish local time hour key (matches energy fake-UTC)."""
    dt = datetime.fromisoformat(ts_str[:19]).replace(tzinfo=timezone.utc)
    loc = dt + timedelta(hours=_se_offset(dt))
    return loc.strftime("%Y-%m-%dT%H")


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            params = parse_qs(urlparse(self.path).query)

            # Stöd både ?days=N (rullande) och ?from_date=YYYY-MM-DD&to_date=YYYY-MM-DD (kalender)
            from_date = params.get("from_date", [None])[0]
            to_date   = params.get("to_date",   [None])[0]

            if from_date and to_date:
                # Energidata lagras i fake-UTC (lokal tid som UTC) → filtrera direkt
                from_ts  = from_date + "T00:00:00"
                to_ts    = to_date   + "T23:59:59"
                # Spotpriser i riktig UTC → utöka med 2h åt varje håll för CET/CEST
                price_from_ts = (datetime.fromisoformat(from_ts) - timedelta(hours=2)).isoformat()
                price_to_ts   = (datetime.fromisoformat(to_ts)   + timedelta(hours=2)).isoformat()
            else:
                days = int(params.get("days", ["30"])[0])
                if days < 1 or days > 365:
                    days = 30
                from_ts       = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
                to_ts         = None
                price_from_ts = from_ts
                price_to_ts   = None

            db = get_public_db()

            # Hämta energidata med paginering
            # Använd current_value (Watt) × 0.25h / 1000 = kWh, precis som lokala appen
            energy_rows = []
            offset = 0
            while True:
                q = (db.table("energy_readings")
                     .select("device_name, timestamp, current_value")
                     .gte("timestamp", from_ts))
                if to_ts:
                    q = q.lte("timestamp", to_ts)
                result = q.order("timestamp", desc=False).range(offset, offset + PAGE_SIZE - 1).execute()
                energy_rows.extend(result.data)
                if len(result.data) < PAGE_SIZE:
                    break
                offset += PAGE_SIZE

            # Hämta spotpriser med paginering (15-min intervall = 96/dag, överskrider 1000-gränsen vid 30+ dagar)
            price_rows = []
            offset = 0
            while True:
                q = (db.table("spot_prices")
                     .select("timestamp, price_sek")
                     .gte("timestamp", price_from_ts))
                if price_to_ts:
                    q = q.lte("timestamp", price_to_ts)
                result = q.order("timestamp", desc=False).range(offset, offset + PAGE_SIZE - 1).execute()
                price_rows.extend(result.data)
                if len(result.data) < PAGE_SIZE:
                    break
                offset += PAGE_SIZE

            # Bygg timme->pris lookup (medelvärde per timme, pris är i öre/kWh)
            price_sum_by_hour = {}
            price_count_by_hour = {}
            for p in price_rows:
                hour_key = _price_hour_key(p["timestamp"])  # UTC → Swedish local time
                ore = p["price_sek"]  # redan i öre/kWh i databasen
                price_sum_by_hour[hour_key] = price_sum_by_hour.get(hour_key, 0) + ore
                price_count_by_hour[hour_key] = price_count_by_hour.get(hour_key, 0) + 1
            price_by_hour = {
                h: price_sum_by_hour[h] / price_count_by_hour[h]
                for h in price_sum_by_hour
            }

            # Aggregera per dag och enhet
            # current_value = Watt, × 0.25h / 1000 = kWh per 15-min mätning
            daily = {}  # {dag: {enhet: {kwh, cost, readings}}}
            for r in energy_rows:
                ts = r["timestamp"]
                day = ts[:10]
                hour_key = ts[:13]
                device = r["device_name"]
                watts = r["current_value"] or 0
                kwh = watts * 0.25 / 1000  # Watt → kWh per 15 min

                # Pris i öre/kWh -> kostnad i kronor
                price_ore = price_by_hour.get(hour_key, 0)
                cost = kwh * price_ore / 100

                if day not in daily:
                    daily[day] = {}
                if device not in daily[day]:
                    daily[day][device] = {"kwh": 0, "cost": 0, "readings": 0, "active_intervals": 0}

                daily[day][device]["kwh"] += kwh
                daily[day][device]["cost"] += cost
                daily[day][device]["readings"] += 1
                if watts > 0:
                    daily[day][device]["active_intervals"] += 1

            # Formatera svar
            result_list = []
            for day in sorted(daily.keys()):
                devices = daily[day]
                total_kwh = sum(d["kwh"] for d in devices.values())
                total_cost = sum(d["cost"] for d in devices.values())
                row = {
                    "day": day,
                    "total_kwh": round(total_kwh, 3),
                    "total_cost": round(total_cost, 2),
                    "devices": {
                        name: {
                            "kwh": round(v["kwh"], 3),
                            "cost": round(v["cost"], 2),
                            "active_hours": round(v["active_intervals"] / 4, 2),
                        }
                        for name, v in devices.items()
                    }
                }
                result_list.append(row)

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
