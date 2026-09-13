"""Contract normalization helpers (§16).

Adapters produce NormalizedContract objects; this module fills in the
cross-venue `normalized_event` / `normalized_outcome` keys used for
grouping candidates before matching. Normalization here is deliberately
conservative text canonicalization — it creates CANDIDATE groups only.
Actual equivalence is decided by the matching pipeline (§18–§20).
"""
from __future__ import annotations

import dataclasses
import re
import unicodedata

from ..core.types import NormalizedContract

_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s\.\-%]")


def canonical_text(text: str) -> str:
    t = unicodedata.normalize("NFKC", text or "").lower().strip()
    t = _PUNCT.sub(" ", t)
    t = _WS.sub(" ", t)
    return t


def normalized_event_key(contract: NormalizedContract) -> str:
    """Build a coarse grouping key: canonical event text + settlement date.

    Coarse on purpose: false grouping is harmless (the deterministic
    matcher rejects), but a missed grouping hides a real match. We key on
    the settlement DATE (UTC) rather than exact time because venues quote
    slightly different expiration timestamps for the same real-world event.
    """
    date = (contract.settlement_time.date().isoformat()
            if contract.settlement_time else "nodate")
    return f"{canonical_text(contract.event_description)}|{date}"


def with_normalized_keys(contract: NormalizedContract) -> NormalizedContract:
    return dataclasses.replace(
        contract,
        normalized_event=normalized_event_key(contract),
        normalized_outcome=canonical_text(contract.outcome),
    )


def group_by_event(contracts: list[NormalizedContract]
                   ) -> dict[str, list[NormalizedContract]]:
    groups: dict[str, list[NormalizedContract]] = {}
    for c in contracts:
        c = c if c.normalized_event else with_normalized_keys(c)
        groups.setdefault(c.normalized_event, []).append(c)
    return groups
