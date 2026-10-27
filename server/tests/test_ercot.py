from server.ercot import parse_condition


def test_parse_condition():
    data = {"current_condition": {"state": "normal", "title": "Normal Conditions", "prc_value": "8,123",
                                  "condition_note": "", "datetime": 1800000000}}
    assert parse_condition(data) == {"state": "normal", "title": "Normal Conditions", "reserves_mw": 8123.0,
                                     "note": ""}


def test_parse_condition_without_reserves():
    assert parse_condition({})["reserves_mw"] is None
