"""GET /api/monthly - Månadsaggregat med cache i monthly_summaries.

Strategi:
  - Avslutade månader (nov 2025 → förra månaden): läses från monthly_summaries-cache.
    Om en månad saknas i cachen beräknas den en gång och sparas.
  - Innevarande månad: beräknas alltid live (rådata för enbart den månaden).
  → Snabb laddning efter första anropet; inga månader före nov 2025 visas.

Tidszoner:
  - Energimätningar: lokal svensk tid lagrad som "UTC" (Z-suffix vid migrering).
  - Spotpriser: korrekt UTC i Supabase. Konverteras till CET/CEST för 15-min matchning.
"""
from http.server import BaseHTTPRequestHandler
from datetime import datetime, timezone, timedelta
import calendar
import json
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))
from _db import get_db          # secret key – läs + skriv cache
from _db import get_public_db   # publishable key – läs rådata

PAGE_SIZE = 1000
FIRST_MONTH = "2025-11"   # Inga månader före detta visas


# ── Tidszonshjälp (CET/CEST) ────────────────────────────────────────────────

def _last_sunday(year, month):
    last_day = calendar.monthrange(year, month)[1]
    days_back = (datetime(year, month, last_day).weekday() + 1) % 7
    return last_day - days_back

def _se_offset(dt_utc):
    """2 (CEST) eller 1 (CET)."""
    y = dt_utc.year
    start = datetime(y, 3,  _last_sunday(y, 3),  1, 0, tzinfo=timezone.utc)
    end   = datetime(y, 10, _last_sunday(y, 10), 1, 0, tzinfo=timezone.utc)
    return 2 if start <= dt_utc < end else 1

def _price_key(ts_utc_str):
    """Spotpris UTC → lokal tid 15-min nyckel."""
    dt = datetime.fromisoformat(ts_utc_str[:19]).replace(tzinfo=timezone.utc)
    loc = dt + timedelta(hours=_se_offset(dt))
    m = (loc.minute // 15) * 15
    return loc.strftime(f"%Y-%m-%dT%H:{m:02d}")

def _energy_key(ts_utc_str):
    """Energimätning face-value (lokal tid) → 15-min nyckel."""
    ts = ts_utc_str[:19]
    m = (int(ts[14:16]) // 15) * 15
    return f"{ts[:13]}:{m:02d}"


# ── Hjälp: alla månader i intervall ─────────────────────────────────────────

def _months_in_range(first, last):
    """Returnerar [first, ..., last] som YYYY-MM-strängar."""
    result = []
    y, mo = int(first[:4]), int(first[5:7])
    ey, emo = int(last[:4]), int(last[5:7])
    while (y, mo) <= (ey, emo):
        result.append(f"{y:04d}-{mo:02d}")
        mo += 1
        if mo > 12:
            mo, y = 1, y + 1
    return result

def _prev_month(ym):
    y, m = int(ym[:4]), int(ym[5:7])
    return f"{y-1}-12" if m == 1 else f"{y:04d}-{m-1:02d}"


# ── Beräkna rådata för ett månadsintervall ──────────────────────────────────

def _fetch_and_compute(pub_db, from_iso, to_iso):
    """Hämtar energi+priser för [from_iso, to_iso) och returnerar
    dict {YYYY-MM: {total_kwh, total_cost, avg_price_ore, readings, partial, devices}}."""

    # Energidata
    energy_rows = []
    offset = 0
    while True:
        res = (pub_db.table("energy_readings")
               .select("device_name, timestamp, current_value")
               .gte("timestamp", from_iso)
               .lt("timestamp", to_iso)
               .order("timestamp", desc=False)
               .range(offset, offset + PAGE_SIZE - 1)
               .execute())
        energy_rows.extend(res.data)
        if len(res.data) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    # Spotpriser (börja 2h tidigt för CEST-täckning)
    price_start = (datetime.fromisoformat(from_iso[:19]).replace(tzinfo=timezone.utc)
                   - timedelta(hours=2)).isoformat()
    price_rows = []
    offset = 0
    while True:
        res = (pub_db.table("spot_prices")
               .select("timestamp, price_sek")
               .gte("timestamp", price_start)
               .lt("timestamp", to_iso)
               .order("timestamp", desc=False)
               .range(offset, offset + PAGE_SIZE - 1)
               .execute())
        price_rows.extend(res.data)
        if len(res.data) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    # Bygg 15-min pris-lookup (lokal tid)
    price_by_15min = {}
    for p in price_rows:
        ore = p["price_sek"]
        key = _price_key(p["timestamp"])
        price_by_15min[key] = ore
        if key[14:16] == "00":          # timvisa priser: pre-fyll övriga kvarter
            h = key[:13]
            for m in (15, 30, 45):
                qk = f"{h}:{m:02d}"
                if qk not in price_by_15min:
                    price_by_15min[qk] = ore

    # Aggregera per månad
    monthly = {}
    readings_by_month = {}
    for r in energy_rows:
        ts     = r["timestamp"]
        mon    = ts[:7]
        ekey   = _energy_key(ts)
        device = r["device_name"]
        kwh    = (r["current_value"] or 0) * 0.25 / 1000
        p_ore  = price_by_15min.get(ekey)
        cost   = kwh * p_ore / 100 if p_ore is not None else None

        if mon not in monthly:
            monthly[mon] = {}
            readings_by_month[mon] = 0
        if device not in monthly[mon]:
            monthly[mon][device] = {"kwh": 0.0, "cost": 0.0,
                                    "kwh_priced": 0.0, "wp": 0.0}
        d = monthly[mon][device]
        d["kwh"] += kwh
        if cost is not None:
            d["cost"]       += cost
            d["kwh_priced"] += kwh
            d["wp"]         += kwh * p_ore
        readings_by_month[mon] += 1

    # Formatera
    results = {}
    for mon, devs in monthly.items():
        tkwh  = sum(d["kwh"]  for d in devs.values())
        tcost = sum(d["cost"] for d in devs.values())
        tkp   = sum(d["kwh_priced"] for d in devs.values())
        twp   = sum(d["wp"]   for d in devs.values())
        avg_p = twp / tkp if tkp > 0 else None

        yr, mo = int(mon[:4]), int(mon[5:7])
        days = calendar.monthrange(yr, mo)[1]
        expected = 4 * 24 * days * len(devs)
        partial = readings_by_month[mon] / max(expected, 1) < 0.5

        results[mon] = {
            "total_kwh":     round(tkwh, 1),
            "total_cost":    round(tcost, 0),
            "avg_price_ore": round(avg_p, 1) if avg_p is not None else None,
            "readings":      readings_by_month[mon],
            "partial":       partial,
            "devices": {
                name: {"kwh": round(d["kwh"], 1), "cost": round(d["cost"], 0)}
                for name, d in devs.items()
            }
        }
    return results


# ── Handler ─────────────────────────────────────────────────────────────────

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            now     = datetime.now(timezone.utc)
            cur_mon = now.strftime("%Y-%m")
            prev_mon = _prev_month(cur_mon)

            # Alla månader vi vill visa (nov 2025 → idag)
            all_months = _months_in_range(FIRST_MONTH, cur_mon)
            completed  = [m for m in all_months if m < cur_mon]  # ej innevarande

            db     = get_db()       # skrivrättigheter (cache-uppdatering)
            pub_db = get_public_db()

            # ── 1. Läs cache ───────────────────────────────────────────────
            cached = {}
            if completed:
                res = (db.table("monthly_summaries")
                       .select("*")
                       .in_("month", completed)
                       .execute())
                for row in res.data:
                    cached[row["month"]] = row

            # ── 2. Beräkna saknade avslutade månader och spara ─────────────
            missing = [m for m in completed if m not in cached]
            if missing:
                # Hämta rådata för alla saknade månader i ett svep
                first_missing = missing[0]
                last_missing  = missing[-1]
                # to_iso = första dagen månaden EFTER sista saknade
                lm_y, lm_mo = int(last_missing[:4]), int(last_missing[5:7])
                if lm_mo == 12:
                    next_y, next_mo = lm_y + 1, 1
                else:
                    next_y, next_mo = lm_y, lm_mo + 1
                from_iso = f"{first_missing}-01T00:00:00+00:00"
                to_iso   = f"{next_y:04d}-{next_mo:02d}-01T00:00:00+00:00"

                computed = _fetch_and_compute(pub_db, from_iso, to_iso)

                # Spara bara avslutade månader (ej innevarande) i cache
                to_upsert = []
                for mon in missing:
                    if mon in computed:
                        row = computed[mon]
                        to_upsert.append({
                            "month":         mon,
                            "total_kwh":     row["total_kwh"],
                            "total_cost":    row["total_cost"],
                            "avg_price_ore": row["avg_price_ore"],
                            "readings":      row["readings"],
                            "partial":       row["partial"],
                            "devices":       row["devices"],
                        })
                        cached[mon] = row   # lägg direkt i lokalt cache

                if to_upsert:
                    db.table("monthly_summaries").upsert(
                        to_upsert, on_conflict="month"
                    ).execute()

            # ── 3. Beräkna innevarande månad live ─────────────────────────
            cur_from = f"{cur_mon}-01T00:00:00+00:00"
            # to_iso för nästa månad
            cy, cmo = int(cur_mon[:4]), int(cur_mon[5:7])
            if cmo == 12:
                nxt = f"{cy+1:04d}-01-01T00:00:00+00:00"
            else:
                nxt = f"{cy:04d}-{cmo+1:02d}-01T00:00:00+00:00"
            cur_computed = _fetch_and_compute(pub_db, cur_from, nxt)
            cur_data = cur_computed.get(cur_mon)

            # ── 4. Bygg svar ───────────────────────────────────────────────
            result_list = []
            for mon in all_months:
                if mon == cur_mon:
                    if cur_data:
                        result_list.append({
                            "month": mon, "is_current": True, "no_data": False,
                            "partial": True,   # innevarande är alltid "pågående"
                            **cur_data
                        })
                    else:
                        result_list.append({
                            "month": mon, "is_current": True, "no_data": True,
                            "total_kwh": None, "total_cost": None,
                            "avg_price_ore": None, "readings": 0, "devices": {}
                        })
                elif mon in cached:
                    row = cached[mon]
                    result_list.append({
                        "month": mon, "is_current": False, "no_data": False,
                        "partial":       row.get("partial", False),
                        "total_kwh":     row.get("total_kwh"),
                        "total_cost":    row.get("total_cost"),
                        "avg_price_ore": row.get("avg_price_ore"),
                        "readings":      row.get("readings", 0),
                        "devices":       row.get("devices", {}),
                    })
                else:
                    # Ingen data alls för denna månad (beräknades men saknad)
                    result_list.append({
                        "month": mon, "is_current": False, "no_data": True,
                        "total_kwh": None, "total_cost": None,
                        "avg_price_ore": None, "readings": 0, "devices": {}
                    })

            result_list.reverse()   # Nyast först

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(result_list).encode())

        except Exception as e:
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    def log_message(self, format, *args):
        pass
