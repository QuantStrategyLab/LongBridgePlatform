"""Offline classifiers for PAPER IAM bootstrap discovery evidence.

No network I/O. Distinguishes hard rejects, UNKNOWN (fail-closed), and a
reserved ACCEPTED path that requires a supported trusted provenance kind.

No supported trusted provenance kind is registered in this repository. Local
booleans, fixture packs, and self-attested completeness flags therefore cannot
produce operational ACCEPTED. Structural tests exercise reject/unknown only.
This module never calls GCP and never mints credentials.
"""

from __future__ import annotations

# Same four permissions the grant bootstrap must hold to run Card C.
BOOTSTRAP_PERMISSIONS = (
    "iam.roles.get",
    "resourcemanager.projects.getIamPolicy",
    "iam.roles.create",
    "resourcemanager.projects.setIamPolicy",
)

# Recipient receives these via Card C as role paperSchedulerIamExecutor.
RECIPIENT_GRANT_PERMISSIONS = BOOTSTRAP_PERMISSIONS

VERDICT_REJECTED = "rejected"
VERDICT_UNKNOWN = "unknown"
VERDICT_ACCEPTED = "accepted"

# Empty until an owner-approved, non-self-attested provenance scheme is wired.
# Do not invent authority here (no local "self_attested" / fixture kinds).
SUPPORTED_TRUSTED_PROVENANCE_KINDS: frozenset[str] = frozenset()


def reject_reasons(
    *,
    candidate_principal: str,
    evaluated_resource: str,
    actual_caller: str,
    evaluated_permissions: tuple[str, ...] | list[str],
    caller_test_iam_permissions: set[str] | None = None,
    candidate_project_role_bindings: dict[str, set[str]] | None = None,
    candidate_role_permission_sets: dict[str, set[str]] | None = None,
    candidate_sa_iam_policy_members: set[str] | None = None,
    impersonation_or_token_mint_used: bool = False,
    recipient_executor_principal: str | None = None,
    expected_resource: str | None = None,
    evidence_resource: str | None = None,
    expected_value_sha256: str | None = None,
    observed_value_sha256: str | None = None,
    **_ignored,
) -> list[str]:
    """Return hard-reject categories (definite failure of the evidence pack)."""
    reasons: list[str] = []
    wanted = set(evaluated_permissions)
    if not candidate_principal or not evaluated_resource or not actual_caller:
        reasons.append("missing_principal_resource_or_caller")
    if not wanted:
        reasons.append("missing_evaluated_permissions")
    if recipient_executor_principal and candidate_principal == recipient_executor_principal:
        reasons.append("candidate_equals_recipient_executor")
    if impersonation_or_token_mint_used:
        reasons.append("impersonation_or_token_mint_forbidden")

    if (expected_resource is None) ^ (evidence_resource is None):
        reasons.append("one_sided_resource_provenance")
    elif expected_resource is not None and evidence_resource is not None:
        if expected_resource != evidence_resource or evaluated_resource != evidence_resource:
            reasons.append("resource_mismatch")

    if (expected_value_sha256 is None) ^ (observed_value_sha256 is None):
        reasons.append("one_sided_value_hash")
    elif expected_value_sha256 is not None and observed_value_sha256 is not None:
        if expected_value_sha256 != observed_value_sha256:
            reasons.append("stale_or_mismatched_value_hash")

    # projects.testIamPermissions returns only the caller's permissions on the
    # resource; the request has no candidate principal field.
    # https://cloud.google.com/resource-manager/reference/rest/v1/projects/testIamPermissions
    if caller_test_iam_permissions is not None:
        if actual_caller != candidate_principal:
            if wanted <= set(caller_test_iam_permissions):
                reasons.append("caller_test_iam_not_candidate_effective")
            reasons.append("test_iam_permissions_is_caller_scoped")

    if candidate_sa_iam_policy_members is not None and candidate_project_role_bindings is None:
        reasons.append("sa_get_iam_policy_is_trust_not_project_perm")

    held: set[str] = set()
    if candidate_project_role_bindings and candidate_role_permission_sets:
        members_union: set[str] = set()
        for role, members in candidate_project_role_bindings.items():
            members_union |= set(members)
            if candidate_principal in members:
                held |= set(candidate_role_permission_sets.get(role, ()))
        if candidate_principal not in members_union:
            reasons.append("candidate_principal_not_in_bindings")
        if not (wanted <= held):
            reasons.append("candidate_project_bindings_missing_permissions")
    elif candidate_project_role_bindings is None and caller_test_iam_permissions is None:
        reasons.append("no_candidate_effective_permission_evidence")
    elif candidate_project_role_bindings is not None and candidate_role_permission_sets is None:
        reasons.append("role_permission_expansion_missing")

    return _dedupe(reasons)


def unknown_reasons(
    *,
    candidate_project_role_bindings: dict[str, set[str]] | None = None,
    candidate_role_permission_sets: dict[str, set[str]] | None = None,
    bindings_have_conditions: bool | None = None,
    conditions_authoritatively_satisfied: bool | None = None,
    deny_policies_authoritatively_evaluated: bool | None = None,
    principal_access_boundary_authoritatively_evaluated: bool | None = None,
    inherited_allow_deny_context_complete: bool | None = None,
    principal_identity_context_complete: bool | None = None,
    time_context_complete: bool | None = None,
    expected_resource: str | None = None,
    evidence_resource: str | None = None,
    expected_value_sha256: str | None = None,
    observed_value_sha256: str | None = None,
    trusted_provenance: dict | None = None,
    **_ignored,
) -> list[str]:
    """Return UNKNOWN categories: fail-closed without trusted provenance.

    Self-attested booleans (conditions/deny/PAB/etc.) are structural checklist
    fields only. They never authorize ACCEPTED by themselves. Resource and hash
    pairs must both be present for a complete pack; missing pairs stay UNKNOWN
    when not already hard-rejected as one-sided.
    """
    reasons: list[str] = []
    has_binding_expansion = bool(
        candidate_project_role_bindings and candidate_role_permission_sets
    )
    if not has_binding_expansion:
        return reasons

    if bindings_have_conditions is None:
        reasons.append("binding_condition_presence_unknown")
    elif bindings_have_conditions and conditions_authoritatively_satisfied is not True:
        reasons.append("conditional_binding_not_authoritatively_satisfied")

    if deny_policies_authoritatively_evaluated is not True:
        reasons.append("deny_policy_context_unknown")
    if principal_access_boundary_authoritatively_evaluated is not True:
        reasons.append("principal_access_boundary_context_unknown")
    if inherited_allow_deny_context_complete is not True:
        reasons.append("inherited_allow_deny_context_unknown")
    if principal_identity_context_complete is not True:
        reasons.append("principal_identity_context_unknown")
    if time_context_complete is not True:
        reasons.append("time_context_unknown")

    if expected_resource is None or evidence_resource is None:
        reasons.append("resource_provenance_incomplete")
    if expected_value_sha256 is None or observed_value_sha256 is None:
        reasons.append("value_hash_provenance_incomplete")

    kind = None
    if isinstance(trusted_provenance, dict):
        kind = trusted_provenance.get("kind")
    if kind not in SUPPORTED_TRUSTED_PROVENANCE_KINDS:
        reasons.append("trusted_provenance_missing_or_unsupported")

    return _dedupe(reasons)


def evidence_verdict(**kwargs) -> str:
    """Three-state verdict: rejected | unknown | accepted.

    ACCEPTED is reserved for a future supported trusted provenance kind.
    With ``SUPPORTED_TRUSTED_PROVENANCE_KINDS`` empty, ACCEPTED is unreachable
    from fixtures or local assertions. Fake packs never prove live IAM.
    """
    if reject_reasons(**kwargs):
        return VERDICT_REJECTED
    if unknown_reasons(**kwargs):
        return VERDICT_UNKNOWN
    if not kwargs.get("candidate_project_role_bindings") or not kwargs.get(
            "candidate_role_permission_sets"):
        return VERDICT_UNKNOWN
    # Provenance allowlist empty ⇒ unreachable; keep explicit guard.
    provenance = kwargs.get("trusted_provenance")
    kind = provenance.get("kind") if isinstance(provenance, dict) else None
    if kind not in SUPPORTED_TRUSTED_PROVENANCE_KINDS:
        return VERDICT_UNKNOWN
    return VERDICT_ACCEPTED


def evidence_accepted(**kwargs) -> bool:
    """True only for ACCEPTED. UNKNOWN and REJECTED are both fail-closed."""
    return evidence_verdict(**kwargs) == VERDICT_ACCEPTED


def _dedupe(reasons: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for reason in reasons:
        if reason not in seen:
            seen.add(reason)
            ordered.append(reason)
    return ordered
