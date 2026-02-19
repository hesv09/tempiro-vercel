"""Tempiro API client - hämtar data från Tempiro molnet."""
import os
import time
import requests
from datetime import datetime, timedelta

TEMPIRO_USERNAME = os.environ["TEMPIRO_USERNAME"]
TEMPIRO_PASSWORD = os.environ["TEMPIRO_PASSWORD"]
BASE_URL = os.environ.get("TEMPIRO_BASE_URL", "http://xmpp.tempiro.com:5000")

_token_cache = {"token": None, "expires": None}


def get_token() -> str:
    """Hämta auth-token, använd cache om giltig."""
    now = datetime.now()
    if _token_cache["token"] and _token_cache["expires"] and now < _token_cache["expires"]:
        return _token_cache["token"]

    resp = requests.post(
        f"{BASE_URL}/Token",
        json={"Username": TEMPIRO_USERNAME, "Password": TEMPIRO_PASSWORD},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    token = data["access_token"]
    _token_cache["token"] = token
    _token_cache["expires"] = now + timedelta(days=6)
    return token


def get_headers() -> dict:
    return {
        "Accept": "application/json",
        "Authorization": f"Bearer {get_token()}"
    }


def get_devices() -> list:
    """Hämta alla enheter."""
    resp = requests.get(f"{BASE_URL}/api/devices", headers=get_headers(), timeout=15)
    resp.raise_for_status()
    return resp.json()


def get_device_values(device_id: str, from_dt: str, to_dt: str) -> list:
    """Hämta mätvärden för en enhet inom ett tidsintervall."""
    resp = requests.get(
        f"{BASE_URL}/api/Values/{device_id}/interval",
        headers=get_headers(),
        params={"from": from_dt, "to": to_dt, "intervalMinutes": 15},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def switch_device(device_id: str, value: int) -> dict:
    """Slå på/av en enhet (value: 1=på, 0=av)."""
    resp = requests.put(
        f"{BASE_URL}/api/devices/{device_id}/switch",
        headers=get_headers(),
        json={"value": value},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json() if resp.content else {}
