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
from ...monitoring.pipeline_report import build_pipeline_report

_REPORT_DIR = reports_dir("paper")


def generate_daily_report(repo: Repository) -> Path:
    today = utcnow().date().isoformat()
    with Session(repo.engine) as s:
        total_ops = s.scalar(select(func.count(OpportunityRow.id)).where(
            OpportunityRow.mode == "paper")) or 0
        tradeable = s.scalar(select(func.count(OpportunityRow.id)).where(
            OpportunityRow.mode == "paper",
            OpportunityRow.status == "TRADE")) or 0
        # net_profit IS NULL ⇒ a mandatory cost was unavailable, so the
        # opportunity has no verified net profit. Reported explicitly: an
        # unpriced opportunity is neither profitable nor zero-profit (D17).
        unpriced = s.scalar(select(func.count(OpportunityRow.id)).where(
            OpportunityRow.mode == "paper",
            OpportunityRow.net_profit.is_(None))) or 0
        trades = s.scalar(select(func.count(TradeRow.id)).where(
            TradeRow.mode == "paper")) or 0
    report = {
        "mode": "paper",
        "date": today,
        "opportunities_detected": int(total_ops),
        "opportunities_tradeable": int(tradeable),
        "opportunities_without_verified_net_profit": int(unpriced),
        "trades_executed": int(trades),
        # §103: the same view `--pipeline-report` prints, so the saved daily
        # file and the console never disagree.
        "pipeline": build_pipeline_report(repo, today),
        "generated_at": utcnow().isoformat(),
    }
    _REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = _REPORT_DIR / f"paper_daily_{today}.json"
    path.write_text(json.dumps(report, indent=2))
    return path
