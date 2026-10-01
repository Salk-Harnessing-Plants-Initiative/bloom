"""Unit tests for the RNA-seq workflow types: the Cell Ranger workflow name and body,
and checks that they still match the repo's Argo files and database functions."""

import json
import re
from pathlib import Path

import pytest
import yaml

import k8s_client
import rnaseq_workflows as wfs
from k8s_client import K8sConfigError

REPO_ROOT = Path(__file__).resolve().parents[3]
ARGO_DIR = REPO_ROOT / "argo" / "scrna" / "cellranger"
MIGRATIONS = REPO_ROOT / "supabase" / "migrations"

RUN = {
    "run_id": 7,
    "workflow_type": "scrna-cellranger",
    "params": {"sample": "tinygex", "reference": "tiny_ref"},
    "run_key": "tinygex__tiny_ref__00000000-0000-0000-0000-000000000001",
    "msg_id": 12,
}


@pytest.fixture(autouse=True)
def _k8s_config(monkeypatch):
    monkeypatch.setattr(k8s_client, "NAMESPACE", "runai-busch-lab")
    monkeypatch.setattr(k8s_client, "ENV_LABEL", "staging")


def _yaml(name: str) -> dict:
    return yaml.safe_load((ARGO_DIR / name).read_text(encoding="utf-8"))


def _template(name: str) -> dict:
    templates = _yaml("cellranger-count-template.yaml")["spec"]["templates"]
    return next(t for t in templates if t["name"] == name)


def _required_inputs(template: dict) -> set[str]:
    return {p["name"] for p in template["inputs"]["parameters"] if "value" not in p}


def _task(body: dict, name: str) -> dict:
    tasks = body["spec"]["templates"][0]["dag"]["tasks"]
    return next(t for t in tasks if t["name"] == name)


def _params(task: dict) -> dict:
    return {p["name"]: p["value"] for p in task["arguments"]["parameters"]}


# --------------------------------------------------------------------------- #
# Workflow name
# --------------------------------------------------------------------------- #


def test_the_name_is_fixed_per_run():
    assert wfs.cellranger_workflow_name(RUN) == wfs.cellranger_workflow_name(dict(RUN))
    assert re.fullmatch(
        r"scrna-cellranger-staging-7-[0-9a-f]{8}", wfs.cellranger_workflow_name(RUN)
    )


def test_a_reused_run_id_with_another_run_key_gets_another_name():
    other = {**RUN, "run_key": "root_a__tair10__00000000-0000-0000-0000-000000000002"}
    assert wfs.cellranger_workflow_name(other) != wfs.cellranger_workflow_name(RUN)


@pytest.mark.parametrize("env", ["", "Staging", "stag ing", "-dev", "dev_1"])
def test_an_env_label_that_cannot_be_part_of_a_name_is_a_config_error(monkeypatch, env):
    monkeypatch.setattr(k8s_client, "ENV_LABEL", env)
    with pytest.raises(K8sConfigError):
        wfs.build_cellranger_body(RUN)


# --------------------------------------------------------------------------- #
# Body
# --------------------------------------------------------------------------- #


def test_the_body_names_and_places_the_workflow():
    meta = wfs.build_cellranger_body(RUN)["metadata"]
    assert meta["name"] == wfs.cellranger_workflow_name(RUN)
    assert "generateName" not in meta
    assert meta["namespace"] == "runai-busch-lab"
    assert meta["annotations"] == {"bloom.salk.edu/run-key": RUN["run_key"]}


def test_the_labels_identify_the_run_and_environment():
    labels = wfs.build_cellranger_body(RUN)["metadata"]["labels"]
    assert labels == {
        "project": "busch-lab",
        "submitted-by": "bloom-pipeline",
        "workflow-type": "scrna-cellranger",
        "rnaseq-run-id": "7",
        "environment": "staging",
    }
    for value in labels.values():
        assert re.fullmatch(r"[A-Za-z0-9]([A-Za-z0-9._-]{0,61}[A-Za-z0-9])?", value)


def test_the_run_is_passed_as_workflow_parameters():
    spec = wfs.build_cellranger_body(RUN)["spec"]
    assert {p["name"]: p["value"] for p in spec["arguments"]["parameters"]} == {
        "sample": "tinygex",
        "reference": "tiny_ref",
        "run-id": RUN["run_key"],
    }


def test_the_run_key_is_the_run_id_so_output_goes_to_its_folder():
    body = wfs.build_cellranger_body(RUN)
    assert _params(_task(body, "sample"))["run-id"] == "{{workflow.parameters.run-id}}"


def test_finished_workflows_are_kept_for_24_hours_by_default():
    assert wfs.DEFAULT_TTL_SECONDS == 24 * 60 * 60
    spec = wfs.build_cellranger_body(RUN)["spec"]
    assert spec["ttlStrategy"] == {"secondsAfterCompletion": wfs.TTL_SECONDS}


def test_the_ttl_is_not_the_sleap_roots_setting(monkeypatch):
    monkeypatch.setattr(k8s_client, "TTL_SECONDS", 3600)
    monkeypatch.setattr(wfs, "TTL_SECONDS", 86400)
    spec = wfs.build_cellranger_body(RUN)["spec"]
    assert spec["ttlStrategy"] == {"secondsAfterCompletion": 86400}


@pytest.mark.parametrize(
    "raw, expected",
    [
        (None, 86400),
        ("7200", 7200),
        ("", 86400),
        ("abc", 86400),
        ("0", 86400),
        ("-5", 86400),
    ],
)
def test_the_ttl_setting_falls_back_to_24_hours(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("WORKFLOWS_RNASEQ_TTL_SECONDS", raising=False)
    else:
        monkeypatch.setenv("WORKFLOWS_RNASEQ_TTL_SECONDS", raw)
    assert wfs._resolve_ttl_seconds() == expected


def test_the_sample_runs_after_the_reference_is_staged():
    body = wfs.build_cellranger_body(RUN)
    assert _task(body, "sample")["depends"] == "stage-reference"


# --------------------------------------------------------------------------- #
# Matches the repo's Argo files
# --------------------------------------------------------------------------- #


def test_the_body_uses_the_same_account_and_pull_secret_as_the_repo_workflow():
    repo = _yaml("cellranger-count-workflow.yaml")["spec"]
    spec = wfs.build_cellranger_body(RUN)["spec"]
    assert spec["serviceAccountName"] == repo["serviceAccountName"]
    assert spec["imagePullSecrets"] == repo["imagePullSecrets"]
    assert spec["entrypoint"] == repo["entrypoint"]


def test_the_body_runs_the_same_steps_as_the_repo_workflow():
    repo_tasks = _yaml("cellranger-count-workflow.yaml")["spec"]["templates"][0]["dag"][
        "tasks"
    ]
    body_tasks = wfs.build_cellranger_body(RUN)["spec"]["templates"][0]["dag"]["tasks"]
    assert [(t["name"], t["templateRef"], t.get("depends")) for t in body_tasks] == [
        (t["name"], t["templateRef"], t.get("depends")) for t in repo_tasks
    ]


def test_the_template_name_is_the_registered_template():
    meta = _yaml("cellranger-count-template.yaml")["metadata"]
    assert wfs.CELLRANGER_TEMPLATE == meta["name"]


@pytest.mark.parametrize("step", ["stage-reference", "sample"])
def test_each_step_passes_exactly_the_templates_required_inputs(step):
    task = _task(wfs.build_cellranger_body(RUN), step)
    template = _template(task["templateRef"]["template"])
    assert set(_params(task)) == _required_inputs(template)


# --------------------------------------------------------------------------- #
# Matches the database functions
# --------------------------------------------------------------------------- #


def test_the_shared_dispatch_functions_exist_in_a_migration():
    sql = "\n".join(p.read_text() for p in sorted(MIGRATIONS.glob("*.sql")))
    for fn in (wfs.CLAIM_FN, wfs.COMPLETE_FN, wfs.FAIL_FN):
        assert re.search(rf"CREATE OR REPLACE FUNCTION public\.{fn}\(", sql), fn


def test_every_registered_type_is_allowed_by_the_runs_table():
    sql = "\n".join(p.read_text() for p in sorted(MIGRATIONS.glob("*.sql")))
    checks = re.findall(
        r"rnaseq_runs_workflow_type_check\s+CHECK \(workflow_type IN \(([^)]*)\)\)",
        sql,
    )
    assert checks, "no rnaseq_runs_workflow_type_check in the migrations"
    allowed = set(re.findall(r"'([^']+)'", checks[-1]))
    assert set(wfs.WORKFLOW_TYPES) <= allowed


def test_cellranger_is_the_one_registered_type():
    assert wfs.WORKFLOW_TYPES == {"scrna-cellranger": wfs.CELLRANGER}


# --------------------------------------------------------------------------- #
# An S3 folder run
# --------------------------------------------------------------------------- #

FOLDER_FILES = [
    {"name": "col0_S1_L001_R1_001.fastq.gz", "size": 10, "etag": '"a"'},
    {"name": "col0_S1_L001_R2_001.fastq.gz", "size": 32, "etag": '"b"'},
]
FOLDER_RUN = {
    **RUN,
    "params": {
        "sample": "col0",
        "reference": "tiny_ref",
        "fastq_url": "s3://lab-data/run42/",
        "fastq_files": FOLDER_FILES,
    },
    "run_key": "col0__tiny_ref__00000000-0000-0000-0000-000000000001",
}


def test_a_folder_run_passes_its_folder_and_files():
    body = wfs.build_cellranger_body(FOLDER_RUN)
    params = {p["name"]: p["value"] for p in body["spec"]["arguments"]["parameters"]}
    assert params["fastq-url"] == "s3://lab-data/run42/"
    assert json.loads(params["fastq-files"]) == FOLDER_FILES
    sample = _params(_task(body, "sample"))
    assert sample["fastq-url"] == "{{workflow.parameters.fastq-url}}"
    assert sample["fastq-files"] == "{{workflow.parameters.fastq-files}}"


def test_a_folder_run_passes_only_inputs_the_template_declares():
    template = _template("sample-pipeline")
    declared = {p["name"] for p in template["inputs"]["parameters"]}
    sample = _params(_task(wfs.build_cellranger_body(FOLDER_RUN), "sample"))
    assert set(sample) <= declared
    assert _required_inputs(template) <= set(sample)


def test_a_run_without_a_folder_passes_no_folder():
    body = wfs.build_cellranger_body(RUN)
    names = {p["name"] for p in body["spec"]["arguments"]["parameters"]}
    assert not names & {"fastq-url", "fastq-files"}
