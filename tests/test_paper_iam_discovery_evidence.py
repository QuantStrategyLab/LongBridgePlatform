"""Fake-only adversarial contracts for bootstrap discovery evidence semantics.

These fixtures exercise classifier fail-closed behavior only. They do not prove
live GCP permissions.
"""
from pathlib import Path

from scripts.paper_iam_discovery_evidence import (
    BOOTSTRAP_PERMISSIONS,
    RECIPIENT_GRANT_PERMISSIONS,
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
EXECUTOR_HASH = "382a7cbea2ea0de0f455ed00cf29a3181766399a5cd28a794d00a79d8697cc22"
STALE_HASH = "0000000000000000000000000000000000000000000000000000000000000000"


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


def _authoritative_context(**overrides):
    """Only for offline ACCEPTED shape tests — not a live IAM attestation."""
    payload = _base(
        bindings_have_conditions=False,
        conditions_authoritatively_satisfied=True,
        deny_policies_authoritatively_evaluated=True,
        principal_access_boundary_authoritatively_evaluated=True,
        inherited_allow_deny_context_complete=True,
        principal_identity_context_complete=True,
        time_context_complete=True,
        expected_resource=PROJECT,
        evidence_resource=PROJECT,
        expected_value_sha256=EXECUTOR_HASH,
        observed_value_sha256=EXECUTOR_HASH,
    )
    payload.update(overrides)
    return payload


def test_bootstrap_and_recipient_permission_strings_match_by_owner_design():
    assert BOOTSTRAP_PERMISSIONS == RECIPIENT_GRANT_PERMISSIONS
    assert len(BOOTSTRAP_PERMISSIONS) == 4


def test_operator_test_iam_holding_all_four_does_not_prove_candidate():
    payload = _base(
        caller_test_iam_permissions=set(BOOTSTRAP_PERMISSIONS),
        candidate_project_role_bindings={ROLE: set()},
    )
    reasons = reject_reasons(**payload)
    assert "caller_test_iam_not_candidate_effective" in reasons
    assert "test_iam_permissions_is_caller_scoped" in reasons
    assert "candidate_principal_not_in_bindings" in reasons
    assert evidence_verdict(**payload) == VERDICT_REJECTED
    assert not evidence_accepted(**payload)


def test_sa_get_iam_policy_alone_is_trust_not_project_permission_proof():
    payload = dict(
        candidate_principal=CANDIDATE,
        evaluated_resource=PROJECT,
        actual_caller=OPERATOR,
        evaluated_permissions=BOOTSTRAP_PERMISSIONS,
        candidate_sa_iam_policy_members={
            "principalSet://iam.googleapis.com/projects/1/locations/global/"
            "workloadIdentityPools/fake/attribute.repository/"
            "QuantStrategyLab/LongBridgePlatform"
        },
    )
    assert "sa_get_iam_policy_is_trust_not_project_perm" in reject_reasons(**payload)
    assert evidence_verdict(**payload) == VERDICT_REJECTED


def test_impersonation_or_token_mint_is_rejected_even_if_bindings_look_complete():
    payload = _authoritative_context(
        actual_caller=CANDIDATE,
        caller_test_iam_permissions=set(BOOTSTRAP_PERMISSIONS),
        impersonation_or_token_mint_used=True,
    )
    assert "impersonation_or_token_mint_forbidden" in reject_reasons(**payload)
    assert evidence_verdict(**payload) == VERDICT_REJECTED


def test_candidate_equals_recipient_executor_is_rejected():
    payload = _authoritative_context(candidate_principal=EXECUTOR)
    assert "candidate_equals_recipient_executor" in reject_reasons(**payload)
    assert evidence_verdict(**payload) == VERDICT_REJECTED


def test_candidate_principal_mismatch_in_bindings_is_rejected():
    payload = _base(candidate_project_role_bindings={ROLE: {OTHER_CANDIDATE}})
    reasons = reject_reasons(**payload)
    assert "candidate_principal_not_in_bindings" in reasons
    assert "candidate_project_bindings_missing_permissions" in reasons
    assert evidence_verdict(**payload) == VERDICT_REJECTED


def test_role_expansion_missing_is_rejected():
    payload = _base(candidate_role_permission_sets=None)
    assert "role_permission_expansion_missing" in reject_reasons(**payload)
    assert evidence_verdict(**payload) == VERDICT_REJECTED


def test_resource_mismatch_is_rejected():
    payload = _authoritative_context(
        expected_resource=PROJECT,
        evidence_resource=OTHER_PROJECT,
        evaluated_resource=OTHER_PROJECT,
    )
    assert "resource_mismatch" in reject_reasons(**payload)
    assert evidence_verdict(**payload) == VERDICT_REJECTED


def test_stale_value_hash_is_rejected():
    payload = _authoritative_context(observed_value_sha256=STALE_HASH)
    assert "stale_or_mismatched_value_hash" in reject_reasons(**payload)
    assert evidence_verdict(**payload) == VERDICT_REJECTED


def test_binding_plus_expansion_alone_is_unknown_not_accepted():
    # Necessary but not sufficient: must not false-PASS as effective permission.
    payload = _base()
    assert reject_reasons(**payload) == []
    unknowns = unknown_reasons(**payload)
    assert "binding_condition_presence_unknown" in unknowns
    assert "deny_policy_context_unknown" in unknowns
    assert "principal_access_boundary_context_unknown" in unknowns
    assert "inherited_allow_deny_context_unknown" in unknowns
    assert "principal_identity_context_unknown" in unknowns
    assert "time_context_unknown" in unknowns
    assert evidence_verdict(**payload) == VERDICT_UNKNOWN
    assert not evidence_accepted(**payload)


def test_conditional_binding_unsatisfied_is_unknown_fail_closed():
    payload = _base(
        bindings_have_conditions=True,
        conditions_authoritatively_satisfied=False,
        deny_policies_authoritatively_evaluated=True,
        principal_access_boundary_authoritatively_evaluated=True,
        inherited_allow_deny_context_complete=True,
        principal_identity_context_complete=True,
        time_context_complete=True,
    )
    assert "conditional_binding_not_authoritatively_satisfied" in unknown_reasons(**payload)
    assert evidence_verdict(**payload) == VERDICT_UNKNOWN
    assert not evidence_accepted(**payload)


def test_unknown_deny_or_pab_context_fail_closed_even_with_unconditional_allow():
    payload = _base(
        bindings_have_conditions=False,
        conditions_authoritatively_satisfied=True,
        deny_policies_authoritatively_evaluated=None,
        principal_access_boundary_authoritatively_evaluated=None,
        inherited_allow_deny_context_complete=True,
        principal_identity_context_complete=True,
        time_context_complete=True,
    )
    unknowns = unknown_reasons(**payload)
    assert "deny_policy_context_unknown" in unknowns
    assert "principal_access_boundary_context_unknown" in unknowns
    assert evidence_verdict(**payload) == VERDICT_UNKNOWN


def test_authoritative_complete_pack_is_accepted_offline_only():
    payload = _authoritative_context()
    assert evidence_verdict(**payload) == VERDICT_ACCEPTED
    assert evidence_accepted(**payload)


def test_remediation_doc_forbids_caller_scoped_and_binding_false_pass():
    text = (Path(__file__).parents[1] / "docs/iam/paper-executor-grant-remediation.md").read_text()
    assert "returns permissions that a caller has on the specified Project" in text
    assert "caller_test_iam_not_candidate_effective" in text
    assert "sa_get_iam_policy_is_trust_not_project_perm" in text
    assert "B1 versus Card C" in text
    assert "operational prerequisite" in text
    assert "technically detect or reject an org-only value" in text
    assert "must not claim a technical org-only block" in text
    assert "allow-binding plus role" in text or "allow binding plus role" in text.lower()
    assert "UNKNOWN" in text or "unknown" in text
