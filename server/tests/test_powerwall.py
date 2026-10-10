import pytest

from server.powerwall import PowerwallError, read_status


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
