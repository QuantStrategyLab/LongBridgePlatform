# LongBridgePlatform

[Chinese README](README.zh-CN.md)

> ⚠️ Investing involves risk. This project does not provide investment advice and is for educational and research purposes only.

## What this project does

LongBridgePlatform is an **Execution platform** in the QuantStrategyLab ecosystem. It executes QuantStrategyLab US and Hong Kong equity strategies through LongBridge-compatible runtime services, with region settings, dry-run controls, and cloud deployment workflows.

## Who this is for

- Engineers and researchers who want to inspect, reproduce, or extend this part of the QuantStrategyLab stack.
- Operators who need a clear entry point before reading the deeper runbooks or workflow files.
- Reviewers who need to understand the repository purpose, safety boundary, and evidence requirements before enabling automation.

## Current status

Production-oriented platform code for brokerage execution; start with dry-run and smallest possible scope.

## Repository layout

- `application/`, `entrypoints/`, `notifications/`, `strategy/`: Python package code.
- `tests/`: unit and contract tests.
- `docs/`: detailed design notes, runbooks, and evidence docs.
- `.github/workflows/`: CI, scheduled jobs, and deployment workflows.
- `scripts/`: operator scripts and local helpers.

## Quick start

From a fresh clone:

```bash
python -m pip install -r requirements.txt
python -m pytest -q
```

If a command requires credentials, run it only after reading the relevant workflow or runbook and configuring secrets outside Git.

## Deployment and operation

Configure GitHub Actions or cloud runtime variables for LongBridge credentials, account scope, strategy package, and dry-run mode. Deploy from the provided workflows or Cloud Run configuration after a successful dry-run.

Prefer manual or dry-run execution first. Enable schedules or live execution only after logs, artifacts, permissions, and rollback steps are reviewed.

## Strategy performance and evidence

This platform consumes live-enabled strategy metadata; it does not decide whether a strategy is good. Use the relevant US/HK strategy and snapshot repositories for return, drawdown, and benchmark evidence.

README files are intentionally not a source of dated performance promises. Re-run the relevant tests, backtests, or pipeline jobs before relying on any result.

## Safety notes

- Never commit API keys, broker credentials, OAuth tokens, cookies, or account identifiers.
- Run new strategies and platform changes in dry-run or paper mode before any live execution.
- Review generated orders, artifacts, and logs manually before enabling schedules.

## Contributing

Keep changes small, reproducible, and covered by the narrowest useful tests. For strategy-facing changes, include the evidence artifact or command used to validate behavior.

## License

See [LICENSE](LICENSE) if present in this repository.
