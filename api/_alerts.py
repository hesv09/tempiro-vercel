"""Alert helpers for water heater uptime and optional email notifications."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
import requests


HEATER_STATE_SYNC_TYPE = "heater_state"
HEATER_STATE_DEVICE_ID = "water_heater"
HEATER_ALERT_SYNC_TYPE = "heater_alert"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _target_device_ids(devices):
    configured = os.environ.get("WATER_HEATER_DEVICE_IDS", "").strip()
    if not configured:
        return {str(d.get("Id") or d.get("id")) for d in devices if d.get("Id") or d.get("id")}
    return {item.strip() for item in configured.split(",") if item.strip()}


def _is_on(device):
    value = device.get("Value", device.get("value", 0))
    return value in (1, True, "1", "true", "True")


def _threshold_minutes() -> int:
    raw = os.environ.get("WATER_HEATER_OFF_ALERT_MINUTES", "180")
    try:
        return max(15, int(raw))
    except ValueError:
        return 180


def _status_row(db, sync_type: str):
    result = (
        db.table("sync_status")
        .select("last_sync, oldest_data")
        .eq("sync_type", sync_type)
        .eq("device_id", HEATER_STATE_DEVICE_ID)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def _upsert_status(db, sync_type: str, *, last_sync: datetime, oldest_data: datetime | None = None):
    db.table("sync_status").upsert({
        "sync_type": sync_type,
        "device_id": HEATER_STATE_DEVICE_ID,
        "last_sync": last_sync.isoformat(),
        "oldest_data": oldest_data.isoformat() if oldest_data else None,
    }, on_conflict="sync_type,device_id").execute()


def read_heater_state(db) -> dict:
    """Read the last persisted water heater state without writing.
    Enhetsnamn sparas inte i sync_status, så off_devices är alltid tom här."""
    now = _utc_now()
    threshold = _threshold_minutes()
    row = _status_row(db, HEATER_STATE_SYNC_TYPE)
    off_since = _parse_dt(row.get("oldest_data")) if row else None

    if off_since is None:
        return {
            "ok": True,
            "severity": "ok",
            "message": (
                "Varmvattenberedaren är på."
                if row else
                "Ingen heater-status har synkats ännu."
            ),
            "off_since": None,
            "off_minutes": 0,
            "threshold_minutes": threshold,
            "off_devices": [],
            "last_checked": row["last_sync"] if row else None,
        }

    off_minutes = int((now - off_since).total_seconds() // 60)
    severity = "critical" if off_minutes >= threshold else "warning"
    return {
        "ok": False,
        "severity": severity,
        "message": f"Varmvattenberedaren har varit av i cirka {off_minutes} min.",
        "off_since": off_since.isoformat(),
        "off_minutes": off_minutes,
        "threshold_minutes": threshold,
        "off_devices": [],
        "last_checked": row["last_sync"],
    }


def update_heater_state(db, devices: list, *, send_email: bool = False) -> dict:
    """Persist and return the current water heater alert state."""
    now = _utc_now()
    target_ids = _target_device_ids(devices)
    target_devices = [
        d for d in devices
        if str(d.get("Id") or d.get("id")) in target_ids
    ]
    off_devices = [
        {
            "id": str(d.get("Id") or d.get("id")),
            "name": d.get("Name") or d.get("name") or str(d.get("Id") or d.get("id")),
        }
        for d in target_devices
        if not _is_on(d)
    ]

    previous = _status_row(db, HEATER_STATE_SYNC_TYPE)
    previous_off_since = _parse_dt(previous.get("oldest_data")) if previous else None
    is_ok = bool(target_devices) and not off_devices

    if is_ok:
        _upsert_status(db, HEATER_STATE_SYNC_TYPE, last_sync=now, oldest_data=None)
        return {
            "ok": True,
            "severity": "ok",
            "message": "Varmvattenberedaren är på.",
            "off_since": None,
            "off_minutes": 0,
            "threshold_minutes": _threshold_minutes(),
            "off_devices": [],
            "configured_devices": len(target_devices),
        }

    off_since = previous_off_since or now
    off_minutes = int((now - off_since).total_seconds() // 60)
    threshold = _threshold_minutes()
    severity = "critical" if off_minutes >= threshold else "warning"

    _upsert_status(db, HEATER_STATE_SYNC_TYPE, last_sync=now, oldest_data=off_since)

    state = {
        "ok": False,
        "severity": severity,
        "message": (
            f"Varmvattenberedaren har varit av i cirka {off_minutes} min."
            if off_devices else
            "Hittar inga konfigurerade varmvatten-säkringar."
        ),
        "off_since": off_since.isoformat(),
        "off_minutes": off_minutes,
        "threshold_minutes": threshold,
        "off_devices": off_devices,
        "configured_devices": len(target_devices),
    }
    if send_email:
        maybe_send_heater_email(db, state)
    return state


def maybe_send_heater_email(db, state: dict) -> bool:
    """Send a throttled email through Resend if configured."""
    if state.get("severity") != "critical":
        return False

    api_key = os.environ.get("RESEND_API_KEY")
    email_to = os.environ.get("ALERT_EMAIL_TO")
    email_from = os.environ.get("ALERT_EMAIL_FROM")
    if not api_key or not email_to or not email_from:
        return False

    repeat_hours = int(os.environ.get("ALERT_REPEAT_HOURS", "6"))
    previous = _status_row(db, HEATER_ALERT_SYNC_TYPE)
    last_sent = _parse_dt(previous.get("last_sync")) if previous else None
    now = _utc_now()
    if last_sent and now - last_sent < timedelta(hours=repeat_hours):
        return False

    off_names = ", ".join(d["name"] for d in state.get("off_devices", [])) or "okända säkringar"
    resp = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "from": email_from,
            "to": [email_to],
            "subject": "Tempiro: varmvattenberedaren verkar vara av",
            "text": (
                f"{state['message']}\n\n"
                f"Avstängda säkringar: {off_names}\n"
                f"Larmgräns: {state['threshold_minutes']} minuter\n"
            ),
        },
        timeout=10,
    )
    resp.raise_for_status()
    _upsert_status(db, HEATER_ALERT_SYNC_TYPE, last_sync=now)
    return True
