"""GET /api/monthly - Energi och kostnad aggregerat per kalendermånad från jan 2025."""
from http.server import BaseHTTPRequestHandler
from datetime import datetime, timezone, timedelta
import json
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))
from _db import get_public_db

PAGE_SIZE = 1000
START_DATE = "2025-01-01T00:00:00+00:00"


def all_months_since_start():
    """Generera lista med alla YYYY-MM från jan 2025 till och med aktuell månad."""
    months = []
    now = datetime.now(timezone.utc)
    current = datetime(2025, 1, 1, tzinfo=timezone.utc)
    while current.year < now.year or (current.year == now.year and current.month <= now.month):
        months.append(current.strftime("%Y-%m"))
        if current.month == 12:
            current = current.replace(year=current.year + 1, month=1)
        else:
            current = current.replace(month=current.month + 1)
    return months


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

            # Bygg dag->pris-lookup med medelvärde per dag.
            # Obs: energimätningar är lagrade som UTC (men var egentligen lokal tid vid migrering).
            # Spotpriser är lagrade korrekt som UTC. Timme-matchning ger fel pga timezone-shift,
            # men dagsmedelvärde fungerar bra för månadsaggregering.
            price_sum_by_day = {}
            price_count_by_day = {}
            for p in price_rows:
                # Supabase returnerar alltid UTC ISO-sträng
                ts_str = p["timestamp"]
                # Ta datum-delen (10 tecken: YYYY-MM-DD) ur UTC-strängen
                day_key = ts_str[:10]
                ore = p["price_sek"]  # i öre/kWh
                price_sum_by_day[day_key] = price_sum_by_day.get(day_key, 0) + ore
                price_count_by_day[day_key] = price_count_by_day.get(day_key, 0) + 1
            price_by_day = {
                d: price_sum_by_day[d] / price_count_by_day[d]
                for d in price_sum_by_day
            }

            # Aggregera per månad och enhet
            # current_value = Watt, × 0.25h / 1000 = kWh per 15-min mätning
            monthly = {}  # {YYYY-MM: {enhet: {kwh, cost, kwh_priced, weighted_price}}}
            readings_by_month = {}  # antal mätningar per månad

            for r in energy_rows:
                ts = r["timestamp"]
                month_key = ts[:7]   # "2025-01"
                day_key = ts[:10]    # "2025-01-15"
                device = r["device_name"]
                watts = r["current_value"] or 0
                kwh = watts * 0.25 / 1000

                price_ore = price_by_day.get(day_key)
                cost = kwh * price_ore / 100 if price_ore is not None else None

                if month_key not in monthly:
                    monthly[month_key] = {}
                    readings_by_month[month_key] = 0
                if device not in monthly[month_key]:
                    monthly[month_key][device] = {
                        "kwh": 0.0, "cost": 0.0,
                        "kwh_priced": 0.0,
                        "weighted_price": 0.0
                    }

                d = monthly[month_key][device]
                d["kwh"] += kwh
                if cost is not None:
                    d["cost"] += cost
                    d["kwh_priced"] += kwh
                    d["weighted_price"] += kwh * price_ore
                readings_by_month[month_key] += 1

            # Bygg resultat för ALLA månader sedan jan 2025
            now_month = datetime.now(timezone.utc).strftime("%Y-%m")
            result_list = []

            for month in all_months_since_start():
                if month not in monthly:
                    # Ingen energidata alls – visa ändå månaden
                    result_list.append({
                        "month": month,
                        "is_current": month == now_month,
                        "no_data": True,
                        "total_kwh": None,
                        "total_cost": None,
                        "avg_price_ore": None,
                        "readings": 0,
                        "devices": {}
                    })
                    continue

                devices = monthly[month]
                total_kwh = sum(d["kwh"] for d in devices.values())
                total_cost = sum(d["cost"] for d in devices.values())
                total_kwh_priced = sum(d["kwh_priced"] for d in devices.values())
                total_weighted_price = sum(d["weighted_price"] for d in devices.values())

                avg_price = (
                    total_weighted_price / total_kwh_priced
                    if total_kwh_priced > 0 else None
                )

                # Räkna förväntade mätningar: 4/h × 24h × dagar × antal enheter
                # Använd detta för att flagga månader med inkomplett data
                days_in_month = 31  # grov uppskattning
                device_count = len(devices)
                expected_readings = 4 * 24 * days_in_month * device_count
                actual_readings = readings_by_month[month]
                completeness = actual_readings / max(expected_readings, 1)

                result_list.append({
                    "month": month,
                    "is_current": month == now_month,
                    "no_data": False,
                    "partial": completeness < 0.5,  # flagga om <50% av förväntad data
                    "total_kwh": round(total_kwh, 1),
                    "total_cost": round(total_cost, 0),
                    "avg_price_ore": round(avg_price, 1) if avg_price is not None else None,
                    "readings": actual_readings,
                    "devices": {
                        name: {
                            "kwh": round(d["kwh"], 1),
                            "cost": round(d["cost"], 0),
                        }
                        for name, d in devices.items()
                    }
                })

            # Sortera nyast först
            result_list.reverse()

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
