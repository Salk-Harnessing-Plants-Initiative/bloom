"""Shape guard and behaviour test for the ER-diagram drift check in compose-health-check.

The drift step redraws _WIKI/SUPABASE/erd.md with tbls from the freshly migrated database.
A stale committed file, or a redraw that fails, is flagged with an annotation (a stale redraw
is uploaded for the author to commit), and the job fails at the end, after the tests have run.
The database password reaches tbls through the environment only. The step's shell also runs
here against a fake docker, so its failure paths are tested, not just its text.
"""
from __future__ import annotations

import importlib.util
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent.parent
SCRIPTS = REPO_ROOT / "scripts"
PR_CHECKS = REPO_ROOT / ".github" / "workflows" / "pr-checks.yml"
MAKEFILE = REPO_ROOT / "Makefile"
JOB = "compose-health-check"
ERD = "_WIKI/SUPABASE/erd.md"
STALE = "steps.erd.outputs.stale == 'true'"
GATE = "steps.erd.outputs.stale == 'true' || steps.erd.outputs.redraw_failed == 'true'"
TBLS_OUTPUT = 'erDiagram\n\n"public.t" {\n  bigint id\n}\n'


def _load_schema_erd():
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location("schema_erd", SCRIPTS / "schema_erd.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


WRAPPED = _load_schema_erd().wrap(TBLS_OUTPUT)


def _job() -> dict:
    return yaml.safe_load(PR_CHECKS.read_text(encoding="utf-8"))["jobs"][JOB]


def _step_index(steps: list[dict], needle: str) -> int:
    matches = [i for i, s in enumerate(steps) if needle in str(s.get("run", "")) or needle == s.get("name")]
    assert len(matches) == 1, f"expected one step matching {needle!r}, found {len(matches)}"
    return matches[0]


def _drift_step() -> tuple[list[dict], int]:
    steps = _job()["steps"]
    return steps, _step_index(steps, "schema_erd.py wrap")


def _drift_run() -> str:
    steps, drift = _drift_step()
    return steps[drift]["run"]


def test_drift_step_runs_after_the_grants_step():
    steps, drift = _drift_step()
    grants = _step_index(steps, "Apply bloom_* schema-USAGE grants")
    assert drift > grants


def test_drift_step_uses_the_pinned_image():
    steps, drift = _drift_step()
    assert "$TBLS_IMAGE" in steps[drift]["run"] or "${TBLS_IMAGE}" in steps[drift]["run"]
    assert "@sha256:" in _job()["env"]["TBLS_IMAGE"]


def test_password_reaches_tbls_through_the_environment_only():
    run = _drift_run()
    assert "-e TBLS_DSN" in run
    assert "--dsn" not in run
    assert not re.search(r"-e TBLS_DSN=", run), "pass the variable by name so its value stays out of argv"


def test_redraw_goes_to_a_temp_file_compared_with_the_committed_one():
    run = _drift_run()
    assert f"> {ERD}" not in run, "redrawing over the committed file loses it when the redraw fails"
    assert "$RUNNER_TEMP" in run
    assert re.search(rf"cmp -s \S+ {re.escape(ERD)}", run)


def test_tbls_sees_only_its_config():
    run = _drift_run()
    assert '-v "$PWD/.tbls.yml:/work/.tbls.yml:ro"' in run
    assert '-v "$PWD:/work"' not in run, "the workspace holds .env.ci and the checkout token"


def test_drift_step_is_not_in_warning_mode():
    steps, drift = _drift_step()
    assert "continue-on-error" not in steps[drift], "a failure that shows green lets a stale diagram merge"


def test_the_stale_redraw_is_uploaded_before_the_tests():
    steps, drift = _drift_step()
    assert steps[drift].get("id") == "erd"
    uploads = [
        i for i, s in enumerate(steps)
        if str(s.get("uses", "")).startswith("actions/upload-artifact")
        and s.get("with", {}).get("name") == "erd"
    ]
    assert len(uploads) == 1, "expected one upload of the redrawn erd.md"
    upload = steps[uploads[0]]
    assert upload.get("if") == STALE
    assert "runner.temp" in str(upload["with"]["path"])
    assert drift < uploads[0] < _step_index(steps, "Run integration tests"), (
        "after a failing step the upload would be skipped, leaving the error pointing at no artifact"
    )


def test_a_stale_or_unredrawable_diagram_fails_the_job_after_the_tests_run():
    steps = _job()["steps"]
    gates = [i for i, s in enumerate(steps) if s.get("if") == GATE and "exit 1" in str(s.get("run", ""))]
    assert len(gates) == 1, "expected one step that fails the job on a stale or unredrawable diagram"
    assert gates[0] > _step_index(steps, "Run integration tests")
    assert gates[0] > _step_index(steps, "Run Playwright E2E tests")
    assert "continue-on-error" not in steps[gates[0]]


def test_makefile_gives_tbls_only_its_config_and_never_truncates_erd_md():
    text = MAKEFILE.read_text(encoding="utf-8")
    assert "$(CURDIR):/work" not in text
    assert text.count('-v "$(CURDIR)/.tbls.yml:/work/.tbls.yml:ro"') == 2
    assert f"> {ERD}" not in text, "make erd must not truncate erd.md before wrap succeeds"


def test_makefile_and_workflow_pin_the_same_image():
    pinned = re.search(r"^TBLS_IMAGE\s*\?=\s*(\S+)", MAKEFILE.read_text(encoding="utf-8"), re.M)
    assert pinned, "Makefile must define TBLS_IMAGE"
    assert pinned.group(1) == _job()["env"]["TBLS_IMAGE"]


# --- the step's shell, run against a fake docker ------------------------------------


def _executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _run_drift_step(tmp_path: Path, tbls_stdout: str, tbls_exit: int = 0, committed: str | None = None):
    """Run the step's shell with docker faked.

    Returns the result, $GITHUB_OUTPUT, the redraw's path, and the arguments of the docker run call.
    """
    work = tmp_path / "work"
    (work / "scripts").mkdir(parents=True)
    for name in ("schema_erd.py", "migration_changes.py", "migration_sql.py"):
        shutil.copy(SCRIPTS / name, work / "scripts" / name)
    if committed is not None:
        (work / ERD).parent.mkdir(parents=True)
        (work / ERD).write_text(committed, encoding="utf-8")
    tbls_out = tmp_path / "tbls.out"
    tbls_out.write_text(tbls_stdout, encoding="utf-8")
    run_args = tmp_path / "docker_run_args"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _executable(
        bin_dir / "docker",
        "#!/bin/sh\n"
        'if [ "$1" = compose ]; then echo db-container; exit 0; fi\n'
        f'printf "%s\\n" "$@" > "{run_args}"\n'
        f'cat "{tbls_out}"\n'
        f"exit {tbls_exit}\n",
    )
    _executable(bin_dir / "python3", f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    runner_temp = tmp_path / "runner"
    runner_temp.mkdir()
    github_output = tmp_path / "github_output"
    github_output.touch()
    script = tmp_path / "step.sh"
    script.write_text(_drift_run(), encoding="utf-8")
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "RUNNER_TEMP": str(runner_temp),
        "GITHUB_OUTPUT": str(github_output),
        "POSTGRES_PASSWORD": "pw",
        "COMPOSE_FILES": "-f docker-compose.prod.yml",
        "TBLS_IMAGE": "tbls@sha256:0",
    }
    result = subprocess.run(["bash", "-e", str(script)], cwd=work, env=env, capture_output=True, text=True)
    args = run_args.read_text(encoding="utf-8").splitlines() if run_args.exists() else []
    return result, github_output.read_text(encoding="utf-8"), runner_temp / "erd" / "erd.md", args


def test_step_passes_quietly_when_the_diagram_is_current(tmp_path):
    result, outputs, _, _ = _run_drift_step(tmp_path, TBLS_OUTPUT, committed=WRAPPED)
    assert result.returncode == 0, result.stdout + result.stderr
    assert outputs == ""
    assert "::error" not in result.stdout


def test_tbls_runs_on_the_database_network_with_only_its_config(tmp_path):
    _, _, _, args = _run_drift_step(tmp_path, TBLS_OUTPUT, committed=WRAPPED)
    assert args[0] == "run"
    assert args[args.index("--network") + 1] == "container:db-container"
    assert args[args.index("-e") + 1] == "TBLS_DSN", "the DSN goes by name, never by value"
    mounts = [args[i + 1] for i, a in enumerate(args) if a == "-v"]
    assert len(mounts) == 1 and mounts[0].endswith("/.tbls.yml:/work/.tbls.yml:ro")


@pytest.mark.parametrize("committed", ["an older diagram\n", None], ids=["differs", "missing"])
def test_stale_diagram_is_flagged_and_the_redraw_kept_for_upload(tmp_path, committed):
    result, outputs, redraw, _ = _run_drift_step(tmp_path, TBLS_OUTPUT, committed=committed)
    assert result.returncode == 0, "the tests still run; a later step fails the job"
    assert outputs == "stale=true\n"
    assert "::error title=ER diagram is stale::" in result.stdout
    assert redraw.read_text(encoding="utf-8") == WRAPPED


@pytest.mark.parametrize(
    "tbls_stdout, tbls_exit",
    [("", 1), ("", 0), ("Error: could not connect to the database\n", 0)],
    ids=["tbls fails", "tbls prints nothing", "tbls prints an error"],
)
def test_a_failed_redraw_is_flagged_and_the_tests_still_run(tmp_path, tbls_stdout, tbls_exit):
    result, outputs, _, _ = _run_drift_step(tmp_path, tbls_stdout, tbls_exit, committed=WRAPPED)
    assert result.returncode == 0, "the tests still run; a later step fails the job"
    assert outputs == "redraw_failed=true\n"
    assert "::error title=ER diagram could not be redrawn::" in result.stdout
