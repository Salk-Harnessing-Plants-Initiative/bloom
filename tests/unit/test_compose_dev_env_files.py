"""A fresh clone must not be blocked by a missing optional env_file.

`bloom-web` references `./web/.env`, which a fresh clone does not have (and which
is redundant — every var it needs is supplied via `environment`/`args`). Compose
v2 fails to start a service whose `env_file` is missing unless the entry is marked
`required: false`. This test pins that so `make dev-up` works from a clean clone
(issue #123).
"""
from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE = REPO_ROOT / "docker-compose.dev.yml"


def _compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def test_web_env_file_is_optional():
    services = _compose()["services"]
    entries = services["bloom-web"].get("env_file", [])
    web_entries = [
        e for e in entries
        if (isinstance(e, str) and "web/.env" in e)
        or (isinstance(e, dict) and "web/.env" in str(e.get("path", "")))
    ]
    assert web_entries, "expected bloom-web to reference ./web/.env"
    for e in web_entries:
        assert isinstance(e, dict) and e.get("required") is False, (
            "bloom-web's ./web/.env env_file must use the long form with "
            "`required: false` so a fresh clone (no web/.env) can `make dev-up` "
            "without Compose erroring on a missing file (issue #123)."
        )
