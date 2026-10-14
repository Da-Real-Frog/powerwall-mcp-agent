import pytest

from server.config import DEFAULT_SITE_FILE, ConfigError, load_site, location


def test_repo_site_file_matches_permit_plans():
    site = load_site(DEFAULT_SITE_FILE)
    assert {a.name: a.panels for a in site.arrays} == {"MP1": 13, "MP2": 3, "MP3": 16}
    assert sum(a.kwp for a in site.arrays) == pytest.approx(12.8)
    assert len(site.inputs) == 6
    assert sum(i.strings for i in site.inputs) == 6
    assert {i.key: i.panels_per_string for i in site.inputs if i.key} == {"PW2-A": 6, "PW2-B": 6, "PW2-D": 4}
    assert site.unused_inputs == {"PW1-A", "PW2-C"}
    assert site.inverters == {"E005VD": 2, "H000RA": 1} and site.local_api_powerwall == 2


@pytest.mark.parametrize("compass, open_meteo", [(180, 0), (210, 30), (300, 120), (90, -90), (0, -180)])
def test_open_meteo_azimuth(compass, open_meteo):
    site = load_site(DEFAULT_SITE_FILE)
    array = site.arrays[0].__class__(name="x", panels=1, tilt=30, azimuth=compass, kwp=0.4)
    assert array.open_meteo_azimuth == open_meteo


def test_wiring_must_match_panel_count(tmp_path):
    path = tmp_path / "site.toml"
    path.write_text(
        '[panel]\nwatts = 400\n'
        '[[arrays]]\nname = "A"\npanels = 10\ntilt = 30\nazimuth = 180\n'
        '[[inputs]]\npowerwall = 1\narray = "A"\npanels_per_string = 6\n'
    )
    with pytest.raises(ConfigError, match="10 panels but its inputs wire 6"):
        load_site(path)


def test_missing_site_file(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_site(tmp_path / "nope.toml")


def test_location_is_required(monkeypatch):
    monkeypatch.delenv("SOLAR_LAT", raising=False)
    monkeypatch.setenv("SOLAR_LON", "-98")
    with pytest.raises(ConfigError, match="SOLAR_LAT"):
        location()
