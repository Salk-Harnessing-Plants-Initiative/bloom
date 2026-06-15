"""Guard against CRLF line endings in the Postgres/Supabase container init scripts.

The scripts under ``volumes/db/`` are bind-mounted into the Linux ``db-dev``
container and executed there. If git checks them out with CRLF (which happens on
a Windows checkout with ``core.autocrlf=true`` unless ``.gitattributes`` pins
``eol=lf``), the ``#!/bin/bash`` shebang becomes ``#!/bin/bash\\r`` and the
container fails with ``/bin/bash^M: bad interpreter``. The init then dies, the
half-built data dir makes Postgres "Skip initialization", and the Supabase roles
+ auth/storage schemas are never created (issue #124).

What this test asserts — and why this form:

- It checks the **declarative checkout rule** via ``git check-attr eol`` (which
  must be ``lf`` for every script), NOT raw bytes in the working tree. A
  working-tree byte check reads CRLF on a Windows checkout and LF on Linux
  regardless of the fix — a platform detector, not a regression test. Even
  ``git show``/``git cat-file`` piped through a byte counter is unreliable on
  Windows because autocrlf smudges the output.
- ``eol=lf`` is what guarantees a fresh clone materialises these files as LF on
  *every* platform, which is the actual fix for #124. (The blobs are already
  stored LF; the bug is purely the checkout/working-tree conversion.)
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _init_scripts() -> list[str]:
    """All tracked ``*.sh``/``*.sql`` files under ``volumes/db/``."""
    out = subprocess.run(
        ["git", "ls-files", "--", "volumes/db"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [
        line
        for line in out.splitlines()
        if line.endswith(".sh") or line.endswith(".sql")
    ]


def _eol_attr(path: str) -> str:
    """Return the ``eol`` gitattribute git resolves for ``path``.

    ``git check-attr eol -- <path>`` prints ``<path>: eol: <value>`` where value
    is ``lf``, ``crlf``, or ``unspecified``.
    """
    out = subprocess.run(
        ["git", "check-attr", "eol", "--", path],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    # Format: "<path>: eol: <value>"
    return out.rsplit(":", 1)[-1].strip()


def test_init_scripts_are_tracked():
    scripts = _init_scripts()
    assert scripts, "expected tracked .sh/.sql init scripts under volumes/db/"


@pytest.mark.parametrize("path", _init_scripts())
def test_init_script_checks_out_as_lf(path: str):
    eol = _eol_attr(path)
    assert eol == "lf", (
        f"{path} has eol='{eol}' (expected 'lf'). Container init scripts must "
        f"check out as LF on every platform or a Windows clone breaks the shebang "
        f"(issue #124). Add an `eol=lf` rule covering it to .gitattributes."
    )
