"""File-level guard for the a9 contract re-pin (``repin-cyl-contract-a9``, bloom#895).

The a9 migration must be ``20260917140000_fix_cyl_redelivery_status_fallback.sql``'s
function definition with exactly one line changed (the ``pinned_version`` literal),
and nothing else: no cutover guard, no ``DROP FUNCTION``, no extra statement. Its
rollback must restore that same definition verbatim -- not the older pre-fallback
body that ``20260917140000``'s own rollback restores.

Why a unit test: the integration suite proves behavior against a live Postgres, but
only a textual check catches silent drift in the copied ~270-line body (a dropped
``SECURITY DEFINER``, a recreated 1-arg overload, a changed grant) and a copied
guard outside the function region. No database needed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = REPO_ROOT / "supabase" / "migrations"
ROLLBACKS = REPO_ROOT / "supabase" / "rollbacks"

BASE = MIGRATIONS / "20260917140000_fix_cyl_redelivery_status_fallback.sql"
MIGRATION_A9 = MIGRATIONS / "20260925120000_cyl_writeback_contract_a9.sql"
ROLLBACK_A9 = ROLLBACKS / "20260925120000_cyl_writeback_contract_a9_rollback.sql"
BASE_ROLLBACK = ROLLBACKS / "20260917140000_fix_cyl_redelivery_status_fallback_rollback.sql"

REGION_START = "CREATE OR REPLACE FUNCTION public.insert_cyl_result_envelope("
REGION_END = "    TO bloom_writer, service_role, bloom_admin, bloom_workflows;"
PIN_A7 = "    pinned_version constant text := '0.1.0a7';"
PIN_A9 = "    pinned_version constant text := '0.1.0a9';"


def _lines(path: Path) -> list[str]:
    assert path.exists(), f"{path.relative_to(REPO_ROOT)} does not exist"
    # splitlines() absorbs CRLF, so a Windows checkout (.gitattributes has no *.sql rule)
    # compares the same as LF.
    return path.read_text(encoding="utf-8").splitlines()


def _split(path: Path) -> tuple[list[str], list[str]]:
    """(function region, everything outside it). The region runs from the CREATE line
    through the GRANT line naming bloom_workflows, inclusive."""
    lines = _lines(path)
    text = "\n".join(lines)
    assert text.count("$fn$") == 2, f"{path.name}: expected exactly one $fn$ … $fn$ body"
    starts = [i for i, line in enumerate(lines) if line.startswith(REGION_START)]
    ends = [i for i, line in enumerate(lines) if line == REGION_END]
    assert len(starts) == 1 and len(ends) == 1, (
        f"{path.name}: expected one CREATE and one closing GRANT, got {starts} / {ends}"
    )
    start, end = starts[0], ends[0]
    assert start < end
    return lines[start : end + 1], lines[:start] + lines[end + 1 :]


def _statements_outside(outside: list[str]) -> list[str]:
    """Non-blank, non-comment lines outside the function region."""
    return [s for line in outside if (s := line.split("--", 1)[0].strip())]


def _diff(a: list[str], b: list[str]) -> list[tuple[str, str]]:
    assert len(a) == len(b), f"line counts differ: {len(a)} vs {len(b)}"
    return [(x, y) for x, y in zip(a, b) if x != y]


def test_a9_migration_differs_from_base_only_in_pinned_version():
    base, _ = _split(BASE)
    a9, _ = _split(MIGRATION_A9)
    assert _diff(base, a9) == [(PIN_A7, PIN_A9)]


def test_a9_rollback_restores_base_definition_verbatim():
    base, _ = _split(BASE)
    rollback, _ = _split(ROLLBACK_A9)
    assert _diff(base, rollback) == []


def test_a9_rollback_is_not_the_pre_fallback_body():
    # 20260917140000's own rollback restores the older 20260912110000 body (no
    # bloom#875 fallback). The a9 rollback must not be a copy of it.
    older, _ = _split(BASE_ROLLBACK)
    rollback, _ = _split(ROLLBACK_A9)
    assert older != rollback
    assert any("bloom#875 fallback" in line for line in rollback)


@pytest.mark.parametrize("path", [MIGRATION_A9, ROLLBACK_A9], ids=["migration", "rollback"])
def test_nothing_outside_the_function_but_the_transaction(path):
    # No cutover guard (DO block / RAISE), no DROP FUNCTION, no data UPDATE/DELETE, no
    # extra statement of any kind: only the BEGIN/COMMIT wrapper sits outside the
    # copied definition (cyl-trait-writeback: "A contract re-pin leaves existing rows
    # untouched").
    _, outside = _split(path)
    assert _statements_outside(outside) == ["BEGIN;", "COMMIT;"]
