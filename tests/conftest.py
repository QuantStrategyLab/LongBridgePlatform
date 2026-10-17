"""All unit tests use injected transports, including during collection."""
import socket

import pytest


_network = pytest.MonkeyPatch()


def _offline(*args, **kwargs):
    raise OSError("Network disabled in unit tests; inject a fake transport")


def pytest_configure(config):
    _network.setattr(socket.socket, "connect", _offline)
    _network.setattr(socket.socket, "connect_ex", _offline)
    _network.setattr(socket.socket, "sendto", _offline)
    _network.setattr(socket, "create_connection", _offline)
    _network.setattr(socket, "getaddrinfo", _offline)


def pytest_unconfigure(config):
    _network.undo()
