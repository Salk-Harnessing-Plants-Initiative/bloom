"""Tests for bloom_mcp.__version__ and the bloom-mcp --version/-V entry point.

Mirrors bloomctl's importlib.metadata version pattern (bloomcli/src/bloomctl/__init__.py) —
the release gate (release-bloommcp.yml) has nothing to assert against without this.
"""

from __future__ import annotations

import importlib
import importlib.metadata as metadata
import re
import sys
from pathlib import Path

import pytest

import bloom_mcp
from bloom_mcp.server import main

BLOOMMCP_ROOT = Path(__file__).parent.parent
PYPROJECT = BLOOMMCP_ROOT / "pyproject.toml"


def _pyproject_version() -> str:
    text = PYPROJECT.read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert match, "no version field found in bloommcp/pyproject.toml"
    return match.group(1)


def test_version_matches_pyproject():
    assert bloom_mcp.__version__ == _pyproject_version()


def test_version_sentinel_when_not_installed(monkeypatch):
    """The PackageNotFoundError fallback — exercised without actually uninstalling
    the package, by making the metadata lookup itself raise.
    """

    def _raise(name):
        raise metadata.PackageNotFoundError(name)

    monkeypatch.setattr(metadata, "version", _raise)
    importlib.reload(bloom_mcp)
    try:
        assert bloom_mcp.__version__ == "0.0.0+unknown"
    finally:
        monkeypatch.undo()
        importlib.reload(bloom_mcp)  # restore the real __version__ for later tests


@pytest.mark.parametrize("flag", ["--version", "-V"])
def test_version_flag_exits_before_env_validation(monkeypatch, capsys, flag):
    # No BLOOM_*/SUPABASE_* env set at all — if main() reached any validate_*_env()
    # call, it would raise RuntimeError before returning.
    for var in (
        "SUPABASE_URL",
        "BLOOM_AGENT_KEY",
        "BLOOM_TRAITS_DIR",
        "BLOOM_OUTPUT_DIR",
        "BLOOM_PLOTS_DIR",
        "BLOOM_PLOTS_URL",
        "BLOOM_STORAGE_BACKEND",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(sys, "argv", ["bloom-mcp", flag])

    main()

    assert bloom_mcp.__version__ in capsys.readouterr().out


def test_version_flag_precedes_other_args(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["bloom-mcp", "--version", "--bogus"])
    main()
    assert bloom_mcp.__version__ in capsys.readouterr().out
