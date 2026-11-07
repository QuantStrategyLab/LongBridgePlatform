"""Offline classifiers for PAPER IAM bootstrap discovery evidence.

No network I/O. Encodes the B0 contract so operator-held permissions cannot be
mistaken for a candidate service account's project permissions.
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
# That outcome is intentional and distinct from using the recipient as bootstrap.
RECIPIENT_GRANT_PERMISSIONS = BOOTSTRAP_PERMISSIONS


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
) -> list[str]:
    """Return fixed reject categories; empty list means evidence is acceptable.

    Distinguishes:
    - candidate_principal: identity whose project powers are under review
    - evaluated_resource: usually projects/<id>
    - evaluated_permissions: permission strings being checked
    - actual_caller: authenticated principal that invoked any API
    - trust binding on the SA (getIamPolicy) vs project-effective permissions
    """
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

    # projects.testIamPermissions returns only the caller's permissions on the
    # resource; the request has no candidate principal field.
    # https://cloud.google.com/resource-manager/reference/rest/v1/projects/testIamPermissions
    if caller_test_iam_permissions is not None:
        if actual_caller != candidate_principal:
            if wanted <= set(caller_test_iam_permissions):
                reasons.append("caller_test_iam_not_candidate_effective")
            reasons.append("test_iam_permissions_is_caller_scoped")

    # SA getIamPolicy describes who may act as the SA (trust), not which project
    # permissions the SA itself holds.
    if candidate_sa_iam_policy_members is not None and candidate_project_role_bindings is None:
        reasons.append("sa_get_iam_policy_is_trust_not_project_perm")

    held: set[str] = set()
    if candidate_project_role_bindings and candidate_role_permission_sets:
        for role, members in candidate_project_role_bindings.items():
            if candidate_principal in members:
                held |= set(candidate_role_permission_sets.get(role, ()))
        if not (wanted <= held):
            reasons.append("candidate_project_bindings_missing_permissions")
    elif candidate_project_role_bindings is None and caller_test_iam_permissions is None:
        reasons.append("no_candidate_effective_permission_evidence")
    elif candidate_project_role_bindings is not None and candidate_role_permission_sets is None:
        reasons.append("role_permission_expansion_missing")

    # De-duplicate while preserving order.
    seen: set[str] = set()
    ordered: list[str] = []
    for reason in reasons:
        if reason not in seen:
            seen.add(reason)
            ordered.append(reason)
    return ordered


def evidence_accepted(**kwargs) -> bool:
    return not reject_reasons(**kwargs)
