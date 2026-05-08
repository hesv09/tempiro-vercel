"""PUT /api/switch - Slår på/av en enhet via Tempiro API."""
from http.server import BaseHTTPRequestHandler
import json
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))
from _tempiro import switch_device


def _pin_is_valid(data: dict) -> bool:
    expected = os.environ.get("SWITCH_PIN")
    if not expected:
        return False
    supplied = data.get("pin")
    return isinstance(supplied, str) and supplied == expected


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "PUT, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_PUT(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            data = json.loads(body)

            device_id = data.get("device_id")
            value = data.get("value")
            if not os.environ.get("SWITCH_PIN"):
                self.send_response(503)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Switch PIN is not configured"}).encode())
                return

            if not _pin_is_valid(data):
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Fel PIN-kod"}).encode())
                return

            if not device_id or value not in (0, 1):
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "device_id och value (0 eller 1) krävs"}).encode())
                return

            result = switch_device(device_id, value)

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(result).encode())

        except Exception as e:
            print(f"switch failed: {e}")
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Switch command failed"}).encode())

    def log_message(self, format, *args):
        pass
