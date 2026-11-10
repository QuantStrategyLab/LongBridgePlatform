from socket import socketpair
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts.paper_observation import http_tick


@pytest.mark.parametrize(
    ("mode", "method", "route", "runner_exit", "expected"),
    [
        ("collect", "POST", "/tick", 0, 204),
        ("watchdog", "POST", "/tick", 1, 500),
        ("collect", "POST", "/tick?mode=watchdog", 0, 404),
        ("watchdog", "GET", "/tick", 0, 501),
    ],
)
def test_tick_exposes_only_the_configured_role(
    monkeypatch, mode, method, route, runner_exit, expected
):
    runner = Mock(return_value=runner_exit)
    monkeypatch.setattr(http_tick, "run_observation", runner)
    client, accepted = socketpair()
    with client, accepted:
        client.settimeout(3)
        client.sendall(f"{method} {route} HTTP/1.0\r\nContent-Length: 0\r\n\r\n".encode())
        http_tick.TickHandler(accepted, ("127.0.0.1", 1), SimpleNamespace(observation_mode=mode))
        with client.makefile("rb") as response:
            assert int(response.readline().split()[1]) == expected
    if expected in (204, 500):
        runner.assert_called_once_with([mode])
    else:
        runner.assert_not_called()
