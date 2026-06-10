"""Integration tests must read `.env.dev` so they run against the local stack.

`tests/integration/conftest.py` loaded only `.env.prod`/`.env.ci`, so locally the
Postgres password resolved to "" and the `pg_conn` fixture silently skipped every
DB-backed test. It must also source `.env.dev` — but AFTER `.env.prod`/`.env.ci`
so CI (`.env.ci`) and a local prod-stack run (`.env.prod`) keep precedence and
adding the dev source can't change their behaviour.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFTEST = REPO_ROOT / "tests" / "integration" / "conftest.py"


def _env_resolution_expr() -> str:
    """The right-hand side of the `_env = _load_env(...) or ...` assignment."""
    text = CONFTEST.read_text(encoding="utf-8")
    m = re.search(r"^_env\s*=\s*(.+)$", text, re.MULTILINE)
    assert m, "could not find the `_env = _load_env(...)` assignment in conftest.py"
    return m.group(1)


def test_conftest_loads_env_dev():
    assert ".env.dev" in _env_resolution_expr(), (
        "conftest.py must source .env.dev so DB-backed tests run locally instead "
        "of silently skipping"
    )


def test_env_dev_has_lowest_precedence():
    expr = _env_resolution_expr()
    pos_dev = expr.find(".env.dev")
    pos_ci = expr.find(".env.ci")
    pos_prod = expr.find(".env.prod")
    assert pos_prod != -1 and pos_ci != -1, "conftest must still load .env.prod/.env.ci"
    assert pos_dev > pos_ci and pos_dev > pos_prod, (
        ".env.dev must be loaded AFTER .env.prod/.env.ci so CI and prod keep "
        "precedence (a left-to-right `or` chain returns the first non-empty)"
    )
