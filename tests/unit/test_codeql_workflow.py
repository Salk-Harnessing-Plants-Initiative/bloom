"""CodeQL must report on merge-queue commits under the check names staging requires.

Default setup never analyzes `merge_group` commits, so a queued PR waited forever for the
required "Analyze (<language>)" checks. The workflow replaces it.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CODEQL = REPO_ROOT / ".github" / "workflows" / "codeql.yml"
# The languages default setup analyzed, which staging's required checks name.
LANGUAGES = ["actions", "javascript-typescript", "python"]


def _workflow() -> dict:
    return yaml.safe_load(CODEQL.read_text(encoding="utf-8"))


def _triggers() -> dict:
    workflow = _workflow()
    return workflow.get("on") or workflow[True]


def test_runs_for_the_merge_queue():
    assert _triggers()["merge_group"] == {"types": ["checks_requested"]}


def test_runs_for_pull_requests_and_pushes_to_both_branches():
    triggers = _triggers()
    assert triggers["pull_request"]["branches"] == ["main", "staging"]
    assert triggers["push"]["branches"] == ["main", "staging"]


def test_job_names_match_the_required_checks():
    job = _workflow()["jobs"]["analyze"]
    assert job["name"] == "Analyze (${{ matrix.language }})"
    assert job["strategy"]["matrix"]["language"] == LANGUAGES


def test_can_upload_results():
    assert _workflow()["jobs"]["analyze"]["permissions"]["security-events"] == "write"


def test_uses_the_default_query_suite():
    init = next(
        s for s in _workflow()["jobs"]["analyze"]["steps"] if "codeql-action/init" in s.get("uses", "")
    )
    assert "queries" not in init["with"]
    assert "config" not in init["with"]
