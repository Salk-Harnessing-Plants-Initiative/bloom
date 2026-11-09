"""bloommcp's container log is capped: it carries one usage line per data-tool call."""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
PROD_COMPOSE = REPO_ROOT / "docker-compose.prod.yml"


def test_bloommcp_log_is_rotated_and_capped_at_50_mb():
    compose = yaml.safe_load(PROD_COMPOSE.read_text(encoding="utf-8"))
    logging = compose["services"]["bloommcp"]["logging"]
    assert logging["driver"] == "json-file"
    assert logging["options"] == {"max-size": "10m", "max-file": "5"}
