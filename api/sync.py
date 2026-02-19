"""GET /api/sync - Synkar data från Tempiro API och spotpriser till Supabase.
Körs automatiskt en gång per dag via Vercel Cron Job."""
from http.server import BaseHTTPRequestHandler
from datetime import datetime, timedelta
import json
import requests
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))
from _db import get_db
from _tempiro import get_devices, get_device_values


PRICE_AREA = "SE3"


def sync_energy(db) -> dict:
    """Synka energidata för alla enheter."""
    devices = get_devices()
    total_saved = 0
    errors = []

    for device in devices:
        device_id = device.get("Id") or device.get("id")
        device_name = device.get("Name") or device.get("name") or device_id

        try:
            # Kolla senaste synk för denna enhet
            status = (
                db.table("sync_status")
                .select("last_sync")
                .eq("sync_type", "energy")
                .eq("device_id", device_id)
                .execute()
            )

            if status.data:
                # Hämta från senaste synk (minus 1h för överlapp)
                last = datetime.fromisoformat(status.data[0]["last_sync"].replace("Z", "+00:00"))
                from_dt = (last - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")
            else:
                # Första synk - hämta 7 dagar bakåt
                from_dt = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")

            to_dt = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")

            values = get_device_values(device_id, from_dt, to_dt)

            if not values:
                continue

            # Förbered rader för upsert
            rows = []
            for v in values:
                ts = v.get("DateTime") or v.get("timestamp")
                if not ts:
                    continue
                rows.append({
                    "device_id": device_id,
                    "device_name": device_name,
                    "timestamp": ts,
                    "delta_power": v.get("DeltaPower", 0),
                    "accumulated_value": v.get("AccumulatedValue", 0),
                    "current_value": v.get("CurrentValue", 0),
                })

            if rows:
                db.table("energy_readings").upsert(
                    rows, on_conflict="device_id,timestamp"
                ).execute()
                total_saved += len(rows)

            # Uppdatera sync_status
            db.table("sync_status").upsert({
                "sync_type": "energy",
                "device_id": device_id,
                "last_sync": datetime.utcnow().isoformat(),
            }, on_conflict="sync_type,device_id").execute()

        except Exception as e:
            errors.append(f"{device_name}: {e}")

    return {"saved": total_saved, "errors": errors}


def sync_prices(db) -> dict:
    """Synka spotpriser från elprisetjustnu.se."""
    total_saved = 0
    errors = []

    for days_ago in range(-1, 3):
        date = datetime.utcnow() - timedelta(days=days_ago)
        date_str = date.strftime("%Y/%m-%d")
        url = f"https://www.elprisetjustnu.se/api/v1/prices/{date_str}_{PRICE_AREA}.json"

        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code != 200:
                continue

            prices = resp.json()
            rows = []
            for p in prices:
                rows.append({
                    "timestamp": p["time_start"],
                    "price_area": PRICE_AREA,
                    "price_sek": p["SEK_per_kWh"],
                    "price_eur": p.get("EUR_per_kWh"),
                })

            if rows:
                db.table("spot_prices").upsert(
                    rows, on_conflict="timestamp,price_area"
                ).execute()
                total_saved += len(rows)

        except Exception as e:
            errors.append(f"{date_str}: {e}")

    return {"saved": total_saved, "errors": errors}


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            db = get_db()

            energy_result = sync_energy(db)
            price_result = sync_prices(db)

            result = {
                "ok": True,
                "timestamp": datetime.utcnow().isoformat(),
                "energy": energy_result,
                "prices": price_result,
            }

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(result).encode())

        except Exception as e:
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": False, "error": str(e)}).encode())

    def log_message(self, format, *args):
        pass
