"""Tests for the scRNA Workflows started by hand: argo/scrna/cellranger/cellranger-count-workflow.yaml
(the sample pipeline for a list of samples) and argo/scrna/fastq_qc/fastq-qc-workflow.yaml (QC only,
for one sample). Each passes the steps of the Cell Ranger template exactly the inputs they take."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ARGO = Path(__file__).resolve().parents[2] / "argo/scrna"
TEMPLATE = yaml.safe_load((ARGO / "cellranger/cellranger-count-template.yaml").read_text())
COUNT = yaml.safe_load((ARGO / "cellranger/cellranger-count-workflow.yaml").read_text())
QC = yaml.safe_load((ARGO / "fastq_qc/fastq-qc-workflow.yaml").read_text())


def _template(name):
    return next(t for t in TEMPLATE["spec"]["templates"] if t["name"] == name)


def _inputs(name):
    params = _template(name)["inputs"]["parameters"]
    return {p["name"] for p in params}, {p["name"] for p in params if "value" not in p}


def _tasks(workflow):
    main = next(t for t in workflow["spec"]["templates"] if t["name"] == workflow["spec"]["entrypoint"])
    return {t["name"]: t for t in main["dag"]["tasks"]}


def _passed(task):
    return {p["name"]: p["value"] for p in task["arguments"]["parameters"]}


def test_each_step_gets_its_required_inputs_and_only_inputs_it_takes():
    for workflow in (COUNT, QC):
        for task in _tasks(workflow).values():
            if "templateRef" not in task:
                continue
            assert task["templateRef"]["name"] == TEMPLATE["metadata"]["name"]
            takes, required = _inputs(task["templateRef"]["template"])
            passed = set(_passed(task))
            assert required <= passed <= takes, (task["name"], passed ^ takes)


def test_a_hand_run_sample_gives_its_name_and_either_read_source():
    passed = _passed(_tasks(COUNT)["sample"])
    assert passed["sample"] == passed["run-id"] == passed["dataset-name"] == "{{item.sample}}"
    assert passed["sra-runs"] == "{{item.sra_runs}}"
    assert passed["fastq-url"] == "{{item.fastq_url}}"
    assert passed["fastq-files"] == "{{item.fastq_files}}"
    # The header shows both forms, with all four keys.
    header = (ARGO / "cellranger/cellranger-count-workflow.yaml").read_text().split("apiVersion")[0]
    for key in ("sample", "sra_runs", "fastq_url", "fastq_files"):
        assert header.count(f'"{key}"') == 2, key


def test_qc_only_runs_one_read_source_then_qc():
    tasks = _tasks(QC)
    assert tasks["fetch-sra"]["when"] == "'{{workflow.parameters.sra-runs}}' != ''"
    assert tasks["stage"]["when"] == "'{{workflow.parameters.sra-runs}}' == ''"
    assert tasks["qc"]["depends"] == "fetch-sra && stage"
    run_ids = {_passed(t)["run-id"] for t in tasks.values() if "arguments" in t}
    assert run_ids == {"qc-{{workflow.name}}"}


def test_qc_only_deletes_its_reads_however_it_ends_and_keeps_the_report():
    assert QC["spec"]["onExit"] == "delete-reads"
    assert "delete-reads" not in _tasks(QC), "it runs as the exit handler, not a DAG task"
    step = next(t for t in QC["spec"]["templates"] if t["name"] == "delete-reads")
    command = step["container"]["args"][0]
    assert re.findall(r'"(/shared/runs/[^"]+)"', command) == [
        "/shared/runs/qc-{{workflow.name}}/fastq",
        "/shared/runs/qc-{{workflow.name}}/sra",
    ]
    assert "/qc" not in command.replace("/qc-", "")


def test_qc_only_uses_the_templates_image_and_the_pipelines_login():
    tags = set(re.findall(r"cellranger:(\S+)", (ARGO / "cellranger/cellranger-count-template.yaml").read_text()))
    step = next(t for t in QC["spec"]["templates"] if t["name"] == "delete-reads")
    assert {step["container"]["image"].split("cellranger:")[1]} == tags
    assert [v["name"] for v in QC["spec"]["volumes"]] == ["bloom-credentials"]
