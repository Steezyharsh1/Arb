# TRADING LOGIC

## Opportunity taxonomy (Principle D)

- **TYPE A (guaranteed)** — implemented. Buy YES@A + Buy NO@B on
  INDEPENDENT venues for an APPROVED-matched contract pair; profitable
  under every settlement outcome after all costs.
- **TYPE B (logical)** — stub, research-only (§22).
- **TYPE C/D** — not eligible for execution; not implemented in v1.

## The actual test (never headline prices)

For executable quantity q found by walking BOTH ask ladders together
(marginal YES+NO price must stay < payout):

```
yes_walk_cost(q) + no_walk_cost(q)
  + taker_fees(both legs) + settlement_fees
  + settlement_delay_cost(capital, lockup, annual_rate)
  < payout × q
```

- Kalshi taker fee: `ceil_cents(0.07 × C × P × (1−P))` (versioned in
  config/fees.yaml with source + verified date).
- ForecastEx: $0.01/contract structural fee (YES+NO pairs at $1.01).
- Missing fee schedule → `FEE_DATA_INCOMPLETE` → status BLOCK. Never
  assumed zero.
- Unknown settlement lockup → BLOCK, never zero.
- Slippage is *inside* the walked cost (real levels consumed); it is
  additionally reported as WAP−best×qty for transparency, not
  double-counted.

## Sizing

`find_max_profit_quantity` merges both ask ladders and stops at the
first marginal unit whose pair cost ≥ payout. Bounded further by
displayed liquidity of each leg (test case 2), caller max, and risk
limits. Whole contracts only.

## Kalshi order-book semantics (important)

Kalshi's book contains YES bids and NO bids (both resting bids). The
executable YES ask is synthesized: `ask = 1 − NO_bid`. Verified against
the live `orderbook_fp` format 2026-08-30.

## ForecastEx microstructure

PAIRED_BID: no asks exist; YES bid + NO bid execute when they sum to
$1.01. The synthetic YES ask = 1.01 − best NO bid. Encoded in the
adapter's microstructure metadata; the fee engine carries the $0.01.

## Status ladder

- TRADE — all checks pass, net ≥ minimum_net_profit and ROC ≥
  minimum_net_return.
- WATCH — profitable but under thresholds.
- BLOCK — any hard failure (independence, fees, settlement unknown,
  net ≤ 0), with reasons recorded.

## Pre-trade checklist (§33) mapping

Implemented across: match certificate (APPROVED required),
independence (INDEPENDENT required; UNKNOWN blocks), fee freshness
(strict mode for live), book freshness (aggregator staleness +
firewall), liquidity (walker), capital (risk limits + per-venue
non-fungible capital), eligibility (compliance module; Utah-aware),
API health (HealthMonitor), kill switch, order-type appropriateness
(limit-only; market orders prohibited on illiquid books), settlement
timing (delay spread limit).
