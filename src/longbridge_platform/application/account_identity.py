"""Read-only LongBridge account-identity evidence adapter."""

from __future__ import annotations

from typing import Any

from quant_platform_kit.common.account_identity import (
    AccountIdentityEvidenceSource,
    BrokerAccountIdentity,
)


def observe_longbridge_account_identity(trade_context: Any) -> BrokerAccountIdentity | None:
    """Return only the account type exposed by LongBridge's positions API.

    LongBridge's public trade API exposes ``account_channel`` on position
    channels, but it does not expose a stable account number or paper/live
    marker for this comparison.  Returning ``None`` on an API failure lets the
    shared policy produce a redacted evidence-unavailable finding.
    """

    try:
        response = trade_context.stock_positions()
    except Exception:
        return None
    channels = getattr(response, "channels", None)
    if channels is None:
        return None
    account_types = tuple(
        str(getattr(channel, "account_channel", "") or "").strip()
        for channel in channels
        if str(getattr(channel, "account_channel", "") or "").strip()
    )
    return BrokerAccountIdentity(
        platform_id="longbridge",
        evidence_source=AccountIdentityEvidenceSource.BROKER_API_PARTIAL,
        account_types=account_types,
    )
