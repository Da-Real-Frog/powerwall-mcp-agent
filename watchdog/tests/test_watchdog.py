import json
import subprocess
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

import watchdog

TZ = ZoneInfo("America/Chicago")
NOW = datetime(2026, 11, 6, 18, 0, tzinfo=timezone.utc)  # noon in Texas


def container(name, status="running", health=None, started=NOW - timedelta(days=3), restarts=0, exit_code=0):
    return {"name": name, "status": status, "health": health, "restarts": restarts, "exit_code": exit_code,
            "started": started.strftime("%Y-%m-%dT%H:%M:%S.123456789Z")}


def cfg(**kw):
    base = dict(host="192.0.2.10", user="someone", ntfy_url="https://ntfy.example/topic", state_file=None)
    base.update(kw)
    return watchdog.Config(**base)


def test_all_good():
    r = watchdog.evaluate([container("mcp-server"), container("home-chat", health="healthy")],
                          ["mcp-server", "home-chat"], NOW, TZ)
    assert r.problems == [] and r.notes == []
    assert r.priority == "low" and r.title == "percula: all good"
    assert "home-chat: running, healthy, up 3 days" in r.body()


def test_missing_stopped_and_unhealthy_are_problems():
    r = watchdog.evaluate([container("mcp-server", status="exited", exit_code=137),
                           container("home-chat", health="unhealthy")],
                          ["mcp-server", "home-chat", "reserve"], NOW, TZ)
    assert r.problems == ["reserve doesn't exist on percula", "mcp-server is exited (exit code 137)",
                          "home-chat is running but unhealthy"]
    assert r.priority == "high" and r.title == "percula: 3 problems"


def test_recent_start_and_new_restarts_are_notes():
    r = watchdog.evaluate([container("mcp-server", started=NOW - timedelta(hours=2), restarts=4)],
                          ["mcp-server"], NOW, TZ, previous_restarts={"mcp-server": 1})
    assert r.problems == []
    assert r.notes == ["mcp-server (re)started Fri 10:00, a deploy or a crash",
                       "Docker restarted mcp-server 3 times since the last check"]
    assert r.priority == "default"


def test_other_containers_are_listed_but_only_failures_noted():
    r = watchdog.evaluate([container("mcp-server"), container("runner", status="exited", exit_code=0),
                           container("old", status="exited", exit_code=1)], ["mcp-server"], NOW, TZ)
    assert r.problems == []
    assert r.notes == ["old (not watched) is exited"]
    assert r.lines[0].startswith("mcp-server") and "(other) runner: exited (exit code 0)" in r.lines


def test_parse_containers_strips_slash_and_rejects_garbage():
    lines = json.dumps({"name": "/mcp-server", "status": "running"}) + "\n\n"
    assert watchdog.parse_containers(lines)[0]["name"] == "mcp-server"
    with pytest.raises(watchdog.Unreachable):
        watchdog.parse_containers("permission denied while trying to connect to the Docker daemon")


def test_parse_started():
    assert watchdog.parse_started("2026-11-05T17:02:11.123456789Z") == datetime(2026, 11, 5, 17, 2, 11,
                                                                                 tzinfo=timezone.utc)
    assert watchdog.parse_started("0001-01-01T00:00:00Z") is None


def test_next_run():
    morning = datetime(2026, 11, 6, 15, 0, tzinfo=timezone.utc)  # 09:00 local
    assert watchdog.next_run(morning, "12:00", TZ) == datetime(2026, 11, 6, 12, 0, tzinfo=TZ)
    assert watchdog.next_run(NOW, "12:00", TZ) == datetime(2026, 11, 7, 12, 0, tzinfo=TZ)


def test_config_defaults_and_missing(monkeypatch):
    c = watchdog.config_from_env({"PERCULA_HOST": "192.0.2.10", "PERCULA_USER": "u", "NTFY_URL": "https://x/t"})
    assert c.expected == ["mcp-server", "home-chat"]
    assert c.http_checks == ["http://192.0.2.10:8080/", "http://192.0.2.10:8000/mcp"]
    c = watchdog.config_from_env({"PERCULA_HOST": "h", "PERCULA_USER": "u", "NTFY_URL": "https://x/t",
                                  "HTTP_CHECKS": "none"})
    assert c.http_checks == []
    with pytest.raises(SystemExit, match="NTFY_URL"):
        watchdog.config_from_env({"PERCULA_HOST": "h", "PERCULA_USER": "u"})


def test_unreachable_percula_is_a_problem(monkeypatch):
    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 255, "", "ssh: connect to host 192.0.2.10 port 22: No route to host\n")

    sent = []
    monkeypatch.setattr(watchdog.subprocess, "run", fake_run)
    monkeypatch.setattr(watchdog, "send_ntfy", lambda c, *a: sent.append(a) or True)
    r = watchdog.run_check(cfg(), NOW)
    assert r.problems == ["Can't reach percula (ssh: connect to host 192.0.2.10 port 22: No route to host). "
                          "It may be down, off the network, or its SSH may have stopped."]
    assert sent[0][0] == "percula: 1 problem" and sent[0][2] == "high"


def test_run_check_probes_and_remembers_restarts(monkeypatch, tmp_path):
    out = "\n".join(json.dumps(c) for c in [container("mcp-server", restarts=2)])
    monkeypatch.setattr(watchdog.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, out, ""))
    monkeypatch.setattr(watchdog, "probe", lambda url: "doesn't answer (refused)" if "8080" in url else None)
    monkeypatch.setattr(watchdog, "send_ntfy", lambda *a: True)
    state = tmp_path / "state.json"
    c = cfg(expected=["mcp-server"], http_checks=["http://h:8080/", "http://h:8000/mcp"], state_file=str(state))
    r = watchdog.run_check(c, NOW)
    assert r.problems == ["http://h:8080/ doesn't answer (refused)"]
    assert json.loads(state.read_text())["restarts"] == {"mcp-server": 2}


def test_ntfy_request_does_not_need_a_real_server(monkeypatch):
    seen = {}

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout):
        seen.update(url=req.full_url, headers=dict(req.headers), data=req.data)
        return Resp()

    monkeypatch.setattr(watchdog.urllib.request, "urlopen", fake_urlopen)
    assert watchdog.send_ntfy(cfg(ntfy_token="tk"), "T", "B", "high", "warning")
    assert seen["url"] == "https://ntfy.example/topic" and seen["data"] == b"B"
    assert seen["headers"]["Authorization"] == "Bearer tk" and seen["headers"]["Priority"] == "high"
