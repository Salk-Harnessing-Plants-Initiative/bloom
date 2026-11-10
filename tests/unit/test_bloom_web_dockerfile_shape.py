"""Shape guard for bloom-web's Node pins.

The prod image's builder and runner must name the same exact Node version, each
pinned by digest; the runner must pin its global npm; and the web CI jobs and the
dev image must run the same Node major as the runner.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent.parent
PROD_DOCKERFILE = REPO_ROOT / "web" / "Dockerfile.bloom-web.prod"
DEV_DOCKERFILE = REPO_ROOT / "web" / "Dockerfile.bloom-web.dev"
PR_CHECKS = REPO_ROOT / ".github" / "workflows" / "pr-checks.yml"

WEB_CI_JOBS = ("build-and-audit", "web-unit-tests", "compose-health-check")

_PINNED_FROM = re.compile(
    r"^FROM node:(?P<version>\d+\.\d+\.\d+)-(?P<variant>[a-z0-9.]+)"
    r"@sha256:[a-f0-9]{64} AS (?P<stage>\w+)$",
    re.M,
)


def _prod_stages() -> dict[str, str]:
    """Stage name -> exact Node version, for each digest-pinned FROM in the prod Dockerfile."""
    text = PROD_DOCKERFILE.read_text(encoding="utf-8")
    return {m["stage"]: m["version"] for m in _PINNED_FROM.finditer(text)}


def _runtime_major() -> str:
    return _prod_stages()["runner"].split(".")[0]


@pytest.mark.parametrize("stage", ["builder", "runner"])
def test_prod_stage_is_digest_pinned_to_an_exact_node_version(stage):
    assert stage in _prod_stages(), (
        f"{PROD_DOCKERFILE.name} needs `FROM node:<x.y.z>-<variant>@sha256:<digest> AS {stage}`"
    )


def test_builder_and_runner_use_the_same_node_version():
    stages = _prod_stages()
    assert stages["builder"] == stages["runner"], stages


def test_runner_pins_global_npm_to_an_exact_version():
    runner = PROD_DOCKERFILE.read_text(encoding="utf-8").split(" AS runner", 1)[1]
    assert re.search(r"^RUN npm install -g npm@\d+\.\d+\.\d+$", runner, re.M), (
        "the runner stage must pin its global npm with `RUN npm install -g npm@<x.y.z>`"
    )


@pytest.mark.parametrize("job", WEB_CI_JOBS)
def test_web_ci_job_runs_the_runtime_node_major(job):
    steps = yaml.safe_load(PR_CHECKS.read_text(encoding="utf-8"))["jobs"][job]["steps"]
    versions = [
        str((step.get("with") or {}).get("node-version"))
        for step in steps
        if str(step.get("uses", "")).startswith("actions/setup-node@")
    ]
    assert versions == [_runtime_major()], f"{job} sets up Node {versions}"


def test_dev_image_runs_the_runtime_node_major():
    match = re.search(r"^FROM node:(\d+)\b", DEV_DOCKERFILE.read_text(encoding="utf-8"), re.M)
    assert match and match.group(1) == _runtime_major(), (
        f"{DEV_DOCKERFILE.name} must use Node {_runtime_major()}, like the prod runner"
    )
