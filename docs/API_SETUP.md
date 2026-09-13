# API setup

## Install and verify

```bash
python3 -m venv .venv && source .venv/bin/activate   # Python 3.11+
pip install -e ".[dev]"
pytest
python -m prediction_arb.main --status              # local only; no orders
```

Equivalent entry points are `python -m prediction_arb.main …`,
`python -m prediction_arb …`, and `prediction-arb …` after installation.
Never paste API secrets into chat or commit `.env.*` files.

## Kalshi

Public REST order-book reads require no credentials, but network availability
is environment-dependent. Phase 1 documentation verification (2026-09-13):

| Environment | Recommended REST | Recommended WebSocket |
|---|---|---|
| Production | `https://external-api.kalshi.com/trade-api/v2` | `wss://external-api-ws.kalshi.com/trade-api/ws/v2` |
| Demo | `https://external-api.demo.kalshi.co/trade-api/v2` | `wss://external-api-ws.demo.kalshi.co/trade-api/ws/v2` |

Official source: https://docs.kalshi.com/getting_started/api_environments
(**VERIFIED / DOC / 2026-09-13**). `config/venues.yaml` (Phase 2,
2026-09-13) now points at the recommended hosts above, with the legacy
`api.elections` / `demo-api` hosts configured as automatic fallbacks: the
adapter rotates to the next base on HTTP 5xx or transport (DNS/TLS/timeout)
failures, and `connect()` probes every base before starting.

For authenticated streaming or future owner-approved order testing:

1. Create the appropriate production or separate demo key in Kalshi account
   settings. Credentials do not cross environments.
2. Copy `.env.example` to the correct non-committed mode file and set the
   documented Key ID and RSA private key variables.
3. Sign the timestamp + uppercase method + path (without query string) using
   RSA-PSS SHA-256 and send `KALSHI-ACCESS-KEY`,
   `KALSHI-ACCESS-SIGNATURE`, and `KALSHI-ACCESS-TIMESTAMP`.
4. Start with demo and retain the adapter-level and mode/firewall gates.

A fresh Phase 1 sandbox HTTP smoke test failed before an HTTP response with a
TLS EOF. That result is **UNAVAILABLE / LIVE**, not proof of an outage or a
successful current payload test.

Direct Kalshi trading is also fee-blocked: series/event fee metadata and
account/fill rounding state are not yet carried into `FeeCalculator`.

## Phase 1 candidate checkpoint

No candidate should be activated until the owner chooses a route. See the
ranked matrix in `docs/VENUE_RESEARCH.md`.

### Polymarket US (recommended direct candidate)

Official REST/WebSocket/auth documentation exists at
https://docs.polymarket.us/ (**VERIFIED / DOC / 2026-09-13**). A live sandbox
response and owner account/Utah eligibility were **UNAVAILABLE**. Do not confuse
Polymarket US (QCX DCM) with the international platform. Fee rounding and
cumulative order state are not integrated.

### Gemini Predictions (second direct candidate)

Official public REST, authenticated WebSocket trading, keys, and separate
sandbox are documented at
https://developer.gemini.com/products/prediction-markets
(**VERIFIED / DOC / 2026-09-13**). A live sandbox response was
**UNAVAILABLE**. Listing-cohort fee selection is not integrated.

### IBKR ForecastEx/CME (operational alternative)

IBKR documents one account/interface for Kalshi, ForecastEx, and CME at
https://www.interactivebrokers.com/predictionmarkets/en/home.php
(**VERIFIED / DOC / 2026-09-13**). Route qualification is mandatory:

- IBKR -> Kalshi is `SAME_VENUE` with direct Kalshi;
- IBKR -> ForecastEx and IBKR -> CME can be independent of Kalshi.

If selected, start with an IBKR paper account and a locally running authenticated
Client Portal/IB Gateway session. Browser-facing code must not call another
machine's `localhost`; proxy through the app/backend. The ForecastEx stub remains
unimplemented until owner approval.

## Existing Webull and Moomoo stubs

The evidenced event-contract routes point to Kalshi and are `SAME_VENUE` with
direct Kalshi. No event-contract automation endpoint was verified. These remain
route/fee-optimization possibilities only, not independent arbitrage legs.

## LLM matcher

No LLM client is configured by default, so ambiguous comparisons fail closed.
If an owner later supplies a provider through local configuration, outputs remain
schema-validated and malformed/contradictory results remain no-match.
