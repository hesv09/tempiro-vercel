"""GET /api/alerts - Returns current operational alerts for the dashboard."""
from http.server import BaseHTTPRequestHandler
import json
import os
import sys
sys.path.insert(0, os.path.dirname(__file__))
from _db import get_db
from _alerts import read_heater_state


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            db = get_db()
            heater = read_heater_state(db)

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({"heater": heater}).encode())

        except Exception as e:
            print(f"alerts failed: {e}")
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Could not load alerts"}).encode())

    def log_message(self, format, *args):
        pass
