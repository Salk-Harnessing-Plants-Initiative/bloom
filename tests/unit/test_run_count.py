"""Tests for argo/scrna/cellranger/run-count.sh with a stand-in cellranger and fastq-qc on PATH.

The stage steps copy the reads and reference into the run's shared folder; run-count works from
there and must not touch S3 (an `aws` on PATH fails the test if called).
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

CELLRANGER = Path(__file__).resolve().parents[2] / "argo/scrna/cellranger"
SCRIPT = CELLRANGER / "run-count.sh"

STUBS = {
    "cellranger": """#!/usr/bin/env bash
if [ "$1" = --version ]; then echo "cellranger cellranger-10.1.0"; exit 0; fi
for a in "$@"; do case "$a" in --id=*) id="${a#--id=}" ;; esac; done
[ -z "${FAKE_CR_FAIL:-}" ] || { echo "cellranger failed" >&2; exit 1; }
mkdir -p "${id}/outs"
echo matrix > "${id}/outs/filtered_feature_bc_matrix.h5"
echo metrics > "${id}/outs/metrics_summary.csv"
echo bam > "${id}/outs/web_summary.html"
""",
    "fastq-qc": """#!/usr/bin/env bash
while [ $# -gt 0 ]; do [ "$1" = --out-dir ] && out="$2"; shift; done
mkdir -p "${out}" && echo '{"chemistry": "SC3Pv3"}' > "${out}/qc_summary.json"
""",
    "aws": """#!/usr/bin/env bash
echo "aws $*" >> "${AWS_CALLS}"
exit 99
""",
    # Records each upload it's asked for; FAKE_PUT_FAIL makes it fail.
    "run-with-log": """#!/usr/bin/env bash
echo "$*" >> "${PUT_CALLS}"
[ -z "${FAKE_PUT_FAIL:-}" ] || exit 1
""",
}


@pytest.fixture
def env(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in STUBS.items():
        path = bin_dir / name
        path.write_text(body)
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
    (bin_dir / "fastq-sample-prefix").symlink_to(CELLRANGER / "fastq-sample-prefix.sh")
    run = tmp_path / "shared/runs/42"
    fastq = run / "fastq/root_tip"
    fastq.mkdir(parents=True)
    for read in ("R1", "R2"):
        (fastq / f"L007-259_S1_L001_{read}_001.fastq.gz").write_text("x")
    ref = tmp_path / "shared/ref/tair"
    ref.mkdir(parents=True)
    (ref / "reference.json").write_text("{}")
    return {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "AWS_CALLS": str(tmp_path / "aws_calls"),
        "PUT_CALLS": str(tmp_path / "put_calls"),
        "SAMPLE": "root_tip",
        "REFERENCE": "tair",
        "RUN_ID": "42",
        "CORES": "2",
        "MEM_GB": "4",
        "WORK_DIR": str(tmp_path / "work"),
        "RESULTS_DIR": str(run),
        "FASTQ_DIR": str(fastq),
        "REF_DIR": str(ref),
        "CELLRANGER_HOME": str(tmp_path),
    }


def count(env):
    return subprocess.run(
        [shutil.which("bash"), str(SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_a_staged_run_is_counted_without_touching_s3(env):
    result = count(env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not Path(env["AWS_CALLS"]).exists(), Path(env["AWS_CALLS"]).read_text()
    outs = Path(env["RESULTS_DIR"]) / "outs"
    assert sorted(p.name for p in outs.iterdir()) == [
        "_SUCCESS",
        "filtered_feature_bc_matrix.h5",
        "metrics_summary.csv",
        "qc_summary.json",
    ]
    assert "run_id=42" in (outs / "_SUCCESS").read_text()


def test_the_bam_is_off_and_the_prefix_comes_from_the_file_names(env, tmp_path):
    log = tmp_path / "cr_args"
    stub = Path(env["PATH"].split(":", 1)[0]) / "cellranger"
    stub.write_text(
        stub.read_text().replace("for a in", f'echo "$*" >> {log}\nfor a in', 1)
    )
    assert count(env).returncode == 0
    args = log.read_text()
    assert "--create-bam=false" in args
    assert "--sample=L007-259" in args


def test_a_run_already_counted_is_left_as_it_is(env):
    assert count(env).returncode == 0
    (Path(env["RESULTS_DIR"]) / "outs/filtered_feature_bc_matrix.h5").write_text("kept")
    result = count(env)
    assert result.returncode == 0
    assert "Already done" in result.stdout
    assert (
        Path(env["RESULTS_DIR"]) / "outs/filtered_feature_bc_matrix.h5"
    ).read_text() == "kept"


def test_a_reference_that_wasnt_staged_fails_with_3(env):
    (Path(env["REF_DIR"]) / "reference.json").unlink()
    result = count(env)
    assert result.returncode == 3
    assert "no staged Cell Ranger reference" in result.stdout
    assert not Path(env["AWS_CALLS"]).exists()


def test_fastqs_that_werent_staged_fail_with_4(env):
    for f in Path(env["FASTQ_DIR"]).iterdir():
        f.unlink()
    result = count(env)
    assert result.returncode == 4
    assert "no staged FASTQs" in result.stdout


def test_a_cellranger_failure_fails_with_5_and_keeps_the_log_in_the_run_folder(env):
    env["FAKE_CR_FAIL"] = "1"
    result = count(env)
    assert result.returncode == 5
    log = Path(env["RESULTS_DIR"]) / "logs/count.log"
    assert "cellranger count failed" in log.read_text()
    assert not (Path(env["RESULTS_DIR"]) / "outs/_SUCCESS").exists()


@pytest.mark.parametrize(
    "script",
    sorted((CELLRANGER.parent).rglob("*.sh")),
    ids=lambda p: p.name,
)
def test_every_pipeline_script_parses(script):
    result = subprocess.run(
        [shutil.which("bash"), "-n", str(script)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


# Only staging an S3 folder's reads touches S3, and only to read. SRA reads come from ENA or
# SRA, the genome from Bloom, every other step works on the shared folder, and the result leaves
# through load-dataset, into Bloom.
S3_STEPS = {"stage-sample"}


def test_only_the_steps_that_touch_s3_hold_s3_keys():
    import yaml

    template = yaml.safe_load(
        (CELLRANGER / "cellranger-count-template.yaml").read_text()
    )
    with_keys = {
        t["name"]
        for t in template["spec"]["templates"]
        if "container" in t
        and any(
            e["name"] == "AWS_ACCESS_KEY_ID" for e in t["container"].get("env") or []
        )
    }
    assert with_keys == S3_STEPS


def test_every_step_names_its_command():
    """Argo can't look up a private image's entrypoint (it would need the pull secret)."""
    import yaml

    template = yaml.safe_load((CELLRANGER / "cellranger-count-template.yaml").read_text())
    missing = [
        t["name"]
        for t in template["spec"]["templates"]
        if "container" in t and not t["container"].get("command")
    ]
    assert missing == []


# --------------------------------------------------------------------------- #
# Cell Ranger's report
# --------------------------------------------------------------------------- #


def puts(env):
    path = Path(env["PUT_CALLS"])
    return path.read_text().splitlines() if path.exists() else []


@pytest.mark.parametrize(
    "run_id, workflow, key",
    [("8", "scrna-cellranger-staging-8-c9bf7bdd", "8"),
     ("", "cellranger-count-x7k2p", "cellranger-count-x7k2p")],
    ids=["run-from-bloom", "run-by-hand"],
)
def test_the_report_is_kept_in_reports_and_stored_under_the_run(env, run_id, workflow, key):
    env.update(BLOOM_RUN_ID=run_id, WORKFLOW_NAME=workflow)
    result = count(env)
    assert result.returncode == 0, result.stdout + result.stderr
    reports = Path(env["RESULTS_DIR"]) / "reports"
    assert sorted(p.name for p in reports.iterdir()) == ["metrics_summary.csv", "web_summary.html"]
    assert puts(env) == [
        f"--put {reports}/web_summary.html --bucket scrna --path reports/{key}/web_summary.html"
        " --content-type text/html",
        f"--put {reports}/metrics_summary.csv --bucket scrna --path reports/{key}/metrics_summary.csv"
        " --content-type text/csv",
    ]


def test_a_failed_upload_doesnt_fail_the_count(env):
    env.update(BLOOM_RUN_ID="8", FAKE_PUT_FAIL="1")
    result = count(env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (Path(env["RESULTS_DIR"]) / "outs/_SUCCESS").exists()


@pytest.mark.parametrize("run_id, workflow", [("", ""), ("../8", ""), ("", "Bad/Name")])
def test_without_a_usable_key_the_report_is_not_uploaded(env, run_id, workflow):
    env.update(BLOOM_RUN_ID=run_id, WORKFLOW_NAME=workflow)
    result = count(env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (Path(env["RESULTS_DIR"]) / "reports/web_summary.html").exists()
    assert puts(env) == []
    assert "it is not stored in Bloom" in result.stdout


def test_the_count_step_passes_the_runs_bloom_id_and_workflow_name():
    import yaml

    doc = yaml.safe_load((CELLRANGER / "cellranger-count-template.yaml").read_text())
    templates = {t["name"]: t for t in doc["spec"]["templates"]}
    env = {e["name"]: e["value"] for e in templates["count"]["container"]["env"]}
    assert env["BLOOM_RUN_ID"] == "{{inputs.parameters.bloom-run-id}}"
    assert env["WORKFLOW_NAME"] == "{{workflow.name}}"
    task = next(t for t in templates["sample-pipeline"]["dag"]["tasks"] if t["name"] == "count")
    passed = {p["name"]: p["value"] for p in task["arguments"]["parameters"]}
    assert passed["bloom-run-id"] == "{{inputs.parameters.bloom-run-id}}"
