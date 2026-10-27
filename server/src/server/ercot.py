"""ERCOT grid conditions from the public dashboard behind ercot.com's Grid Conditions page (no API key).

ERCOT publishes no feed for its TXANS notices (Weather Watch, Conservation Appeal), so this reads the
current condition and reserves the dashboard shows. Energy Emergency Alerts start when reserves stay
below 2,500 MW; ERCOT's older "Watch" level was below 3,000 MW.
"""
import httpx

GRID_CONDITIONS_URL = "https://www.ercot.com/api/1/services/read/dashboards/daily-prc.json"
EMERGENCY_MW = 2500
TIGHT_MW = 3000


def parse_condition(data: dict) -> dict:
    c = data.get("current_condition") or {}
    try:
        reserves = float(str(c.get("prc_value")).replace(",", ""))
    except ValueError:
        reserves = None
    return {
        "state": str(c.get("state") or ""),  # "normal" on a normal day
        "title": c.get("title"),
        "reserves_mw": reserves,
        "note": c.get("condition_note"),
    }


def classify(condition: dict) -> tuple[str | None, str | None]:
    """Returns (emergency, tight): a label for an Energy Emergency Alert, or for any other condition
    that is not normal (a conservation appeal, a watch, reserves under TIGHT_MW). None when normal."""
    text = f"{condition.get('state') or ''} {condition.get('title') or ''}".strip()
    lowered = text.lower()
    name = condition.get("title") or condition.get("state")
    mw = condition.get("reserves_mw")
    if "eea" in lowered or "emergency" in lowered or (mw is not None and mw < EMERGENCY_MW):
        # No reserves figure in this label: it keys the warning hold, so it has to stay the same between checks
        return f"ERCOT {name or f'reserves below {EMERGENCY_MW:,} MW'}", None
    if (lowered and "normal" not in lowered) or (mw is not None and mw < TIGHT_MW):
        label = f"ERCOT {name or 'reserves tight'}"
        return None, label + (f" ({mw:,.0f} MW reserves)" if mw is not None else "")
    return None, None


def get_condition() -> dict:
    # ercot.com turns away requests without a browser-like User-Agent
    response = httpx.get(GRID_CONDITIONS_URL, headers={"User-Agent": "Mozilla/5.0 (Powerwall-MCP-Agent)"},
                         timeout=10.0)
    response.raise_for_status()
    return parse_condition(response.json())
