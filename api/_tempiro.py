"""Tempiro API client - hämtar data från Tempiro molnet."""
import os
import time
import requests

TEMPIRO_USERNAME = os.environ["TEMPIRO_USERNAME"]
TEMPIRO_PASSWORD = os.environ["TEMPIRO_PASSWORD"]

BASE_URL = "https://api.tempiro.com"

_token_cache = {"token": None, "expires": 0}


def get_token() -> str:
    """Hämta auth-token, använd cache om giltig."""
    now = time.time()
    if _token_cache["token"] and _token_cache["expires"] > now + 60:
        return _token_cache["token"]

    resp = requests.post(
        f"{BASE_URL}/auth/token",
        json={"username": TEMPIRO_USERNAME, "password": TEMPIRO_PASSWORD},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    token = data["access_token"]
    expires_in = data.get("expires_in", 3600)
    _token_cache["token"] = token
    _token_cache["expires"] = now + expires_in
    return token


def get_headers() -> dict:
    return {"Authorization": f"Bearer {get_token()}"}


def get_devices() -> list:
    """Hämta alla enheter."""
    resp = requests.get(f"{BASE_URL}/devices", headers=get_headers(), timeout=15)
    resp.raise_for_status()
    return resp.json()


def get_device_values(device_id: str, from_dt: str, to_dt: str) -> list:
    """Hämta mätvärden för en enhet inom ett tidsintervall."""
    resp = requests.get(
        f"{BASE_URL}/devices/{device_id}/values",
        headers=get_headers(),
        params={"from": from_dt, "to": to_dt, "interval": 15},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def switch_device(device_id: str, value: int) -> dict:
    """Slå på/av en enhet (value: 1=på, 0=av)."""
    resp = requests.put(
        f"{BASE_URL}/devices/{device_id}/switch",
        headers=get_headers(),
        json={"value": value},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()
