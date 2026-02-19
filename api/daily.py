"""GET /api/daily?days=30 - Daglig energi och kostnad per enhet från Supabase."""
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timedelta, timezone
import json
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))
from _db import get_public_db

PAGE_SIZE = 1000


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            params = parse_qs(urlparse(self.path).query)
            days = int(params.get("days", ["30"])[0])

            if days < 1 or days > 365:
                days = 30

            from_ts = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            db = get_public_db()

            # Hämta energidata med paginering
            energy_rows = []
            offset = 0
            while True:
                result = (
                    db.table("energy_readings")
                    .select("device_name, timestamp, delta_power")
                    .gte("timestamp", from_ts)
                    .order("timestamp", desc=False)
                    .range(offset, offset + PAGE_SIZE - 1)
                    .execute()
                )
                energy_rows.extend(result.data)
                if len(result.data) < PAGE_SIZE:
                    break
                offset += PAGE_SIZE

            # Hämta spotpriser
            price_rows = (
                db.table("spot_prices")
                .select("timestamp, price_sek")
                .gte("timestamp", from_ts)
                .order("timestamp", desc=False)
                .execute()
            ).data

            # Bygg timme->pris lookup
            price_by_hour = {}
            for p in price_rows:
                hour_key = p["timestamp"][:13]  # "2026-02-19T14"
                price_by_hour[hour_key] = p["price_sek"]

            # Aggregera per dag och enhet
            daily = {}  # {dag: {enhet: {kwh, cost, readings}}}
            for r in energy_rows:
                ts = r["timestamp"]
                day = ts[:10]
                hour_key = ts[:13]
                device = r["device_name"]
                kwh = r["delta_power"] or 0
                price = price_by_hour.get(hour_key, 0)
                cost = kwh * price

                if day not in daily:
                    daily[day] = {}
                if device not in daily[day]:
                    daily[day][device] = {"kwh": 0, "cost": 0, "readings": 0}

                daily[day][device]["kwh"] += kwh
                daily[day][device]["cost"] += cost
                daily[day][device]["readings"] += 1

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
