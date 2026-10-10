"""Round-trip codec between `NormalizedContract` and `MarketRow` (D4).

One module owns both directions so a field written at collect time cannot
be silently dropped at replay time. The rule that matters:

    **Decode never invents.** A column that is NULL stays unknown. If a row
    does not carry enough recorded truth to rebuild a contract, decoding
    returns ``None`` and the caller reports the skip — it does not
    substitute a plausible-looking default (§92: never fabricate data).

Rows recorded before migration 002 have every new column NULL, so they
decode to ``None`` and are counted, not guessed at.
"""
from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from typing import Any, Optional

from ..core.types import (
    MarketMicrostructure,
    MicrostructureType,
    NormalizedContract,
    OrderTypeSupport,
    OutcomeType,
)


def _dec(value: Optional[str]) -> Optional[Decimal]:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _json(value: Any) -> Optional[str]:
    if value is None:
        return None
    try:
        return json.dumps(value, default=str)
    except (TypeError, ValueError):
        return None


def _unjson(value: Optional[str]) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def encode_contract(contract: NormalizedContract) -> dict[str, Any]:
    """The `MarketRow` column values for a contract (migration-002 fields).

    Returns only the D4 columns; identity/mode/timestamp columns are still
    the caller's responsibility.
    """
    micro = contract.market_microstructure
    orders = contract.supported_order_types
    return {
        "underlying_exchange": contract.underlying_exchange,
        "clearing_entity": contract.clearing_entity or None,
        "description": contract.event_description,
        "normalized_event": contract.normalized_event or None,
        "outcome_type": contract.outcome_type.value,
        "outcome": contract.outcome or None,
        "normalized_outcome": contract.normalized_outcome or None,
        "outcome_count": contract.outcome_count,
        "all_outcomes_json": _json(list(contract.all_outcomes)),
        "settlement_time": contract.settlement_time,
        "timezone": contract.timezone or None,
        "cutoff_time": contract.cutoff_time,
        "resolution_source": contract.resolution_source or None,
        "resolution_method": contract.resolution_method or None,
        "threshold": (None if contract.threshold is None
                      else str(contract.threshold)),
        "threshold_operator": contract.threshold_operator or None,
        "units": contract.units or None,
        "payout": str(contract.payout),
        "currency": contract.currency or None,
        "early_resolution_rules": contract.early_resolution_rules or None,
        "cancellation_rules": contract.cancellation_rules or None,
        "contract_version": contract.contract_version or None,
        "tick_size": (None if contract.tick_size is None
                      else str(contract.tick_size)),
        "min_order_size": (None if contract.min_order_size is None
                           else str(contract.min_order_size)),
        "max_order_size": (None if contract.max_order_size is None
                           else str(contract.max_order_size)),
        "jurisdiction_requirements": (
            contract.jurisdiction_requirements or None),
        "category": contract.category,
        "microstructure_json": _json({
            "type": micro.type.value,
            "has_order_book": micro.has_order_book,
            "has_pricing_function": micro.has_pricing_function,
            "quote_validity_ms": micro.quote_validity_ms,
            "verified": micro.verified,
            "source": micro.source,
        }),
        "order_types_json": _json({
            "market": orders.market, "limit": orders.limit,
            "ioc": orders.ioc, "fok": orders.fok, "gtc": orders.gtc,
            "verified": orders.verified, "source": orders.source,
        }),
        "raw_json": _json(contract.raw),
    }


#: Columns that must be present (non-NULL) to rebuild a contract at all.
#: Tick/minimum order rules are optional observations: NULL round-trips as
#: unknown, and any future order-capable consumer must gate on them rather
#: than infer a value.
_REQUIRED_FIELDS = (
    "outcome_type", "payout", "contract_version",
)


def decode_contract(row: Any) -> Optional[NormalizedContract]:
    """Rebuild a contract from a `MarketRow`, or ``None`` if under-recorded.

    ``None`` means "this row does not carry enough recorded truth" — the
    caller must count it, never paper over it.
    """
    for field in _REQUIRED_FIELDS:
        if getattr(row, field, None) in (None, ""):
            return None

    payout = _dec(row.payout)
    tick = _dec(getattr(row, "tick_size", None))
    min_size = _dec(getattr(row, "min_order_size", None))
    if payout is None or not payout.is_finite() or payout <= 0:
        return None
    # NULL rules remain explicitly unknown for faithful data replay. A
    # recorded non-finite or non-positive rule is corrupt, not a default.
    if tick is not None and (not tick.is_finite() or tick <= 0):
        return None
    if min_size is not None and (not min_size.is_finite() or min_size <= 0):
        return None

    try:
        outcome_type = OutcomeType(row.outcome_type)
    except ValueError:
        return None

    micro_raw = _unjson(getattr(row, "microstructure_json", None)) or {}
    try:
        micro_type = MicrostructureType(
            micro_raw.get("type", MicrostructureType.UNKNOWN.value))
    except ValueError:
        micro_type = MicrostructureType.UNKNOWN
    micro = MarketMicrostructure(
        type=micro_type,
        has_order_book=bool(micro_raw.get("has_order_book", False)),
        has_pricing_function=bool(micro_raw.get("has_pricing_function", False)),
        quote_validity_ms=micro_raw.get("quote_validity_ms"),
        verified=bool(micro_raw.get("verified", False)),
        source=str(micro_raw.get("source", "")))

    orders_raw = _unjson(getattr(row, "order_types_json", None)) or {}
    orders = OrderTypeSupport(
        market=bool(orders_raw.get("market", False)),
        limit=bool(orders_raw.get("limit", False)),
        ioc=bool(orders_raw.get("ioc", False)),
        fok=bool(orders_raw.get("fok", False)),
        gtc=bool(orders_raw.get("gtc", False)),
        verified=bool(orders_raw.get("verified", False)),
        source=str(orders_raw.get("source", "")))

    outcomes = _unjson(getattr(row, "all_outcomes_json", None))
    settlement = getattr(row, "settlement_time", None)
    if settlement is not None and settlement.tzinfo is None:
        from datetime import timezone
        settlement = settlement.replace(tzinfo=timezone.utc)
    cutoff = getattr(row, "cutoff_time", None)
    if cutoff is not None and cutoff.tzinfo is None:
        from datetime import timezone
        cutoff = cutoff.replace(tzinfo=timezone.utc)

    return NormalizedContract(
        venue=row.venue,
        underlying_exchange=row.underlying_exchange or "",
        clearing_entity=getattr(row, "clearing_entity", None) or "",
        market_id=row.market_id,
        contract_id=row.contract_id,
        event_description=row.description or "",
        normalized_event=getattr(row, "normalized_event", None) or "",
        outcome_type=outcome_type,
        outcome=getattr(row, "outcome", None) or "",
        normalized_outcome=getattr(row, "normalized_outcome", None) or "",
        outcome_count=getattr(row, "outcome_count", None) or 0,
        all_outcomes=tuple(outcomes) if outcomes else (),
        settlement_time=settlement,
        timezone=getattr(row, "timezone", None) or "UTC",
        cutoff_time=cutoff,
        resolution_source=getattr(row, "resolution_source", None) or "",
        resolution_method=getattr(row, "resolution_method", None) or "",
        threshold=_dec(getattr(row, "threshold", None)),
        threshold_operator=getattr(row, "threshold_operator", None) or None,
        units=getattr(row, "units", None) or None,
        payout=payout,
        currency=getattr(row, "currency", None) or "USD",
        early_resolution_rules=(
            getattr(row, "early_resolution_rules", None) or ""),
        cancellation_rules=getattr(row, "cancellation_rules", None) or "",
        contract_version=row.contract_version or "",
        tick_size=tick,
        min_order_size=min_size,
        max_order_size=_dec(getattr(row, "max_order_size", None)),
        jurisdiction_requirements=(
            getattr(row, "jurisdiction_requirements", None) or ""),
        category=row.category or "",
        market_microstructure=micro,
        supported_order_types=orders,
        raw=_unjson(getattr(row, "raw_json", None)))


def encode_snapshot_contract(row: Any) -> Optional[str]:
    """Freeze a recorded contract row, not a reference to mutable markets."""
    contract = decode_contract(row)
    if contract is None:
        return None
    values = {"venue": contract.venue, "market_id": contract.market_id,
              "contract_id": contract.contract_id, **encode_contract(contract)}
    return json.dumps(values, default=lambda value: value.isoformat(),
                      sort_keys=True)


def decode_snapshot_contract(payload: Optional[str]) -> Optional[NormalizedContract]:
    """Decode snapshot-bound terms; malformed context stays unavailable."""
    from ..core.clock import parse_iso_utc
    values = _unjson(payload)
    if not isinstance(values, dict):
        return None
    if not all(values.get(k) for k in ("venue", "market_id", "contract_id")):
        return None
    for name in ("settlement_time", "cutoff_time"):
        value = values.get(name)
        parsed = parse_iso_utc(value) if isinstance(value, str) else None
        if value is not None and parsed is None:
            return None
        values[name] = parsed
    try:
        return decode_contract(SimpleNamespace(**values))
    except (AttributeError, TypeError, ValueError, InvalidOperation):
        return None
