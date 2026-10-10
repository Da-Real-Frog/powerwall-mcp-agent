from server.weather import parse_alerts


def test_parse_alerts():
    data = {"features": [{"properties": {
        "event": "Tornado Watch", "severity": "Severe", "urgency": "Expected",
        "onset": "2026-10-10T18:00:00-05:00", "expires": "2026-10-11T01:00:00-05:00", "ends": None,
        "headline": "Tornado Watch issued",
    }}]}
    [alert] = parse_alerts(data)
    assert alert["event"] == "Tornado Watch"
    assert alert["ends"] == "2026-10-11T01:00:00-05:00"


def test_no_alerts():
    assert parse_alerts({"features": []}) == []
