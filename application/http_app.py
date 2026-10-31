"""HTTP application assembly using explicitly supplied route handlers."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from flask import Flask


def register_http_routes(application: Flask, handlers: Mapping[str, Callable[..., Any]]) -> None:
    """Register the platform routes in their existing order."""
    application.route("/run", methods=["POST"])(handlers["handle_trigger"])
    application.route("/backfill", methods=["POST", "GET"])(handlers["handle_backfill"])
    application.route("/dry-run", methods=["POST", "GET"])(handlers["handle_dry_run"])
    application.route("/probe", methods=["POST"])(handlers["handle_probe"])
    application.route("/paper-command-consumer", methods=["POST"])(handlers["handle_paper_execution_command_consumer"])
    application.route("/reconcile", methods=["POST"])(handlers["handle_broker_reconciliation"])
    application.route("/account-snapshot", methods=["GET"])(handlers["handle_account_snapshot"])
    application.route("/v7-paper-application-loaded", methods=["GET"])(handlers["handle_v7_paper_application_loaded"])
    application.route("/monitor-dispatch", methods=["POST", "GET"])(handlers["handle_monitor_dispatch"])
    application.route("/healthz", methods=["GET"])(
        application.route("/health", methods=["GET"])(handlers["health"])
    )


def create_http_app(import_name: str, handlers: Mapping[str, Callable[..., Any]]) -> Flask:
    """Create a Flask application without constructing a platform runtime."""
    from flask import Flask

    application = Flask(import_name)
    register_http_routes(application, handlers)
    return application
