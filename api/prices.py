"""GET /api/prices?days=1 - Hämtar spotpriser från Supabase."""
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timedelta, timezone
import json
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))
from _db import get_public_db


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            params = parse_qs(urlparse(self.path).query)
            days = int(params.get("days", ["1"])[0])

            if days < 1 or days > 90:
                days = 1

            from_ts = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            db = get_public_db()

            result = (
                db.table("spot_prices")
                .select("timestamp, price_sek, price_area")
                .gte("timestamp", from_ts)
                .order("timestamp", desc=False)
                .execute()
            )

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(result.data).encode())

        except Exception as e:
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    def log_message(self, format, *args):
        pass
