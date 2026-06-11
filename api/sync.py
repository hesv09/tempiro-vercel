"""GET /api/sync - Synkar data från Tempiro API och spotpriser till Supabase.
Körs automatiskt varje timme via Vercel Cron Job (kräver Pro-plan)."""
from http.server import BaseHTTPRequestHandler
from datetime import datetime, timedelta, timezone
import json
import requests
import sys
import os
import zoneinfo
sys.path.insert(0, os.path.dirname(__file__))
from _db import get_db
from _tempiro import get_devices, get_device_values
from _alerts import update_heater_state

TZ_STOCKHOLM = zoneinfo.ZoneInfo("Europe/Stockholm")


PRICE_AREA = "SE3"


def _authorized(headers) -> bool:
    secret = os.environ.get("CRON_SECRET")
    if not secret:
        return True
    return headers.get("Authorization") == f"Bearer {secret}"


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

            # Använd lokal Stockholm-tid för Tempiro-API:t (tolkar timestamps som lokal tid)
            now_local = datetime.now(TZ_STOCKHOLM)

            if status.data:
                # Hämta från senaste synk (minus 1h för överlapp), konvertera till lokal tid
                last_utc = datetime.fromisoformat(status.data[0]["last_sync"].replace("Z", "+00:00"))
                from_dt = (last_utc - timedelta(hours=1)).astimezone(TZ_STOCKHOLM).strftime("%Y-%m-%dT%H:%M:%S")
            else:
                # Första synk - hämta 7 dagar bakåt
                from_dt = (now_local - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")

            to_dt = now_local.strftime("%Y-%m-%dT%H:%M:%S")

            values = get_device_values(device_id, from_dt, to_dt)

            if not values:
                current_power = device.get("CurrentPower", device.get("currentPower", device.get("current_value", 0))) or 0
                snapshot_ts = datetime.now(TZ_STOCKHOLM).strftime("%Y-%m-%dT%H:%M:%S")
                rows = [{
                    "device_id": device_id,
                    "device_name": device_name,
                    "timestamp": snapshot_ts,
                    "delta_power": current_power / 4,
                    "accumulated_value": 0,
                    "current_value": current_power,
                }]
                db.table("energy_readings").upsert(rows, on_conflict="device_id,timestamp").execute()
                total_saved += len(rows)
                db.table("sync_status").upsert({
                    "sync_type": "energy",
                    "device_id": device_id,
                    "last_sync": datetime.utcnow().isoformat(),
                }, on_conflict="sync_type,device_id").execute()
                errors.append(f"{device_name}: no interval data, used snapshot fallback (current={current_power}W)")
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
                    "price_sek": p["SEK_per_kWh"] * 100,  # Konvertera till öre/kWh
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
        if not _authorized(self.headers):
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": False, "error": "Unauthorized"}).encode())
            return

        try:
            db = get_db()

            energy_result = sync_energy(db)
            price_result = sync_prices(db)
            heater_result = update_heater_state(db, get_devices(), send_email=True)

            result = {
                "ok": True,
                "timestamp": datetime.utcnow().isoformat(),
                "energy": energy_result,
                "prices": price_result,
                "heater": heater_result,
            }

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(result).encode())

        except Exception as e:
            print(f"sync failed: {e}")
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": False, "error": "Sync failed"}).encode())

    def log_message(self, format, *args):
        pass
