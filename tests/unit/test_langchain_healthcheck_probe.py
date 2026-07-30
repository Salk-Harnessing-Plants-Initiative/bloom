"""langchain-agent's healthcheck must invoke a binary its image actually ships.

The probe used to be `curl`, which was present only as a side effect of the
NodeSource install step (`apt-get install curl` existed to pipe setup_20.x into
bash). When that step was replaced by a `COPY --from=node:20-bookworm` of the
Node binaries, curl went with it — python:3.11-slim ships no curl — and every
`docker compose up --wait` failed on `langchain-agent is unhealthy` even though
the app was serving /health fine. Nothing tied the two together, so removing the
Node install silently broke an unrelated healthcheck.

These tests pin the invariant rather than the specific command: whatever binary
the probe runs, the image must install it.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "langchain" / "Dockerfile"
COMPOSE_FILES = (
    REPO_ROOT / "docker-compose.dev.yml",
    REPO_ROOT / "docker-compose.prod.yml",
)
SERVICE = "langchain-agent"

# Interpreters python:3.11-slim + the Node COPY block guarantee. Anything else a
# probe invokes has to be apt-installed explicitly.
ALWAYS_PRESENT = frozenset({"python", "python3", "node"})

# Probes that need a package the base image does not carry.
NEEDS_INSTALL = frozenset({"curl", "wget"})


def _healthcheck_argv(compose_path: Path) -> list[str]:
    spec = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    test = spec["services"][SERVICE]["healthcheck"]["test"]
    assert isinstance(test, list) and test, f"{compose_path.name}: expected a list-form test"
    # Drop the CMD / CMD-SHELL prefix.
    return [a for a in test if a not in ("CMD", "CMD-SHELL")]


def _dockerfile_healthcheck() -> str:
    text = DOCKERFILE.read_text(encoding="utf-8")
    # HEALTHCHECK may continue across backslash-escaped newlines.
    joined = re.sub(r"\\\n\s*", " ", text)
    match = re.search(r"^HEALTHCHECK\s+(.+)$", joined, re.MULTILINE)
    assert match, "langchain/Dockerfile has no HEALTHCHECK instruction"
    return match.group(1)


def _installed_packages() -> set[str]:
    text = DOCKERFILE.read_text(encoding="utf-8")
    # Strip comments so prose mentioning a package name never counts as an install.
    body = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
    packages: set[str] = set()
    for args in re.findall(r"apt-get install\s+([^\n&|;]*)", body):
        packages.update(tok for tok in args.split() if not tok.startswith("-"))
    return packages


@pytest.mark.parametrize("compose_path", COMPOSE_FILES, ids=lambda p: p.name)
def test_compose_probe_binary_is_available_in_image(compose_path: Path):
    argv = _healthcheck_argv(compose_path)
    binary = Path(argv[0]).name
    if binary in NEEDS_INSTALL:
        assert binary in _installed_packages(), (
            f"{compose_path.name}: {SERVICE} healthcheck runs {binary!r}, which "
            f"langchain/Dockerfile does not install. The probe will fail with "
            f"'executable file not found' and the container will never report "
            f"healthy, failing `docker compose up --wait`."
        )
    else:
        assert binary in ALWAYS_PRESENT, (
            f"{compose_path.name}: {SERVICE} healthcheck runs {binary!r}, which is "
            f"neither guaranteed by the base image nor apt-installed."
        )


def test_dockerfile_probe_binary_is_available_in_image():
    instruction = _dockerfile_healthcheck()
    body = instruction.split("CMD", 1)[1] if "CMD" in instruction else instruction
    binary = body.strip().split()[0]
    if binary in NEEDS_INSTALL:
        assert binary in _installed_packages(), (
            f"langchain/Dockerfile HEALTHCHECK runs {binary!r}, which the same "
            f"Dockerfile does not install."
        )
    else:
        assert binary in ALWAYS_PRESENT, (
            f"langchain/Dockerfile HEALTHCHECK runs {binary!r}, which is neither "
            f"guaranteed by the base image nor apt-installed."
        )


def test_dev_and_prod_probes_stay_in_sync():
    dev, prod = (_healthcheck_argv(p) for p in COMPOSE_FILES)
    assert dev == prod, (
        "langchain-agent's healthcheck diverged between dev and prod compose. A "
        "probe fixed in one file but not the other leaves the untested "
        "environment broken."
    )


@pytest.mark.parametrize("compose_path", COMPOSE_FILES, ids=lambda p: p.name)
def test_probe_targets_the_unauthenticated_health_route(compose_path: Path):
    """/health takes no auth dependency; every /langchain/* route requires a JWT
    and would return 401, marking a healthy container unhealthy."""
    probe = " ".join(_healthcheck_argv(compose_path))
    assert "5002/health" in probe, (
        f"{compose_path.name}: probe must target the unauthenticated /health "
        f"route. Current: {probe!r}"
    )
    assert "/langchain/" not in probe, (
        f"{compose_path.name}: probe must not target an auth-gated /langchain/* route."
    )
