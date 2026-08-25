from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
QPK_SRC = ROOT.parent / "QuantPlatformKit" / "src"
if QPK_SRC.exists() and str(QPK_SRC) not in sys.path:
    sys.path.insert(0, str(QPK_SRC))

from application.account_identity import observe_longbridge_account_identity


def test_longbridge_identity_observer_uses_broker_account_channels_only():
    observation = observe_longbridge_account_identity(
        SimpleNamespace(
            stock_positions=lambda: SimpleNamespace(
                channels=(
                    SimpleNamespace(account_channel="Cash"),
                    SimpleNamespace(account_channel="Margin"),
                    SimpleNamespace(account_channel="Cash"),
                )
            )
        )
    )

    assert observation is not None
    assert observation.platform_id == "longbridge"
    assert observation.evidence_source.value == "broker_api_partial"
    assert observation.account_types == ("cash", "margin")
    assert observation.account_id_fingerprint is None
    assert observation.account_modes == ()


def test_longbridge_identity_observer_returns_no_evidence_when_broker_read_fails():
    class FailingContext:
        def stock_positions(self):
            raise RuntimeError("broker unavailable")

    assert observe_longbridge_account_identity(FailingContext()) is None
