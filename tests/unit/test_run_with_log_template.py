"""Tests that the Cell Ranger template and both step images run every RNA-seq step through
run-with-log (argo/scrna/run_with_log.py), under the run page's step names, with the Bloom
credential mounted read-only."""

from pathlib import Path

import pytest

WRAPPER = Path(__file__).resolve().parents[2] / "argo" / "scrna" / "run_with_log.py"

ARGO = Path(__file__).resolve().parents[2] / "argo" / "scrna"
# The run page's name for each template step (services/workflows/rnaseq_status.py).
STEP_NAMES = {
    "stage-reference": "stage-reference",
    "fetch-sra": "fetch-sra",
    "stage-sample": "stage",
    "qc": "qc",
    "count": "count",
    "preprocess": "preprocess",
    "cluster": "cluster",
    "build-h5ad": "build-h5ad",
    "load-dataset": "load-dataset",
    "cleanup": "cleanup",
}


def _templates():
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load(
        (ARGO / "cellranger" / "cellranger-count-template.yaml").read_text()
    )
    return {t["name"]: t for t in doc["spec"]["templates"]}


@pytest.mark.parametrize("template, step", sorted(STEP_NAMES.items()))
def test_each_step_runs_through_the_wrapper_under_its_run_page_name(template, step):
    command = _templates()[template]["container"]["command"]
    assert command[:4] == [
        "run-with-log",
        "--path",
        f"scrna/{{{{workflow.name}}}}/{step}.log",
        "--",
    ]
    assert len(command) > 4, "the step's own command must follow --"


@pytest.mark.parametrize("template", sorted(STEP_NAMES))
def test_each_step_mounts_the_bloom_credential_read_only(template):
    mounts = _templates()[template]["container"]["volumeMounts"]
    assert {
        "name": "bloom-credentials",
        "mountPath": "/etc/bloom",
        "readOnly": True,
    } in mounts


def test_the_names_match_the_run_pages_steps():
    status = (
        Path(__file__).resolve().parents[2]
        / "services"
        / "workflows"
        / "rnaseq_status.py"
    ).read_text()
    for template, step in STEP_NAMES.items():
        assert f'"{template}": "{step}"' in status


def test_the_smoke_test_step_is_left_alone():
    assert _templates()["testrun"]["container"]["command"] == ["bash", "-c"]


def test_the_hand_submitted_workflow_mounts_prods_pipeline_login():
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load(
        (ARGO / "cellranger" / "cellranger-count-workflow.yaml").read_text()
    )
    assert {
        "name": "bloom-credentials",
        "secret": {
            "secretName": "genericsecret-bloom-prod-pipeline-credentials",
            "optional": True,
        },
    } in doc["spec"]["volumes"]


def test_the_services_volume_has_the_templates_name():
    source = (
        Path(__file__).resolve().parents[2]
        / "services"
        / "workflows"
        / "rnaseq_workflows.py"
    ).read_text()
    assert 'BLOOM_CREDENTIALS_VOLUME = "bloom-credentials"' in source


def test_both_step_images_install_the_wrapper():
    cellranger = (ARGO / "Dockerfile").read_text()
    analysis = (ARGO / "analysis" / "Dockerfile").read_text()
    assert "COPY run_with_log.py /usr/local/bin/run-with-log" in cellranger
    assert (
        "/usr/local/bin/run-with-log"
        in cellranger.split("RUN chmod +x", 1)[1].split("\n", 1)[0]
    )
    assert "COPY --from=scrna run_with_log.py /usr/local/bin/run-with-log" in analysis
    assert (
        "chmod +x /usr/local/bin/scrna-analysis /usr/local/bin/run-with-log" in analysis
    )


def test_the_wrapper_runs_as_a_script():
    assert WRAPPER.read_text().startswith("#!/usr/bin/env python3\n")
