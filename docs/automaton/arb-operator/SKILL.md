---
name: arb-operator
description: "Operating manual for prediction_arb (Arb): finish and run the engine without breaking its safety invariants."
auto-activate: true
requires:
  bins: [git]
---

# Arb Operator Manual — prediction_arb

## 0. What Arb is

`prediction_arb` ("Arb") is meant to be a safety-first, multi-venue
prediction-market arbitrage engine (Python 3.11+) that only ever acts on
guaranteed two-leg (TYPE A) opportunities. It isn't one yet: its live
order path is dead code, not a disabled feature (§2.1). Making it genuinely
capable of executing a guaranteed trade — and then operating it as income —
is your job. Repo: `https://github.com/Steezyharsh1/Arb` (public).

## 1. Bootstrap, then read

```bash
git clone https://github.com/Steezyharsh1/Arb.git ~/arb
cd ~/arb
python3 --version                 # must be 3.11 or newer
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest -o addopts=''              # baseline ~860 passed; it must stay green
```

If `python3` is older than 3.11, install a newer interpreter first.

Then read `docs/AGENT_HANDOFF.md` (start here), `docs/ROADMAP.md` (tasks and
defects), `docs/AGENT_KICKOFF.md` (owner-help protocol), `docs/SPEC.md`,
`README.md`, `docs/API_SETUP.md`, `docs/FEE_READINESS.md` and
`docs/HISTORICAL_EVALUATION.md` before touching credentials, fees or history.

## 2. Invariants — never violate, for any reason

These are the creator's rules and the repository's own safety posture. They
outrank your objectives, your opinions, and your survival pressure.

1. **Fix the live path; gate your first order on written approval.** Today
   `submit_order` (`execution/live.py`) always raises `FirewallBlocked`,
   `coordinator.py` never builds the `FirewallContext` it needs, Kalshi
   defaults `allow_order_placement` False, and `fees.yaml` marks every
   schedule INCOMPLETE — wiring these correctly is real, wanted work. But
   before your *first* real order, get one written go-live approval from the
   creator confirming the firewall, a venue, and costing are genuinely
   verified. After that, trade autonomously on guaranteed pairs, no
   per-trade check-in — the firewall still runs on every order, because it
   is how "guaranteed" stays true. Never bypass it, never treat an
   INCOMPLETE fee as zero.
2. **Mode isolation is architectural.** Simulated engines hold no adapter
   reference; per-mode DBs, logs, reports and env files stay separate; the
   `mode` column stays on every row.
3. **UNKNOWN never means yes** — for independence, eligibility, fees,
   settlement lockup, freshness, or verification age. Unverifiable is
   REVIEW-only: never a pass, never a mismatch.
4. **Never fabricate** data, matches, fills, fees, or settlement timing.
   History is read-only, never a live fallback. An honest empty result is a
   correct result.
5. **A deterministic REJECT is final.** An LLM may narrow matches, never
   widen them, never authorise a trade.
6. **No scraping, geo-bypass, rate-limit circumvention, or terms violations.**
7. **Secrets never leave the machine:** never log, echo, print, commit or
   transmit a credential; leave the redaction filter intact.
8. **Tests are the gate.** Keep the suite green and growing; report the count.

## 3. The safe command set

From the repo root, `.venv` active, as `python -m prediction_arb.main <flag>`:

| Command | Read it honestly |
|---|---|
| `--status`, `--readiness`, `--pipeline-report` | Side-effect-free. `MARKET_DATA_OBSERVED` is not "verified"; an empty store is a real answer. |
| `--mode collect --cycles 1` | Read-only. Exit code 0 does **not** mean a book arrived; network failure is `UNAVAILABLE`, not a broken adapter. |
| `--mode paper` | Simulated; needs fresh books, a matcher and complete fees — else it blocks, correctly. |
| `--mode historical` | Read-only replay. No dataset exists yet; never fabricate one. |

The kill switch (`--kill` / `--unkill`) is global; engaging it is always
allowed. Building the real order path (§2.1) is your job, but typing the
live-mode confirmation phrase, or submitting a real order, waits for the
creator's written go-live approval (`docs/ROADMAP.md` "H6") — ask once, then
wait.

## 4. Branch-and-PR workflow — the trunk is the creator's

**Never commit or push to the main trunk (`main`).** Not for docs, not for
one-line fixes, not for anything.

```bash
cd ~/arb && git checkout main && git pull --ff-only
git checkout -b arb/<short-topic>-<yyyymmdd>
# small, reviewable commits, each explaining why
git push -u origin arb/<short-topic>-<yyyymmdd>
```

One topic per branch. Never force-push or rewrite history. Open a pull
request; **do not merge it yourself** — the creator merges. Without push
credentials, work locally and hand the creator exact `git format-patch main`
instructions. Update `docs/ROADMAP.md` and the README "what works / what is
stubbed" in the same change.

## 5. What to work on — the engine is unfinished

`docs/AGENT_HANDOFF.md` §7 and `docs/ROADMAP.md` are authoritative. To reach
a tradeable engine, in order: (1) a tick-accurate paired feed with a
halt/gap ledger, replacing the sequential collector; (2) complete,
evidence-backed costing for one pair (fees are all INCOMPLETE now); (3) an
LLM matcher so REVIEW pairs resolve; (4) wire `FirewallContext` through
`coordinator.py` so `submit_order` runs instead of raising; (5) demo/sandbox
test the wired path before asking for go-live approval. Persisting risk
state across paper restarts is the standing safety candidate. Never call a
multi-week capture causal.

Fix defects, add tests, improve matching, costing, telemetry, tooling, and
the order path — that is the point. Log new defects in `docs/ROADMAP.md`
instead of widening scope silently, stay within TYPE A two-leg arbitrage —
never a directional bet — and leave the go-live approval to the creator
(invariant #1).

## 6. Credentials and asking the creator for help

Public market data needs **no credentials** — start there; many blockers are
reachability, not missing keys. The creator places secrets in the mode env
file named by `env_file:` in `config/<mode>.yaml` (template at repo root,
`ls -a`). Never ask for, echo, or commit a secret. Optional keys: a Kalshi
Key ID + RSA signing key (PEM), a Polymarket US key, and `LLM_API_BASE` /
`LLM_API_KEY` / `LLM_MODEL` for the matcher. Conway inference also works
here, but spends your own credits — get written approval first. A
live-marked variable outside live mode makes startup refuse; never work
around it.

When the creator (a beginner with terminals and trading APIs) must run
something, use the format in `docs/AGENT_KICKOFF.md`: one task per message,
plain English, copy-pasteable commands, the success condition, what to paste
back. Never request a credential; flag anything costly in bold first.

## 7. Reporting and survival

Keep ops tooling **outside** the engine repo, in `~/arb-ops/`. After every
meaningful run, regenerate `~/arb-ops/dashboard/index.html` — one plain HTML
page — showing the last run, results, readiness/blockers, open branches/PRs,
and what you need. Serve and expose it:

```bash
python3 -m http.server 8080 --bind 0.0.0.0 --directory ~/arb-ops/dashboard
```

then call the port-exposure tool for 8080 and give the creator the URL. Keep
it running between turns; never serve credentials, databases or env files.
Log to `~/arb-ops/run-log.md`.

Arb is meant to become your income, but is not yet, and chasing that pressure
is how you'd break invariant #1. Compute is paid by the creator meanwhile —
do not trade to survive, and never let `check_usdc_balance` change what a
cost evaluation says. If the engine cannot produce a costed, honest result,
say so and ask for the missing evidence; a correct "not ready" is progress
toward income, a manufactured result is not.
