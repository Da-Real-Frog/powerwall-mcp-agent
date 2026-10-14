from server import health
from server.config import load_site
from server.health import Finding, Monitor
from server.storage import BUCKET, Store

NOW = 1_800_000_000 // BUCKET * BUCKET + 60  # one minute into a bucket


def buckets(watts: dict[str, float], count: int = 3) -> list[dict]:
    """Five-minute averages for the `count` buckets before NOW's bucket."""
    current = NOW // BUCKET * BUCKET
    return [{"bucket": current - n * BUCKET, "input": letter, "watts": w}
            for n in range(1, count + 1) for letter, w in watts.items()]


def test_dead_string_is_found_against_its_neighbours():
    # A and B have 6 panels, D has 4: 300 W is 50 W per panel
    findings = health.check_strings(load_site(), buckets({"PW2-A": 300, "PW2-B": 0, "PW2-D": 200, "PW2-C": 0}))
    assert [(f.kind, f.subject) for f in findings] == [("string_dead", "PW2-B")]
    assert "MP3, 6 panels" in findings[0].message


def test_low_string_is_found_per_panel():
    # D makes 80 W on 4 panels (20 W each) against 50 W per panel on A and B
    findings = health.check_strings(load_site(), buckets({"PW2-A": 300, "PW2-B": 300, "PW2-D": 80}))
    assert [(f.kind, f.subject) for f in findings] == [("string_low", "PW2-D")]


def test_equal_output_per_panel_is_healthy():
    assert health.check_strings(load_site(), buckets({"PW2-A": 300, "PW2-B": 290, "PW2-D": 200})) == []


def test_no_verdict_in_weak_sun():
    assert health.check_strings(load_site(), buckets({"PW2-A": 60, "PW2-B": 0, "PW2-D": 40})) == []


def test_short_dip_is_not_a_fault():
    assert health.check_strings(load_site(), buckets({"PW2-A": 300, "PW2-B": 0, "PW2-D": 200}, count=2)) == []


def test_missing_input_and_grid_and_stale_collector():
    assert [f.subject for f in health.check_inputs_present(load_site(), {"PW2-A", "PW2-C", "PW2-D"})] == ["PW2-B"]
    assert health.check_grid({"grid_status": "DOWN", "battery_percent": 64.0})[0].kind == "grid_down"
    assert health.check_grid({"grid_status": "UP", "battery_percent": 64.0}) == []
    assert health.check_collector(NOW - 600, NOW - 3600, NOW)[0].kind == "collector_stale"
    assert health.check_collector(NOW - 30, NOW - 3600, NOW) == []
    assert health.check_collector(None, NOW - 60, NOW) == []  # just started


class RecordingNotifier:
    def __init__(self):
        self.sent = []

    def send(self, title, message, priority="default", tags=""):
        self.sent.append((title, priority))


def test_alert_opens_once_and_resolves_after_three_clear_checks(tmp_path):
    store = Store(str(tmp_path / "pw.db"))
    notifier = RecordingNotifier()
    monitor = Monitor(load_site(), store, notifier, "America/Chicago")
    dead = [Finding("string_dead", "PW2-B", "high", "input B is producing nothing")]

    monitor.apply(dead, NOW)
    monitor.apply(dead, NOW + 300)
    assert len(store.open_alerts()) == 1
    assert notifier.sent == [("Solar alert: string dead", "high")]

    for n in range(1, 3):
        monitor.apply([], NOW + 300 + n * 300)
    assert len(store.open_alerts()) == 1  # two clear checks are not enough

    monitor.apply([], NOW + 1500)
    assert store.open_alerts() == []
    assert notifier.sent[-1] == ("Resolved: string dead", "low")
    assert len(store.alerts_since(NOW)) == 1


def test_a_clear_check_in_between_does_not_resolve(tmp_path):
    store = Store(str(tmp_path / "pw.db"))
    monitor = Monitor(load_site(), store, RecordingNotifier(), "America/Chicago")
    dead = [Finding("string_dead", "PW2-B", "high", "dead")]
    for findings in (dead, [], [], dead, [], []):
        monitor.apply(findings, NOW)
    assert len(store.open_alerts()) == 1


def test_evaluate_reads_from_the_store(tmp_path):
    store = Store(str(tmp_path / "pw.db"))
    status = {"battery_level_percent": 80.0, "grid_status": "UP", "grid_power_watts": 0.0,
              "solar_power_watts": 500.0, "home_usage_watts": 500.0, "battery_power_watts": 0.0}
    strings = {"PW2-A": {"Power": 300}, "PW2-B": {"Power": 0}, "PW2-C": {"Power": 0}, "PW2-D": {"Power": 200}}
    current = NOW // BUCKET * BUCKET
    for ts in range(current - 3 * BUCKET, NOW, 30):
        store.add(ts, status, strings)
    store.rollup(0)

    findings = health.evaluate(load_site(), store, last_success=NOW - 30, started=NOW - 3600, now=NOW)
    assert [(f.kind, f.subject) for f in findings] == [("string_dead", "PW2-B")]


def test_ntfy_request(monkeypatch):
    calls = []

    class Response:
        def raise_for_status(self):
            pass

    monkeypatch.setattr(health.httpx, "post", lambda url, **kw: calls.append((url, kw)) or Response())
    health.Notifier("https://ntfy.sh/topic", "tk").send("Title", "Body", "high", "warning")
    url, kw = calls[0]
    assert url == "https://ntfy.sh/topic" and kw["content"] == b"Body"
    assert kw["headers"] == {"Title": "Title", "Priority": "high", "Tags": "warning", "Authorization": "Bearer tk"}

    health.Notifier(None).send("Title", "Body")  # unset: sends nothing
    assert len(calls) == 1


def test_stale_strings_point_at_the_bridge(tmp_path):
    store = Store(str(tmp_path / "pw.db"))
    status = {"battery_level_percent": 80.0, "grid_status": "UP", "grid_power_watts": 0.0,
              "solar_power_watts": 500.0, "home_usage_watts": 500.0, "battery_power_watts": 0.0}
    store.add(NOW - 900, status, {"PW2-A": {"Power": 300}})
    store.add(NOW - 30, status, {})  # the gateway still answers, TEDAPI does not

    findings = health.evaluate(load_site(), store, last_success=NOW - 30, started=NOW - 3600, now=NOW)
    assert [f.kind for f in findings] == ["strings_stale"]  # not one input_missing per input
