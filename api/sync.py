"""GET /api/sync - sync Tempiro data and spot prices to Supabase."""
from http.server import BaseHTTPRequestHandler
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse
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
VALID_MODES = {"all", "energy", "prices", "heater"}
DEFAULT_SYNC_LOOKBACK_DAYS = 2


def _authorized(headers) -> bool:
    secret = os.environ.get("CRON_SECRET")
    if not secret:
        return False
    return headers.get("Authorization") == f"Bearer {secret}"


def _send_json(request_handler, status: int, payload: dict):
    request_handler.send_response(status)
    request_handler.send_header("Content-Type", "application/json")
    request_handler.end_headers()
    request_handler.wfile.write(json.dumps(payload).encode())


def _request_options(path: str) -> tuple[str, str | None]:
    params = parse_qs(urlparse(path).query)
    mode = params.get("mode", ["all"])[0].lower()
    device = params.get("device", [None])[0]
    if mode not in VALID_MODES:
        raise ValueError(f"Unknown sync mode: {mode}")
    if device and mode != "energy":
        raise ValueError("device can only be used with mode=energy")
    return mode, device


def _device_id(device: dict):
    return device.get("Id") or device.get("id")


def _device_name(device: dict):
    return device.get("Name") or device.get("name") or _device_id(device)


def _select_devices(devices: list, selector: str | None) -> list:
    if not selector:
        return devices
    normalized = selector.casefold()
    return [
        device for device in devices
        if str(_device_id(device)) == selector
        or str(_device_name(device)).casefold() == normalized
    ]


def _max_lookback_days() -> int:
    try:
        return max(1, int(os.environ.get(
            "TEMPIRO_SYNC_LOOKBACK_DAYS", DEFAULT_SYNC_LOOKBACK_DAYS
        )))
    except (TypeError, ValueError):
        return DEFAULT_SYNC_LOOKBACK_DAYS


def _from_datetime(status_data: list, now_local: datetime) -> datetime:
    """Keep routine syncs small enough for Tempiro and Vercel to finish."""
    earliest = now_local - timedelta(days=_max_lookback_days())
    if not status_data:
        return earliest

    last_sync = datetime.fromisoformat(
        status_data[0]["last_sync"].replace("Z", "+00:00")
    )
    if last_sync.tzinfo is None:
        last_sync = last_sync.replace(tzinfo=timezone.utc)
    requested = (last_sync - timedelta(hours=1)).astimezone(TZ_STOCKHOLM)
    return max(requested, earliest)


def _update_energy_status(db, device_id: str):
    db.table("sync_status").upsert({
        "sync_type": "energy",
        "device_id": device_id,
        "last_sync": datetime.now(timezone.utc).isoformat(),
    }, on_conflict="sync_type,device_id").execute()


def _save_snapshot(db, device: dict, now_local: datetime) -> int:
    """Persist the live reading when Tempiro's interval endpoint is unavailable."""
    current_power = None
    for key in ("CurrentPower", "currentPower", "current_value"):
        if key in device and device[key] is not None:
            current_power = device[key]
            break
    if current_power is None:
        raise ValueError("live power value is missing")

    device_id = _device_id(device)
    row = {
        "device_id": device_id,
        "device_name": _device_name(device),
        # Existing readings use local Stockholm time without an offset.
        "timestamp": now_local.strftime("%Y-%m-%dT%H:%M:%S"),
        "delta_power": current_power / 4,
        "accumulated_value": 0,
        "current_value": current_power,
    }
    db.table("energy_readings").upsert(
        [row], on_conflict="device_id,timestamp"
    ).execute()
    _update_energy_status(db, device_id)
    return 1


def sync_energy(db, devices) -> dict:
    """Sync one or more devices without letting one failure block the others."""
    total_saved = 0
    snapshots = 0
    failed_devices = 0
    errors = []

    for device in devices:
        device_id = _device_id(device)
        device_name = _device_name(device)
        now_local = datetime.now(TZ_STOCKHOLM)

        try:
            status = (
                db.table("sync_status")
                .select("last_sync")
                .eq("sync_type", "energy")
                .eq("device_id", device_id)
                .execute()
            )
            from_dt = _from_datetime(status.data, now_local).strftime(
                "%Y-%m-%dT%H:%M:%S"
            )
            to_dt = now_local.strftime("%Y-%m-%dT%H:%M:%S")
            values = get_device_values(device_id, from_dt, to_dt)

            rows = []
            for value in values or []:
                timestamp = value.get("DateTime") or value.get("timestamp")
                if not timestamp:
                    continue
                rows.append({
                    "device_id": device_id,
                    "device_name": device_name,
                    "timestamp": timestamp,
                    "delta_power": value.get("DeltaPower", 0),
                    "accumulated_value": value.get("AccumulatedValue", 0),
                    "current_value": value.get("CurrentValue", 0),
                })

            if not rows:
                raise ValueError("no usable interval data")

            db.table("energy_readings").upsert(
                rows, on_conflict="device_id,timestamp"
            ).execute()
            _update_energy_status(db, device_id)
            total_saved += len(rows)
        except Exception as interval_error:
            try:
                saved = _save_snapshot(db, device, now_local)
                total_saved += saved
                snapshots += saved
                errors.append(
                    f"{device_name}: interval sync failed; saved live snapshot "
                    f"({interval_error})"
                )
            except Exception as snapshot_error:
                failed_devices += 1
                errors.append(
                    f"{device_name}: interval sync failed ({interval_error}); "
                    f"snapshot failed ({snapshot_error})"
                )

    return {
        "saved": total_saved,
        "snapshots": snapshots,
        "failed_devices": failed_devices,
        "errors": errors,
    }


def sync_prices(db) -> dict:
    """Sync spot prices from elprisetjustnu.se."""
    total_saved = 0
    errors = []

    for days_ago in range(-1, 3):
        date = datetime.now(timezone.utc) - timedelta(days=days_ago)
        date_str = date.strftime("%Y/%m-%d")
        url = f"https://www.elprisetjustnu.se/api/v1/prices/{date_str}_{PRICE_AREA}.json"

        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code != 200:
                continue

            rows = [{
                "timestamp": price["time_start"],
                "price_area": PRICE_AREA,
                "price_sek": price["SEK_per_kWh"] * 100,
                "price_eur": price.get("EUR_per_kWh"),
            } for price in resp.json()]

            if rows:
                db.table("spot_prices").upsert(
                    rows, on_conflict="timestamp,price_area"
                ).execute()
                total_saved += len(rows)
        except Exception as error:
            errors.append(f"{date_str}: {error}")

    return {"saved": total_saved, "errors": errors}


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if not _authorized(self.headers):
            _send_json(self, 401, {"ok": False, "error": "Unauthorized"})
            return

        try:
            mode, device_selector = _request_options(self.path)
        except ValueError as error:
            _send_json(self, 400, {"ok": False, "error": str(error)})
            return

        try:
            db = get_db()
            result = {
                "ok": True,
                "mode": mode,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            devices = None

            if mode in {"all", "energy", "heater"}:
                try:
                    devices = get_devices()
                except Exception as error:
                    print(f"[sync] get_devices error: {error}")
                    if mode != "all":
                        raise

            if mode in {"all", "energy"}:
                if devices is None:
                    energy_result = {
                        "saved": 0,
                        "snapshots": 0,
                        "failed_devices": 1,
                        "errors": ["could not fetch devices"],
                    }
                else:
                    selected_devices = _select_devices(devices, device_selector)
                    if device_selector and not selected_devices:
                        _send_json(self, 404, {
                            "ok": False,
                            "error": f"Device not found: {device_selector}",
                        })
                        return
                    energy_result = sync_energy(db, selected_devices)
                result["energy"] = energy_result
                if energy_result["failed_devices"]:
                    result["ok"] = False

            if mode in {"all", "prices"}:
                try:
                    price_result = sync_prices(db)
                except Exception as error:
                    print(f"[sync] prices error: {error}")
                    price_result = {"saved": 0, "errors": ["price sync failed"]}
                result["prices"] = price_result
                if price_result["errors"] and not price_result["saved"]:
                    result["ok"] = False

            if mode in {"all", "heater"}:
                if devices is None:
                    heater_result = {
                        "sync_ok": False,
                        "error": "could not fetch devices",
                    }
                    result["ok"] = False
                else:
                    heater_result = update_heater_state(
                        db, devices, send_email=True
                    )
                    heater_result["sync_ok"] = True
                result["heater"] = heater_result

            _send_json(self, 200 if result["ok"] else 502, result)
        except Exception as error:
            print(f"sync failed: {error}")
            _send_json(self, 500, {"ok": False, "error": "Sync failed"})

    def log_message(self, format, *args):
        pass
