"""GET /api/monthly - Energi och kostnad aggregerat per kalendermånad från jan 2025."""
from http.server import BaseHTTPRequestHandler
from datetime import datetime, timezone
import json
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))
from _db import get_public_db

PAGE_SIZE = 1000
START_DATE = "2025-01-01T00:00:00+00:00"


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            db = get_public_db()

            # Hämta all energidata från jan 2025 med paginering
            energy_rows = []
            offset = 0
            while True:
                result = (
                    db.table("energy_readings")
                    .select("device_name, timestamp, current_value")
                    .gte("timestamp", START_DATE)
                    .order("timestamp", desc=False)
                    .range(offset, offset + PAGE_SIZE - 1)
                    .execute()
                )
                energy_rows.extend(result.data)
                if len(result.data) < PAGE_SIZE:
                    break
                offset += PAGE_SIZE

            # Hämta alla spotpriser från jan 2025
            price_rows = []
            offset = 0
            while True:
                result = (
                    db.table("spot_prices")
                    .select("timestamp, price_sek")
                    .gte("timestamp", START_DATE)
                    .order("timestamp", desc=False)
                    .range(offset, offset + PAGE_SIZE - 1)
                    .execute()
                )
                price_rows.extend(result.data)
                if len(result.data) < PAGE_SIZE:
                    break
                offset += PAGE_SIZE

            # Bygg timme->pris-lookup (öre/kWh, medelvärde per timme)
            price_sum_by_hour = {}
            price_count_by_hour = {}
            for p in price_rows:
                hour_key = p["timestamp"][:13]
                ore = p["price_sek"]
                price_sum_by_hour[hour_key] = price_sum_by_hour.get(hour_key, 0) + ore
                price_count_by_hour[hour_key] = price_count_by_hour.get(hour_key, 0) + 1
            price_by_hour = {
                h: price_sum_by_hour[h] / price_count_by_hour[h]
                for h in price_sum_by_hour
            }

            # Aggregera per månad och enhet
            # current_value = Watt, × 0.25h / 1000 = kWh per 15-min mätning
            monthly = {}  # {YYYY-MM: {enhet: {kwh, cost, kwh_priced}}}
            for r in energy_rows:
                ts = r["timestamp"]
                month_key = ts[:7]   # "2025-01"
                hour_key = ts[:13]   # "2025-01-01T14"
                device = r["device_name"]
                watts = r["current_value"] or 0
                kwh = watts * 0.25 / 1000

                price_ore = price_by_hour.get(hour_key, None)
                cost = kwh * price_ore / 100 if price_ore is not None else None

                if month_key not in monthly:
                    monthly[month_key] = {}
                if device not in monthly[month_key]:
                    monthly[month_key][device] = {
                        "kwh": 0.0, "cost": 0.0,
                        "kwh_priced": 0.0,  # kWh med känt pris (för snittpris)
                        "weighted_price": 0.0  # summa(kwh*pris) för vägat snittpris
                    }

                d = monthly[month_key][device]
                d["kwh"] += kwh
                if cost is not None:
                    d["cost"] += cost
                    d["kwh_priced"] += kwh
                    d["weighted_price"] += kwh * price_ore

            # Formatera svar
            now_month = datetime.now(timezone.utc).strftime("%Y-%m")
            result_list = []
            for month in sorted(monthly.keys(), reverse=True):
                devices = monthly[month]
                total_kwh = sum(d["kwh"] for d in devices.values())
                total_cost = sum(d["cost"] for d in devices.values())
                total_kwh_priced = sum(d["kwh_priced"] for d in devices.values())
                total_weighted_price = sum(d["weighted_price"] for d in devices.values())

                # Vägat snittpris i öre/kWh
                avg_price = (
                    total_weighted_price / total_kwh_priced
                    if total_kwh_priced > 0 else None
                )

                row = {
                    "month": month,
                    "is_current": month == now_month,
                    "total_kwh": round(total_kwh, 1),
                    "total_cost": round(total_cost, 0),
                    "avg_price_ore": round(avg_price, 1) if avg_price is not None else None,
                    "devices": {
                        name: {
                            "kwh": round(d["kwh"], 1),
                            "cost": round(d["cost"], 0),
                        }
                        for name, d in devices.items()
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
