# Daily notification runtime overlay

This patch backports the daily-notification behavior from #634 onto the
reviewed runtime source baseline `822037aae994c26a38586a46397f941de5b91a60`.
It retains that baseline's strategy, risk, account and dependency code. Before
applying it, verify every affected baseline file against the selected immutable
image. Use `git apply --check --unidiff-zero` before `git apply --unidiff-zero`; the patch does not authorize
a deployment or configuration changes.

Run `test_daily_policy_offline.py` and the notification contract, storage and
routing tests against the exact baseline dependencies. Verify the resulting
image layers and non-image runtime configuration before a rollout. Runtime
bindings and deployment-specific values belong in protected configuration.
