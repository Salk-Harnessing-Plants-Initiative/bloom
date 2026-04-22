"""Unit test fixtures.

Loads `scripts/check-uv-locks.py` by path since the dash in the filename
prevents a standard `import scripts.check_uv_locks` statement.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check-uv-locks.py"


@pytest.fixture(scope="session")
def check_uv_locks_module():
    spec = importlib.util.spec_from_file_location("check_uv_locks", _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_uv_locks"] = module
    spec.loader.exec_module(module)
    return module
