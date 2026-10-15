# Advisory shared-dependency impact report

The `Shared dependency impact (advisory)` PR workflow complements existing exact-pin,
checkout SHA and lock-consistency checks. It compares the PR event's exact base and
head commits, resolves each direct shared dependency from `pyproject.toml` and
`uv.lock`, and reports every changed upstream production/other path separately from
documentation, tests and CI/configuration. Supported repositories are explicitly
allowlisted: QuantPlatformKit, UsEquityStrategies and HkEquityStrategies.

The report uses a full Git tree diff, not a paginated GitHub compare response.
Renames are represented as old and new paths so neither side disappears. Fixed
path hints highlight authentication/token refresh, possible cloud/secret writes,
execution, risk and scheduler/automation changes. They are review prompts, not
semantic analysis, a generic dependency graph, a risk score or an allowlist.
Unmatched paths and dynamic imports remain unresolved, never presumed safe.

## Safety and limitations

- Report success is **not** an upgrade-safety verdict or merge approval. This adds
  no auto-block/auto-approve policy and does not change existing CI gates.
- Nothing installs/imports upstream packages, invokes token refresh, reads token
  contents, calls broker/cloud APIs, changes deployment, or writes secrets.
- The workflow uses `pull_request` with `contents: read`, no secrets/OIDC, no PR
  comment writes, and checkout credentials are not persisted. Git fetch targets
  fixed repository URLs with validated full SHAs, no shell interpolation, depth
  one and per-command timeouts. No extra PAT/app grant is requested for forks or
  private dependencies. Unavailable upstream access is explicitly INCOMPLETE.
- Missing pins, mismatches, failed comparisons, invalid/truncated output and report
  size limits produce INCOMPLETE rather than a false clean report. The report is
  advisory, so it returns zero after successfully writing that warning. Test or
  infrastructure failures still fail the workflow; inspect the actual summary.
- Normal local startup wires `main.build_composer` through
  `application/runtime_bootstrap_adapters.py`'s
  `LongBridgeRuntimeBootstrap.build_contexts` to the injected upstream refresh
  helper. Account-snapshot contexts bypass refresh. These are explicit local
  review notes to revalidate when wiring changes, not generated reachability proof.
- The synthetic `350dd38ece0952beef096893a7dcf8ae3871609a` to
  `155d74a615ccdebf7223787939c2dc7151d75efd` fixture includes
  `src/quant_platform_kit/longbridge/auth.py` along with presentation changes so an
  authentication change cannot be hidden by describing the upgrade as a facade.

## Local use

Python 3.11+ and Git suffice; no runtime environment or credentials are needed for
synthetic tests:

```sh
python -m unittest discover -s tests -p test_review_shared_dependency_delta.py -v
python scripts/review_shared_dependency_delta.py \
  --base-ref FULL_BASE_COMMIT_SHA --head-ref FULL_HEAD_COMMIT_SHA \
  --output /tmp/shared-dependency-impact.md
```

The real comparison needs read access to those GitHub upstream trees. No-change
pins do not fetch upstream. Output contains paths and commit identifiers only,
not source patches or credentials. Fetch and diff output is bounded to 2 MB and
the rendered summary to 900 KB; exceeding either makes the report INCOMPLETE.
