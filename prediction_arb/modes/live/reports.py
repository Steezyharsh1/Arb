"""Live-mode daily report (§77). Written to reports/live/ only."""
from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...core.clock import utcnow
from ...core.paths import reports_dir
from ...database.models import OpportunityRow, TradeRow
from ...database.repository import Repository

_REPORT_DIR = reports_dir("live")


def generate_daily_report(repo: Repository) -> Path:
    today = utcnow().date().isoformat()
    with Session(repo.engine) as s:
        ops = s.scalar(select(func.count(OpportunityRow.id)).where(
            OpportunityRow.mode == "live")) or 0
        trades = s.scalar(select(func.count(TradeRow.id)).where(
            TradeRow.mode == "live")) or 0
        filled = s.scalar(select(func.count(TradeRow.id)).where(
            TradeRow.mode == "live", TradeRow.status == "FILLED")) or 0
    report = {
        "mode": "live",
        "date": today,
        "opportunities_detected": int(ops),
        "trades_submitted": int(trades),
        "trades_filled": int(filled),
        "generated_at": utcnow().isoformat(),
    }
    _REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = _REPORT_DIR / f"live_daily_{today}.json"
    path.write_text(json.dumps(report, indent=2))
    return path
