"""Regression guard for ``.github/workflows/auto-close-issues-on-staging.yml``.

This workflow closes issues referenced by closing keywords when a PR is merged
into ``staging`` (GitHub's native ``Closes #N`` only fires on the default
branch ``main``; our flow merges feature PRs into ``staging`` first). See #336.

Why a unit test at all: the workflow triggers ONLY on
``pull_request: closed`` against ``staging``, so it can never run in PR CI —
it won't execute until it is already on ``staging``. That means its own CI can
never catch a regression in the closing-keyword regex. This test is the only
pre-merge gate. It does two things:

1. Asserts the workflow's SHAPE — the trigger, the ``merged == true`` guard,
   ``issues: write`` permission, a SHA-pinned ``github-script`` action, and the
   ``state_reason: 'completed'`` close — so a future edit can't silently weaken
   the safety properties the reviews signed off on.

2. EXTRACTS the closing-keyword regex from the embedded script and runs it
   against a case table. The pattern is deliberately free of JS-only constructs
   (no named groups, no lookbehind), so the same literal compiles and behaves
   identically under Python ``re`` — letting us lock the match/no-match
   behavior (keyword families, optional colon, multi-line, word boundary,
   bare mentions, cross-repo refs) without a JS runtime.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "auto-close-issues-on-staging.yml"
JOB = "close-referenced-issues"


def _load_workflow() -> dict:
    """Parse the workflow YAML and return the dict."""
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _on_block(workflow: dict) -> dict:
    """Return the ``on:`` block.

    PyYAML parses the bare key ``on`` as the boolean ``True`` (YAML 1.1
    treats on/off/yes/no as booleans), so accept either key.
    """
    return workflow.get("on") or workflow.get(True)


def _script(workflow: dict) -> str:
    """Return the inline github-script body from the single step."""
    step = workflow["jobs"][JOB]["steps"][0]
    return step["with"]["script"]


def _closing_regex(workflow: dict) -> re.Pattern[str]:
    """Extract the ``const re = /.../gi;`` literal and compile it under Python.

    The pattern contains no ``/`` of its own, so the slash delimiters bound it
    unambiguously. JS ``gi`` flags map to Python ``re.IGNORECASE`` (the global
    flag is implicit in ``findall``).
    """
    script = _script(workflow)
    match = re.search(r"const re = /(.+)/gi;", script)
    assert match, (
        "could not find the `const re = /.../gi;` closing-keyword pattern in "
        "the workflow script — has its shape changed?"
    )
    pattern = match.group(1)
    # Guard the portability contract: JS-only constructs would make this Python
    # compile diverge from the workflow's actual behavior.
    assert "?<" not in pattern, (
        "closing regex uses a named group or lookbehind (?<...); keep it "
        "portable so this test exercises the real pattern (see #336 review I1)."
    )
    return re.compile(pattern, re.IGNORECASE)


def _matches(rx: re.Pattern[str], text: str) -> list[int]:
    """Mirror the workflow: dedupe via a set, return sorted issue numbers."""
    return sorted({int(n) for n in rx.findall(text)})


# ---------------------------------------------------------------------------
# Shape invariants
# ---------------------------------------------------------------------------


def test_triggers_on_pr_closed_against_staging() -> None:
    """Fires on pull_request: closed targeting staging — not main, not opened."""
    on = _on_block(_load_workflow())
    pr = on["pull_request"]
    assert "closed" in (pr.get("types") or []), (
        "must trigger on the 'closed' activity type — that's when merge status "
        f"is known. Got types={pr.get('types')!r}"
    )
    assert "staging" in (pr.get("branches") or []), (
        "must be scoped to base branch 'staging'; native auto-close already "
        f"handles merges to main. Got branches={pr.get('branches')!r}"
    )


def test_guarded_on_merged_true() -> None:
    """The job only runs for actually-merged PRs (not closed-without-merge)."""
    job = _load_workflow()["jobs"][JOB]
    cond = str(job.get("if", ""))
    assert "merged == true" in cond, (
        "job must be gated on `github.event.pull_request.merged == true` so a "
        f"PR closed without merging never closes its issues. Got if={cond!r}"
    )


def test_grants_issues_write_least_privilege() -> None:
    """issues: write is required to close; nothing broader should be granted."""
    perms = _load_workflow().get("permissions") or {}
    assert perms.get("issues") == "write", (
        f"must grant issues: write to close issues; got {perms.get('issues')!r}"
    )
    assert perms.get("contents") == "read", (
        "contents should stay read-only (least privilege); got "
        f"{perms.get('contents')!r}"
    )


def test_github_script_action_is_sha_pinned() -> None:
    """The privileged github-script step must be SHA-pinned, not floating."""
    step = _load_workflow()["jobs"][JOB]["steps"][0]
    uses = step.get("uses", "")
    assert uses.startswith("actions/github-script@"), (
        f"expected the step to use actions/github-script; got {uses!r}"
    )
    ref = uses.split("@", 1)[1]
    assert re.fullmatch(r"[0-9a-f]{40}", ref), (
        f"actions/github-script must be pinned to a 40-char commit SHA, got "
        f"{ref!r} — matches the repo's setup-uv SHA-pin convention."
    )


def test_close_marks_state_reason_completed() -> None:
    """Closed issues are marked 'completed' (not 'not_planned')."""
    script = _script(_load_workflow())
    assert "state_reason: 'completed'" in script, (
        "issues should be closed with state_reason: 'completed' so the close "
        "reads as done-work, not as won't-fix."
    )


# ---------------------------------------------------------------------------
# Closing-keyword regex behavior (the actual matcher, extracted from the YAML)
# ---------------------------------------------------------------------------

# (text, expected sorted issue numbers). Bias is toward false-negatives: a
# missed close just leaves an issue open (harmless); a false-positive would
# wrongly close an unrelated issue.
KEYWORD_CASES = [
    # --- keyword families, case-insensitivity, optional colon ---
    ("Closes #5", [5]),
    ("closes #5", [5]),
    ("CLOSES #5", [5]),
    ("Closed #42", [42]),
    ("Close #7", [7]),
    ("Fixes #5", [5]),
    ("Fix #5", [5]),
    ("Fixed #5", [5]),
    ("Resolves #9", [9]),
    ("Resolve #9", [9]),
    ("Resolved #9", [9]),
    ("Closes: #5", [5]),  # colon + space is supported (GitHub-native)
    # --- multiple refs need a keyword EACH (documented convention) ---
    ("Fixes #1, closes #2", [1, 2]),
    ("Fixes #1, #2", [1]),  # second has no keyword -> only #1
    ("Close #7 and close #8", [7, 8]),
    ("closes #5 closes #5", [5]),  # set-dedupe
    # --- multi-line: \s+ spans the newline ---
    ("Resolves\n#9", [9]),
    ("title\n\nCloses #12", [12]),
    # --- no-match: bare mentions and cross-repo refs ---
    ("(#5)", []),
    ("see #5 for context", []),
    ("(#315, #305 AC5)", []),  # the exact style that left #305 open
    ("owner/repo#5", []),
    ("see talmolab/sleap-roots-analyze#162", []),
    # cross-repo ref consumes the keyword; trailing bare "#5" has none -> []
    ("Closes owner/repo#9 and #5", []),
    # --- no-match: keyword not adjacent to the ref ---
    ("fix bug in #305 handling", []),
    ("This does not directly reference #5", []),
    # --- no-match: colon without a following space (false-negative, documented)
    ("Closes:#5", []),
]


@pytest.mark.parametrize("text, expected", KEYWORD_CASES)
def test_closing_keyword_regex_cases(text: str, expected: list[int]) -> None:
    """The extracted regex matches exactly the documented cases."""
    rx = _closing_regex(_load_workflow())
    assert _matches(rx, text) == expected, (
        f"closing-keyword regex mismatch for {text!r}: "
        f"got {_matches(rx, text)}, expected {expected}"
    )
