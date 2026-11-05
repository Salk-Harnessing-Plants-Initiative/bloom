"""CI runs the repo's Python tests on the same Python the services run, and keeps the
Playwright job on a runner Playwright supports."""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


def _version(path: Path) -> str:
    return path.read_text().strip()


def test_the_root_python_matches_the_services():
    root = _version(REPO_ROOT / ".python-version")
    for service in ("bloommcp", "langchain", "services/video-worker"):
        assert _version(REPO_ROOT / service / ".python-version") == root, service


def test_the_playwright_job_runs_on_a_supported_ubuntu():
    workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/pr-checks.yml").read_text())
    assert workflow["jobs"]["compose-health-check"]["runs-on"] == "ubuntu-24.04"
