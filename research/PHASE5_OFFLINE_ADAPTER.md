# Phase 5 LongBridge offline research adapter

## Frozen scope

This slice compares the already frozen Phase 4 v3 QQQM/TQQQ/BOXX research
candidate with an isolated LongBridge offline representation. It does not
change LongBridge production execution, account, market-data, release, risk,
workflow or broker submission behavior. It does not grant paper, shadow or live
authority.

The exact write whitelist is:

- `research/phase5_offline_adapter.py`: a pure, unregistered research adapter;
- `tests/test_phase5_offline_adapter.py`: synthetic and approved-local-input checks;
- `research/PHASE5_OFFLINE_ADAPTER.md`: this scope and observed evidence.

The branch starts at LongBridgePlatform `origin/main`
`aa8432bd6d3c8dd8ed3a441241a146dd0ff1ca30`. Its locked shared package
refs are QPK `c7646a7168b3dafa763ef7751a182d23e8de7790` and UES
`8412d8338879f335ef8830678c2733e02e53e7c7`. Neither includes the
unmerged frozen Phase 4 v3 research candidate. The authorized research source
remains the separate UES worktree at
`aa555fa7cf04d85805de6301de468884755ccdde`, with Phase 4 v3 policy
SHA256 `95d19f727a5e7cae1d9ab5a4251d260acca0f5370ef075c85312a06887e2e20e`
and approved input manifest SHA256
`cb14a511083c824a748d137a271c93cfe0e8adf38f648905b26e37decf4c6182`.

The original LongBridge worktree has unrelated uncommitted changes and is
untouched. Inspected workflows run CI on pull requests; deployment and paper
candidate application require explicit dispatch, and automatic merge applies
only to eligible non-draft Dependabot pull requests. This branch will remain a
draft research PR.

## Starting diagnostic

The existing pure LongBridge paper mapper at
`application/paper_execution_command_consumer.py` had source SHA256
`71a6297f2dd0de678fa2e66c1b7ced3ee8983536643a6a15c28c96d29f56fe18`.
On the eight SHA-checked frozen 10 bps first-open paths, its value-delta
quantity matched no complete research whole-share set; all eight paths had at
least one fractional proposal, and one proposed a positive quantity where the
research ledger bought zero shares. No account, command consumer, execution
cycle or broker port was called. This diagnoses the pure mapper, not an actual
LongBridge fill or rejection.

The default native execution path also has distinct small-account bootstrap,
safe-haven cash substitution and limit-price funding rules. Those rules are
not implicitly changed to fit the frozen research candidate. The bounded
offline adapter must preserve the original research targets, member ownership,
whole-share quantities, costs and funding state separately from native order
semantics; unsupported states must remain explicit.

## Implemented conversion contract

`convert_funded_phase5_proposal(plan, funded)` accepts the frozen prior-close
plan and an already computed, open-price funded research proposal. It verifies
candidate and budget-policy identity, signal and execution dates, whole-share
targets, member ownership, buy order, filled quantities, cost at the declared
5/10/15 bps scenario, available settled cash, TQQQ member reserve, post-open
shares and cash, and the account wealth identity. It independently recomputes
the first-open funding bound from the supplied plan; it does not generate the
strategy target or run the historical core. The proposal cannot simply assert
success: altered trades, costs, cash, reserves, ownership or dates are rejected.

The output keeps the frozen USD and share targets, the explicitly unfilled
shares, research costs and simulated account state separate from a LongBridge
offline row. Each row has the `.US` platform symbol, buy side, positive whole
share quantity and `quantity_step=1`, open-price reference, proposed before and
after shares, and exposure direction. This row is not a native LongBridge
order, limit-price decision or broker fill. Zero-share targets produce no row.

This slice supports the cash-only first open with no existing holdings, sale
queue, receivables, option/collateral obligations or external flows. Other
states fail explicitly. It does not claim sale, T+2, dividend, corporate-action
reconstruction, full-window or production-platform equivalence. The verified
proposal is supplied separately; this adapter must not call the existing
fractional value-target mapper to recalculate it.

## Direct results

The approved local private source and daily ledgers were reused without new
acquisition. The eight predeclared first-open paths at 10 bps comprise four
principal scales and the enhanced/matched-defense pair. The test consumed the
reviewed Schwab pure research funding projection as an upstream calculation,
then independently checked its frozen plan and converted the result. All eight
LongBridge offline share sets matched the separately saved research ledger;
settled cash, executed cost and member reserve matched at `1e-6` USD
tolerance. One funded shortfall remained explicit. This is conversion fidelity
plus direct first-open accounting verification, not an independent rerun of the
funding strategy or a native LongBridge execution test.

Sixteen synthetic/negative direct test cases passed alongside the eight-path
opt-in private test. The cases cover repeated pure evaluation, source mutation
protection, whole-share output, zero-share omission, and rejection of altered
cost, post-state, reserve, owner, status, target or time. The ordinary CI test
skips the private case. No raw private prices, source rows or ledgers are part
of the branch or public PR artifacts.

Astra's preimplementation review recommended this bounded plan-plus-funded
proposal conversion instead of copying a third complete research funding
engine. It required independent verification of the original plan, arithmetic
and account state rather than trusting a success label. Final implementation
review and PR/CI evidence will be recorded after completion.

## Incremental independent review

Astra reviewed the three implemented files against the frozen research funding
contract and returned GO for this bounded offline conversion. It found no
blocking mismatch in the first-open buy order, whole shares, executed-cost
accounting, restricted cash, member reserve, account identity, candidate or
time checks. The review used source and synthetic evidence only; it did not
read or independently rerun private daily data. The eight-path conversion
result above comes from the direct approved-local test. Native LongBridge
execution, complete history and production authorization remain unverified.
