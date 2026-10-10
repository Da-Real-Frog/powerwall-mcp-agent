from datetime import datetime

import pytest

from server.config import DEFAULT_SITE_FILE, load_site
from server.forecast import build_forecast


def _times(days=3):
    return [f"2026-10-{10 + d:02d}T{h:02d}:00" for d in range(days) for h in range(24)]


def _flat(value):
    times = _times()
    gti = [value if 8 <= int(t[11:13]) < 18 else 0 for t in times]
    return times, gti


def test_starts_at_current_hour_and_covers_48_hours():
    site = load_site(DEFAULT_SITE_FILE)
    per_array = {a.name: _flat(500) for a in site.arrays}
    result = build_forecast(site, per_array, datetime(2026, 10, 10, 14, 30))

    assert result["hourly"][0]["time"] == "2026-10-10T14:00"
    assert result["hourly"][-1]["time"] == "2026-10-12T13:00"
    assert list(result["daily_kwh"]) == ["2026-10-10", "2026-10-11", "2026-10-12"]


def test_watts_use_kwp_and_derate():
    site = load_site(DEFAULT_SITE_FILE)
    per_array = {a.name: _flat(1000) for a in site.arrays}
    result = build_forecast(site, per_array, datetime(2026, 10, 10, 0, 0))

    noon = next(h for h in result["hourly"] if h["time"] == "2026-10-10T12:00")
    # 12.8 kWp at 1000 W/m2 with the 0.85 derate
    assert noon["expected_watts"] == round(12800 * site.derate)
    assert noon["per_array_watts"]["MP2"] == round(1200 * site.derate)
    # 10 sunny hours a day
    assert result["daily_kwh"]["2026-10-10"] == pytest.approx(12.8 * site.derate * 10, abs=0.1)


def test_missing_irradiance_counts_as_zero_and_night_is_dropped():
    site = load_site(DEFAULT_SITE_FILE)
    times, gti = _flat(400)
    gti = [None if g == 0 else g for g in gti]
    per_array = {a.name: (times, gti) for a in site.arrays}
    result = build_forecast(site, per_array, datetime(2026, 10, 10, 0, 0))
    assert all(h["expected_watts"] > 0 for h in result["hourly"])
    assert len(result["hourly"]) == 20


def test_mismatched_time_axes_are_rejected():
    site = load_site(DEFAULT_SITE_FILE)
    per_array = {a.name: _flat(500) for a in site.arrays}
    times, gti = per_array["MP3"]
    per_array["MP3"] = (times[1:], gti[1:])
    with pytest.raises(ValueError, match="MP3"):
        build_forecast(site, per_array, datetime(2026, 10, 10, 0, 0))
