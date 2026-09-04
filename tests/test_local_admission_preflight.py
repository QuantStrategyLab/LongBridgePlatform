from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from application.broker_reconciliation import (  # noqa: E402
    LongBridgeReconciliationCandidate,
)
from application.local_admission_preflight import evaluate_local_admission  # noqa: E402
from quant_platform_kit.common.broker_reconciliation import (  # noqa: E402
    build_broker_reconciliation_evidence,
)


SCRIPT_PATH = ROOT / "scripts" / "verify_local_admission_preflight.py"
SPEC = importlib.util.spec_from_file_location("local_admission_preflight_script", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
admission_script = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(admission_script)


def _runtime_target() -> dict[str, object]:
    return {
        "platform_id": "longbridge",
        "strategy_profile": "soxl_soxx_trend_income",
        "dry_run_only": False,
        "strategy_release": {
            "release_id": "soxl-p2-v3.20260904",
            "manifest_sha256": "a" * 64,
            "strategy_revision": "soxl-p2-v3",
            "config_sha256": "b" * 64,
            "risk_policy_sha256": "c" * 64,
            "evidence_sha256": "d" * 64,
            "plugin_bundle_sha256": "e" * 64,
            "effective_session": "2026-09-04",
        },
    }


def _reconciliation_receipt() -> dict[str, object]:
    evidence = build_broker_reconciliation_evidence(
        platform_id="longbridge",
        strategy_profile="soxl_soxx_trend_income",
        account_scope_sha256="1" * 64,
        baseline_id="longbridge-paper-baseline",
        baseline_target_sha256="2" * 64,
        runtime_target_sha256="3" * 64,
        observed_at="2026-09-04T00:00:00Z",
        broker_connected=True,
        account_identity_match=True,
        positions_match=True,
        cash_match=True,
        open_orders_match=True,
        recent_executions_match=True,
        local_execution_ledger_match=True,
        positions_sha256="4" * 64,
        cash_sha256="5" * 64,
        open_orders_sha256="6" * 64,
        recent_executions_sha256="7" * 64,
        local_execution_ledger_sha256="8" * 64,
    )
    return LongBridgeReconciliationCandidate(
        evidence=evidence,
        recovery_blockers=(),
        expected_digests_configured=True,
        execution_ledger_records_count=0,
    ).to_safe_dict()


def _input(**overrides: object) -> dict[str, object]:
    return {
        "live_ready": True,
        "runtime_target": _runtime_target(),
        "mandate": True,
        "broker_session_token_refresh": True,
        "data_entitlement": True,
        "ledger": True,
        "unknown_pending_orders": False,
        "reconciliation_receipt": _reconciliation_receipt(),
        **overrides,
    }


def test_all_verified_gates_are_ready_with_a_fixed_sanitized_payload() -> None:
    result = evaluate_local_admission(_input())

    assert result == {
        "disposition": "READY",
        "reason_code": "ready",
        "gates": {
            "live_ready": True,
            "release": True,
            "mandate": True,
            "broker_session_token_refresh": True,
            "data_entitlement": True,
            "ledger": True,
            "unknown_pending_orders": False,
            "reconciliation": True,
        },
    }


def test_unconfirmed_gate_parks_without_exposing_the_input() -> None:
    result = evaluate_local_admission(_input(data_entitlement=False))

    assert result["disposition"] == "PARK"
    assert result["reason_code"] == "data_entitlement_not_confirmed"
    assert set(result) == {"disposition", "reason_code", "gates"}
    assert "runtime_target" not in result
    assert "reconciliation_receipt" not in result


def test_invalid_or_unreleased_target_parks_at_the_release_gate() -> None:
    result = evaluate_local_admission(_input(runtime_target={"platform_id": "longbridge"}))

    assert result["disposition"] == "PARK"
    assert result["reason_code"] == "release_not_verified"
    assert result["gates"]["release"] is False


def test_unknown_pending_orders_parks_even_when_every_other_gate_is_verified() -> None:
    result = evaluate_local_admission(_input(unknown_pending_orders=True))

    assert result["disposition"] == "PARK"
    assert result["reason_code"] == "unknown_pending_orders_present"
    assert result["gates"]["unknown_pending_orders"] is True


def test_command_emits_only_the_sanitized_preflight_result(tmp_path: Path, capsys) -> None:
    evidence_path = tmp_path / "admission.json"
    evidence_path.write_text(json.dumps(_input()), encoding="utf-8")

    assert admission_script.main(["--input", str(evidence_path)]) == 0
    assert json.loads(capsys.readouterr().out) == evaluate_local_admission(_input())
