"""prediction_arb — multi-venue prediction-market arbitrage engine.

Conservative and safety-first by construction:

* hard mode isolation (historical / paper / live / collect) — see
  :mod:`prediction_arb.core.config` and docs/MODE_SEPARATION.md
* a live-order firewall re-verifying every order — :mod:`prediction_arb.execution.firewall`
* guaranteed (TYPE A) two-leg arbitrage only — :mod:`prediction_arb.arbitrage.two_leg`
* honest handling of everything UNKNOWN: missing fee data, unknown venue
  independence, unknown settlement lockup and unknown eligibility all BLOCK

Nothing in this package places a real order unless the application is in
live mode, the configuration explicitly allows it, and the operator has
typed the confirmation phrase.

Start at ``README.md``, then ``docs/ROADMAP.md`` (phase map + defect
register) and ``docs/SPEC.md`` (reconstructed specification).
"""
from __future__ import annotations

__version__ = "1.0.0"

__all__ = ["__version__"]
