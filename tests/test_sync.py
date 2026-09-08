import importlib.util
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock
from zoneinfo import ZoneInfo


API_DIR = Path(__file__).resolve().parents[1] / "api"


def _load_sync_module():
    fake_requests = types.ModuleType("requests")
    fake_requests.get = Mock()
    fake_db = types.ModuleType("_db")
    fake_db.get_db = Mock()
    fake_tempiro = types.ModuleType("_tempiro")
    fake_tempiro.get_devices = Mock()
    fake_tempiro.get_device_values = Mock()
    fake_alerts = types.ModuleType("_alerts")
    fake_alerts.update_heater_state = Mock()

    sys.modules["requests"] = fake_requests
    sys.modules["_db"] = fake_db
    sys.modules["_tempiro"] = fake_tempiro
    sys.modules["_alerts"] = fake_alerts
    spec = importlib.util.spec_from_file_location("tempiro_sync", API_DIR / "sync.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sync = _load_sync_module()


class Result:
    def __init__(self, data=None):
        self.data = data or []


class FakeQuery:
    def __init__(self, db, table):
        self.db = db
        self.table = table
        self.kind = None
        self.payload = None

    def select(self, *_args):
        self.kind = "select"
        return self

    def eq(self, *_args):
        return self

    def upsert(self, payload, **_kwargs):
        self.kind = "upsert"
        self.payload = payload
        return self

    def execute(self):
        if self.kind == "select":
            return Result(self.db.status_data)
        self.db.writes.append((self.table, self.payload))
        return Result()


class FakeDb:
    def __init__(self, status_data=None):
        self.status_data = status_data or []
        self.writes = []

    def table(self, name):
        return FakeQuery(self, name)


class SyncTests(unittest.TestCase):
    def test_request_can_target_one_energy_device(self):
        self.assertEqual(
            sync._request_options("/api/sync?mode=energy&device=VVB1"),
            ("energy", "VVB1"),
        )

    def test_device_selection_accepts_name_or_id(self):
        devices = [
            {"Id": "id-1", "Name": "VVB1"},
            {"Id": "id-2", "Name": "VVB2"},
        ]
        self.assertEqual(sync._select_devices(devices, "vvb1"), [devices[0]])
        self.assertEqual(sync._select_devices(devices, "id-2"), [devices[1]])

    def test_old_sync_is_limited_to_small_lookback_window(self):
        stockholm = ZoneInfo("Europe/Stockholm")
        now = datetime(2026, 9, 8, 18, 0, tzinfo=stockholm)
        old_status = [{"last_sync": "2026-01-01T00:00:00+00:00"}]
        os.environ["TEMPIRO_SYNC_LOOKBACK_DAYS"] = "2"

        self.assertEqual(
            sync._from_datetime(old_status, now),
            now - timedelta(days=2),
        )

    def test_recent_sync_keeps_one_hour_overlap(self):
        stockholm = ZoneInfo("Europe/Stockholm")
        now = datetime(2026, 9, 8, 18, 0, tzinfo=stockholm)
        last_sync = datetime(2026, 9, 8, 14, 30, tzinfo=timezone.utc)

        actual = sync._from_datetime(
            [{"last_sync": last_sync.isoformat()}], now
        )

        self.assertEqual(
            actual,
            (last_sync - timedelta(hours=1)).astimezone(stockholm),
        )

    def test_interval_failure_upserts_live_snapshot_without_deletes(self):
        db = FakeDb([{"last_sync": "2026-09-08T14:00:00+00:00"}])
        sync.get_device_values = Mock(side_effect=TimeoutError("Tempiro timeout"))
        device = {"Id": "id-1", "Name": "VVB1", "CurrentPower": 892}

        result = sync.sync_energy(db, [device])

        self.assertEqual(result["saved"], 1)
        self.assertEqual(result["snapshots"], 1)
        self.assertEqual(result["failed_devices"], 0)
        self.assertEqual([table for table, _ in db.writes], [
            "energy_readings",
            "sync_status",
        ])
        reading = db.writes[0][1][0]
        self.assertEqual(reading["current_value"], 892)
        self.assertEqual(reading["delta_power"], 223)


if __name__ == "__main__":
    unittest.main()
