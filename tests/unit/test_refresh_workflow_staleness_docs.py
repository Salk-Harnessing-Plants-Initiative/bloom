"""bloom#736 (fix-cyl-scan-traits-latest-rollup Section 15): the doc/comment sites describing
production's `n_traits` refresh staleness must not claim it is already bounded.

Found by `/review-pr` on PR #738: the refresh workflow's `runs-on: ubuntu-latest` had no network
route to either host, so `refresh_cyl_experiment_trait_counts()` had never once actually succeeded
via GitHub Actions -- production's staleness was, in fact, unbounded, identically to staging,
despite two doc/comment sites previously asserting a bounded claim as settled fact. Both sites were
corrected to state the bound holds only once 15.7 confirms an actual successful refresh.

This test pins the corrected wording and fences against the old, unconditional claim silently
reappearing -- mirroring `test_bloommcp_local_mode_docs.py`'s banned/required-phrase pattern.
Whitespace (including newlines) is normalized before matching, since both files hard-wrap prose.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

WIKI_README = "_WIKI/BLOOMMCP/README.md"
LIST_EXPERIMENTS_MODULE = "bloommcp/src/bloom_mcp/sections/core/list_available_experiments.py"

# The pre-fix, unconditional claim each file made -- must not reappear verbatim.
BANNED_PHRASES = {
    WIKI_README: (
        "bounded to roughly one refresh interval on production, but still unbounded on staging"
    ),
    LIST_EXPERIMENTS_MODULE: (
        "bounded to roughly one refresh interval, but a missed or delayed scheduled run"
    ),
}

# The corrected, conditional claim each file must state instead.
REQUIRED_PHRASES = {
    WIKI_README: "bloom#736",
    LIST_EXPERIMENTS_MODULE: "bloom#736",
}


def _normalized_text(filename: str) -> str:
    return " ".join((REPO_ROOT / filename).read_text(encoding="utf-8").split())


def test_staleness_docs_have_no_stale_unconditional_bound_claim():
    for filename, banned in BANNED_PHRASES.items():
        text = _normalized_text(filename)
        assert banned not in text, (
            f"{filename}: stale unconditional staleness-bound claim {banned!r} -- "
            "reword to the bloom#736-conditional guarantee (Section 15)."
        )


def test_staleness_docs_reference_bloom_736():
    for filename, required in REQUIRED_PHRASES.items():
        text = _normalized_text(filename)
        assert required in text, (
            f"{filename}: expected a reference to {required!r} conditioning the staleness-bound "
            "claim on an actual successful refresh (Section 15)."
        )
