"""GET /api/energy?days=7&device_id=xxx - Hämtar energidata från Supabase med paginering."""
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timedelta, timezone
import json
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))
from _db import get_public_db

PAGE_SIZE = 1000


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            params = parse_qs(urlparse(self.path).query)
            days = int(params.get("days", ["7"])[0])
            device_id = params.get("device_id", [None])[0]

            if days < 1 or days > 365:
                days = 7

            from_ts = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            db = get_public_db()

            # Hämta alla sidor
            all_data = []
            offset = 0
            while True:
                query = (
                    db.table("energy_readings")
                    .select("device_id, device_name, timestamp, delta_power, current_value")
                    .gte("timestamp", from_ts)
                    .order("timestamp", desc=False)
                    .range(offset, offset + PAGE_SIZE - 1)
                )
                if device_id:
                    query = query.eq("device_id", device_id)

                result = query.execute()
                all_data.extend(result.data)

                if len(result.data) < PAGE_SIZE:
                    break
                offset += PAGE_SIZE

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(all_data).encode())

        except Exception as e:
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    def log_message(self, format, *args):
        pass
