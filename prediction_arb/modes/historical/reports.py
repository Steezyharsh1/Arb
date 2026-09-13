"""Historical-mode reports (§44, §77). Written to reports/historical/."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ...core.clock import utcnow
from ...core.paths import reports_dir

_REPORT_DIR = reports_dir("historical")


def write_replay_report(summary: dict[str, Any]) -> Path:
    _REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ts = utcnow().strftime("%Y%m%d_%H%M%S")
    path = _REPORT_DIR / f"historical_replay_{ts}.json"
    payload = {"mode": "historical", "generated_at": utcnow().isoformat(),
               **summary}
    path.write_text(json.dumps(payload, indent=2))
    return path
