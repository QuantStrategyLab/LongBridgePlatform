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


def test_build_runtime_bootstrap_can_build_read_only_contexts_without_indicators():
    observed = {}
    bootstrap = build_runtime_bootstrap(
        project_id="project-1",
        secret_name="secret-1",
        token_refresh_threshold_days=30,
        fetch_token_from_secret_fn=lambda *_args, **_kwargs: "refresh-token",
        refresh_token_if_needed_fn=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("read-only bootstrap must not refresh or mutate a token secret")
        ),
        build_contexts_fn=lambda app_key, app_secret, token: (
            observed.setdefault("contexts", (app_key, app_secret, token)),
            ("quote-context", "trade-context"),
        )[-1],
        calculate_strategy_indicators_fn=lambda _quote_context: (_ for _ in ()).throw(
            AssertionError("read-only bootstrap must not calculate indicators")
        ),
        env_reader=lambda name, default="": {
            "LONGPORT_APP_KEY": "app-key",
            "LONGPORT_APP_SECRET": "app-secret",
        }.get(name, default),
    )

    assert bootstrap.build_read_only_contexts() == ("quote-context", "trade-context")
    assert observed["contexts"] == ("app-key", "app-secret", "refresh-token")


def test_account_snapshot_contexts_use_one_metadata_response_without_refresh():
    observed = {"latest_reads": 0, "metadata_reads": 0}

    class Metadata:
        def __init__(self, value, version_name):
            self.value = value
            self.version_name = version_name

    def read_latest(*_args, **_kwargs):
        observed["latest_reads"] += 1
        return "rotated-token"

    def read_metadata(project_id, secret_name):
        observed["metadata_reads"] += 1
        observed["metadata_request"] = (project_id, secret_name)
        if observed["metadata_reads"] > 1:
            return Metadata("rotated-token", "projects/p/secrets/token/versions/9")
        return Metadata("snapshot-token", "projects/p/secrets/token/versions/4")

    bootstrap = build_runtime_bootstrap(
        project_id="project-1",
        secret_name="secret-1",
        token_refresh_threshold_days=30,
        fetch_token_from_secret_fn=read_latest,
        refresh_token_if_needed_fn=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("account snapshot must not refresh a token")
        ),
        build_contexts_fn=lambda app_key, app_secret, token: (
            observed.setdefault("contexts", (app_key, app_secret, token)),
            ("quote-context", "trade-context"),
        )[-1],
        calculate_strategy_indicators_fn=lambda _quote_context: (_ for _ in ()).throw(
            AssertionError("account snapshot must not calculate indicators")
        ),
        env_reader=lambda name, default="": {
            "LONGPORT_APP_KEY": "app-key",
            "LONGPORT_APP_SECRET": "app-secret",
        }.get(name, default),
    )

    quote_context, trade_context, version_name = bootstrap.build_account_snapshot_contexts(
        fetch_token_with_metadata_fn=read_metadata,
    )

    assert (quote_context, trade_context) == ("quote-context", "trade-context")
    assert observed["contexts"] == ("app-key", "app-secret", "snapshot-token")
    assert version_name == "projects/p/secrets/token/versions/4"
    assert observed["metadata_reads"] == 1
    assert observed["metadata_request"] == ("project-1", "secret-1")
    assert observed["latest_reads"] == 0

    missing_version = build_runtime_bootstrap(
        project_id="project-1",
        secret_name="secret-1",
        token_refresh_threshold_days=30,
        fetch_token_from_secret_fn=read_latest,
        refresh_token_if_needed_fn=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("account snapshot must not refresh a token")
        ),
        build_contexts_fn=lambda app_key, app_secret, token: (token, "trade"),
        calculate_strategy_indicators_fn=lambda _quote_context: None,
        env_reader=lambda name, default="": {
            "LONGPORT_APP_KEY": "app-key",
            "LONGPORT_APP_SECRET": "app-secret",
        }.get(name, default),
    )
    token, _trade, version_name = missing_version.build_account_snapshot_contexts(
        fetch_token_with_metadata_fn=lambda *_args, **_kwargs: Metadata("snapshot-token", None),
    )
    assert token == "snapshot-token"
    assert version_name is None
    assert observed["latest_reads"] == 0


def test_account_snapshot_strips_trailing_newline_before_building_context():
    version_name = "projects/p/secrets/token/versions/4"
    observed = {}
    bootstrap = build_runtime_bootstrap(
        project_id="project-1",
        secret_name="secret-1",
        token_refresh_threshold_days=30,
        fetch_token_from_secret_fn=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("whitespace normalization must not read another token")
        ),
        refresh_token_if_needed_fn=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("account snapshot must not refresh a token")
        ),
        build_contexts_fn=lambda app_key, app_secret, token: (
            observed.setdefault("contexts", (app_key, app_secret, token)),
            ("quote-context", "trade-context"),
        )[-1],
        calculate_strategy_indicators_fn=lambda _quote_context: None,
        env_reader=lambda name, default="": {
            "LONGPORT_APP_KEY": "app-key",
            "LONGPORT_APP_SECRET": "app-secret",
        }.get(name, default),
    )

    _quote, _trade, bound_version = bootstrap.build_account_snapshot_contexts(
        fetch_token_with_metadata_fn=lambda *_args, **_kwargs: type(
            "Metadata",
            (),
            {"value": "synthetic-token\n", "version_name": version_name},
        )(),
    )

    assert observed["contexts"] == ("app-key", "app-secret", "synthetic-token")
    assert bound_version == version_name
