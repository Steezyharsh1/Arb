"""Project-layout and path-resolution tests (Phase 0; §49, §51, §52, §71, §77).

These guard the decisions made when the repository was restructured:
  * the importable package is `prediction_arb/` and contains CODE ONLY
  * config/, data/, logs/ and reports/ live at the PROJECT ROOT, never
    inside the package (an installed package may be read-only, and per-mode
    data must never ship inside it)
  * the project root is discovered, overridable, and refuses to guess
"""
from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from prediction_arb.core import paths
from prediction_arb.core.types import Mode

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _reset_root_cache():
    """Never leak a cached (or monkeypatched) project root between tests."""
    paths.reset_cache()
    yield
    paths.reset_cache()


class TestProjectRootDiscovery:
    def test_root_contains_the_marker(self):
        assert (paths.project_root() / "config" / "settings.yaml").is_file()

    def test_root_is_the_repository_root(self):
        assert paths.project_root() == REPO_ROOT

    def test_package_lives_inside_the_root_and_holds_code_only(self):
        pkg = Path(paths.__file__).resolve().parents[1]
        assert pkg.name == "prediction_arb"
        assert pkg.parent == paths.project_root()
        # No runtime artifacts inside the importable package.
        forbidden = ("*.sqlite3", "*.jsonl", "*.log")
        for pattern in forbidden:
            assert not list(pkg.rglob(pattern)), f"{pattern} found in package"
        for dirname in ("data", "logs", "reports"):
            assert not (pkg / dirname).exists(), f"{dirname}/ inside package"

    def test_env_override_is_honoured(self, tmp_path, monkeypatch):
        (tmp_path / "config").mkdir()
        (tmp_path / "config" / "settings.yaml").write_text("mode: paper\n")
        monkeypatch.setenv("PREDICTION_ARB_HOME", str(tmp_path))
        assert paths.project_root() == tmp_path.resolve()

    def test_env_override_without_marker_raises(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PREDICTION_ARB_HOME", str(tmp_path))
        with pytest.raises(paths.ProjectRootError) as exc:
            paths.project_root()
        assert "PREDICTION_ARB_HOME" in str(exc.value)

    def test_refuses_to_guess_when_no_marker_exists(self, tmp_path, monkeypatch):
        """A wrong root would write one mode's data into another mode's
        directory (§49), so discovery must fail loudly instead."""
        monkeypatch.delenv("PREDICTION_ARB_HOME", raising=False)
        monkeypatch.setattr(paths, "_PACKAGE_DIR", tmp_path / "pkg")
        monkeypatch.chdir(tmp_path)
        with pytest.raises(paths.ProjectRootError):
            paths.project_root()


class TestPerModeSeparation:
    def test_mode_enum_and_string_agree(self):
        assert paths.data_dir(Mode.PAPER) == paths.data_dir("paper")
        assert paths.logs_dir(Mode.LIVE) == paths.logs_dir("live")
        assert paths.reports_dir(Mode.COLLECT) == paths.reports_dir("collect")

    def test_every_mode_gets_its_own_directories(self):
        for mode in Mode:
            assert paths.data_dir(mode).name == mode.value
            assert paths.logs_dir(mode).name == mode.value
            assert paths.reports_dir(mode).name == mode.value

    def test_directories_never_cross_modes(self):
        paper, live = paths.logs_dir(Mode.PAPER), paths.logs_dir(Mode.LIVE)
        assert paper != live
        assert live not in paper.parents and paper not in live.parents

    def test_database_path_is_per_mode_and_inside_data(self):
        p = paths.database_path(Mode.PAPER, "prediction_arb_paper")
        assert p == paths.project_root() / "data" / "paper" / \
            "prediction_arb_paper.sqlite3"

    def test_kill_switch_is_global_not_per_mode(self):
        """§71: one switch stops everything, so it is not mode-scoped."""
        assert paths.kill_switch_file() == paths.project_root() / "data" / \
            "KILL_SWITCH"

    def test_create_flag_makes_directories(self, tmp_path, monkeypatch):
        (tmp_path / "config").mkdir()
        (tmp_path / "config" / "settings.yaml").write_text("mode: paper\n")
        monkeypatch.setenv("PREDICTION_ARB_HOME", str(tmp_path))
        made = paths.logs_dir(Mode.HISTORICAL, create=True)
        assert made.is_dir()
        assert made == tmp_path / "logs" / "historical"


@pytest.fixture(scope="module")
def pyproject():
    with open(REPO_ROOT / "pyproject.toml", "rb") as f:
        return tomllib.load(f)


class TestPackaging:
    def test_console_script_entry_point(self, pyproject):
        scripts = pyproject["project"]["scripts"]
        assert scripts["prediction-arb"] == "prediction_arb.main:main"

    def test_requires_python_311(self, pyproject):
        assert pyproject["project"]["requires-python"] == ">=3.11"

    def test_only_the_package_is_distributed(self, pyproject):
        include = pyproject["tool"]["setuptools"]["packages"]["find"]["include"]
        assert include == ["prediction_arb*"]

    def test_runtime_dependencies_are_dynamic_from_requirements(self, pyproject):
        assert pyproject["project"]["dynamic"] == ["dependencies"]
        assert pyproject["tool"]["setuptools"]["dynamic"]["dependencies"] == \
            {"file": ["requirements.txt"]}

    def test_dev_extra_provides_pytest(self, pyproject):
        dev = pyproject["project"]["optional-dependencies"]["dev"]
        assert any(d.startswith("pytest") for d in dev)

    def test_pytest_is_configured_to_run_tests_dir(self, pyproject):
        assert pyproject["tool"]["pytest"]["ini_options"]["testpaths"] == \
            ["tests"]

    def test_package_is_importable_and_versioned(self):
        import prediction_arb
        assert prediction_arb.__version__
        assert hasattr(prediction_arb, "__file__")

    def test_module_runner_exists(self):
        """`python -m prediction_arb` must work as well as `.main`."""
        assert (REPO_ROOT / "prediction_arb" / "__main__.py").is_file()


class TestRepoHygiene:
    def test_gitignore_covers_artifacts_and_secrets(self):
        ignore = (REPO_ROOT / ".gitignore").read_text()
        for needle in ("__pycache__/", "*.py[cod]", "data/", "logs/",
                       "reports/", ".env", "!.env.example", "*.pem"):
            assert needle in ignore, f".gitignore is missing {needle!r}"

    def test_env_template_is_documented_and_empty_of_secrets(self):
        example = (REPO_ROOT / ".env.example").read_text()
        assert "KALSHI_API_KEY_ID" in example
        # Every credential line in the template must stay commented out.
        for line in example.splitlines():
            if "KALSHI_" in line or "PRIVATE_KEY" in line:
                assert line.lstrip().startswith("#"), \
                    f"template leaks a live-looking assignment: {line!r}"

    def test_scratch_scripts_live_in_scripts_dir(self):
        assert (REPO_ROOT / "scripts" / "ibkr_gateway_session.py").is_file()
        assert not (REPO_ROOT / "reconnect.py").exists()
