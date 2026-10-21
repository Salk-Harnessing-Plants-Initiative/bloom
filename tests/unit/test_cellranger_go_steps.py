"""Tests for the GO steps in argo/scrna/cellranger's sample pipeline: each runs a go-pathways-template
leaf in the run's .go folder, the annotation builds only when Bloom has none, enrichment only once a
dataset loaded, and cleanup waits for every GO step whatever its outcome, so none fails the run."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[2] / "argo/scrna"
CELLRANGER = yaml.safe_load((ROOT / "cellranger/cellranger-count-template.yaml").read_text())
GO = yaml.safe_load((ROOT / "go-pathways/go-pathways-template.yaml").read_text())
GO_LEAVES = {t["name"]: t for t in GO["spec"]["templates"] if "container" in t}
PIPELINE = next(t for t in CELLRANGER["spec"]["templates"] if t["name"] == "sample-pipeline")
TASKS = {t["name"]: t for t in PIPELINE["dag"]["tasks"]}
GO_TASKS = {n: t for n, t in TASKS.items() if t.get("templateRef", {}).get("name") == "go-pathways-template"}
BUILD = {"fetch-references", "proteins", "eggnog-db", "emapper", "build-annotation", "upload-annotation"}
ENRICH = {"fetch-de", "enrich", "upload-enrichment"}
OUTCOMES = ("Succeeded", "Failed", "Errored", "Skipped", "Omitted")


def _passed(task):
    return {p["name"]: p["value"] for p in task.get("arguments", {}).get("parameters", [])}


def _named(depends):
    words = depends.replace("(", " ").replace(")", " ").split()
    return {w.split(".")[0] for w in words if w not in ("&&", "||")}


def test_the_pipeline_has_every_go_step():
    assert set(GO_TASKS) == {"find-annotation", "upload-annotation-logs"} | BUILD | ENRICH


def test_the_eggnog_steps_logs_are_saved_once_they_end():
    # Their images have no run-with-log; upload-logs copies their logs to run-logs.
    task = GO_TASKS["upload-annotation-logs"]
    assert task["templateRef"]["template"] == "upload-logs"
    assert "when" not in task, "it runs even when the annotation was reused or failed"
    assert _named(task["depends"]) == {"proteins", "eggnog-db", "emapper"}
    for name in ("proteins", "eggnog-db", "emapper"):
        outcomes = " || ".join(f"{name}.{o}" for o in OUTCOMES)
        assert f"({outcomes})" in task["depends"], name
    assert _passed(task)["run-id"] == "{{inputs.parameters.run-id}}.go"


@pytest.mark.parametrize("name", sorted({"find-annotation"} | BUILD | ENRICH))
def test_each_go_task_runs_the_template_leaf_of_its_name(name):
    assert GO_TASKS[name]["templateRef"]["template"] == name
    assert name in GO_LEAVES


@pytest.mark.parametrize("name", sorted({"find-annotation"} | BUILD | ENRICH))
def test_each_go_task_gets_its_leafs_required_inputs_and_only_inputs_it_takes(name):
    params = GO_LEAVES[name].get("inputs", {}).get("parameters", [])
    takes = {p["name"] for p in params}
    required = {p["name"] for p in params if "value" not in p}
    assert required <= set(_passed(GO_TASKS[name])) <= takes


@pytest.mark.parametrize("name", sorted({"find-annotation"} | BUILD | ENRICH))
def test_go_steps_work_beside_the_run_folder_which_fetch_sra_and_stage_clear(name):
    assert _passed(GO_TASKS[name])["run-id"] == "{{inputs.parameters.run-id}}.go"


def test_the_annotation_steps_need_only_the_genome_not_the_reads():
    upstream = set()
    for name in BUILD | {"find-annotation"}:
        upstream |= _named(GO_TASKS[name].get("depends", ""))
    assert upstream - BUILD - {"find-annotation"} == {"download-genome"}


@pytest.mark.parametrize("name", sorted(BUILD))
def test_an_annotation_is_built_only_when_bloom_has_none(name):
    assert GO_TASKS[name]["when"] == "'{{tasks.find-annotation.outputs.parameters.decision}}' == 'build'"


@pytest.mark.parametrize("name", sorted(ENRICH))
def test_enrichment_runs_only_once_a_dataset_loaded(name):
    assert GO_TASKS[name]["when"] == "'{{tasks.load-dataset.outputs.parameters.dataset-id}}' != ''"


def test_every_when_quotes_its_value():
    # Argo's expression parser reads an unquoted empty value as a syntax error.
    for task in TASKS.values():
        if "when" in task:
            assert re.fullmatch(r"'[^']*' (==|!=) '[^']*'", task["when"]), task["name"]


def test_enrich_uses_a_found_or_a_just_built_annotation():
    assert _named(GO_TASKS["enrich"]["depends"]) == {"fetch-de", "find-annotation", "upload-annotation"}
    assert "upload-annotation.Skipped" in GO_TASKS["enrich"]["depends"]


def test_the_enrichment_is_recorded_with_the_cell_ranger_run_as_its_job():
    assert _passed(GO_TASKS["upload-enrichment"])["rnaseq-run-id"] == "{{inputs.parameters.bloom-run-id}}"


def test_the_species_kegg_code_reaches_the_reference_download():
    assert _passed(GO_TASKS["fetch-references"])["kegg-org"] == "{{inputs.parameters.kegg-org}}"
    inputs = {p["name"]: p for p in PIPELINE["inputs"]["parameters"]}
    assert inputs["kegg-org"]["value"] == ""


def test_cleanup_waits_for_every_go_step_whatever_its_outcome():
    depends = TASKS["cleanup"]["depends"]
    assert _named(depends) == {"load-dataset"} | set(GO_TASKS)
    for name in GO_TASKS:
        outcomes = " || ".join(f"{name}.{o}" for o in OUTCOMES)
        assert f"({outcomes})" in depends, name


def test_a_failed_go_step_doesnt_stop_the_rest_of_the_pipeline_starting():
    # Argo's default stops scheduling every new task once any task fails.
    assert PIPELINE["dag"]["failFast"] is False


def test_no_task_uses_continue_on_which_argo_refuses_with_depends():
    assert not any("continueOn" in t for t in TASKS.values())


def test_cleanup_deletes_the_go_folder_too():
    cleanup = next(t for t in CELLRANGER["spec"]["templates"] if t["name"] == "cleanup")
    assert ".go" in "\n".join(cleanup["container"].get("args", []) + cleanup["container"].get("command", []))
