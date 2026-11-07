"""Fake-only negative contracts for bootstrap discovery evidence semantics."""
from scripts.paper_iam_discovery_evidence import (
    BOOTSTRAP_PERMISSIONS,
    RECIPIENT_GRANT_PERMISSIONS,
    evidence_accepted,
    reject_reasons,
)

PROJECT = "projects/fake-paper-project"
OPERATOR = "user:operator@example.invalid"
CANDIDATE = "serviceAccount:candidate@fake-paper-project.iam.gserviceaccount.com"
EXECUTOR = "serviceAccount:executor@fake-paper-project.iam.gserviceaccount.com"
ROLE = "projects/fake-paper-project/roles/paperIamBootstrap"


def test_bootstrap_and_recipient_permission_strings_match_by_owner_design():
    # Same strings; different stage/principal — see remediation B1 versus Card C.
    assert BOOTSTRAP_PERMISSIONS == RECIPIENT_GRANT_PERMISSIONS
    assert len(BOOTSTRAP_PERMISSIONS) == 4


def test_operator_test_iam_holding_all_four_does_not_prove_candidate():
    reasons = reject_reasons(
        candidate_principal=CANDIDATE,
        evaluated_resource=PROJECT,
        actual_caller=OPERATOR,
        evaluated_permissions=BOOTSTRAP_PERMISSIONS,
        caller_test_iam_permissions=set(BOOTSTRAP_PERMISSIONS),
        candidate_project_role_bindings={ROLE: set()},  # candidate unbound
        candidate_role_permission_sets={ROLE: set(BOOTSTRAP_PERMISSIONS)},
        candidate_sa_iam_policy_members={OPERATOR},
    )
    assert "caller_test_iam_not_candidate_effective" in reasons
    assert "test_iam_permissions_is_caller_scoped" in reasons
    assert "candidate_project_bindings_missing_permissions" in reasons
    assert not evidence_accepted(
        candidate_principal=CANDIDATE,
        evaluated_resource=PROJECT,
        actual_caller=OPERATOR,
        evaluated_permissions=BOOTSTRAP_PERMISSIONS,
        caller_test_iam_permissions=set(BOOTSTRAP_PERMISSIONS),
    )


def test_sa_get_iam_policy_alone_is_trust_not_project_permission_proof():
    reasons = reject_reasons(
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
    assert "sa_get_iam_policy_is_trust_not_project_perm" in reasons
    assert not evidence_accepted(
        candidate_principal=CANDIDATE,
        evaluated_resource=PROJECT,
        actual_caller=OPERATOR,
        evaluated_permissions=BOOTSTRAP_PERMISSIONS,
        candidate_sa_iam_policy_members={"user:anyone@example.invalid"},
    )


def test_impersonation_or_token_mint_is_rejected_even_if_bindings_look_complete():
    reasons = reject_reasons(
        candidate_principal=CANDIDATE,
        evaluated_resource=PROJECT,
        actual_caller=CANDIDATE,
        evaluated_permissions=BOOTSTRAP_PERMISSIONS,
        caller_test_iam_permissions=set(BOOTSTRAP_PERMISSIONS),
        candidate_project_role_bindings={ROLE: {CANDIDATE}},
        candidate_role_permission_sets={ROLE: set(BOOTSTRAP_PERMISSIONS)},
        impersonation_or_token_mint_used=True,
    )
    assert "impersonation_or_token_mint_forbidden" in reasons
    assert not evidence_accepted(
        candidate_principal=CANDIDATE,
        evaluated_resource=PROJECT,
        actual_caller=CANDIDATE,
        evaluated_permissions=BOOTSTRAP_PERMISSIONS,
        candidate_project_role_bindings={ROLE: {CANDIDATE}},
        candidate_role_permission_sets={ROLE: set(BOOTSTRAP_PERMISSIONS)},
        impersonation_or_token_mint_used=True,
    )


def test_candidate_equals_recipient_executor_is_rejected():
    reasons = reject_reasons(
        candidate_principal=EXECUTOR,
        evaluated_resource=PROJECT,
        actual_caller=OPERATOR,
        evaluated_permissions=BOOTSTRAP_PERMISSIONS,
        candidate_project_role_bindings={ROLE: {EXECUTOR}},
        candidate_role_permission_sets={ROLE: set(BOOTSTRAP_PERMISSIONS)},
        recipient_executor_principal=EXECUTOR,
    )
    assert "candidate_equals_recipient_executor" in reasons


def test_project_bindings_with_role_expansion_accept_distinct_candidate():
    assert evidence_accepted(
        candidate_principal=CANDIDATE,
        evaluated_resource=PROJECT,
        actual_caller=OPERATOR,
        evaluated_permissions=BOOTSTRAP_PERMISSIONS,
        candidate_project_role_bindings={ROLE: {CANDIDATE}},
        candidate_role_permission_sets={ROLE: set(BOOTSTRAP_PERMISSIONS)},
        recipient_executor_principal=EXECUTOR,
        impersonation_or_token_mint_used=False,
    )


def test_remediation_doc_forbids_caller_scoped_false_pass():
    from pathlib import Path
    text = (Path(__file__).parents[1] / "docs/iam/paper-executor-grant-remediation.md").read_text()
    assert "returns permissions that a caller has on the specified Project" in text
    assert "caller_test_iam_not_candidate_effective" in text
    assert "sa_get_iam_policy_is_trust_not_project_perm" in text
    assert "B1 versus Card C" in text
    assert "operational prerequisite" in text
    assert "technically detect or reject an org-only value" in text
    assert "must not claim a technical org-only block" in text
