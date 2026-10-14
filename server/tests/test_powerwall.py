import pytest

from server.config import load_site
from server.powerwall import PowerwallError, read_status, strings_from_vitals


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
