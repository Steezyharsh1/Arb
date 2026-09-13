"""Global kill switch (§71). File-based so `python -m prediction_arb.main
--kill` works across processes; remains engaged until manually cleared."""
from __future__ import annotations

import json

from ..core.clock import utcnow
from ..core.paths import kill_switch_file

# Module-level so tests can monkeypatch it; read at call time below.
KILL_FILE = kill_switch_file()


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
