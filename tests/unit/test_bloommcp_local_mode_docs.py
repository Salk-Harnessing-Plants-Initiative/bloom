"""bloommcp's local-mode docs must claim data-locality, not zero network activity.

bloom#641: local mode still attempted (and failed) a Supabase usage-telemetry
RPC on every request, which the docs' absolute "no connection to the shared
server at all" / "nothing shared with anyone else" / "fully offline" framing
didn't account for. Fixed by gating that RPC on `is_local_backend()`
(bloommcp/src/bloom_mcp/identity.py) — but the docs' guarantee is corrected
independently, to the narrower, always-true claim: no *experiment data*
leaves the machine, not that no code path ever attempts network activity.
These tests pin the corrected wording in every location that made the old,
stronger claim, and fence against it silently reappearing.

Whitespace is normalized (all runs of whitespace, including newlines,
collapsed to a single space) before matching — these docs hard-wrap prose at
a fixed column width, so a banned or required phrase can otherwise straddle
a line break and be missed by a naive single-line search.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Every location that made the old, absolute "zero network activity" claim
# about bloommcp's local storage backend.
DOC_FILES = [
    "bloommcp/docs/connecting-claude-code.md",
    "bloommcp/docs/storage-backends.md",
    "_WIKI/BLOOMMCP/README.md",
]

BANNED_PHRASES = [
    "no connection to the shared server at all",
    "nothing shared with anyone else",
    "fully offline",
    "fully-local (offline)",
]

REQUIRED_PHRASE = "no experiment data leaves your machine"


def _normalized_text(filename: str) -> str:
    return " ".join((REPO_ROOT / filename).read_text(encoding="utf-8").split())


def test_local_mode_docs_have_no_stale_zero_network_claim():
    for filename in DOC_FILES:
        text = _normalized_text(filename)
        for phrase in BANNED_PHRASES:
            assert phrase not in text, (
                f"{filename}: stale zero-network-activity claim {phrase!r} — "
                "reword to the data-locality guarantee (bloom#641)."
            )


def test_local_mode_docs_state_the_data_locality_guarantee():
    for filename in DOC_FILES:
        text = _normalized_text(filename)
        assert REQUIRED_PHRASE in text, (
            f"{filename}: expected the guarantee to be stated as "
            f"{REQUIRED_PHRASE!r} (bloom#641)."
        )
