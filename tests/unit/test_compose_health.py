"""Unit tests for scripts/compose_health.py — what counts as a healthy stack.

The CI gate this backs used to pass no matter what was running: `docker compose
ps --format json` prints either one object per line or one array, the filter
only understood the array form, and the resulting error was discarded and read
as "nothing is broken" (issue #163).

Two further holes outlived that bug, and are pinned here: `ps` omits every
non-running container unless given `-a`, so a crashed service is absent rather
than failing; and a crash-looping container reports neither a healthcheck
result nor an exit code, so state-blind filters miss it entirely.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = REPO_ROOT / "scripts" / "compose_health.py"


def _load():
    spec = importlib.util.spec_from_file_location("compose_health", _SCRIPT)
    assert spec and spec.loader, f"cannot load {_SCRIPT}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


compose_health = _load()


# --------------------------------------------------------------------------- #
# Classification — dev leniency (moved from test_check_health.py).
# --------------------------------------------------------------------------- #

def test_optional_llm_service_unhealthy_is_a_warning_not_a_failure():
    """langchain-agent needs user-supplied LLM config (LOCAL_LLM_URL/OPENAI_API_KEY)
    to ever be healthy, so a fresh `make dev-up` must NOT report the whole stack
    unhealthy because of it — it's a warning."""
    rows = [
        {"Service": "db-dev", "Health": "healthy", "State": "running"},
        {"Service": "langchain-agent", "Health": "unhealthy", "State": "running"},
    ]
    problems, warnings = compose_health._classify_service_rows(rows)
    assert not problems, f"optional service must not fail the check: {problems}"
    assert any("langchain-agent" in w for w in warnings)


def test_core_service_unhealthy_is_a_failure():
    rows = [{"Service": "db-dev", "Health": "unhealthy", "State": "running"}]
    problems, warnings = compose_health._classify_service_rows(rows)
    assert any("db-dev" in p for p in problems)
    assert not warnings


def test_bloommcp_is_required_not_optional():
    """Once its healthcheck targets /health, bloommcp should be genuinely healthy
    in dev (it has generated keys), so it stays REQUIRED."""
    rows = [{"Service": "bloommcp", "Health": "unhealthy", "State": "running"}]
    problems, _ = compose_health._classify_service_rows(rows)
    assert any("bloommcp" in p for p in problems)


def test_exited_core_service_with_nonzero_code_is_a_failure():
    """A crashed core service (e.g. realtime dying ~30s in on a bad DB_ENC_KEY)
    reports State=exited with a non-zero ExitCode and an EMPTY Health field (a
    dead container has no healthcheck result). That must still be a problem."""
    rows = [{"Service": "realtime", "Health": "", "State": "exited", "ExitCode": 1}]
    problems, warnings = compose_health._classify_service_rows(rows)
    assert any("realtime" in p and "exited" in p for p in problems), problems
    assert not warnings


def test_exited_oneshot_service_with_zero_code_is_not_a_failure():
    """A one-shot init container (e.g. minio-init) legitimately exits 0 — a
    completed job, not a failure."""
    rows = [{"Service": "minio-init", "Health": "", "State": "exited", "ExitCode": 0}]
    problems, warnings = compose_health._classify_service_rows(rows)
    assert problems == [] and warnings == []


# --------------------------------------------------------------------------- #
# Strict mode — a merge gate does not get the dev stack's leniency.
# --------------------------------------------------------------------------- #

def test_strict_mode_makes_the_optional_service_a_failure():
    """In CI langchain-agent gets a key and does come up healthy, so the gate
    must not inherit dev's 'optional' excuse for it."""
    rows = [{"Service": "langchain-agent", "Health": "unhealthy", "State": "running"}]
    problems, warnings = compose_health._classify_service_rows(rows, strict=True)
    assert any("langchain-agent" in p for p in problems)
    assert not warnings


def test_strict_mode_holds_an_optional_service_to_settling_too():
    rows = [{"Service": "langchain-agent", "Health": "starting", "State": "running"}]
    assert compose_health._services_still_settling(rows, strict=True) == [
        "langchain-agent"
    ]


# --------------------------------------------------------------------------- #
# The states that reported neither a healthcheck result nor an exit code.
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("state", ["restarting", "created", "dead", "paused"])
def test_a_service_in_a_non_running_state_is_a_failure(state):
    """A crash-looping container keeps Running=true, so compose reports
    Health="" and ExitCode=0 — invisible to both a health filter and an exit
    filter. 17 of 19 services carry restart: unless-stopped, so this is the
    stack's default failure mode, not an edge case."""
    rows = [{"Service": "cyl-pipeline-worker", "Health": "", "State": state,
             "ExitCode": 0}]
    problems, _ = compose_health._classify_service_rows(rows)
    assert any("cyl-pipeline-worker" in p and state in p for p in problems), problems


def test_a_restarting_service_is_given_time_to_settle_first():
    """A service may restart once while a dependency comes up; only a service
    still restarting at the deadline is a problem."""
    rows = [{"Service": "storage", "Health": "", "State": "restarting"}]
    assert compose_health._services_still_settling(rows) == ["storage"]


def test_a_running_service_with_no_healthcheck_is_not_flagged():
    """rest/cyl-pipeline-worker/cyl-status-poller define no healthcheck, so
    Health is "". Running with no probe is all we can assert about them."""
    rows = [{"Service": "rest", "Health": "", "State": "running"}]
    problems, warnings = compose_health._classify_service_rows(rows)
    assert problems == [] and warnings == []


def test_starting_required_service_is_settling():
    """A required service still in `starting` means 'keep waiting', not 'fail'."""
    rows = [
        {"Service": "bloommcp", "Health": "starting", "State": "running"},
        {"Service": "db-dev", "Health": "healthy", "State": "running"},
    ]
    assert compose_health._services_still_settling(rows) == ["bloommcp"]


def test_optional_starting_service_is_not_settling():
    """An optional service (langchain-agent) starting must not hold up the check."""
    rows = [{"Service": "langchain-agent", "Health": "starting", "State": "running"}]
    assert compose_health._services_still_settling(rows) == []


def test_all_healthy_means_settled():
    rows = [
        {"Service": "db-dev", "Health": "healthy", "State": "running"},
        {"Service": "bloommcp", "Health": "healthy", "State": "running"},
    ]
    assert compose_health._services_still_settling(rows) == []


# --------------------------------------------------------------------------- #
# Completeness — a service that is absent must not read as healthy.
# --------------------------------------------------------------------------- #

def test_a_service_with_no_container_is_reported_missing():
    """Without -a, `ps` drops a container that exited and stayed down, so the
    subtractive count sees nothing wrong. bloom-web and minio-init carry no
    restart policy, so they take exactly this path."""
    expected = ["bloom-web", "db-prod", "kong"]
    rows = [
        {"Service": "db-prod", "Health": "healthy", "State": "running"},
        {"Service": "kong", "Health": "healthy", "State": "running"},
    ]
    assert compose_health._missing_services(expected, rows) == ["bloom-web"]


def test_nothing_is_missing_when_every_service_is_listed():
    expected = ["db-prod", "kong"]
    rows = [
        {"Service": "db-prod", "Health": "healthy", "State": "running"},
        {"Service": "kong", "Health": "healthy", "State": "running"},
    ]
    assert compose_health._missing_services(expected, rows) == []


def test_missing_services_falls_back_to_the_container_name():
    """`ps` rows always carry Name; Service is absent for a one-off container."""
    expected = ["db-prod"]
    rows = [{"Name": "db-prod", "State": "running"}]
    assert compose_health._missing_services(expected, rows) == []


# --------------------------------------------------------------------------- #
# Output shapes — the original bug, plus failing closed on an unknown one.
# --------------------------------------------------------------------------- #

_SERVICES = [
    {"Name": "db-prod", "Health": "healthy", "State": "running", "ExitCode": 0},
    {"Name": "bloommcp", "Health": "starting", "State": "running", "ExitCode": 0},
]


def test_one_object_per_line_is_parsed():
    """The shape the old jq filter crashed on."""
    raw = "\n".join(json.dumps(s) for s in _SERVICES)
    rows, problems = compose_health._parse_ps_json(raw)
    assert problems == []
    assert [r["Name"] for r in rows] == ["db-prod", "bloommcp"]


def test_one_big_array_is_parsed():
    rows, problems = compose_health._parse_ps_json(json.dumps(_SERVICES))
    assert problems == []
    assert [r["Name"] for r in rows] == ["db-prod", "bloommcp"]


def test_both_shapes_classify_identically():
    """The version of Docker on the runner must not change the verdict."""
    ndjson, _ = compose_health._parse_ps_json(
        "\n".join(json.dumps(s) for s in _SERVICES)
    )
    array, _ = compose_health._parse_ps_json(json.dumps(_SERVICES))
    assert compose_health._classify_service_rows(
        ndjson
    ) == compose_health._classify_service_rows(array)


def test_an_unrecognised_shape_fails_closed():
    """A future wrapper object must not pass through as one healthy-looking
    record with Health and State both null."""
    rows, problems = compose_health._parse_ps_json('{"services":[{"Name":"a"}]}')
    assert rows is None
    assert any("names no service" in p or "unexpected" in p for p in problems)


def test_a_top_level_scalar_fails_closed():
    rows, problems = compose_health._parse_ps_json('"hello"')
    assert rows is None and problems


def test_truncated_json_is_reported_not_swallowed():
    """The whole point of the original bug: an error must not read as zero."""
    rows, problems = compose_health._parse_ps_json('{"Name": "db-prod", "Heal')
    assert rows is None
    assert any("could not parse" in p for p in problems)


def test_an_empty_array_yields_no_rows_rather_than_an_error():
    rows, problems = compose_health._parse_ps_json("[]")
    assert rows == [] and problems == []


# --------------------------------------------------------------------------- #
# The compose command itself.
# --------------------------------------------------------------------------- #

def test_ps_asks_for_stopped_containers_when_completeness_is_required():
    """Without -a the completeness check cannot see a container that exited."""
    args = compose_health._compose_args(
        [Path("docker-compose.prod.yml"), Path("docker-compose.ci.yml")],
        Path(".env.ci"),
    )
    assert args[:2] == ["docker", "compose"]
    assert args.count("-f") == 2
    assert "docker-compose.ci.yml" in args
    assert args[-2:] == ["--env-file", ".env.ci"]


def test_compose_args_default_to_the_dev_stack():
    args = compose_health._compose_args(None, None)
    assert str(compose_health.COMPOSE_FILE) in args
    assert str(compose_health.ENV_DEV) in args


def _captured_ps_argv(monkeypatch, **kwargs) -> list[str]:
    """Run _compose_ps_rows against a stub subprocess and return the argv."""
    seen: list[str] = []

    class _Result:
        returncode = 0
        stdout = '[{"Name":"db-prod","State":"running","Health":"healthy"}]'
        stderr = ""

    def _fake_run(cmd, **_):
        seen.extend(cmd)
        return _Result()

    monkeypatch.setattr(compose_health.subprocess, "run", _fake_run)
    compose_health._compose_ps_rows(**kwargs)
    return seen


def test_the_ps_command_asks_for_stopped_containers_when_completeness_matters(
    monkeypatch,
):
    """Without -a compose omits every non-running container, so the crashed
    service the gate exists to catch is simply absent from the answer."""
    argv = _captured_ps_argv(monkeypatch, all_containers=True)
    assert "ps" in argv and "-a" in argv, argv
    assert argv.index("-a") > argv.index("ps"), f"-a must be a ps flag: {argv}"


def test_the_ps_command_omits_a_when_not_asked(monkeypatch):
    argv = _captured_ps_argv(monkeypatch, all_containers=False)
    assert "-a" not in argv, argv


def test_the_ps_command_narrows_to_the_named_services(monkeypatch):
    argv = _captured_ps_argv(monkeypatch, services=["supabase-minio"])
    assert argv[-1] == "supabase-minio", argv


def test_a_list_of_non_objects_fails_closed_rather_than_crashing():
    """Belt and braces for the shape check: rows must be dicts before anything
    indexes them, or an odd shape raises TypeError instead of failing cleanly."""
    rows, problems = compose_health._parse_ps_json("[1, 2]")
    assert rows is None
    assert any("unexpected" in p for p in problems), problems
