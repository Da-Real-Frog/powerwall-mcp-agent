"""One shared, lazily created connection to the local Powerwall gateway."""
import os
import threading

import pypowerwall

from .config import require_env, timezone

_lock = threading.Lock()
_pw: pypowerwall.Powerwall | None = None


class PowerwallError(Exception):
    """Raised when the gateway cannot be reached or returns no data."""


def get_connection() -> pypowerwall.Powerwall:
    """Logs in once and reuses the session. The auth cache lives in PW_CACHE_DIR so it survives restarts."""
    global _pw
    with _lock:
        if _pw is None:
            cache_dir = os.getenv("PW_CACHE_DIR", ".")
            os.makedirs(cache_dir, exist_ok=True)
            pw = pypowerwall.Powerwall(
                host=require_env("PW_HOST"),
                password=require_env("PW_PASSWORD"),
                email=require_env("PW_EMAIL"),
                timezone=timezone(),
                cachefile=os.path.join(cache_dir, ".powerwall"),
                authpath=cache_dir,
            )
            if not pw.is_connected():
                raise PowerwallError(f"Could not log in to the Powerwall gateway at {os.getenv('PW_HOST')}")
            _pw = pw
        return _pw


def read_status(pw) -> dict:
    """Current readings. pypowerwall returns None instead of raising when a read fails, so None is an error here."""
    status = {
        "battery_level_percent": pw.level(scale=True),  # the Tesla app's scale, which hides the bottom 5%
        "grid_status": pw.grid_status(),  # "UP", "DOWN" or "SYNCING"
        "grid_power_watts": pw.grid(),  # positive = importing, negative = exporting
        "solar_power_watts": pw.solar(),
        "home_usage_watts": pw.home(),
        "battery_power_watts": pw.battery(),
    }
    missing = [name for name, value in status.items() if value is None]
    if missing:
        raise PowerwallError(f"The gateway returned no value for: {', '.join(missing)}")
    return status
