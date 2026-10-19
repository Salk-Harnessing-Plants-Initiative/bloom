"""Tests for argo/scrna/go-pathways: the GO and pathway template and its two hand-run Workflows.
Each DAG task passes its step exactly the inputs it takes, every step on the scrna-analysis image
logs through run-with-log with the Bloom credential mounted read-only, and eggNOG's steps run
only for the eggnog source."""

from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

FOLDER = Path(__file__).resolve().parents[2] / "argo/scrna/go-pathways"
TEMPLATE = yaml.safe_load((FOLDER / "go-pathways-template.yaml").read_text())
WORKFLOWS = {
    name: yaml.safe_load((FOLDER / f"{name}-workflow.yaml").read_text())
    for name in ("genome-annotation", "scrna-enrichment")
}
STEPS = {t["name"]: t for t in TEMPLATE["spec"]["templates"]}
DAGS = ("genome-annotation", "scrna-enrichment")
ANALYSIS_IMAGE = "ghcr.io/salk-harnessing-plants-initiative/scrna-analysis:"
# The step that uploads logs with run-with-log --put instead of running under it.
UPLOADERS = {"upload-logs"}
EGGNOG_ONLY = {"proteins", "eggnog-db", "emapper"}


def _inputs(template):
    params = template.get("inputs", {}).get("parameters", [])
    return {p["name"] for p in params}, {p["name"] for p in params if "value" not in p}


def _passed(task):
    return {p["name"] for p in task.get("arguments", {}).get("parameters", [])}


def _leaves():
    return {name: t for name, t in STEPS.items() if "container" in t}


def test_the_template_has_both_entry_points():
    assert TEMPLATE["metadata"]["name"] == "go-pathways-template"
    assert set(DAGS) <= set(STEPS)


@pytest.mark.parametrize("dag", DAGS)
def test_each_task_gets_its_steps_required_inputs_and_only_inputs_it_takes(dag):
    for task in STEPS[dag]["dag"]["tasks"]:
        takes, required = _inputs(STEPS[task["template"]])
        assert required <= _passed(task) <= takes, (task["name"], _passed(task) ^ takes)


@pytest.mark.parametrize("dag", DAGS)
def test_each_task_depends_only_on_tasks_of_its_dag(dag):
    names = {t["name"] for t in STEPS[dag]["dag"]["tasks"]}
    for task in STEPS[dag]["dag"]["tasks"]:
        words = task.get("depends", "").replace("(", " ").replace(")", " ").split()
        named = {w.split(".")[0] for w in words if w not in ("&&", "||")}
        assert named <= names, (task["name"], named - names)


def test_eggnogs_steps_run_only_for_the_eggnog_source():
    tasks = {t["name"]: t for t in STEPS["genome-annotation"]["dag"]["tasks"]}
    for name in EGGNOG_ONLY:
        assert tasks[name]["when"] == "'{{inputs.parameters.source}}' == 'eggnog'"
    assert "emapper.Skipped" in tasks["build-annotation"]["depends"], "tair-go skips eggNOG"


@pytest.mark.parametrize("name", sorted(set(_leaves()) - EGGNOG_ONLY - UPLOADERS))
def test_each_analysis_step_logs_through_run_with_log(name):
    command = _leaves()[name]["container"]["command"]
    assert command[:4] == ["run-with-log", "--path", f"scrna/{{{{workflow.name}}}}/{name}.log", "--"]
    assert len(command) > 4


@pytest.mark.parametrize("name", sorted(EGGNOG_ONLY))
def test_steps_without_the_wrapper_keep_their_log_for_upload_logs(name):
    script = _leaves()[name]["container"]["args"][0]
    assert f"logs/{name}.log" in script


@pytest.mark.parametrize("name", sorted(set(_leaves()) - EGGNOG_ONLY))
def test_each_step_on_the_analysis_image_mounts_the_bloom_credential_read_only(name):
    container = _leaves()[name]["container"]
    assert container["image"].startswith(ANALYSIS_IMAGE)
    assert {"name": "bloom-credentials", "mountPath": "/etc/bloom", "readOnly": True} \
        in container["volumeMounts"]


def test_every_step_uses_one_analysis_image_tag():
    tags = {t["container"]["image"] for t in _leaves().values()
            if t["container"]["image"].startswith(ANALYSIS_IMAGE)}
    assert len(tags) == 1, tags


@pytest.mark.parametrize("name", ["download-genome", "fetch-references", "fetch-de",
                                  "fetch-annotation"])
def test_only_a_network_error_is_retried(name):
    assert STEPS[name]["retryStrategy"]["expression"] == "asInt(lastRetry.exitCode) == 1"


@pytest.mark.parametrize("workflow", sorted(WORKFLOWS))
def test_each_hand_run_workflow_passes_its_entry_point_what_it_needs(workflow):
    doc = WORKFLOWS[workflow]
    main = next(t for t in doc["spec"]["templates"] if t["name"] == doc["spec"]["entrypoint"])
    (task,) = main["dag"]["tasks"]
    assert task["templateRef"] == {"name": "go-pathways-template", "template": workflow}
    takes, required = _inputs(STEPS[workflow])
    assert required <= _passed(task) <= takes
    secret = doc["spec"]["volumes"][0]
    assert secret["name"] == "bloom-credentials"
    assert secret["secret"]["secretName"].startswith("genericsecret-bloom-")


def test_the_annotation_run_uploads_eggnogs_logs_however_it_ends():
    doc = WORKFLOWS["genome-annotation"]
    handler = next(t for t in doc["spec"]["templates"] if t["name"] == doc["spec"]["onExit"])
    (task,) = handler["dag"]["tasks"]
    assert task["templateRef"] == {"name": "go-pathways-template", "template": "upload-logs"}


@pytest.mark.parametrize("dag, last", [("genome-annotation", "upload-logs"),
                                       ("scrna-enrichment", "upload-enrichment")])
def test_each_run_records_its_result_in_bloom_before_its_files_are_deleted(dag, last):
    tasks = {t["name"]: t for t in STEPS[dag]["dag"]["tasks"]}
    assert tasks["cleanup"]["depends"] == last


def test_the_annotation_runs_logs_are_uploaded_before_cleanup_deletes_them():
    tasks = {t["name"]: t for t in STEPS["genome-annotation"]["dag"]["tasks"]}
    assert tasks["upload-logs"]["depends"] == "upload-annotation"


def test_uploads_that_start_a_record_in_bloom_are_not_retried():
    # A retry would find its own uploading record and be refused; the step abandons it instead.
    for name in ("upload-annotation", "upload-enrichment"):
        assert "retryStrategy" not in STEPS[name]


def test_every_condition_quotes_its_values():
    # Argo reads a bare word in a `when` as a value or an operator: unquoted, tair-go would be
    # read as tair minus go, and the condition would error instead of skipping the step.
    import re

    for dag in DAGS:
        for task in STEPS[dag]["dag"]["tasks"]:
            if "when" in task:
                sides = [side.strip() for side in re.split(r"==|!=", task["when"])]
                assert all(side.startswith("'") and side.endswith("'") for side in sides), task


def _eggnog_db_script():
    return STEPS["eggnog-db"]["container"]["args"][0]


def test_the_eggnog_database_is_downloaded_apart_and_renamed_into_place():
    script = _eggnog_db_script()
    assert 'work="${root}/.partial-${EGGNOG_DB}-${WORKFLOW_NAME}"' in script
    assert script.index("touch .verified") < script.index('mv -- "${work}" "${db}"'), \
        "marked verified in its own folder, before it's moved into place"
    assert "{{workflow.name}}" in str(STEPS["eggnog-db"]["container"]["env"])


def test_each_download_step_stops_the_script_when_it_fails():
    # A command joined with && doesn't stop set -e when it fails, so each one has its own line.
    lines = [line.strip() for line in _eggnog_db_script().splitlines()]
    for command in ("wget", "gzip -t", "gunzip", "tar -zxf"):
        steps = [line for line in lines if line.startswith(command)]
        assert steps and all("&&" not in line for line in steps), command


def test_every_archive_is_checked_before_it_is_unpacked():
    script = _eggnog_db_script()
    for archive in ("eggnog.db.gz", "eggnog_proteins.dmnd.gz", "eggnog.taxa.tar.gz"):
        assert script.index(f"gzip -t {archive}") < script.index("gunzip eggnog.db.gz"), archive


def test_a_stopped_download_removes_its_own_partial_copy():
    assert """trap 'rm -rf -- "${work}"' EXIT""" in _eggnog_db_script()
