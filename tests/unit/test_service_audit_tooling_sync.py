"""Standing guard: the 4 places that declare "which services get audited"
must stay in sync with each other.

Adding a service to only some of scripts/check-uv-locks.py's SERVICES tuple,
.pre-commit-config.yaml's uv-lock-check hook, pr-checks.yml's python-audit
job, and .claude/commands/pre-merge.md's audit loop is easy to do by
accident (bloomcli itself almost shipped with a similar gap — see
openspec/changes/add-bloomcli-container-release/design.md Decision 7). This
test is a standing guard for the *next* service added, not just a one-time
confirmation that bloomcli landed correctly.

Known, documented exception: `services/workflows` is present in the first
three sources but missing from pre-merge.md's audit loop (a pre-existing gap
predating this test, not introduced by it). Excluded explicitly below rather
than silently dropped from the equality check.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
CHECK_UV_LOCKS = REPO_ROOT / "scripts" / "check-uv-locks.py"
PRE_COMMIT_CONFIG = REPO_ROOT / ".pre-commit-config.yaml"
PR_CHECKS = REPO_ROOT / ".github" / "workflows" / "pr-checks.yml"
PRE_MERGE_MD = REPO_ROOT / ".claude" / "commands" / "pre-merge.md"

# services/workflows is missing from pre-merge.md's audit loop — a
# pre-existing gap, not introduced by this test. Documented, not silent.
KNOWN_PRE_MERGE_MD_EXCEPTIONS = {"workflows"}


def _normalize(service_path: str) -> str:
    """'services/video-worker' -> 'video-worker'; 'bloommcp' -> 'bloommcp'."""
    return service_path.rsplit("/", 1)[-1]


def _services_from_check_uv_locks() -> set[str]:
    spec = importlib.util.spec_from_file_location("check_uv_locks", CHECK_UV_LOCKS)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return {_normalize(s) for s in module.SERVICES}


def _services_from_pre_commit() -> set[str]:
    # Several hooks (black/ruff/ruff-format) have similarly-shaped `files:`
    # regexes scoped to a different (and currently narrower) set of
    # services — that's a separate, pre-existing concern. Scope the search
    # specifically to the uv-lock-check hook's block, not just the first
    # `files:` line of this shape in the file.
    text = PRE_COMMIT_CONFIG.read_text(encoding="utf-8")
    hook_match = re.search(r"- id: uv-lock-check\b.*?(?=\n\s*- id:|\Z)", text, re.DOTALL)
    assert hook_match, "could not find the uv-lock-check hook block"
    files_match = re.search(r"files:\s*\^\(([^)]+)\)/", hook_match.group(0))
    assert files_match, "could not find the uv-lock-check hook's files: regex"
    return {_normalize(s) for s in files_match.group(1).split("|")}


def _services_from_pr_checks_audit_steps() -> set[str]:
    text = PR_CHECKS.read_text(encoding="utf-8")
    matches = re.findall(r"name:\s*Audit ([\w-]+) dependencies", text)
    assert matches, "no 'Audit <service> dependencies' steps found in pr-checks.yml"
    return {_normalize(s) for s in matches}


def _services_from_pre_merge_loop() -> set[str]:
    text = PRE_MERGE_MD.read_text(encoding="utf-8")
    matches = re.findall(r"for svc in ([\w /-]+); do", text)
    assert matches, "no 'for svc in ...' audit loop found in pre-merge.md"
    services: set[str] = set()
    for group in matches:
        services.update(_normalize(s) for s in group.split())
    return services


def test_check_uv_locks_matches_pre_commit_config():
    assert _services_from_check_uv_locks() == _services_from_pre_commit()


def test_check_uv_locks_matches_pr_checks_audit_steps():
    assert _services_from_check_uv_locks() == _services_from_pr_checks_audit_steps()


def test_pre_merge_md_matches_modulo_known_exceptions():
    expected = _services_from_check_uv_locks() - KNOWN_PRE_MERGE_MD_EXCEPTIONS
    actual = _services_from_pre_merge_loop()
    assert actual == expected, (
        f"pre-merge.md's audit loop ({actual}) doesn't match the expected set "
        f"({expected}) — if a new service was added, update pre-merge.md's loop(s); "
        f"if `services/workflows` was finally added there, remove it from "
        f"KNOWN_PRE_MERGE_MD_EXCEPTIONS in this test."
    )
