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

- Kalshi's standard taker *model* is `0.07 × M × C × P × (1−P)`, but
  series/event overrides and account/fill rounding state are contextual.
  The production Kalshi schedule is therefore `INCOMPLETE` and blocks; the
  model is not used as a venue-wide execution estimate.
- ForecastEx: opposite bids pair at **$1.00** and a separate **$0.01 per
  executed side** fee applies.
- Missing or context-incomplete schedule → `FEE_DATA_INCOMPLETE` → BLOCK.
  Never assumed zero and never replaced by an execution-facing estimate.
- Unknown settlement lockup → BLOCK, never zero.
- Slippage is *inside* the walked cost (real levels consumed); it is
  additionally reported as WAP−best×qty for transparency, not
  double-counted.

## Unavailable net profit is not a number (D17)

The profit test above is only *evaluable* when every mandatory cost is
known. Each cost is therefore its own
`core.types.CostComponent` (`kalshi:trade_fee`, `forecast_ex:trade_fee`,
each venue's `settlement_fee`, `settlement_delay`), collected into a
`CostLedger`:

| Field | Meaning when a mandatory cost is unavailable |
|---|---|
| `gross_edge` | payout×q − walked cost. **Always** available. Pre-cost research figure, never profit |
| `known_costs` / `CostLedger.known_total` | sum of the costs that *could* be computed. A partial cost, not a total |
| `total_fees`, `settlement_delay_cost` | `None` |
| `capital_required` | `None` — it is `gross_cost + fees`, so unknown fees mean unknown capital |
| `net_profit`, `net_profit_per_contract`, `return_on_capital` | `None` — UNVERIFIED, never `0` and never a positive figure |
| `status` | BLOCK, with one block reason per unavailable component |
| `CostLedger.total` | `None`; the ledger is `complete` only when no component is missing |

Rules this enforces:

- An unknown mandatory cost is **never** substituted with zero. Zero is a
  claim that the cost is free; `None` says it is unknown.
- A BLOCKed opportunity therefore cannot display a positive "verified net
  profit" — the earlier behaviour (BLOCK alongside `+$9.96` computed with
  fabricated zero fees) is what D17 removed.
- A **verified negative** net profit *is* still a number and is still
  reported: a computed loss and an uncomputable profit are different facts.
- A BLOCK for a non-cost reason (e.g. independence UNKNOWN) keeps its
  verified net profit, because every cost was computed.
- Reporting surfaces render unavailable money as `n/a`
  (`monitoring/formatting.py`), the database stores SQL `NULL` plus the
  names of the missing components in `opportunities.unavailable_costs`, and
  fee-drag ratios are withheld rather than averaged over a partially costed
  set.
- `Opportunity.is_executable` — the gate the paper engine uses — requires a
  TRADE verdict *and* a verified positive net profit *and* known capital, so
  a positive gross edge can never be mistaken for an executable
  opportunity.

## Sizing

`find_max_profit_quantity` merges both ask ladders and stops at the
first marginal unit whose pair cost ≥ payout. Bounded further by
displayed liquidity of each leg (test case 2), caller max, and risk
limits. Whole contracts only.

## Kalshi order-book semantics (important)

Kalshi's documented `orderbook_fp` contains YES bids and NO bids. The
executable YES ask is synthesized as `1.00 − NO_bid`. The format was
re-verified in official documentation on 2026-09-13; the fresh sandbox
network smoke test was UNAVAILABLE and is not described as a live test.

## ForecastEx microstructure

PAIRED_BID: opposite YES and NO bids execute when they sum to **$1.00**.
The synthetic YES ask is `1.00 − best NO bid`. ForecastEx separately
charges $0.01 to each executed side. The adapter metadata records this but
the adapter remains an untested structured stub.

## Status ladder

- TRADE — all checks pass, net ≥ minimum_net_profit and ROC ≥
  minimum_net_return. A TRADE always has a verified positive net profit.
- WATCH — profitable but under thresholds.
- BLOCK — any hard failure (independence, fees, settlement unknown,
  net ≤ 0), with reasons recorded. When the failure is an unavailable
  mandatory cost, net profit is `n/a`/`None` rather than a number.

## Pre-trade checklist (§33) mapping

Implemented across: match certificate (APPROVED required),
independence (INDEPENDENT required; UNKNOWN blocks), fee freshness
(strict mode for live), book freshness (aggregator staleness +
firewall), liquidity (walker), capital (risk limits + per-venue
non-fungible capital), eligibility (compliance module; Utah-aware),
API health (HealthMonitor), kill switch, order-type appropriateness
(limit-only; market orders prohibited on illiquid books), settlement
timing (delay spread limit).
