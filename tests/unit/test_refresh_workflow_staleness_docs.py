"""The docs describing how `n_traits` is refreshed must describe the mechanism that actually runs.

- `_WIKI/BLOOMMCP/README.md` describes the nightly pg_cron job
  (`refresh_changed_cyl_experiment_trait_counts()`), not the GitHub Action.
- `list_available_experiments.py` still describes the GitHub Action until that Action is removed,
  so its staleness claim must stay conditioned on bloom#736 and bloom#806, the two fixes that had to
  land before the Action could succeed.

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
    ),
}

REQUIRED_PHRASES = {
    WIKI_README: ("pg_cron", "refresh_changed_cyl_experiment_trait_counts()"),
    LIST_EXPERIMENTS_MODULE: ("bloom#736", "bloom#806"),
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
