import pytest

from server import powerwall
from server.config import load_site
from server.powerwall import PowerwallError, read_status, read_strings, strings_from_vitals


class FakePowerwall:
    def __init__(self, **overrides):
        self.values = {"level": 80.0, "grid_status": "UP", "grid": -1200.0, "solar": 5000.0, "home": 1500.0,
                       "battery": -2300.0, **overrides}

    def level(self, scale=False):
        return self.values["level"]

    def grid_status(self):
        return self.values["grid_status"]

    def grid(self):
        return self.values["grid"]

    def solar(self):
        return self.values["solar"]

    def home(self):
        return self.values["home"]

    def battery(self):
        return self.values["battery"]


def test_reads_grid_status_separately_from_grid_power():
    status = read_status(FakePowerwall())
    assert status["grid_status"] == "UP"
    assert status["grid_power_watts"] == -1200.0


def test_none_from_gateway_is_an_error():
    with pytest.raises(PowerwallError, match="solar_power_watts"):
        read_status(FakePowerwall(solar=None))


def test_strings_from_vitals_are_named_per_powerwall():
    vitals = {
        "PVAC--1538000-25-F--TG12xxxxE005VD": {
            "PVAC_PvState_A": "PV_Active", "PVAC_PVMeasuredVoltage_A": 199.0, "PVAC_PVCurrent_A": 2.3,
            "PVAC_PVMeasuredPower_A": 460.0, "PVAC_PvState_C": "PV_Disabled", "PVAC_Pout": 900.0,
        },
        "PVAC--1538000-25-F--TG12xxxxH000RA": {"PVAC_PVMeasuredPower_B": 300.0},
        "PVAC--1538000-25-F--TG12xxxxZZZZZZ": {"PVAC_PVMeasuredPower_D": 5.0},
        "TETHC--x": {"PVAC_PVMeasuredPower_A": 1.0},
    }
    strings = strings_from_vitals(vitals, load_site())
    assert strings == {
        "PW2-A": {"State": "PV_Active", "Voltage": 199.0, "Current": 2.3, "Power": 460.0},
        "PW2-C": {"State": "PV_Disabled"},
        "PW1-B": {"Power": 300.0},
        "ZZZZZZ-D": {"Power": 5.0},  # an inverter missing from site.toml keeps its serial suffix
    }


class FakeTEDAPI:
    def __init__(self, results):
        self.results = results  # one entry per vitals() call: a dict, None, or an exception to raise
        self.calls = 0

    def vitals(self):
        result = self.results[self.calls]
        self.calls += 1
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture
def tedapi(monkeypatch):
    clock = {"now": 1000.0}
    monkeypatch.setenv("PW_GW_PWD", "x")
    monkeypatch.setattr(powerwall.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(powerwall, "_tedapi_failures", 0)
    monkeypatch.setattr(powerwall, "_tedapi_retry_at", 0.0)

    def install(results):
        fake = FakeTEDAPI(results)
        monkeypatch.setattr(powerwall, "get_tedapi", lambda: fake)
        return fake

    return install, clock


def test_tedapi_failures_back_off_then_reset(tedapi):
    install, clock = tedapi
    site = load_site()
    good = {"PVAC--1538000-25-F--TG12xxxxH000RA": {"PVAC_PVMeasuredPower_B": 300.0}}
    fake = install([TimeoutError("timed out"), None, good])

    with pytest.raises(TimeoutError):
        read_strings(None, site)
    clock["now"] += 30  # the next poll is inside the one-minute wait, so the gateway is left alone
    with pytest.raises(PowerwallError, match="next try in 31s"):
        read_strings(None, site)
    assert fake.calls == 1

    clock["now"] += 31
    with pytest.raises(PowerwallError, match="no vitals"):
        read_strings(None, site)
    assert fake.calls == 2
    clock["now"] += 119  # the second failure waits two minutes
    with pytest.raises(PowerwallError, match="next try"):
        read_strings(None, site)

    clock["now"] += 1
    assert read_strings(None, site) == {"PW1-B": {"Power": 300.0}}
    assert powerwall._tedapi_failures == 0


def test_tedapi_backoff_is_capped(tedapi, monkeypatch):
    install, clock = tedapi
    install([TimeoutError("timed out")])
    monkeypatch.setattr(powerwall, "_tedapi_failures", 10)
    with pytest.raises(TimeoutError):
        read_strings(None, load_site())
    assert powerwall._tedapi_retry_at == clock["now"] + powerwall.TEDAPI_BACKOFF_MAX
