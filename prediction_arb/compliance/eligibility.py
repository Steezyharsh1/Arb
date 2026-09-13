"""Pre-trade eligibility gate (§35). UNKNOWN → block live, allow paper."""
from __future__ import annotations

from dataclasses import dataclass

from ..core.types import Mode, NormalizedContract
from .jurisdiction import EligibilityStatus, get_eligibility


@dataclass
class EligibilityDecision:
    allowed: bool
    status: EligibilityStatus
    reason: str


def check_contract_eligibility(contract: NormalizedContract,
                               jurisdiction: str,
                               mode: Mode,
                               block_unknown_status: bool = True
                               ) -> EligibilityDecision:
    rec = get_eligibility(jurisdiction, contract.venue, contract.category)
    if rec.status == EligibilityStatus.ELIGIBLE:
        return EligibilityDecision(True, rec.status, "eligible")
    if rec.status == EligibilityStatus.RESTRICTED:
        return EligibilityDecision(False, rec.status,
                                   f"restricted: {rec.source}")
    # UNKNOWN
    if mode == Mode.LIVE or block_unknown_status:
        if mode == Mode.LIVE:
            return EligibilityDecision(
                False, rec.status,
                f"eligibility UNKNOWN for {contract.venue}/"
                f"{contract.category} in {jurisdiction} — live trading "
                f"blocked (§35)")
    # paper/historical research may continue but the decision is recorded
    allowed = mode in (Mode.PAPER, Mode.HISTORICAL, Mode.COLLECT)
    return EligibilityDecision(
        allowed, rec.status,
        f"eligibility UNKNOWN — {'research allowed' if allowed else 'blocked'}")
