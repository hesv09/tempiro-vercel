"""GET /api/devices - Hämtar aktuell status för alla enheter från Tempiro API."""
from http.server import BaseHTTPRequestHandler
import json
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))
from _tempiro import get_devices


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            devices = get_devices()

            # Normalisera till samma format som lokala Flask-appen
            result = []
            for d in devices:
                result.append({
                    "id": d.get("Id") or d.get("id"),
                    "name": d.get("Name") or d.get("name"),
                    "deviceId": d.get("DeviceId") or d.get("deviceId"),
                    "value": d.get("Value", d.get("value", 0)),
                    "currentPower": d.get("CurrentPower", d.get("currentPower", 0)),
                    "batteryOK": d.get("BatteryOK", d.get("batteryOK", True)),
                    "fuseVoltageOK": d.get("FuseVoltageOK", d.get("fuseVoltageOK", True)),
                    "offline": d.get("Offline", d.get("offline", False)),
                    "lastUpdate": d.get("LastUpdate") or d.get("lastUpdate"),
                    "hoursActive": d.get("HoursActive", d.get("hoursActive", 0)),
                })

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(result).encode())

        except Exception as e:
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    def log_message(self, format, *args):
        pass
