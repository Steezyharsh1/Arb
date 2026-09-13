"""Structured JSON logging, tagged with the active mode.

Every log record carries `mode` so any line can be attributed to
historical / paper / live / collect unambiguously (spec §52, §76).
Log files are written under logs/<mode>/ and never cross modes (§51).
Credentials are never logged (§62): a redaction filter drops any record
attribute whose key looks secret-like.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from .clock import utcnow
from .types import Mode

_SECRET_RE = re.compile(r"(key|secret|token|password|credential|private)", re.I)

_ROOT = Path(__file__).resolve().parent.parent


class JsonFormatter(logging.Formatter):
    def __init__(self, mode: Mode):
        super().__init__()
        self.mode = mode

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": utcnow().isoformat(),
            "mode": self.mode.value,
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        extra = getattr(record, "data", None)
        if isinstance(extra, dict):
            payload["data"] = {
                k: v for k, v in extra.items() if not _SECRET_RE.search(str(k))
            }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def setup_logging(mode: Mode, level: int = logging.INFO) -> logging.Logger:
    log_dir = _ROOT / "logs" / mode.value
    log_dir.mkdir(parents=True, exist_ok=True)
    logfile = log_dir / f"{mode.value}_{utcnow().strftime('%Y%m%d')}.jsonl"

    root = logging.getLogger("prediction_arb")
    root.setLevel(level)
    root.handlers.clear()

    fh = logging.FileHandler(logfile, encoding="utf-8")
    fh.setFormatter(JsonFormatter(mode))
    root.addHandler(fh)

    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter(
        f"%(asctime)s [{mode.value.upper()}] %(levelname)s %(name)s: %(message)s"))
    ch.setLevel(logging.WARNING)
    root.addHandler(ch)
    return root


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"prediction_arb.{name}")
