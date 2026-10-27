"""The docs describing how `n_traits` is refreshed must describe the mechanism that actually runs.

Both files describe the nightly pg_cron job. Neither may describe the GitHub Action that used to
do this: it is deleted, and its `on: schedule` / `workflow_dispatch` split no longer makes staging
and production differ in how fresh a cache row is.

Whitespace (including newlines) is normalized before matching, since both files hard-wrap prose.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

WIKI_README = "_WIKI/BLOOMMCP/README.md"
LIST_EXPERIMENTS_MODULE = (
    "bloommcp/src/bloom_mcp/sections/core/list_available_experiments.py"
)

BANNED_PHRASES = {
    WIKI_README: (
        "bounded to roughly one refresh interval on production, but still unbounded on staging",
        ".github/workflows/refresh-cyl-experiment-trait-counts.yml",
    ),
    LIST_EXPERIMENTS_MODULE: (
        "bounded to roughly one refresh interval, but a missed or delayed scheduled run",
        "`on: schedule`",
        "workflow_dispatch",
        "on a schedule or on demand",
        "the environment's own refresh cadence",
    ),
}

REQUIRED_PHRASES = {
    WIKI_README: ("pg_cron", "refresh_changed_cyl_experiment_trait_counts()"),
    LIST_EXPERIMENTS_MODULE: ("pg_cron", "nightly"),
}


def _normalized_text(filename: str) -> str:
    return " ".join((REPO_ROOT / filename).read_text(encoding="utf-8").split())


@pytest.mark.parametrize("filename", sorted(BANNED_PHRASES))
def test_staleness_docs_have_no_banned_phrase(filename):
    text = _normalized_text(filename)
    for banned in BANNED_PHRASES[filename]:
        assert banned not in text, f"{filename}: must not say {banned!r}"


@pytest.mark.parametrize("filename", sorted(REQUIRED_PHRASES))
def test_staleness_docs_name_the_current_refresh_mechanism(filename):
    text = _normalized_text(filename)
    for required in REQUIRED_PHRASES[filename]:
        assert required in text, f"{filename}: expected {required!r}"
