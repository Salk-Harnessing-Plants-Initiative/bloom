"""CI runs the repo's Python tests on the same Python the services run, and keeps the
Playwright and Python-audit jobs on Ubuntu 24.04, where both pass."""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


def _version(path: Path) -> str:
    return path.read_text().strip()


def test_the_root_python_matches_the_services():
    root = _version(REPO_ROOT / ".python-version")
    for service in ("bloommcp", "langchain", "services/video-worker"):
        assert _version(REPO_ROOT / service / ".python-version") == root, service


def test_the_playwright_and_audit_jobs_run_on_ubuntu_24_04():
    workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/pr-checks.yml").read_text())
    for job in ("compose-health-check", "python-audit"):
        assert workflow["jobs"][job]["runs-on"] == "ubuntu-24.04", job
