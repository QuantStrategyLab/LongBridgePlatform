import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from application.runtime_bootstrap_adapters import build_runtime_bootstrap


def test_build_runtime_bootstrap_refreshes_token_and_builds_contexts():
    observed = {}
    bootstrap = build_runtime_bootstrap(
        project_id="project-1",
        secret_name="secret-1",
        token_refresh_threshold_days=30,
        fetch_token_from_secret_fn=lambda project_id, secret_name: (
            observed.setdefault("fetch_secret", (project_id, secret_name)),
            "refresh-token",
        )[-1],
        refresh_token_if_needed_fn=lambda token, **kwargs: (
            observed.setdefault("refresh", (token, kwargs)),
            "live-token",
        )[-1],
        build_contexts_fn=lambda app_key, app_secret, token: (
            observed.setdefault("contexts", (app_key, app_secret, token)),
            ("quote-context", "trade-context"),
        )[-1],
        calculate_strategy_indicators_fn=lambda quote_context: (
            observed.setdefault("indicators", quote_context),
            {"qqq": {"price": 123.45}},
        )[-1],
        env_reader=lambda name, default="": {
            "LONGPORT_APP_KEY": "app-key",
            "LONGPORT_APP_SECRET": "app-secret",
        }.get(name, default),
    )

    result = bootstrap()

    assert observed["fetch_secret"] == ("project-1", "secret-1")
    assert observed["refresh"] == (
        "refresh-token",
        {
            "project_id": "project-1",
            "secret_name": "secret-1",
            "app_key": "app-key",
            "app_secret": "app-secret",
            "refresh_threshold_days": 30,
        },
    )
    assert observed["contexts"] == ("app-key", "app-secret", "live-token")
    assert observed["indicators"] == "quote-context"
    assert result == ("quote-context", "trade-context", {"qqq": {"price": 123.45}})


def test_build_runtime_bootstrap_raises_when_indicators_unavailable():
    bootstrap = build_runtime_bootstrap(
        project_id=None,
        secret_name="secret-1",
        token_refresh_threshold_days=30,
        fetch_token_from_secret_fn=lambda *_args, **_kwargs: "refresh-token",
        refresh_token_if_needed_fn=lambda token, **_kwargs: token,
        build_contexts_fn=lambda *_args, **_kwargs: ("quote-context", "trade-context"),
        calculate_strategy_indicators_fn=lambda _quote_context: None,
        env_reader=lambda _name, default="": default,
    )

    try:
        bootstrap()
    except Exception as exc:  # noqa: PERF203
        assert str(exc) == "Quote data missing or API limited; cannot compute indicators"
    else:
        raise AssertionError("expected bootstrap to raise when indicators are unavailable")
