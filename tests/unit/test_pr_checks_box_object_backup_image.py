"""Regression guard: the Box object backup image's own pull-request check.

The image is built and scanned in a job of its own, run only when a pull
request changes the job's folder, rather than in the shared docker-build job.
That job already builds every service image on one disk-limited runner, and a
CRITICAL finding there blocks every pull request. The gate is a job-level
`if:`: GitHub counts a skipped job as passing, while a required check left out
by a workflow `paths:` filter stays pending and blocks the merge.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

# Plain "bash" can resolve to WSL's on Windows; prefer Git Bash when present.
_GIT_BASH = r"C:\Program Files\Git\usr\bin\bash.exe"
BASH = _GIT_BASH if Path(_GIT_BASH).exists() else shutil.which("bash") or "bash"

REPO_ROOT = Path(__file__).parent.parent.parent
PR_CHECKS = REPO_ROOT / ".github" / "workflows" / "pr-checks.yml"
PUBLISH = REPO_ROOT / ".github" / "workflows" / "docker-build-box-object-backup.yml"

JOB_PATH = "scheduled-jobs/box-object-backup"
DETECT = "detect-box-object-backup-changes"
IMAGE_JOB = "box-object-backup-image"
TAG = "box-object-backup:ci"


def _workflow() -> dict:
    return yaml.safe_load(PR_CHECKS.read_text(encoding="utf-8"))


def _job(name: str) -> dict:
    return _workflow()["jobs"][name]


def _step(job: dict, name: str) -> dict:
    for step in job["steps"]:
        if step.get("name") == name:
            return step
    raise AssertionError(f"no step named {name!r}")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        [
            "git", "-C", str(repo),
            "-c", "user.name=test", "-c", "user.email=test@example.com",
            "-c", "commit.gpgsign=false",
            *args,
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _run_change_check(tmp_path: Path, changed_file: str) -> str:
    """Run the step's real script against a repo with a base and a head commit."""
    repo = tmp_path / "repo"
    (repo / JOB_PATH).mkdir(parents=True)
    (repo / "web").mkdir()
    (repo / JOB_PATH / "backup_objects.py").write_text("a\n")
    (repo / "web" / "page.tsx").write_text("a\n")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    (repo / changed_file).write_text("b\n")
    _git(repo, "commit", "-q", "-am", "head")
    head = _git(repo, "rev-parse", "HEAD")

    script = _step(_job(DETECT), "Check for changes to the backup job")["run"]
    script = script.replace("${{ github.event.pull_request.base.sha }}", base)
    script = script.replace("${{ github.sha }}", head)
    assert "${{" not in script
    output = tmp_path / "github_output"
    output.write_text("")
    subprocess.run(
        [BASH, "-c", script],
        cwd=repo,
        env={**os.environ, "GITHUB_OUTPUT": str(output)},
        check=True,
    )
    return output.read_text()


def test_pull_request_trigger_has_no_paths_filter():
    wf = _workflow()
    pull_request = (wf.get("on") or wf.get(True))["pull_request"]
    assert "paths" not in pull_request
    assert "paths-ignore" not in pull_request


def test_change_check_outputs_whether_the_folder_changed():
    job = _job(DETECT)
    assert job["outputs"] == {"changed": "${{ steps.filter.outputs.changed }}"}
    checkout = job["steps"][0]
    assert checkout["uses"].startswith("actions/checkout@")
    assert checkout["with"]["fetch-depth"] == 0
    assert isinstance(job.get("timeout-minutes"), int)


def test_change_inside_the_job_folder_is_detected(tmp_path: Path):
    assert _run_change_check(tmp_path, f"{JOB_PATH}/backup_objects.py") == "changed=true\n"


def test_change_outside_the_job_folder_is_not(tmp_path: Path):
    assert _run_change_check(tmp_path, "web/page.tsx") == "changed=false\n"


def test_image_job_runs_only_when_the_folder_changed():
    job = _job(IMAGE_JOB)
    assert job["needs"] == DETECT
    assert job["if"] == f"needs.{DETECT}.outputs.changed == 'true'"
    assert isinstance(job.get("timeout-minutes"), int)


def test_image_is_built_from_the_job_folder_without_pushing():
    with_ = _step(_job(IMAGE_JOB), "Build box-object-backup image")["with"]
    assert with_["context"] == f"./{JOB_PATH}"
    assert with_["file"] == f"{JOB_PATH}/Dockerfile"
    assert with_["tags"] == TAG
    assert with_["load"] is True
    assert with_.get("push") in (None, False)


def test_image_gets_a_report_scan_and_a_separate_critical_gate():
    job = _job(IMAGE_JOB)
    report = _step(job, "Scan box-object-backup image")["with"]
    gate = _step(job, "Check box-object-backup for critical CVEs")["with"]
    assert report["image-ref"] == gate["image-ref"] == TAG
    assert report["severity"] == "CRITICAL,HIGH"
    assert str(report["exit-code"]) == "0"
    assert gate["severity"] == "CRITICAL"
    assert str(gate["exit-code"]) == "1"
    assert report["trivyignores"] == gate["trivyignores"] == ".trivyignore"


def test_image_is_smoke_tested_after_the_build():
    job = _job(IMAGE_JOB)
    names = [step.get("name") for step in job["steps"]]
    assert names.index("Build box-object-backup image") < names.index(
        "Smoke-test box-object-backup image"
    )
    run = _step(job, "Smoke-test box-object-backup image")["run"]
    for check in ("--help", "rclone version", "psql --version", "_test.py", "id -u"):
        assert check in run, f"smoke test does not check {check!r}"


def _run_in_image_checks(tmp_path: Path, extra_files: list[str]) -> int:
    """Run the smoke step's in-image script against a fake /app, with stub binaries."""
    run = _step(_job(IMAGE_JOB), "Smoke-test box-object-backup image")["run"]
    pieces = run.split("'")
    assert len(pieces) == 3, "the in-image checks must be one single-quoted sh -c script"
    app = tmp_path / "app"
    app.mkdir()
    for name in ["backup_objects.py", *extra_files]:
        (app / name).write_text("")
    stubs = tmp_path / "bin"
    stubs.mkdir()
    for tool, output in (("rclone", "rclone v1.75.1"), ("psql", "psql 17"), ("id", "100")):
        stub = stubs / tool
        stub.write_text(f"#!/bin/sh\necho '{output}'\n", newline="\n")
        stub.chmod(0o755)
    return subprocess.run(
        [BASH, "-c", pieces[1].replace("/app/", f"{app}/")],
        env={**os.environ, "PATH": f"{stubs}{os.pathsep}{os.environ.get('PATH', '')}"},
        capture_output=True,
    ).returncode


def test_in_image_checks_pass_on_a_clean_app(tmp_path: Path):
    assert _run_in_image_checks(tmp_path, []) == 0


@pytest.mark.parametrize("shipped", [["copier_test.py"], ["conftest.py"]], ids=["test-module", "conftest"])
def test_in_image_checks_fail_when_test_files_ship(tmp_path: Path, shipped: list[str]):
    assert _run_in_image_checks(tmp_path, shipped) != 0


def test_shared_docker_build_job_does_not_build_it():
    assert "box-object-backup" not in yaml.safe_dump(_job("docker-build"))


def test_build_matches_the_publish_workflow():
    pr = _step(_job(IMAGE_JOB), "Build box-object-backup image")["with"]
    publish = yaml.safe_load(PUBLISH.read_text(encoding="utf-8"))
    builds = [
        step["with"]
        for job in publish["jobs"].values()
        for step in job["steps"]
        if "build-push-action" in str(step.get("uses", ""))
    ]
    assert [(b["context"], b["file"]) for b in builds] == [(pr["context"], pr["file"])]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
