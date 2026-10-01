"""Studio's SQL editor needs a persisted snippets folder.

Studio reads SNIPPETS_MANAGEMENT_FOLDER as the directory for saved queries and
shows "SNIPPETS_MANAGEMENT_FOLDER env var is not set" on every editor load when
it is missing. The folder must be mounted writable from ./volumes/snippets,
alongside the database data, or saved queries are lost whenever the container
is recreated.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILES = ("docker-compose.prod.yml", "docker-compose.dev.yml")
SNIPPETS_VAR = "SNIPPETS_MANAGEMENT_FOLDER"
SNIPPETS_SOURCE = "./volumes/snippets"


def _studio(name: str) -> dict:
    compose = yaml.safe_load((REPO_ROOT / name).read_text(encoding="utf-8"))
    return compose["services"]["studio"]


@pytest.mark.parametrize("name", COMPOSE_FILES)
def test_studio_sets_snippets_folder(name: str):
    folder = _studio(name)["environment"].get(SNIPPETS_VAR)
    assert folder, f"{name}: studio must set {SNIPPETS_VAR} or the SQL editor errors"
    assert folder.startswith("/"), f"{name}: {SNIPPETS_VAR} must be absolute, got {folder!r}"


@pytest.mark.parametrize("name", COMPOSE_FILES)
def test_snippets_folder_is_a_writable_mount_of_volumes_snippets(name: str):
    studio = _studio(name)
    folder = studio["environment"].get(SNIPPETS_VAR)
    assert folder, f"{name}: studio must set {SNIPPETS_VAR}"

    mounts = [str(m).split(":") for m in studio.get("volumes") or []]
    matches = [m for m in mounts if len(m) >= 2 and m[1] == folder]
    assert matches, f"{name}: nothing is mounted at {folder}, so saved queries die with the container"

    source, options = matches[0][0], ",".join(matches[0][2:]).split(",")
    assert source == SNIPPETS_SOURCE, (
        f"{name}: {folder} is mounted from {source!r}; it must be {SNIPPETS_SOURCE}, "
        "beside the rest of the stack's persisted data"
    )
    assert "ro" not in options, f"{name}: {folder} is mounted read-only, so Studio cannot save queries"
