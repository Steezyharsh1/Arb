"""Live-mode data provider: identical structure to paper's provider —
live market data via verified adapters (§57)."""
from __future__ import annotations

from ..paper.data_provider import PaperDataProvider


class LiveDataProvider(PaperDataProvider):
    """Same data path as paper (real market data). Execution differs."""
