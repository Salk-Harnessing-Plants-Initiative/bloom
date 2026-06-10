"""`scripts/generate_KEYS` was retired to a deprecation pointer that directs
developers to `make init`. Because it is now a tracked, committed pointer, it
must NOT be git-ignored — a lingering ignore rule would hide edits to it from
`git status` and contradicts the point of keeping a discoverable pointer.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_gitignore_does_not_ignore_generate_keys_pointer():
    """The .gitignore must not carry a `scripts/generate_KEYS` rule. (A plain
    `git check-ignore` is a false-green here — git never reports a *tracked* file
    as ignored — so assert against the ignore rule itself.)"""
    entries = {
        ln.strip()
        for ln in (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    }
    assert "scripts/generate_KEYS" not in entries, (
        "scripts/generate_KEYS is a tracked deprecation pointer; remove its "
        ".gitignore rule so edits to it stay visible in `git status`"
    )
