"""`.supabase-version` is the single source of truth for the pinned Supabase CLI.

`scripts/doctor.sh` and `DEV_SETUP.md` read this file. The CI workflows still
carry a literal `SUPABASE_VERSION: "x.y.z"`; this drift guard fails if any of
them diverges from `.supabase-version`, so the pin can't silently fork.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PIN_FILE = REPO_ROOT / ".supabase-version"
WORKFLOWS = [
    REPO_ROOT / ".github" / "workflows" / "pr-checks.yml",
    REPO_ROOT / ".github" / "workflows" / "deploy.yml",
]
_ENV_RE = re.compile(
    r'^\s*SUPABASE_VERSION:\s*"?([0-9]+\.[0-9]+\.[0-9]+)"?', re.MULTILINE
)


def test_pin_file_exists_and_is_a_version():
    assert PIN_FILE.exists(), ".supabase-version must exist (the canonical pin)"
    assert re.fullmatch(
        r"[0-9]+\.[0-9]+\.[0-9]+", PIN_FILE.read_text().strip()
    ), ".supabase-version must contain exactly one semver line"


def test_workflows_match_the_pin():
    pin = PIN_FILE.read_text().strip()
    problems = []
    for wf in WORKFLOWS:
        if not wf.exists():
            continue
        for v in _ENV_RE.findall(wf.read_text(encoding="utf-8")):
            if v != pin:
                problems.append(
                    f"{wf.name}: SUPABASE_VERSION={v} != .supabase-version={pin}"
                )
    assert not problems, "Supabase CLI pin drift:\n" + "\n".join(problems)
