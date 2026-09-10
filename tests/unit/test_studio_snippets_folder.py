"""Studio's SQL editor needs a persisted snippets folder.

Studio reads SNIPPETS_MANAGEMENT_FOLDER as the directory for saved queries and
shows "SNIPPETS_MANAGEMENT_FOLDER env var is not set" on every editor load when
it is missing. The folder must be mounted from ./volumes/ alongside the database
data, or saved queries are lost whenever the container is recreated.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILES = ("docker-compose.prod.yml", "docker-compose.dev.yml")
SNIPPETS_VAR = "SNIPPETS_MANAGEMENT_FOLDER"
DATA_ROOT = "./volumes/"


def _studio(name: str) -> dict:
    compose = yaml.safe_load((REPO_ROOT / name).read_text(encoding="utf-8"))
    return compose["services"]["studio"]


@pytest.mark.parametrize("name", COMPOSE_FILES)
def test_studio_sets_snippets_folder(name: str):
    folder = _studio(name)["environment"].get(SNIPPETS_VAR)
    assert folder, f"{name}: studio must set {SNIPPETS_VAR} or the SQL editor errors"
    assert folder.startswith("/"), f"{name}: {SNIPPETS_VAR} must be absolute, got {folder!r}"


@pytest.mark.parametrize("name", COMPOSE_FILES)
def test_snippets_folder_is_mounted_from_the_data_directory(name: str):
    studio = _studio(name)
    folder = studio["environment"].get(SNIPPETS_VAR)
    assert folder, f"{name}: studio must set {SNIPPETS_VAR}"

    mounts = [str(m).split(":") for m in studio.get("volumes") or []]
    sources = [m[0] for m in mounts if len(m) >= 2 and m[1] == folder]
    assert sources, f"{name}: nothing is mounted at {folder}, so saved queries die with the container"
    assert sources[0].startswith(DATA_ROOT), (
        f"{name}: {folder} is mounted from {sources[0]!r}; it must live under "
        f"{DATA_ROOT} with the rest of the stack's persisted data"
    )
