"""Check deterministic replay and demonstrate that its comparator detects drift."""

from copy import deepcopy
import sys

from gunicorn.errors import AppImportError
import pytest

import startup_replay_support as replay


@pytest.fixture(scope="module")
def baseline():
    return replay.replay(replay.ROOT / "main.py", "main:app")


def test_current_baseline_replays_deterministically(baseline):
    repeated = replay.replay(replay.ROOT / "main.py", "main:app")
    result = replay.compare(baseline, repeated)
    assert result["all_recorded_observations_equal"] is True
    assert result["initial_launch_and_http_equal"] is True
    assert result["production_equivalence"] == "not_established"
    assert result["execution_authority_granted"] is False


def test_replay_observes_the_supported_startup_and_http_contract(baseline):
    success = baseline["service_cli"]["success"]
    assert success["error"] is None
    assert all(success["lifetimes"].values())
    assert success["repeated_success"] == [True, True]
    assert success["repeat_events"] == []
    assert len(success["routes"]) == 12
    assert all(route["handler_identity"] for route in success["routes"])
    assert {entry["path"] for entry in success["http"]} == {route[0] for route in replay.composition.ROUTES} | {"/absent"}
    assert {entry["method"] for entry in success["http"]} == {"GET", "POST", "HEAD", "OPTIONS", "PUT"}
    assert {entry["status"] for entry in success["http"]} >= {200, 404, 405, 409, 500, 503}
    assert all(record["effects"] == [] for record in baseline["service_cli"].values())
    assert baseline["direct_import_diagnostics"]["bare-import"]["effects"] == []
    assert baseline["service_cli"]["cli-None"]["calls"][0]["arguments"] == {"host": "0.0.0.0", "port": 8080}
    assert baseline["service_cli"]["cli-8181"]["calls"][0]["arguments"] == {"host": "0.0.0.0", "port": 8181}
    assert baseline["identity"]["observed_repository_command"][-1] == "main:app"


def test_replay_preserves_first_failure_identity_cause_and_worker_classification(baseline):
    for name, case in replay.cases().items():
        if name == "success":
            continue
        record = baseline["service_cli"][name]
        assert record["worker_exit"] == 3
        assert record["repeated_failure"]["worker_exit"] == 3
        assert record["error"] == record["repeated_failure"]["error"]
        assert record["error"]["cause"] is not None
        assert record["same_injected_error"] is ("premium" not in case)
        assert record["repeated_failure"]["same_injected_error"] is ("premium" not in case)
        assert baseline["direct_import_diagnostics"][name]["after_load"]["present"] is False
        assert baseline["direct_import_diagnostics"][name]["after_repeated_failure"]["present"] is False


@pytest.mark.parametrize("change", ["http", "startup_order", "exit_classification", "cli"])
def test_comparator_detects_behavior_drift(baseline, change):
    candidate = deepcopy(baseline)
    if change == "http":
        candidate["service_cli"]["success"]["http"][0]["body"] = "different response"
    elif change == "startup_order":
        events = candidate["service_cli"]["success"]["startup"]
        events[0], events[1] = events[1], events[0]
    elif change == "exit_classification":
        candidate["service_cli"]["settings-attribute"]["worker_exit"] = 4
    else:
        candidate["service_cli"]["cli-None"]["calls"][0]["arguments"]["port"] = 8181
    result = replay.compare(baseline, candidate)
    assert result["initial_launch_and_http_equal"] is False
    assert result["all_recorded_observations_equal"] is False
    assert result["service_cli_differences"]


def test_comparator_keeps_retry_and_module_retention_differences_visible(baseline):
    candidate = deepcopy(baseline)
    candidate["service_cli"]["stage-settings"]["repeated_failure"]["timeline"].pop(0)
    candidate["direct_import_diagnostics"]["stage-settings"]["after_load"]["present"] = True
    result = replay.compare(baseline, candidate)
    assert result["initial_launch_and_http_equal"] is True
    assert result["same_interpreter_retry_differences"] == ["stage-settings"]
    assert result["direct_import_differences"] == ["stage-settings"]
    assert result["all_recorded_observations_equal"] is False
    assert result["supported_launch_transcripts_equal"] is False
    assert result["production_equivalence"] == "not_established"


@pytest.mark.parametrize("change", ["boolean-to-integer", "integer-to-float"])
def test_comparator_preserves_boolean_and_numeric_types(baseline, change):
    candidate = deepcopy(baseline)
    if change == "boolean-to-integer":
        response = next(value for value in candidate["service_cli"]["success"]["http"]
                        if value["path"] == "/backfill" and value["method"] == "GET")
        assert response["callbacks"][0][1]["force_run"] is True
        response["callbacks"][0][1]["force_run"] = 1
    else:
        candidate["service_cli"]["cli-None"]["calls"][0]["ready_routes"] = 12.0
    result = replay.compare(baseline, candidate)
    assert result["all_recorded_observations_equal"] is False
    assert result["initial_launch_and_http_equal"] is False


@pytest.mark.parametrize(("case", "field"), [
    ("success", "http"), ("success", "lifetimes"), ("stage-settings", "startup"),
    ("stage-settings", "repeated_failure"), ("cli-None", "calls"),
])
def test_matching_missing_observations_are_rejected_on_both_sides(baseline, case, field):
    incomplete = deepcopy(baseline)
    incomplete["service_cli"][case].pop(field)
    with pytest.raises(ValueError, match="incomplete replay observation"):
        replay.compare(incomplete, deepcopy(incomplete))


@pytest.mark.parametrize(("case", "field"), [
    ("success", "http"), ("success", "routes"), ("stage-settings", "startup"), ("cli-None", "calls"),
])
def test_matching_empty_observation_lists_are_rejected_on_both_sides(baseline, case, field):
    incomplete = deepcopy(baseline)
    incomplete["service_cli"][case][field] = []
    with pytest.raises(ValueError, match="incomplete replay observation"):
        replay.compare(incomplete, deepcopy(incomplete))


def test_matching_missing_runtime_identity_is_rejected(baseline):
    incomplete = deepcopy(baseline)
    incomplete["environment"].pop("gunicorn")
    with pytest.raises(ValueError):
        replay.compare(incomplete, deepcopy(incomplete))


@pytest.mark.parametrize(("section", "case"), [("service_cli", "success"), ("direct_import_diagnostics", "bare-import")])
def test_matching_null_effect_observations_are_rejected(baseline, section, case):
    incomplete = deepcopy(baseline)
    incomplete[section][case]["effects"] = None
    with pytest.raises(ValueError, match="invalid external-effect observations"):
        replay.compare(incomplete, deepcopy(incomplete))


@pytest.mark.parametrize("invalid", ["source", "case", "environment", "effects", "bare_effects", "launcher", "launcher_type", "missing_effects", "missing_bare_effects"])
def test_comparator_rejects_incomplete_or_incompatible_evidence(baseline, invalid):
    candidate = deepcopy(baseline)
    if invalid == "source":
        candidate["identity"].pop("source_sha256")
    elif invalid == "case":
        candidate["service_cli"].pop("stage-settings")
    elif invalid == "environment":
        candidate["environment"]["gunicorn"] = "other-version"
    elif invalid == "effects":
        candidate["service_cli"]["success"]["effects"] = ["socket.connect"]
    elif invalid == "bare_effects":
        candidate["direct_import_diagnostics"]["bare-import"]["effects"] = ["socket.connect"]
    elif invalid == "launcher":
        candidate["identity"]["effective_command"][-1] = "main:other"
    elif invalid == "launcher_type":
        candidate["identity"]["effective_command"][-2] = 570
    elif invalid == "missing_bare_effects":
        candidate["direct_import_diagnostics"]["bare-import"].pop("effects")
    else:
        candidate["service_cli"]["success"].pop("effects")
    with pytest.raises(ValueError):
        replay.compare(baseline, candidate)


def test_network_audit_guard_rejects_a_deliberate_probe():
    attempts = []
    token = replay._ACTIVE_EFFECTS.set(attempts)
    try:
        with pytest.raises(AssertionError, match="external I/O"):
            # Exercise CPython's installed hook without replacing the repository's
            # separate DNS/socket denial fixtures or attempting external I/O.
            sys.audit("socket.getaddrinfo", "startup-replay.synthetic.invalid", 0)
    finally:
        replay._ACTIVE_EFFECTS.reset(token)
    assert attempts == ["socket.getaddrinfo"]


def test_installed_worker_classifier_distinguishes_application_import_error():
    assert replay._worker_exit(RuntimeError("synthetic boot failure")) == 3
    assert replay._worker_exit(AppImportError("synthetic application import failure")) == 4
