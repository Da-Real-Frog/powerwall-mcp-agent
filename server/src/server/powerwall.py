"""One shared, lazily created connection to the local Powerwall gateway."""
import os
import threading
import time

import pypowerwall
from pypowerwall.tedapi import TEDAPI

from .config import Site, require_env, timezone

_lock = threading.Lock()
_pw: pypowerwall.Powerwall | None = None
_tedapi: TEDAPI | None = None
_tedapi_failures = 0
_tedapi_retry_at = 0.0  # time.monotonic() before which TEDAPI is left alone

TEDAPI_BACKOFF_FIRST = 60  # seconds to wait after the first failure; doubles on each failure after that
TEDAPI_BACKOFF_MAX = 300  # the longest wait, kept within health.STALE_SECONDS so a short outage clears quickly


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


def get_cloud_connection() -> pypowerwall.Powerwall:
    """Tesla cloud connection, used only as the fallback for reserve writes.

    Needs a one-time sign-in that saves tokens in PW_CACHE_DIR (see the README); without it this fails.
    """
    cache_dir = os.getenv("PW_CACHE_DIR", ".")
    return pypowerwall.Powerwall(host="", email=require_env("PW_EMAIL"), timezone=timezone(), cloudmode=True,
                                 authpath=cache_dir, cachefile=os.path.join(cache_dir, ".powerwall-cloud"))


def tedapi_enabled() -> bool:
    return bool(os.getenv("PW_GW_PWD", "").strip())


def get_tedapi() -> TEDAPI:
    """TEDAPI session at the gateway's internal address (192.168.91.1), logged in with the label password.

    It reports every input on both inverters, where the local API on current firmware reports only one.
    """
    global _tedapi
    with _lock:
        if _tedapi is None:
            api = TEDAPI(require_env("PW_GW_PWD"), timezone=timezone())
            if not api.connect():
                raise PowerwallError("Could not log in to TEDAPI at 192.168.91.1 (check the route and PW_GW_PWD)")
            _tedapi = api
        return _tedapi


def reset_tedapi() -> None:
    """Drops the session so the next read logs in again, for example after the bridge comes back."""
    global _tedapi
    with _lock:
        _tedapi = None


def strings_from_vitals(vitals: dict, site: Site) -> dict:
    """Per-input readings from TEDAPI vitals, named "PW<n>-<letter>" from the site's inverter serials.

    An inverter missing from site.inverters is named by its serial suffix instead.
    """
    result = {}
    for device, values in vitals.items():
        if not device.startswith("PVAC--"):
            continue
        suffix = device[-6:]
        number = site.inverters.get(suffix)
        prefix = f"PW{number}" if number else suffix
        for key, value in values.items():
            for field, prefix_key in (("Voltage", "PVAC_PVMeasuredVoltage_"), ("Current", "PVAC_PVCurrent_"),
                                      ("Power", "PVAC_PVMeasuredPower_"), ("State", "PVAC_PvState_")):
                if key.startswith(prefix_key) and len(key) == len(prefix_key) + 1:
                    result.setdefault(f"{prefix}-{key[-1]}", {})[field] = value
    return result


def _tedapi_failed() -> None:
    """Drops the session and waits longer before the next login, so a struggling gateway isn't hit every poll."""
    global _tedapi_failures, _tedapi_retry_at
    reset_tedapi()
    _tedapi_failures += 1
    delay = min(TEDAPI_BACKOFF_FIRST * 2 ** (_tedapi_failures - 1), TEDAPI_BACKOFF_MAX)
    _tedapi_retry_at = time.monotonic() + delay


def read_strings(pw, site: Site) -> dict:
    """Every input's readings: over TEDAPI when PW_GW_PWD is set, else the local API's single inverter.

    After a TEDAPI failure it skips TEDAPI for 1, 2, 4, then at most 5 minutes, raising without contacting it.
    """
    global _tedapi_failures
    if tedapi_enabled():
        wait = _tedapi_retry_at - time.monotonic()
        if wait > 0:
            raise PowerwallError(f"TEDAPI failed {_tedapi_failures} time(s); next try in {int(wait) + 1}s")
        try:
            vitals = get_tedapi().vitals()
        except Exception:
            _tedapi_failed()
            raise
        if not vitals:
            _tedapi_failed()
            raise PowerwallError("TEDAPI returned no vitals")
        _tedapi_failures = 0
        return strings_from_vitals(vitals, site)
    return {f"PW{site.local_api_powerwall}-{name}": values for name, values in (pw.strings() or {}).items()}
