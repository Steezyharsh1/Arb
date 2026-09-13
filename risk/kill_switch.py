"""Global kill switch (§71). File-based so `python main.py --kill` works
across processes; remains engaged until manually cleared."""
from __future__ import annotations

import json
from pathlib import Path

from ..core.clock import utcnow

_ROOT = Path(__file__).resolve().parent.parent
KILL_FILE = _ROOT / "data" / "KILL_SWITCH"


def engage(reason: str = "manual") -> None:
    KILL_FILE.parent.mkdir(parents=True, exist_ok=True)
    KILL_FILE.write_text(json.dumps({
        "engaged_at": utcnow().isoformat(), "reason": reason}))


def disengage() -> None:
    if KILL_FILE.exists():
        KILL_FILE.unlink()


def is_engaged() -> bool:
    return KILL_FILE.exists()


def status() -> dict:
    if not KILL_FILE.exists():
        return {"engaged": False}
    try:
        data = json.loads(KILL_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        data = {}
    data["engaged"] = True
    return data
