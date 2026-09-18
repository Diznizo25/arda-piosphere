"""Rain-outlook logic tests. Pure: no DB, no network, no GEE.

Run: python scripts/test_rain_outlook.py
"""
from __future__ import annotations

import sys
from calendar import monthrange
from datetime import date, timedelta

sys.path.insert(0, ".")

from app.services import forecast as f  # noqa: E402

DIM = lambda y, m: monthrange(y, m)[1]  # noqa: E731

# --- 1) dry spell length -----------------------------------------------------
assert f.dry_spell_days([0, 0, 3.0, 0, 0]) == 2          # last rain 2 days ago
assert f.dry_spell_days([0, 0, 0]) == 3                  # "at least 3 days"
assert f.dry_spell_days([]) == 0
assert f.dry_spell_days([5.0]) == 0                      # rained on the last day
assert f.dry_spell_days([0.4, 0.4, 0.4]) == 3            # drizzle is not "real rain"
print("dry-spell length OK")

# --- 2) deficit is relative to THIS place and month --------------------------
# 30-day window crossing Aug/Sep, normals 10 mm (Aug) and 60 mm (Sep).
clim = {8: 10.0, 9: 60.0}
# 30-day window starting 2026-08-15 = 17 days of Aug + 14 days of Sep, so the
# normal is 10*17/31 + 60*14/30 = 33.48 mm: a weighted blend, not a flat mean.
normal = f.normal_for_window(clim, date(2026, 8, 15), 31, DIM)
assert 33.0 < normal < 34.0, normal
assert f.rain_deficit_pct(20.0, 50.0) == -60.0
assert f.rain_deficit_pct(20.0, None) is None            # never divide by a guess
assert f.rain_deficit_pct(20.0, 0.0) is None
assert f.normal_for_window({}, date(2026, 8, 15), 31, DIM) is None
# Same 20 mm in a month whose normal is 5 mm is a WET month, not a drought.
assert f.rain_deficit_pct(20.0, 5.0) == 300.0
print("climatology weighting + deficit OK")

# --- 3) onset detection ------------------------------------------------------
dry = [{"date": f"2026-09-{d:02d}", "rain_mm": 0.0} for d in range(1, 11)]
assert f.forecast_onset(dry) is None                     # no onset inside horizon
wet = dry + [{"date": "2026-09-11", "rain_mm": 2.0},
             {"date": "2026-09-12", "rain_mm": 3.0}]
# The onset is the first day with real rain inside the qualifying 3-day window
# (11th + 12th = 2 + 3 mm), so it matches what a herder would see on the ground -
# never "it will rain on this day".
assert f.forecast_onset(wet) == date(2026, 9, 11)
assert f.forecast_onset([{"date": "x", "rain_mm": 99.0}]) is None  # junk ignored
assert f.forecast_onset([{"date": "2026-09-01", "rain_mm": None}]) is None
print("onset detection OK")

# --- 4) staleness: an expired forecast is dropped, never narrated ------------
stale = f.build_outlook(
    recent_rain=[0.0] * 30, climatology={9: 30.0},
    daily_forecast=[{"date": "2026-09-01", "rain_mm": 40.0}],
    window_days=30, as_of=date(2026, 9, 30),
    forecast_generated_on=date(2026, 9, 1), days_in_month=DIM)
assert stale.has_forecast is False
assert stale.forecast_total_mm is None
assert "40" not in (f.rain_line(stale) or "")
print("stale forecast dropped OK")

# --- 5) the assembled outlook, and what the line actually says --------------
o = f.build_outlook(
    recent_rain=[0.0] * 30, climatology={9: 40.0},
    daily_forecast=(
        [{"date": (date(2026, 9, 18) + timedelta(days=i)).isoformat(), "rain_mm": 0.2}
         for i in range(16)]
        + [{"date": "2026-10-04", "rain_mm": 9.0},
           {"date": "2026-10-05", "rain_mm": 6.0}]
    ),
    window_days=30, as_of=date(2026, 9, 17),
    forecast_generated_on=date(2026, 9, 17), days_in_month=DIM)
assert o.dry_spell_days == 30 and o.rain_30d_mm == 0.0
assert o.normal_30d_mm and o.deficit_pct == -100.0
assert f.outlook_severity(o) == "very_dry"
assert o.has_forecast and o.onset_date == date(2026, 10, 4)
# Onset is 17 days out -> NOT high confidence, and must not be stated as a date.
assert o.confidence == "moderate"
line = f.rain_line(o, "swahili")
assert line.startswith("Mvua: "), line
assert "Siku 30 bila mvua" in line, line
assert "pungufu sana" in line, line
assert "makadirio" in line and "Utabiri" in line, line
assert "Oktoba" not in line and "10-04" not in line, line  # no bare date promise
eng = f.rain_line(o, "english")
assert eng.startswith("Rain: ") and "rough estimate" in eng, eng
print("assembled outlook + wording OK")

# --- 6) severity buckets are conservative ------------------------------------
assert f.outlook_severity(f.RainOutlook(deficit_pct=-29.9)) == "normal"
assert f.outlook_severity(f.RainOutlook(deficit_pct=-30.0)) == "dry"
assert f.outlook_severity(f.RainOutlook(deficit_pct=-59.9)) == "dry"
assert f.outlook_severity(f.RainOutlook(deficit_pct=-60.0)) == "very_dry"
assert f.outlook_severity(f.RainOutlook(deficit_pct=None)) == "normal"
print("severity buckets OK")

# --- 6b) a deficit against a negligible normal is the DRY SEASON, not drought -
# The real case from live data: Lengwenyi, late September. 0.1 mm in 30 days
# against a 30-day normal of 6.9 mm is -98%, yet it is the ordinary dry season
# before the short rains. Calling that a drought would be dishonest and would
# teach herders to ignore us.
dry_season = f.RainOutlook(dry_spell_days=30, rain_30d_mm=0.1, normal_30d_mm=6.87,
                           deficit_pct=-98.5, has_forecast=True, horizon_days=15,
                           forecast_total_mm=1.3, generated_on=date(2026, 9, 17))
assert f.outlook_severity(dry_season) == "dry_season"
line_ds = f.rain_line(dry_season, "swahili")
assert "Mvua ni kidogo, lakini huu ni msimu wa kiangazi wa kawaida." in line_ds, line_ds
assert "hakuna mvua inayotarajiwa" in line_ds, line_ds
# No double-warning about drought in a dry month.
assert "kausha linaendelea" not in line_ds, line_ds
assert f.outlook_severity(f.RainOutlook(deficit_pct=-98.5, normal_30d_mm=60.0)) == "very_dry"
msg_ds = f.mvua_message(dry_season, lang="swahili")
assert "Mvua ni kidogo, lakini huu ni msimu wa kiangazi wa kawaida." in msg_ds, msg_ds
assert "hakuna mvua inayotarajiwa" in msg_ds, msg_ds
print("dry-season vs drought distinction OK")


# --- 7) fail-open: nothing stored yet means nothing is claimed ---------------
empty = f.build_outlook(recent_rain=[], climatology={}, daily_forecast=[],
                        window_days=30, as_of=date(2026, 9, 17), days_in_month=DIM)
assert empty.dry_spell_days is None and empty.has_forecast is False
assert f.rain_line(empty) is None
assert f.rain_line(None) is None
m = f.mvua_message(None)
assert "hatuna data ya mvua" in m
print("fail-open with no data OK")

# --- 8) the 'mvua' answer carries numbers + an action ------------------------
msg = f.mvua_message(o, place="Lengwenyi", lang="swahili")
assert "MVUA (Lengwenyi)" in msg
assert "kawaida 23 mm" in msg
assert "Ushauri:" in msg and "hamia taratibu" in msg
assert "Utabiri" in msg and "makadirio" in msg
print("mvua message OK")

print("rain-outlook tests OK")

# --- 9) the wiring other modules must not silently lose ----------------------
# The advisory reads the stored outlook and appends the line; the WhatsApp handler
# routes 'mvua' to it. Both are pure string/structure checks, no DB or network.
import io  # noqa: E402

adv_src = io.open("app/services/advisory_service.py", encoding="utf-8").read()
assert "environment.outlook(" in adv_src, "advisory must read the stored rain outlook"
assert "rain_line(" in adv_src, "advisory must append the rain line"
assert "rain_dry_spell_days=" in adv_src, "AdvisoryResult must expose the rain fields"
# Fail-open: the outlook read is wrapped in try/except, so a DB/weather problem
# can never break an advisory (the except must therefore follow the call).
_after_outlook = adv_src.split("environment.outlook(")[1][:900]
assert "except Exception" in _after_outlook, \
    "the rain lookup must be fail-open (no stored data still answers)"

wa_src = io.open("app/routers/whatsapp.py", encoding="utf-8").read()
assert "RAIN_KEYWORDS" in wa_src and "_handle_rain_request" in wa_src
assert '"8": "rain"' in wa_src, "menu slot 8 must be the rain service"
assert "mvua_message" in wa_src, "the mvua service must use the honest formatter"

from app.services import environment as env  # noqa: E402

# The request path must never fetch weather live: only the refresh script fetches.
assert env.FORECAST_DAYS >= 10 and env.PAST_DAYS >= 30
assert "def fetch_open_meteo" in io.open(
    "app/services/environment.py", encoding="utf-8").read()
print("advisory + WhatsApp + menu wiring OK")

# --- 10) the COG archive is what makes the past survive ----------------------
from app.services.storage import cog_archive_key, cog_key  # noqa: E402

assert cog_key("abc") == "cogs/abc/indices.tif"
assert cog_archive_key("abc", "2026-09-18") == "cogs/abc/archive/indices_2026-09-18.tif"
assert cog_archive_key("abc", "2026-09-18") != cog_key("abc"), \
    "an archive key must never collide with the live key"
transfer_src = io.open("scripts/transfer_assets_to_r2.py", encoding="utf-8").read()
assert "archive_current_cog(" in transfer_src and "_set_indices_as_of(" in transfer_src
print("dated COG archive wiring OK")

