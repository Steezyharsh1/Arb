"""Typed, claim-by-claim adapter evidence for operator-facing status (§101).

A documentation review, an HTTP response, a usable market-data snapshot,
and full adapter verification are different claims. Keeping them in a
small immutable record prevents one successful check (or one network
failure) from being misrepresented as another.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class EvidenceState(str, Enum):
    DOCUMENTED = "DOCUMENTED"
    OBSERVED = "OBSERVED"
    NOT_OBSERVED = "NOT_OBSERVED"
    UNAVAILABLE = "UNAVAILABLE"
    NOT_VERIFIED = "NOT_VERIFIED"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True)
class EvidenceClaim:
    """One factual claim, with its date, source, and scope."""

    state: EvidenceState
    as_of: Optional[str] = None
    source: str = ""
    detail: str = ""

    @property
    def label(self) -> str:
        """Compact state/date for tables; detail and source stay adjacent."""
        return f"{self.state.value} ({self.as_of})" if self.as_of else self.state.value


@dataclass(frozen=True)
class AdapterEvidence:
    """Evidence layers that must not be collapsed into one status."""

    documentation: EvidenceClaim
    connectivity: EvidenceClaim
    usable_market_data: EvidenceClaim
    full_adapter: EvidenceClaim
    latest_smoke: EvidenceClaim
    surfaces: tuple[str, ...] = ()


def unevidenced_adapter() -> AdapterEvidence:
    """Safe default for adapter doubles and adapters without a ledger."""
    missing = EvidenceClaim(
        EvidenceState.NOT_OBSERVED,
        detail="No adapter-specific evidence record is supplied.")
    return AdapterEvidence(
        documentation=missing,
        connectivity=missing,
        usable_market_data=missing,
        full_adapter=EvidenceClaim(
            EvidenceState.NOT_VERIFIED,
            detail="Full adapter verification has not been evidenced."),
        latest_smoke=EvidenceClaim(EvidenceState.NOT_APPLICABLE),
    )
