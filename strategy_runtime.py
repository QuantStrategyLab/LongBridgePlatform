from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from quant_platform_kit.strategy_contracts import StrategyContext, StrategyDecision, StrategyEntrypoint

from strategy_loader import load_strategy_entrypoint_for_profile


@dataclass(frozen=True)
class StrategyEvaluationResult:
    decision: StrategyDecision
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LoadedStrategyRuntime:
    entrypoint: StrategyEntrypoint
    runtime_overrides: Mapping[str, Any] = field(default_factory=dict)
    merged_runtime_config: Mapping[str, Any] = field(default_factory=dict)

    @property
    def profile(self) -> str:
        return self.entrypoint.manifest.profile

    @property
    def managed_symbols(self) -> tuple[str, ...]:
        configured = self.merged_runtime_config.get("managed_symbols", ())
        return tuple(str(symbol) for symbol in configured)

    def evaluate(
        self,
        *,
        indicators,
        account_state,
        translator: Callable[[str], str],
    ) -> StrategyEvaluationResult:
        runtime_config = dict(self.runtime_overrides)
        runtime_config.setdefault("translator", translator)
        ctx = StrategyContext(
            as_of=datetime.now(timezone.utc),
            market_data={
                "indicators": indicators,
                "account_state": account_state,
            },
            runtime_config=runtime_config,
        )
        decision = self.entrypoint.evaluate(ctx)
        return StrategyEvaluationResult(
            decision=decision,
            metadata={"strategy_profile": self.profile},
        )


def load_strategy_runtime(raw_profile: str | None) -> LoadedStrategyRuntime:
    entrypoint = load_strategy_entrypoint_for_profile(raw_profile)
    merged_runtime_config = dict(entrypoint.manifest.default_config)
    return LoadedStrategyRuntime(
        entrypoint=entrypoint,
        merged_runtime_config=merged_runtime_config,
    )
