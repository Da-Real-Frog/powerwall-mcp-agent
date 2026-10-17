"""Site layout (site.toml) and runtime settings (environment variables)."""
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_SITE_FILE = Path(__file__).resolve().parents[2] / "site.toml"


class ConfigError(Exception):
    """Raised when site.toml or a required environment variable is missing or invalid."""


@dataclass(frozen=True)
class Array:
    name: str
    panels: int
    tilt: float
    azimuth: float  # compass bearing, 0 = North, 180 = South
    kwp: float

    @property
    def open_meteo_azimuth(self) -> float:
        """Open-Meteo measures azimuth from South: 0 = South, -90 = East, 90 = West."""
        return (self.azimuth % 360) - 180


@dataclass(frozen=True)
class Input:
    powerwall: int
    input: str
    array: str
    panels_per_string: int
    strings: int

    @property
    def key(self) -> str:
        """The name the collector records this input under, such as "PW2-A", or "" while unmapped."""
        return f"PW{self.powerwall}-{self.input}" if self.input else ""


@dataclass(frozen=True)
class ReserveSettings:
    """Reserve percentages per rule and the controller's safeguards, from [reserve] in site.toml."""
    warning: float = 100
    watch: float = 80
    low_solar: float = 60
    partial: float = 40
    default: float = 20
    rain: float = 40  # rule 7: the least reserve when rain is likely
    rain_chance: float = 20  # % chance of rain in any of the next 24 hours above which rule 7 applies
    warning_hold_hours: float = 6
    battery_kwh: float = 27
    daytime_use_kwh: float = 15  # used until 14 days of history exist
    overnight_use_kwh: float = 10
    min_change: float = 5
    min_interval_minutes: float = 60
    manual_pause_hours: float = 24


@dataclass(frozen=True)
class Site:
    derate: float
    panel_watts: float
    arrays: tuple[Array, ...]
    inputs: tuple[Input, ...]
    unused_inputs: frozenset[str] = frozenset()  # input names ("PW2-C") with nothing wired to them
    inverters: dict[str, int] = field(default_factory=dict)  # inverter serial suffix -> Powerwall number
    local_api_powerwall: int = 1  # the Powerwall whose inputs the gateway's local API reports
    reserve: ReserveSettings = ReserveSettings()


def load_site(path: str | Path | None = None) -> Site:
    """Reads and checks the site layout. The panels wired to inputs must add up to each array's panel count."""
    path = Path(path or os.getenv("SITE_CONFIG") or DEFAULT_SITE_FILE)
    try:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
    except FileNotFoundError:
        raise ConfigError(f"Site file not found: {path}") from None
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"Site file {path} is not valid TOML: {e}") from e

    try:
        panel_watts = float(raw["panel"]["watts"])
        derate = float(raw.get("derate", 1.0))
        arrays = tuple(
            Array(
                name=a["name"],
                panels=int(a["panels"]),
                tilt=float(a["tilt"]),
                azimuth=float(a["azimuth"]),
                kwp=int(a["panels"]) * panel_watts / 1000,
            )
            for a in raw["arrays"]
        )
        inputs = tuple(
            Input(
                powerwall=int(i["powerwall"]),
                input=str(i.get("input", "")),
                array=i["array"],
                panels_per_string=int(i["panels_per_string"]),
                strings=int(i.get("strings", 1)),
            )
            for i in raw.get("inputs", [])
        )
        unused = frozenset(str(x) for x in raw.get("unused_inputs", []))
        inverters = {str(k): int(v) for k, v in raw.get("inverters", {}).items()}
        local_api_powerwall = int(raw.get("local_api_powerwall", 1))
        reserve = ReserveSettings(**{k: float(v) for k, v in raw.get("reserve", {}).items()})
    except (KeyError, TypeError, ValueError) as e:
        raise ConfigError(f"Site file {path} is missing or has an invalid field: {e}") from e
    for name in ("warning", "watch", "low_solar", "partial", "default", "rain", "rain_chance"):
        if not 0 <= getattr(reserve, name) <= 100:
            raise ConfigError(f"reserve.{name} must be between 0 and 100")

    if not arrays:
        raise ConfigError(f"Site file {path} defines no arrays")
    if not 0 < derate <= 1:
        raise ConfigError(f"derate must be between 0 and 1, got {derate}")

    keys = [i.key for i in inputs if i.key]
    if len(keys) != len(set(keys)):
        raise ConfigError("Each Powerwall input letter can be mapped only once")
    if unused & set(keys):
        raise ConfigError(f"Inputs listed as unused are also mapped: {sorted(unused & set(keys))}")

    names = {a.name for a in arrays}
    for i in inputs:
        if i.array not in names:
            raise ConfigError(f"Input on Powerwall {i.powerwall} refers to unknown array '{i.array}'")
    if inputs:
        for a in arrays:
            wired = sum(i.panels_per_string * i.strings for i in inputs if i.array == a.name)
            if wired != a.panels:
                raise ConfigError(f"Array {a.name} has {a.panels} panels but its inputs wire {wired}")

    return Site(derate=derate, panel_watts=panel_watts, arrays=arrays, inputs=inputs, unused_inputs=unused,
                inverters=inverters, local_api_powerwall=local_api_powerwall, reserve=reserve)


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigError(f"{name} is not set in the environment")
    return value


def location() -> tuple[float, float]:
    """Home latitude and longitude. Kept out of the repo on purpose: set SOLAR_LAT and SOLAR_LON."""
    try:
        return float(require_env("SOLAR_LAT")), float(require_env("SOLAR_LON"))
    except ValueError:
        raise ConfigError("SOLAR_LAT and SOLAR_LON must be numbers") from None


def timezone() -> str:
    return os.getenv("PW_TIMEZONE", "America/Chicago")
