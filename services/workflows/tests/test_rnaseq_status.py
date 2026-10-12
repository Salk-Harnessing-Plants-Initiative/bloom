"""Unit tests for reading a Cell Ranger run's status from its Argo Workflow. The
fixtures are real Workflows from runai-busch-lab, trimmed to the fields read, recorded
before references came from Bloom: their reference step, once stage-reference, is renamed
download-genome, and they have no build-reference. The status reader goes by each pod's
template, not where it sits in the DAG. `_genome_workflow` builds the current shape by hand."""

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


def test_a_just_started_workflow_is_running_at_download_genome():
    status = st.read_cellranger_status(_load("started"), RUN)
    assert status == st.RunStatus(
        "running",
        "download-genome",
        {"download-genome": f"{WF}-download-genome-3880791564"},
    )


def test_a_counting_workflow_is_running_at_count_with_each_started_pod():
    status = st.read_cellranger_status(_load("counting"), RUN)
    assert (status.status, status.current_step) == ("running", "count")
    assert status.step_pods == {
        "download-genome": f"{WF}-download-genome-3880791564",
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
        "download-genome",
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


def test_a_genome_that_isnt_available_fails_at_download_genome_with_exit_3():
    status = st.read_cellranger_status(_load("no_reference"), NO_REF_RUN)
    assert status == st.RunStatus(
        "failed",
        "download-genome",
        {
            "download-genome": "scrna-cellranger-dev-900002-28b84579-download-genome-2584972205"
        },
        3,
        "Genome no_such_ref isn't available in Bloom, or a downloaded file doesn't match "
        "Bloom's record; the download-genome step's log says which",
    )


def test_every_reported_step_is_one_the_table_allows():
    allowed = {"download-genome", "build-reference", "stage", "qc", "count", "cleanup"}
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
            "3",
            "No Cell Ranger reference for tiny_ref was found to count against",
        ),
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


# --------------------------------------------------------------------------- #
# The genome download and reference build
# --------------------------------------------------------------------------- #

GENOME_RUN = {
    "params": {"sample": "col0", "reference": "athaliana_col0.v2"},
    "run_key": "col0__athaliana_col0.v2__u",
}


def _failed_at_new_step(template: str, exit_code: str) -> dict:
    """A counting Workflow with one more pod, of `template`, that failed last."""
    wf = _load("counting")
    wf["status"]["phase"] = "Failed"
    pod = copy.deepcopy(_pod(wf, "count"))
    pod.pop("templateRef", None)
    pod["templateName"] = template
    pod["id"] = f"{WF}-2222222222"
    pod["phase"] = "Failed"
    pod["startedAt"] = pod["finishedAt"] = "2099-01-01T00:00:00Z"
    pod["outputs"] = {"exitCode": exit_code}
    wf["status"]["nodes"][pod["id"]] = pod
    return wf


@pytest.mark.parametrize(
    "template, exit_code, message",
    [
        (
            "download-genome",
            "3",
            "Genome athaliana_col0.v2 isn't available in Bloom, or a downloaded file doesn't "
            "match Bloom's record; the download-genome step's log says which",
        ),
        (
            "download-genome",
            "1",
            "Couldn't download genome athaliana_col0.v2 from Bloom; start the run again, and ask "
            "the Bloom admins if it fails again",
        ),
        (
            "download-genome",
            "16",
            "The pipeline has no Bloom credentials, so the genome couldn't be downloaded; ask "
            "the Bloom admins",
        ),
        (
            "download-genome",
            "6",
            "The run's genome athaliana_col0.v2 or its folder couldn't be used; this isn't "
            "something wrong with the genome, so ask the Bloom admins",
        ),
        ("download-genome", "2", "Step download-genome failed (exit 2)"),
        ("build-reference", "4", "Step build-reference failed (exit 4)"),
        (
            "build-reference",
            "17",
            "Cell Ranger couldn't build a reference from genome athaliana_col0.v2; the "
            "build-reference step's log has Cell Ranger's error",
        ),
        (
            "build-reference",
            "3",
            "The files of genome athaliana_col0.v2 weren't found on the shared disk to build "
            "from; start the run again, and ask the Bloom admins if it fails again",
        ),
        (
            "build-reference",
            "6",
            "The run's genome athaliana_col0.v2 or its folder couldn't be used; this isn't "
            "something wrong with the genome, so ask the Bloom admins",
        ),
        ("build-reference", "137", "Step build-reference failed (exit 137)"),
    ],
)
def test_a_failed_genome_step_says_why(template, exit_code, message):
    status = st.read_cellranger_status(
        _failed_at_new_step(template, exit_code), GENOME_RUN
    )
    assert (status.status, status.current_step, status.exit_code, status.message) == (
        "failed",
        template,
        int(exit_code),
        message,
    )
    assert status.step_pods[template] == f"{WF}-{template}-2222222222"


def test_the_genome_steps_come_after_the_reads_and_just_before_count():
    steps = list(st.CELLRANGER_STEPS.values())
    assert steps.index("qc") < steps.index("download-genome")
    assert steps.index("build-reference") + 1 == steps.index("count")


@pytest.mark.parametrize("reverse", [False, True], ids=["in-order", "reversed"])
def test_steps_running_side_by_side_report_the_later_one_whatever_the_node_order(
    reverse,
):
    wf = _load("counting")
    count = _pod(wf, "count")
    nodes = []
    for template, n in (("qc", "1"), ("build-reference", "2")):
        node = copy.deepcopy(count)
        node.pop("templateRef", None)
        node["templateName"] = template
        node["id"] = f"{WF}-900000000{n}"
        node["phase"] = "Running"
        node["startedAt"] = "2099-01-01T00:00:00Z"
        nodes.append(node)
    ordered = list(reversed(nodes)) if reverse else nodes
    wf["status"]["nodes"] = {n["id"]: n for n in ordered}
    wf["status"]["phase"] = "Running"
    assert st.read_cellranger_status(wf, RUN).current_step == "build-reference"


def test_a_retired_step_is_no_longer_a_template_step():
    assert not set(st.RETIRED_CELLRANGER_STEPS) & set(st.CELLRANGER_STEPS.values())


def _genome_workflow(phases: dict[str, str]) -> dict:
    """A hand-built Workflow of today's shape: every step a Pod under the sample pipeline."""
    nodes = {}
    for i, (template, phase) in enumerate(phases.items()):
        node_id = f"{WF}-80000000{i}"
        nodes[node_id] = {
            "id": node_id,
            "name": f"{WF}.sample.{template}",
            "type": "Pod",
            "templateName": template,
            "boundaryID": f"{WF}-700000000",
            "phase": phase,
            "startedAt": f"2099-01-01T00:0{i}:00Z",
            "finishedAt": None if phase == "Running" else f"2099-01-01T00:0{i}:30Z",
        }
    return {"metadata": {"name": WF}, "status": {"phase": "Running", "nodes": nodes}}


def test_a_run_building_its_reference_after_its_reads_reports_the_build():
    wf = _genome_workflow(
        {
            "download-genome": "Succeeded",
            "stage-sample": "Succeeded",
            "qc": "Succeeded",
            "build-reference": "Running",
        }
    )
    status = st.read_cellranger_status(wf, GENOME_RUN)
    assert (status.status, status.current_step) == ("running", "build-reference")
    assert set(status.step_pods) == {
        "download-genome",
        "stage",
        "qc",
        "build-reference",
    }
    assert status.step_pods["build-reference"] == f"{WF}-build-reference-800000003"
