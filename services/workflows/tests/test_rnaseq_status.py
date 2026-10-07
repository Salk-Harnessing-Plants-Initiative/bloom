"""Unit tests for reading a Cell Ranger run's status from its Argo Workflow. The
fixtures are real Workflows from runai-busch-lab, trimmed to the fields read."""

import copy
import json
from pathlib import Path

import pytest

import rnaseq_status as st

FIXTURES = Path(__file__).parent / "fixtures" / "argo"
WF = "scrna-cellranger-dev-900001-a5d34435"
RUN = {
    "params": {"sample": "tinygex", "reference": "tiny_ref"},
    "run_key": "tinygex__tiny_ref__poller-sample-ok",
}
NO_REF_RUN = {
    "params": {"sample": "tinygex", "reference": "no_such_ref"},
    "run_key": "tinygex__no_such_ref__poller-sample-noref",
}


def _load(name: str) -> dict:
    return json.loads((FIXTURES / f"cellranger_{name}.json").read_text())


def _pod(workflow: dict, template: str) -> dict:
    return next(
        n
        for n in workflow["status"]["nodes"].values()
        if n["type"] == "Pod" and st._template(n) == template
    )


# --------------------------------------------------------------------------- #
# Real Workflows
# --------------------------------------------------------------------------- #


def test_a_just_started_workflow_is_running_at_stage_reference():
    status = st.read_cellranger_status(_load("started"), RUN)
    assert status == st.RunStatus(
        "running",
        "stage-reference",
        {"stage-reference": f"{WF}-stage-reference-3880791564"},
    )


def test_a_counting_workflow_is_running_at_count_with_each_started_pod():
    status = st.read_cellranger_status(_load("counting"), RUN)
    assert (status.status, status.current_step) == ("running", "count")
    assert status.step_pods == {
        "stage-reference": f"{WF}-stage-reference-3880791564",
        "stage": f"{WF}-stage-sample-2425514970",
        "qc": f"{WF}-qc-1465883946",
        "count": f"{WF}-count-3938252481",
    }
    assert (status.exit_code, status.message) == (None, None)


def _with_load(workflow: dict, dataset_name: str | None) -> dict:
    """The Workflow as the current template runs it: a succeeded load-dataset step, and the
    dataset-name argument the worker passes only for a run naming a dataset."""
    if dataset_name is not None:
        workflow.setdefault("spec", {})["arguments"] = {
            "parameters": [{"name": "dataset-name", "value": dataset_name}]
        }
    workflow["status"]["nodes"]["load"] = {
        "id": "load",
        "type": "Pod",
        "templateName": "load-dataset",
        "phase": "Succeeded",
        "startedAt": "2026-09-29T01:20:00Z",
    }
    return workflow


def test_a_succeeded_workflow_finishes_at_cleanup():
    status = st.read_cellranger_status(_load("succeeded"), RUN)
    assert (status.status, status.current_step, status.exit_code) == (
        "succeeded",
        "cleanup",
        0,
    )
    assert set(status.step_pods) == {
        "stage-reference",
        "stage",
        "qc",
        "count",
        "cleanup",
    }


@pytest.mark.parametrize(
    "dataset_name, message",
    [
        ("Root atlas", "Finished: loaded into Bloom"),
        (None, "Finished; the run named no dataset, so nothing was loaded into Bloom"),
    ],
)
def test_a_succeeded_run_says_whether_it_loaded_a_dataset(dataset_name, message):
    workflow = _with_load(_load("succeeded"), dataset_name)
    assert st.read_cellranger_status(workflow, RUN).message == message


def test_a_missing_reference_fails_at_stage_reference_with_exit_3():
    status = st.read_cellranger_status(_load("no_reference"), NO_REF_RUN)
    assert status == st.RunStatus(
        "failed",
        "stage-reference",
        {
            "stage-reference": "scrna-cellranger-dev-900002-28b84579-stage-reference-2584972205"
        },
        3,
        "No reference at reference_genome/no_such_ref/",
    )


def test_every_reported_step_is_one_the_table_allows():
    allowed = {"stage-reference", "stage", "qc", "count", "cleanup"}
    for name in ("started", "counting", "succeeded", "no_reference"):
        status = st.read_cellranger_status(_load(name), RUN)
        assert status.current_step in allowed
        assert set(status.step_pods) <= allowed


# --------------------------------------------------------------------------- #
# Cases built from the real Workflows
# --------------------------------------------------------------------------- #


def test_a_retried_step_records_its_latest_attempt():
    wf = _load("counting")
    first = _pod(wf, "count")
    retry = copy.deepcopy(first)
    retry["id"] = f"{WF}-1111111111"
    retry["startedAt"] = "2099-01-01T00:00:00Z"
    first["phase"] = "Failed"
    wf["status"]["nodes"][retry["id"]] = retry
    status = st.read_cellranger_status(wf, RUN)
    assert status.step_pods["count"] == f"{WF}-count-1111111111"
    assert status.current_step == "count"


def _failed_at(template: str, exit_code: str | None) -> dict:
    wf = _load("counting")
    wf["status"]["phase"] = "Failed"
    pod = _pod(wf, template)
    pod["phase"] = "Failed"
    pod["finishedAt"] = "2099-01-01T00:00:00Z"
    if exit_code is None:
        pod.get("outputs", {}).pop("exitCode", None)
    else:
        pod.setdefault("outputs", {})["exitCode"] = exit_code
    return wf


@pytest.mark.parametrize(
    "template, step, exit_code, message",
    [
        ("stage-sample", "stage", "4", "No FASTQs at raw_reads/tinygex/"),
        (
            "count",
            "count",
            "5",
            "Cell Ranger failed; its log is at "
            "/hpi/hpi_dev/users/bfernando/scrna/runs/tinygex__tiny_ref__poller-sample-ok/logs/count.log",
        ),
        (
            "count",
            "count",
            "6",
            "Sample tinygex can't be used as a Cell Ranger run id "
            "(letters, digits, '_' or '-', at most 64)",
        ),
        (
            "stage-sample",
            "stage",
            "7",
            "The FASTQs in raw_reads/tinygex/ must be named like "
            "<name>_S1_L001_R1_001.fastq.gz, with an R1 and an R2 for every lane; "
            "the stage step's log lists the files",
        ),
        ("qc", "qc", "137", "Step qc failed (exit 137)"),
        ("qc", "qc", None, "Step qc failed"),
    ],
)
def test_a_failed_step_explains_its_exit_code(template, step, exit_code, message):
    status = st.read_cellranger_status(_failed_at(template, exit_code), RUN)
    assert (status.status, status.current_step, status.message) == (
        "failed",
        step,
        message,
    )
    assert status.exit_code == (int(exit_code) if exit_code else None)


def test_a_workflow_that_failed_before_any_step_says_so():
    wf = {"metadata": {"name": WF}, "status": {"phase": "Error", "nodes": {}}}
    assert st.read_cellranger_status(wf, RUN) == st.RunStatus(
        "failed", None, {}, None, "The workflow failed before a step ran"
    )


def test_nothing_is_reported_before_any_step_starts():
    wf = {"metadata": {"name": WF}, "status": {"phase": "Pending", "nodes": {}}}
    assert st.read_cellranger_status(wf, RUN) is None


def test_a_workflow_with_no_status_yet_reports_nothing():
    assert st.read_cellranger_status({"metadata": {"name": WF}}, RUN) is None


def test_nodes_of_other_templates_are_ignored():
    wf = _load("counting")
    stray = copy.deepcopy(_pod(wf, "qc"))
    stray["id"] = f"{WF}-2222222222"
    stray["templateRef"] = {"name": "x", "template": "not-a-step"}
    wf["status"]["nodes"][stray["id"]] = stray
    status = st.read_cellranger_status(wf, RUN)
    assert not any("not-a-step" in pod for pod in status.step_pods.values())


def test_the_steps_match_the_template_file():
    import yaml

    template = yaml.safe_load(
        (
            Path(__file__).resolve().parents[3]
            / "argo"
            / "scrna"
            / "cellranger"
            / "cellranger-count-template.yaml"
        ).read_text()
    )
    names = {t["name"] for t in template["spec"]["templates"]}
    assert set(st.CELLRANGER_STEPS) <= names


FOLDER_RUN = {
    "params": {
        "sample": "tinygex",
        "reference": "tiny_ref",
        "fastq_url": "s3://lab-data/run42/",
        "fastq_files": [
            {"name": "tinygex_S1_L001_R1_001.fastq.gz", "size": 1, "etag": '"a"'}
        ],
    },
    "run_key": "tinygex__tiny_ref__poller-sample-ok",
}


@pytest.mark.parametrize(
    "exit_code, words",
    [
        (
            "4",
            "The FASTQs in s3://lab-data/run42/ were removed after the run was started",
        ),
        ("6", "The run's folder s3://lab-data/run42/ or its recorded file list"),
        ("7", "The FASTQs in s3://lab-data/run42/ must be named"),
        ("8", "s3://lab-data/run42/ changed after the run was started"),
        ("9", "The FASTQs in s3://lab-data/run42/ are named for another sample"),
        ("10", "Couldn't list or copy s3://lab-data/run42/"),
    ],
)
def test_a_folder_runs_stage_failure_names_its_folder(exit_code, words):
    status = st.read_cellranger_status(
        _failed_at("stage-sample", exit_code), FOLDER_RUN
    )
    assert status.current_step == "stage"
    assert status.message.startswith(words)


def test_a_folder_runs_other_steps_fail_as_before():
    status = st.read_cellranger_status(_failed_at("count", "5"), FOLDER_RUN)
    assert status.message.startswith("Cell Ranger failed")


@pytest.mark.parametrize(
    "exit_code, words",
    [
        (
            "4",
            "No FASTQs were found on the shared disk after copying s3://lab-data/run42/",
        ),
        ("6", "Sample tinygex can't be used as a Cell Ranger run id"),
        ("7", "The FASTQs copied from s3://lab-data/run42/ must be named"),
    ],
)
def test_a_folder_runs_count_failure_uses_the_count_messages(exit_code, words):
    status = st.read_cellranger_status(_failed_at("count", exit_code), FOLDER_RUN)
    assert status.current_step == "count"
    assert status.message.startswith(words)
    assert "raw_reads/" not in status.message


def test_a_registered_runs_count_failure_names_raw_reads():
    status = st.read_cellranger_status(_failed_at("count", "4"), RUN)
    assert status.message == f"No FASTQs at raw_reads/{RUN['params']['sample']}/"
