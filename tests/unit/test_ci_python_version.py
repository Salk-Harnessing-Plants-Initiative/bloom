"""CI runs the repo's Python tests on the same Python the services run, keeps the
Playwright job on Ubuntu 24.04, and holds supabase below the release that retries timeouts."""

import tomllib
from pathlib import Path

import yaml
from packaging.requirements import Requirement

REPO_ROOT = Path(__file__).resolve().parents[2]


def _version(path: Path) -> str:
    return path.read_text().strip()


def test_the_root_python_matches_the_services():
    root = _version(REPO_ROOT / ".python-version")
    for service in ("bloommcp", "langchain", "services/video-worker"):
        assert _version(REPO_ROOT / service / ".python-version") == root, service


def test_the_playwright_job_runs_on_ubuntu_24_04():
    workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/pr-checks.yml").read_text())
    assert workflow["jobs"]["compose-health-check"]["runs-on"] == "ubuntu-24.04"


def test_the_root_tests_hold_supabase_below_2_34():
    extras = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())["project"]["optional-dependencies"]
    supabase = next(Requirement(r) for r in extras["test"] if Requirement(r).name == "supabase")
    assert "2.33.0" in supabase.specifier
    assert "2.34.0" not in supabase.specifier
