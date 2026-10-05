"""The compose-health-check job's health gate: Compose's own --wait, plus a crash-loop check.

Shape tests pin what pr-checks.yml says; behavioural tests run the start steps with a
fake `docker` on PATH and pin what they exit with.
"""

from __future__ import annotations

import os
import re
import shlex
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent.parent
PR_CHECKS = REPO_ROOT / ".github" / "workflows" / "pr-checks.yml"
COMPOSE_PROD = REPO_ROOT / "docker-compose.prod.yml"
START_STEPS = ("Start MinIO and create buckets", "Start database", "Start remaining services")


def _job() -> dict:
    return yaml.safe_load(PR_CHECKS.read_text())["jobs"]["compose-health-check"]


def _step(name: str) -> dict:
    (step,) = [s for s in _job()["steps"] if s.get("name") == name]
    return step


def _tokens(text: str) -> list[str]:
    lexer = shlex.shlex(text, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        return list(lexer)
    except ValueError:  # a quote opened before this command and closed after it
        return text.split()


def _compose_calls() -> list[list[str]]:
    """Every `docker compose ...` command in the job, as shell tokens up to its end."""
    calls = []
    for step in _job()["steps"]:
        run = re.sub(r"\\\s*\n\s*", " ", str(step.get("run") or ""))
        for line in run.splitlines():
            for match in re.finditer(r"docker compose\b", line):
                tokens = _tokens(line[match.start():])
                end = next((i for i, t in enumerate(tokens) if t and set(t) <= set(";&|()")), len(tokens))
                calls.append(tokens[:end])
    return calls


# --- shape --------------------------------------------------------------------------


def test_every_compose_up_in_the_job_waits_for_health():
    ups = [c for c in _compose_calls() if "up" in c]
    assert len(ups) >= 3, ups
    for call in ups:
        assert "--wait" in call, f"compose up without --wait: {call}"
        i = call.index("--wait-timeout")
        assert call[i + 1].isdigit(), call


@pytest.mark.parametrize("target", ["supabase-minio", "db-prod", "--build"])
def test_each_start_target_is_brought_up(target):
    assert any("up" in c and target in c for c in _compose_calls()), target


def test_the_job_never_parses_compose_ps_json():
    for call in _compose_calls():
        if "ps" in call:
            assert not any(t == "json" or t.endswith("=json") for t in call), call


def test_the_start_steps_cannot_be_made_to_pass_on_failure():
    for name in START_STEPS:
        step = _step(name)
        assert not step.get("continue-on-error"), name
        assert "|| true" not in str(step["run"]), name


# --- behaviour ----------------------------------------------------------------------

FAKE_DOCKER = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$DOCKER_CALLS"
args=" $* "
if [[ "$args" == *" up "* && -n "${FAIL_UP:-}" ]]; then exit 1; fi
if [[ "$args" == *" --status restarting "* ]]; then printf '%s' "${RESTARTING:-}"; fi
exit 0
"""


def _run_step(tmp_path: Path, name: str, **env: str) -> tuple[int, str]:
    fake = tmp_path / "bin"
    fake.mkdir(exist_ok=True)
    for tool, body in (("docker", FAKE_DOCKER), ("sleep", "#!/usr/bin/env bash\nexit 0\n")):
        path = fake / tool
        path.write_text(body)
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
    calls = tmp_path / "calls.txt"
    calls.write_text("")
    full_env = {
        "PATH": f"{fake}{os.pathsep}{os.environ['PATH']}",
        "COMPOSE_FILES": "-f docker-compose.prod.yml -f docker-compose.ci.yml",
        "DOCKER_CALLS": str(calls),
        **{k: v for k, v in (_step(name).get("env") or {}).items()},
        **env,
    }
    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", _step(name)["run"]],
        env=full_env, capture_output=True, text=True, timeout=30,
    )
    return result.returncode, calls.read_text() + result.stdout + result.stderr


@pytest.mark.parametrize("name", START_STEPS)
def test_a_start_step_passes_when_compose_does(tmp_path, name):
    code, out = _run_step(tmp_path, name)
    assert code == 0, out


@pytest.mark.parametrize("name", START_STEPS)
def test_a_start_step_fails_when_compose_up_fails(tmp_path, name):
    code, out = _run_step(tmp_path, name, FAIL_UP="1")
    assert code != 0, out


def test_a_failed_wait_shows_service_states_and_logs(tmp_path):
    code, out = _run_step(tmp_path, "Start remaining services", FAIL_UP="1")
    assert code != 0
    assert "ps --all" in out and "logs --tail=" in out
    assert "::error::" in out and "300s" not in out.split("::error::", 1)[1].splitlines()[0]


def test_a_service_still_restarting_after_the_wait_fails_the_step(tmp_path):
    code, out = _run_step(tmp_path, "Start remaining services", RESTARTING="abc123")
    assert code != 0, out
    assert "restarting" in out.lower()


# --- compose settings the gate depends on --------------------------------------------


@pytest.fixture(scope="module")
def services():
    return yaml.safe_load(COMPOSE_PROD.read_text())["services"]


def test_supavisor_gets_its_open_files_limit_from_docker(services):
    """Its start script raises nofile to 100000; with cap_drop ALL it can't on its own."""
    sv = services["supavisor"]
    assert sv["ulimits"]["nofile"] == {"soft": 100000, "hard": 100000}
    assert sv["cap_drop"] == ["ALL"]


@pytest.mark.parametrize("name", ["studio", "langchain-agent", "workflows"])
def test_slow_starting_services_have_a_grace_period(services, name):
    assert services[name]["healthcheck"].get("start_period"), name
