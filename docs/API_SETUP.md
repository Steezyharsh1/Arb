# API SETUP

## Install

```bash
python3 -m venv .venv && source .venv/bin/activate   # Python 3.11+
pip install -e ".[dev]"          # installs the package + pytest
pytest                           # verify: 107 tests should pass
python -m prediction_arb.main --status    # venue/provenance status, no network
```

After an editable install three equivalent entry points exist:
`python -m prediction_arb.main …`, `python -m prediction_arb …` and the console
script `prediction-arb …`.

`requirements.txt` holds runtime dependencies only; test tooling lives in the
`dev` extra (`pyproject.toml`). If you prefer not to install the package,
`pip install -r requirements.txt && python -m pytest tests/` also works —
`tests/conftest.py` puts the project root on `sys.path`.

## Kalshi

Market data (REST) needs NO credentials — `--mode collect` and
`--mode paper` work immediately.

For WebSocket streaming and (eventually) live trading:
1. Create a Kalshi account at kalshi.com; generate an API key in
   account settings (you get a Key ID and download an RSA private key).
2. Paper/streaming: copy the template (`cp .env.example .env.paper`) and put
   them into `.env.paper`:
   ```
   KALSHI_API_KEY_ID=<key id>
   KALSHI_PRIVATE_KEY_PEM="-----BEGIN RSA PRIVATE KEY-----\n...\n-----END RSA PRIVATE KEY-----"
   ```
3. Live: use `.env.live` with `KALSHI_LIVE_API_KEY_ID` /
   `KALSHI_LIVE_PRIVATE_KEY_PEM`. Live-marked keys are refused outside
   live mode.
4. Demo environment (recommended before live):
   `https://demo-api.kalshi.co/trade-api/v2` — point
   `config/venues.yaml: kalshi.rest_base` at it to exercise order flow
   safely.

Signing: RSA-PSS SHA256 over `timestamp + METHOD + /trade-api/v2<path>`,
headers `KALSHI-ACCESS-KEY/SIGNATURE/TIMESTAMP` (implemented in
`prediction_arb/adapters/kalshi.py`).

## ForecastEx (IBKR) — activation checklist

1. Open an IBKR account; request a paper account; enable event
   contracts / ForecastTrader.
2. Run IB Gateway or Client Portal Gateway locally
   (default `https://localhost:5000/v1/api`).
3. Implement the marked methods in `prediction_arb/adapters/forecast_ex.py`
   (contract search, snapshot/book, orders) against the paper account.
4. Only after paper verification, change the adapter's `api_status`.
   The provenance record already exists and is INDEPENDENT of Kalshi.

## Webull / Moomoo

Intentionally stubs. Both route event contracts to Kalshi's exchange
(SAME_VENUE — see docs/VENUE_RESEARCH.md), so they cannot be arbitrage
legs against Kalshi regardless of API availability.

## LLM matcher

v1 ships with no LLM configured → all ambiguous matches end in REVIEW
(conservative). To enable: construct `LLMMatcher(client=fn,
model_version="...")` in `main.py`, where `fn: async (prompt) -> str`
calls your provider. Output is schema-validated; malformed output is
treated as no-match.
