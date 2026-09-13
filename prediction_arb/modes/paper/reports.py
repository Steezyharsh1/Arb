"""Paper-mode daily report (§77). Writes to reports/paper/ only."""
from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...core.clock import utcnow
from ...core.paths import reports_dir
from ...database.models import OpportunityRow, TradeRow
from ...database.repository import Repository

_REPORT_DIR = reports_dir("paper")


def generate_daily_report(repo: Repository) -> Path:
    today = utcnow().date().isoformat()
    with Session(repo.engine) as s:
        total_ops = s.scalar(select(func.count(OpportunityRow.id)).where(
            OpportunityRow.mode == "paper")) or 0
        tradeable = s.scalar(select(func.count(OpportunityRow.id)).where(
            OpportunityRow.mode == "paper",
            OpportunityRow.status == "TRADE")) or 0
        trades = s.scalar(select(func.count(TradeRow.id)).where(
            TradeRow.mode == "paper")) or 0
    report = {
        "mode": "paper",
        "date": today,
        "opportunities_detected": int(total_ops),
        "opportunities_tradeable": int(tradeable),
        "trades_executed": int(trades),
        "generated_at": utcnow().isoformat(),
    }
    _REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = _REPORT_DIR / f"paper_daily_{today}.json"
    path.write_text(json.dumps(report, indent=2))
    return path
