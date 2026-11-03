"""All unit tests use injected transports, including during collection."""
import socket

import pytest


_network = pytest.MonkeyPatch()
for _key, _value in {
    "GCP_PROJECT_ID": "fake-project",
    "GCP_WORKLOAD_IDENTITY_PROVIDER": "projects/123456789012/locations/global/workloadIdentityPools/github-actions/providers/github-main",
    "GCP_WORKLOAD_IDENTITY_SERVICE_ACCOUNT": "longbridge-platform-deploy@fake-project.iam.gserviceaccount.com",
    "GCP_RUNTIME_SERVICE_ACCOUNT": "longbridge-platform-runtime@fake-project.iam.gserviceaccount.com",
    "GCP_SCHEDULER_SERVICE_ACCOUNT": "longbridge-platform-scheduler@fake-project.iam.gserviceaccount.com",
}.items():
    _network.setenv(_key, _value)


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
