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

            # Bygg timme->pris lookup (medelvärde per timme, pris är i öre/kWh)
            price_sum_by_hour = {}
            price_count_by_hour = {}
            for p in price_rows:
                hour_key = p["timestamp"][:13]  # "2026-02-19T14"
                ore = p["price_sek"]  # redan i öre/kWh i databasen
                price_sum_by_hour[hour_key] = price_sum_by_hour.get(hour_key, 0) + ore
                price_count_by_hour[hour_key] = price_count_by_hour.get(hour_key, 0) + 1
            price_by_hour = {
                h: price_sum_by_hour[h] / price_count_by_hour[h]
                for h in price_sum_by_hour
            }

            # Aggregera per dag och enhet
            # Max rimligt delta per 15 min: 3000W * 0.25h / 1000 = 0.75 kWh
            # Vi sätter gränsen till 2 kWh för säkerhetsmarginal
            MAX_KWH_PER_READING = 2.0

            daily = {}  # {dag: {enhet: {kwh, cost, readings}}}
            for r in energy_rows:
                ts = r["timestamp"]
                day = ts[:10]
                hour_key = ts[:13]
                device = r["device_name"]
                kwh = r["delta_power"] or 0

                # Filtrera bort ackumulerade totaler (orimligt stora värden)
                if kwh > MAX_KWH_PER_READING:
                    continue

                # Pris i öre/kWh -> kostnad i kronor
                price_ore = price_by_hour.get(hour_key, 0)
                cost = kwh * price_ore / 100

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
