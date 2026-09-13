"""Project path resolution — the single place that decides where `config/`,
`data/`, `logs/` and `reports/` live (§49, §51, §52, §77).

Repository layout (Phase 0 — see docs/ROADMAP.md):

    <project root>/
        prediction_arb/       the importable package (CODE ONLY)
        config/               user-editable YAML (settings/venues/fees/risk/modes)
        data/<mode>/          per-mode SQLite databases; data/KILL_SWITCH
        logs/<mode>/          per-mode JSONL audit logs
        reports/<mode>/       generated reports
        tests/  docs/  scripts/  pyproject.toml

Runtime artifacts deliberately live OUTSIDE the package: an installed
package may sit in a read-only site-packages tree, and per-mode data must
never be shipped inside it (§49).

Project-root resolution order:
  1. ``$PREDICTION_ARB_HOME`` — explicit override; must contain the marker
     file ``config/settings.yaml``.
  2. The first ancestor of the package directory containing the marker.
  3. The first ancestor of the current working directory containing it.

Anything else raises ``ProjectRootError`` rather than guessing. A wrong
root here would silently write one mode's data into another mode's
directory, which is exactly the failure mode §49 exists to prevent.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Union

__all__ = [
    "ProjectRootError",
    "project_root",
    "reset_cache",
    "config_dir",
    "config_file",
    "env_file",
    "data_dir",
    "database_path",
    "kill_switch_file",
    "logs_dir",
    "reports_dir",
]

#: Marker used to recognize the project root.
MARKER = Path("config") / "settings.yaml"

_PACKAGE_DIR = Path(__file__).resolve().parents[1]

_PROJECT_ROOT: Optional[Path] = None

ModeLike = Union[str, "object"]


class ProjectRootError(RuntimeError):
    """The project root could not be located."""


def _mode_value(mode: ModeLike) -> str:
    """Accept either a ``Mode`` enum member or a plain string."""
    return str(getattr(mode, "value", mode))


def _has_marker(path: Path) -> bool:
    return (path / MARKER).is_file()


def _discover() -> Path:
    override = os.environ.get("PREDICTION_ARB_HOME")
    if override:
        root = Path(override).expanduser().resolve()
        if not _has_marker(root):
            raise ProjectRootError(
                f"PREDICTION_ARB_HOME={root} does not contain {MARKER}. "
                f"Point it at the repository root (the directory holding "
                f"config/, data/, logs/, reports/).")
        return root

    for start in (_PACKAGE_DIR, Path.cwd()):
        for candidate in (start, *start.parents):
            if _has_marker(candidate):
                return candidate

    raise ProjectRootError(
        f"Could not locate the project root (no ancestor of {_PACKAGE_DIR} "
        f"or of the working directory contains {MARKER}). Either run from "
        f"inside a checkout, install with `pip install -e .` from the "
        f"repository root, or set PREDICTION_ARB_HOME=/path/to/repo.")


def project_root() -> Path:
    """Absolute path of the project root (cached; see :func:`reset_cache`)."""
    global _PROJECT_ROOT
    if _PROJECT_ROOT is None:
        _PROJECT_ROOT = _discover()
    return _PROJECT_ROOT


def reset_cache() -> None:
    """Forget the cached root (used by tests that change the environment)."""
    global _PROJECT_ROOT
    _PROJECT_ROOT = None


# --------------------------------------------------------------------- config
def config_dir() -> Path:
    return project_root() / "config"


def config_file(name: str) -> Path:
    """Path to a config file, e.g. ``config_file("fees.yaml")``."""
    return config_dir() / name


def env_file(name: str) -> Path:
    """Path to a mode env file at the project root, e.g. ``.env.paper``."""
    return project_root() / name


# ----------------------------------------------------------------------- data
def data_dir(mode: Optional[ModeLike] = None, create: bool = False) -> Path:
    """``data/`` or ``data/<mode>/``. Creates the directory on request."""
    path = project_root() / "data"
    if mode is not None:
        path = path / _mode_value(mode)
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def database_path(mode: ModeLike, database_name: str,
                  create: bool = True) -> Path:
    """Per-mode SQLite file: ``data/<mode>/<database_name>.sqlite3`` (§49)."""
    return data_dir(mode, create=create) / f"{database_name}.sqlite3"


def kill_switch_file() -> Path:
    """Global (cross-mode) kill-switch state file (§71)."""
    return data_dir() / "KILL_SWITCH"


# ----------------------------------------------------------------- logs, etc.
def logs_dir(mode: ModeLike, create: bool = False) -> Path:
    """``logs/<mode>/`` — logs never cross modes (§51, §52)."""
    path = project_root() / "logs" / _mode_value(mode)
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def reports_dir(mode: ModeLike, create: bool = False) -> Path:
    """``reports/<mode>/`` — reports never cross modes (§77)."""
    path = project_root() / "reports" / _mode_value(mode)
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path
