"""Fake-only adversarial contracts for bootstrap discovery evidence semantics.

Structural fixture tests only. They do not prove live GCP permissions and must
not unlock operational ACCEPTED without a supported trusted provenance kind.
Synthetic digests below are labeled fixtures, not live Environment fingerprints.
"""
from pathlib import Path

from scripts.paper_iam_discovery_evidence import (
    BOOTSTRAP_PERMISSIONS,
    RECIPIENT_GRANT_PERMISSIONS,
    SUPPORTED_TRUSTED_PROVENANCE_KINDS,
    VERDICT_ACCEPTED,
    VERDICT_REJECTED,
    VERDICT_UNKNOWN,
    evidence_accepted,
    evidence_verdict,
    reject_reasons,
    unknown_reasons,
)

PROJECT = "projects/fake-paper-project"
OTHER_PROJECT = "projects/other-fake-project"
OPERATOR = "user:operator@example.invalid"
CANDIDATE = "serviceAccount:candidate@fake-paper-project.iam.gserviceaccount.com"
OTHER_CANDIDATE = "serviceAccount:other@fake-paper-project.iam.gserviceaccount.com"
EXECUTOR = "serviceAccount:executor@fake-paper-project.iam.gserviceaccount.com"
ROLE = "projects/fake-paper-project/roles/paperIamBootstrap"
# Explicitly synthetic fixture digests — not live mapping fingerprints.
SYNTHETIC_HASH_A = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
SYNTHETIC_HASH_B = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


def _base(**overrides):
    payload = dict(
        candidate_principal=CANDIDATE,
        evaluated_resource=PROJECT,
        actual_caller=OPERATOR,
        evaluated_permissions=BOOTSTRAP_PERMISSIONS,
        candidate_project_role_bindings={ROLE: {CANDIDATE}},
        candidate_role_permission_sets={ROLE: set(BOOTSTRAP_PERMISSIONS)},
        recipient_executor_principal=EXECUTOR,
        impersonation_or_token_mint_used=False,
    )
    payload.update(overrides)
    return payload


def _self_attested_flags(**overrides):
    """Checklist booleans only — not trusted provenance."""
    payload = _base(
        bindings_have_conditions=False,
        conditions_authoritatively_satisfied=True,
        deny_policies_authoritatively_evaluated=True,
        principal_access_boundary_authoritatively_evaluated=True,
        inherited_allow_deny_context_complete=True,
        principal_identity_context_complete=True,
        time_context_complete=True,
    )
    payload.update(overrides)
    return payload


def test_bootstrap_and_recipient_permission_strings_match_by_owner_design():
    assert BOOTSTRAP_PERMISSIONS == RECIPIENT_GRANT_PERMISSIONS
    assert len(BOOTSTRAP_PERMISSIONS) == 4


def test_no_supported_trusted_provenance_kinds_are_registered():
    assert SUPPORTED_TRUSTED_PROVENANCE_KINDS == frozenset()


def test_operator_test_iam_holding_all_four_does_not_prove_candidate():
    payload = _base(
        caller_test_iam_permissions=set(BOOTSTRAP_PERMISSIONS),
        candidate_project_role_bindings={ROLE: set()},
    )
    reasons = reject_reasons(**payload)
    assert "caller_test_iam_not_candidate_effective" in reasons
    assert "test_iam_permissions_is_caller_scoped" in reasons
    assert evidence_verdict(**payload) == VERDICT_REJECTED


def test_sa_get_iam_policy_alone_is_trust_not_project_permission_proof():
    payload = dict(
        candidate_principal=CANDIDATE,
        evaluated_resource=PROJECT,
        actual_caller=OPERATOR,
        evaluated_permissions=BOOTSTRAP_PERMISSIONS,
        candidate_sa_iam_policy_members={"principalSet://iam.googleapis.com/fake"},
    )
    assert "sa_get_iam_policy_is_trust_not_project_perm" in reject_reasons(**payload)
    assert evidence_verdict(**payload) == VERDICT_REJECTED


def test_impersonation_or_token_mint_is_rejected():
    payload = _self_attested_flags(
        actual_caller=CANDIDATE,
        caller_test_iam_permissions=set(BOOTSTRAP_PERMISSIONS),
        impersonation_or_token_mint_used=True,
    )
    assert "impersonation_or_token_mint_forbidden" in reject_reasons(**payload)
    assert evidence_verdict(**payload) == VERDICT_REJECTED


def test_candidate_equals_recipient_executor_is_rejected():
    payload = _self_attested_flags(candidate_principal=EXECUTOR)
    assert "candidate_equals_recipient_executor" in reject_reasons(**payload)
    assert evidence_verdict(**payload) == VERDICT_REJECTED


def test_candidate_principal_mismatch_in_bindings_is_rejected():
    payload = _base(candidate_project_role_bindings={ROLE: {OTHER_CANDIDATE}})
    reasons = reject_reasons(**payload)
    assert "candidate_principal_not_in_bindings" in reasons
    assert evidence_verdict(**payload) == VERDICT_REJECTED


def test_role_expansion_missing_is_rejected():
    payload = _base(candidate_role_permission_sets=None)
    assert "role_permission_expansion_missing" in reject_reasons(**payload)
    assert evidence_verdict(**payload) == VERDICT_REJECTED


def test_resource_mismatch_is_rejected():
    payload = _self_attested_flags(
        expected_resource=PROJECT,
        evidence_resource=OTHER_PROJECT,
        evaluated_resource=OTHER_PROJECT,
        expected_value_sha256=SYNTHETIC_HASH_A,
        observed_value_sha256=SYNTHETIC_HASH_A,
    )
    assert "resource_mismatch" in reject_reasons(**payload)
    assert evidence_verdict(**payload) == VERDICT_REJECTED


def test_one_sided_value_hash_is_rejected():
    payload = _self_attested_flags(expected_value_sha256=SYNTHETIC_HASH_A)
    assert "one_sided_value_hash" in reject_reasons(**payload)
    assert evidence_verdict(**payload) == VERDICT_REJECTED


def test_one_sided_resource_provenance_is_rejected():
    payload = _self_attested_flags(expected_resource=PROJECT)
    assert "one_sided_resource_provenance" in reject_reasons(**payload)
    assert evidence_verdict(**payload) == VERDICT_REJECTED


def test_stale_value_hash_is_rejected():
    payload = _self_attested_flags(
        expected_resource=PROJECT,
        evidence_resource=PROJECT,
        expected_value_sha256=SYNTHETIC_HASH_A,
        observed_value_sha256=SYNTHETIC_HASH_B,
    )
    assert "stale_or_mismatched_value_hash" in reject_reasons(**payload)
    assert evidence_verdict(**payload) == VERDICT_REJECTED


def test_binding_plus_expansion_alone_is_unknown_not_accepted():
    payload = _base()
    assert reject_reasons(**payload) == []
    assert evidence_verdict(**payload) == VERDICT_UNKNOWN
    assert not evidence_accepted(**payload)


def test_self_attested_six_booleans_without_provenance_stay_unknown():
    # Former false-PASS path: bindings + six True flags and no resource/hash.
    payload = _self_attested_flags()
    assert reject_reasons(**payload) == []
    unknowns = unknown_reasons(**payload)
    assert "resource_provenance_incomplete" in unknowns
    assert "value_hash_provenance_incomplete" in unknowns
    assert "trusted_provenance_missing_or_unsupported" in unknowns
    assert evidence_verdict(**payload) == VERDICT_UNKNOWN
    assert not evidence_accepted(**payload)


def test_self_attested_flags_plus_matching_hashes_still_unknown_without_trusted_kind():
    payload = _self_attested_flags(
        expected_resource=PROJECT,
        evidence_resource=PROJECT,
        expected_value_sha256=SYNTHETIC_HASH_A,
        observed_value_sha256=SYNTHETIC_HASH_A,
        trusted_provenance={"kind": "self_attested_fixture"},
    )
    assert "trusted_provenance_missing_or_unsupported" in unknown_reasons(**payload)
    assert evidence_verdict(**payload) == VERDICT_UNKNOWN
    assert evidence_verdict(**payload) != VERDICT_ACCEPTED


def test_forged_provenance_flags_cannot_unlock_accepted():
    payload = _self_attested_flags(
        expected_resource=PROJECT,
        evidence_resource=PROJECT,
        expected_value_sha256=SYNTHETIC_HASH_A,
        observed_value_sha256=SYNTHETIC_HASH_A,
        trusted_provenance={"kind": "forged", "signed": True, "authority": "local"},
    )
    assert evidence_verdict(**payload) == VERDICT_UNKNOWN
    assert not evidence_accepted(**payload)


def test_conditional_binding_unsatisfied_is_unknown_fail_closed():
    payload = _self_attested_flags(
        bindings_have_conditions=True,
        conditions_authoritatively_satisfied=False,
        expected_resource=PROJECT,
        evidence_resource=PROJECT,
        expected_value_sha256=SYNTHETIC_HASH_A,
        observed_value_sha256=SYNTHETIC_HASH_A,
    )
    assert "conditional_binding_not_authoritatively_satisfied" in unknown_reasons(**payload)
    assert evidence_verdict(**payload) == VERDICT_UNKNOWN


def test_caller_candidate_mismatch_with_complete_looking_pack_still_rejects():
    payload = _self_attested_flags(
        caller_test_iam_permissions=set(BOOTSTRAP_PERMISSIONS),
        expected_resource=PROJECT,
        evidence_resource=PROJECT,
        expected_value_sha256=SYNTHETIC_HASH_A,
        observed_value_sha256=SYNTHETIC_HASH_A,
        trusted_provenance={"kind": "forged"},
    )
    assert evidence_verdict(**payload) == VERDICT_REJECTED


def test_remediation_doc_avoids_live_fingerprints_and_documents_unknown():
    import re
    root = Path(__file__).parents[1]
    remediation = (root / "docs/iam/paper-executor-grant-remediation.md").read_text()
    design = (root / "docs/iam/paper-scheduler-get.md").read_text()
    workflow = (root / ".github/workflows/grant-paper-executor-permissions.yml").read_text()
    for text in (remediation, design, workflow):
        assert not re.search(r"\b[0-9a-f]{64}\b", text)
        assert not re.search(r"environments/\d+/edit", text)
        assert not re.search(r"\brun\s+`?\d{8,}", text, flags=re.I)
    assert "SUPPORTED_TRUSTED_PROVENANCE_KINDS" in remediation
    assert "unreachable" in remediation
    assert "history scrubbing" in remediation
    assert "D3 residual" in remediation
    assert "operational prerequisite" in remediation
