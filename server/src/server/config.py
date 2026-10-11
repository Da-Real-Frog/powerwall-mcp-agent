"""Site layout (site.toml) and runtime settings (environment variables)."""
import os
import tomllib
from dataclasses import dataclass
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


@dataclass(frozen=True)
class Site:
    derate: float
    panel_watts: float
    arrays: tuple[Array, ...]
    inputs: tuple[Input, ...]
    unused_inputs: frozenset[str] = frozenset()  # gateway letters with nothing wired to them


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
    except (KeyError, TypeError, ValueError) as e:
        raise ConfigError(f"Site file {path} is missing or has an invalid field: {e}") from e

    if not arrays:
        raise ConfigError(f"Site file {path} defines no arrays")
    if not 0 < derate <= 1:
        raise ConfigError(f"derate must be between 0 and 1, got {derate}")

    letters = [i.input for i in inputs if i.input]
    if len(letters) != len(set(letters)):
        raise ConfigError("Each input letter can be mapped only once")
    if unused & set(letters):
        raise ConfigError(f"Inputs listed as unused are also mapped: {sorted(unused & set(letters))}")

    names = {a.name for a in arrays}
    for i in inputs:
        if i.array not in names:
            raise ConfigError(f"Input on Powerwall {i.powerwall} refers to unknown array '{i.array}'")
    if inputs:
        for a in arrays:
            wired = sum(i.panels_per_string * i.strings for i in inputs if i.array == a.name)
            if wired != a.panels:
                raise ConfigError(f"Array {a.name} has {a.panels} panels but its inputs wire {wired}")

    return Site(derate=derate, panel_watts=panel_watts, arrays=arrays, inputs=inputs, unused_inputs=unused)


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
