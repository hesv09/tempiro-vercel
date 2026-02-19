"""
Migrerar befintlig SQLite-data till Supabase.
Kör en gång lokalt: python migrate_to_supabase.py
"""
import sqlite3
import os
import sys
from supabase import create_client

SUPABASE_URL = "https://vkecqtpxygfhwqesievk.supabase.co"
SUPABASE_SECRET = os.environ.get("SUPABASE_SECRET")
SQLITE_PATH = "/Users/henriksvedstrom/Documents/Claude_projects/tempiro-integration/tempiro_data.db"
BATCH_SIZE = 500  # Antal rader per batch


def migrate():
    if not SUPABASE_SECRET:
        print("Sätt miljövariabel: export SUPABASE_SECRET=din_secret_key")
        sys.exit(1)

    db = create_client(SUPABASE_URL, SUPABASE_SECRET)
    conn = sqlite3.connect(SQLITE_PATH)
    conn.row_factory = sqlite3.Row

    # --- Migrera energy_readings ---
    print("\n=== Migrerar energy_readings ===")
    cursor = conn.execute("SELECT COUNT(*) FROM energy_readings")
    total = cursor.fetchone()[0]
    print(f"Totalt {total} rader att migrera...")

    cursor = conn.execute("""
        SELECT device_id, device_name, timestamp, delta_power, accumulated_value, current_value
        FROM energy_readings
        ORDER BY timestamp ASC
    """)

    batch = []
    migrated = 0
    errors = 0

    for row in cursor:
        # Konvertera timestamp till ISO-format med tidzon
        ts = row["timestamp"]
        if "T" not in ts:
            ts = ts.replace(" ", "T")
        if not ts.endswith("Z") and "+" not in ts:
            ts += "Z"

        batch.append({
            "device_id": row["device_id"],
            "device_name": row["device_name"],
            "timestamp": ts,
            "delta_power": row["delta_power"] or 0,
            "accumulated_value": row["accumulated_value"] or 0,
            "current_value": row["current_value"] or 0,
        })

        if len(batch) >= BATCH_SIZE:
            try:
                db.table("energy_readings").upsert(
                    batch, on_conflict="device_id,timestamp"
                ).execute()
                migrated += len(batch)
                print(f"  {migrated}/{total} rader migrerade...", end="\r")
            except Exception as e:
                errors += 1
                print(f"\n  FEL i batch: {e}")
            batch = []

    # Sista batchen
    if batch:
        try:
            db.table("energy_readings").upsert(
                batch, on_conflict="device_id,timestamp"
            ).execute()
            migrated += len(batch)
        except Exception as e:
            errors += 1
            print(f"\n  FEL i sista batch: {e}")

    print(f"\n  Klart! {migrated} rader migrerade, {errors} fel.")

    # --- Migrera spot_prices ---
    print("\n=== Migrerar spot_prices ===")
    cursor = conn.execute("SELECT COUNT(*) FROM spot_prices")
    total = cursor.fetchone()[0]
    print(f"Totalt {total} rader att migrera...")

    cursor = conn.execute("""
        SELECT timestamp, price_area, price_sek, price_eur
        FROM spot_prices
        ORDER BY timestamp ASC
    """)

    batch = []
    migrated = 0
    errors = 0

    for row in cursor:
        ts = row["timestamp"]
        if "T" not in ts:
            ts = ts.replace(" ", "T")
        if not ts.endswith("Z") and "+" not in ts:
            ts += "Z"

        batch.append({
            "timestamp": ts,
            "price_area": row["price_area"],
            "price_sek": row["price_sek"] or 0,
            "price_eur": row["price_eur"],
        })

        if len(batch) >= BATCH_SIZE:
            try:
                db.table("spot_prices").upsert(
                    batch, on_conflict="timestamp,price_area"
                ).execute()
                migrated += len(batch)
                print(f"  {migrated}/{total} rader migrerade...", end="\r")
            except Exception as e:
                errors += 1
                print(f"\n  FEL i batch: {e}")
            batch = []

    if batch:
        try:
            db.table("spot_prices").upsert(
                batch, on_conflict="timestamp,price_area"
            ).execute()
            migrated += len(batch)
        except Exception as e:
            errors += 1
            print(f"\n  FEL i sista batch: {e}")

    print(f"\n  Klart! {migrated} rader migrerade, {errors} fel.")

    conn.close()
    print("\n✅ Migrering klar!")


if __name__ == "__main__":
    migrate()
