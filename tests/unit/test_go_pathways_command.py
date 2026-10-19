"""Tests for argo/scrna/analysis: the go-pathways command the GO and pathway workflow steps run
(fetch-de's shaping of what it reads from Bloom, build-annotation, enrich). No scanpy needed."""

from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "argo/scrna/analysis"))

from bloom_scrna_analysis import go_pathways  # noqa: E402
from bloom_scrna_analysis.steps import (  # noqa: E402
    EXIT_BAD_ANNOTATION,
    EXIT_BAD_ENRICHMENT_INPUT,
    StepError,
)
from tests.unit.test_go_pathway_annotation import GTF, KEGG_LIST, OBO, emapper  # noqa: E402

# --------------------------------------------------------------------------- #
# fetch-de
# --------------------------------------------------------------------------- #

RUNS = [
    {"id": 3, "status": "complete", "completed_at": "2026-10-01T00:00:00+00:00"},
    {"id": 5, "status": "complete", "completed_at": "2026-10-09T00:00:00+00:00"},
    {"id": 6, "status": "running", "completed_at": None},
]


def test_the_newest_complete_analysis_is_used_by_default():
    assert go_pathways.pick_de_run(RUNS, None) == 5


def test_a_named_analysis_must_be_complete():
    assert go_pathways.pick_de_run(RUNS, 3) == 3
    with pytest.raises(StepError, match="analysis 6 is running"):
        go_pathways.pick_de_run(RUNS, 6)
    with pytest.raises(StepError, match="no analysis 9"):
        go_pathways.pick_de_run(RUNS, 9)


def test_a_dataset_without_a_complete_analysis_is_refused():
    with pytest.raises(StepError, match="no complete") as exc:
        go_pathways.pick_de_run(RUNS[2:], None)
    assert exc.value.code == EXIT_BAD_ENRICHMENT_INPUT


def test_de_rows_become_comparisons_by_contrast_with_gene_names():
    genes = [{"id": 10, "gene_name": "G1"}, {"id": 11, "gene_name": "G2"}]
    comparisons = [{"id": 1, "cluster_id": "0", "contrast": "0_vs_rest"},
                   {"id": 2, "cluster_id": "1", "contrast": None}]
    rows = [{"de_id": 1, "gene_id": 10, "log2fc": 1.5, "fdr": 0.01},
            {"de_id": 2, "gene_id": 11, "log2fc": -2.0, "fdr": 0.001}]
    doc = go_pathways.de_document(dataset_id=25, de_run_id=5, genes=genes,
                                  comparisons=comparisons, rows=rows)
    assert doc == {
        "dataset_id": 25, "de_run_id": 5, "genes": ["G1", "G2"],
        "comparisons": [
            {"comparison": "0_vs_rest", "genes": [{"gene": "G1", "log2fc": 1.5, "fdr": 0.01}]},
            {"comparison": "1", "genes": [{"gene": "G2", "log2fc": -2.0, "fdr": 0.001}]},
        ],
    }


# --------------------------------------------------------------------------- #
# build-annotation and enrich, from files
# --------------------------------------------------------------------------- #


@pytest.fixture
def inputs(tmp_path):
    (tmp_path / "go-basic.obo").write_text(OBO)
    (tmp_path / "kegg.tsv").write_text(KEGG_LIST)
    (tmp_path / "genes.gtf").write_text(GTF)
    (tmp_path / "emapper.tsv").write_text(emapper(
        ("G1.t1", "Light response protein", "LRP1", "GO:0009416", "ko00010,map00010"),
    ))
    return tmp_path


def build(inputs, *extra):
    out = inputs / "annotation.json.gz"
    code = go_pathways.main([
        "build-annotation", "--source", "eggnog", "--emapper", str(inputs / "emapper.tsv"),
        "--gtf", str(inputs / "genes.gtf"), "--obo", str(inputs / "go-basic.obo"),
        "--kegg-list", str(inputs / "kegg.tsv"), "--genome", "sbicolor.btx623.hpiv3",
        "--version", "1", "--tool", "eggnog_mapper=2.1.13", "--tool", "eggnog_db=5.0.2",
        "--out", str(out), *extra,
    ])
    return code, out


def test_build_annotation_writes_the_file_with_the_go_release(inputs):
    code, out = build(inputs)
    assert code == 0
    with gzip.open(out, "rt") as fh:
        doc = json.load(fh)
    assert doc["source"] == "eggnog"
    assert doc["tools"] == {"eggnog_mapper": "2.1.13", "eggnog_db": "5.0.2",
                            "go_basic": "releases/2026-09-01"}
    assert "map00010" in doc["genes"]["G1"]["terms"]


def test_build_annotation_exits_with_the_steps_code_on_bad_input(inputs, capsys):
    (inputs / "emapper.tsv").write_text("not eggnog output\n")
    code, _ = build(inputs)
    assert code == EXIT_BAD_ANNOTATION
    assert "ERROR:" in capsys.readouterr().err


def test_a_tool_version_must_be_name_equals_version(inputs):
    with pytest.raises(SystemExit):
        build(inputs, "--tool", "eggnog_mapper")


def test_enrich_reads_the_annotation_and_de_and_writes_the_run_file(inputs):
    _, annotation_path = build(inputs)
    de = inputs / "de.json"
    de.write_text(json.dumps({"dataset_id": 25, "de_run_id": 5, "genes": ["G1", "G2"],
                              "comparisons": []}))
    out = inputs / "run.json.gz"
    code = go_pathways.main([
        "enrich", "--annotation", str(annotation_path), "--de", str(de),
        "--label", "sbicolor.btx623.hpiv3.v1/eggnog-b1", "--out", str(out),
    ])
    assert code == 0
    with gzip.open(out, "rt") as fh:
        doc = json.load(fh)
    assert (doc["dataset_id"], doc["de_run_id"], doc["background_genes"],
            doc["annotated_genes"]) == (25, 5, 2, 1)
    assert doc["params"]["top_n"] == 300 and doc["results"] == []


def test_enrich_takes_its_settings_from_flags(inputs):
    _, annotation_path = build(inputs)
    de = inputs / "de.json"
    de.write_text(json.dumps({"dataset_id": 1, "de_run_id": 1, "genes": ["G1"],
                              "comparisons": []}))
    out = inputs / "run.json.gz"
    go_pathways.main(["enrich", "--annotation", str(annotation_path), "--de", str(de),
                      "--label", "x", "--out", str(out), "--top-n", "200", "--max-term", "800"])
    with gzip.open(out, "rt") as fh:
        params = json.load(fh)["params"]
    assert (params["top_n"], params["max_term"]) == (200, 800)


# --------------------------------------------------------------------------- #
# fetch-references
# --------------------------------------------------------------------------- #


def test_references_are_the_go_ontology_plus_each_sources_own_files():
    assert go_pathways.reference_urls("eggnog", "sbi") == {
        "go-basic.obo": "https://current.geneontology.org/ontology/go-basic.obo",
        "kegg-pathways.tsv": "https://rest.kegg.jp/list/pathway/sbi",
    }
    assert go_pathways.reference_urls("tair-go", "") == {
        "go-basic.obo": "https://current.geneontology.org/ontology/go-basic.obo",
        "tair.gaf.gz": "https://current.geneontology.org/annotations/tair.gaf.gz",
    }


def test_without_a_kegg_organism_eggnog_has_go_only():
    assert set(go_pathways.reference_urls("eggnog", "")) == {"go-basic.obo"}


@pytest.mark.parametrize("org", ["SBI", "sb1", "a/b", "sbix1"])
def test_a_kegg_organism_must_be_kegg_s_three_or_four_letters(org):
    with pytest.raises(StepError, match="KEGG organism"):
        go_pathways.reference_urls("eggnog", org)


def test_references_are_downloaded_into_the_folder(tmp_path, monkeypatch):
    fetched = []

    def fake(url, path):
        fetched.append(url)
        Path(path).write_text("x")

    monkeypatch.setattr(go_pathways, "_download", fake)
    go_pathways.main(["fetch-references", "--source", "eggnog", "--kegg-org", "sbi",
                      "--to", str(tmp_path / "ref")])
    assert sorted(p.name for p in (tmp_path / "ref").iterdir()) == ["go-basic.obo",
                                                                     "kegg-pathways.tsv"]
    assert len(fetched) == 2


def test_downloads_name_themselves_since_go_refuses_pythons_default_agent():
    request = go_pathways._request(go_pathways.GO_URL)
    assert request.get_header("User-agent") == go_pathways.USER_AGENT
    assert "Python-urllib" not in go_pathways.USER_AGENT


# --------------------------------------------------------------------------- #
# Recording in Bloom
# --------------------------------------------------------------------------- #


@pytest.fixture
def recorded(monkeypatch):
    """The catalogue calls the command makes, with a stand-in client."""
    calls = []
    monkeypatch.setattr(go_pathways, "_client", lambda: "client")

    def fake(name, result):
        def call(*args, **kwargs):
            calls.append((name, args, kwargs))
            return result
        return call

    from bloom_scrna_analysis import catalogue
    monkeypatch.setattr(catalogue, "upload_annotation",
                        fake("upload_annotation", {"build_id": 3, "build": 1, "file_path": "p"}))
    monkeypatch.setattr(catalogue, "fetch_annotation",
                        fake("fetch_annotation", {"id": 3, "source": "eggnog"}))
    monkeypatch.setattr(catalogue, "label", fake("label", "g.v1/eggnog-b1"))
    monkeypatch.setattr(catalogue, "upload_enrichment",
                        fake("upload_enrichment", {"run_id": 9, "file_path": "q"}))
    return calls


def test_upload_annotation_records_the_file(recorded, tmp_path, capsys):
    assert go_pathways.main(["upload-annotation", "--file", str(tmp_path / "a.json.gz")]) == 0
    assert recorded == [("upload_annotation", ("client", tmp_path / "a.json.gz"), {})]
    assert "build 1 (id 3)" in capsys.readouterr().out


def test_fetch_annotation_saves_the_file_and_its_label(recorded, tmp_path):
    out = tmp_path / "annotation.json.gz"
    assert go_pathways.main(["fetch-annotation", "--build-id", "3", "--out", str(out),
                             "--label-out", str(tmp_path / "label")]) == 0
    assert recorded[0] == ("fetch_annotation", ("client", 3, out), {})
    assert (tmp_path / "label").read_text() == "g.v1/eggnog-b1"


def test_upload_enrichment_records_the_run_and_the_job_when_given(recorded, tmp_path):
    path = tmp_path / "e.json.gz"
    go_pathways.main(["upload-enrichment", "--file", str(path), "--build-id", "3"])
    go_pathways.main(["upload-enrichment", "--file", str(path), "--build-id", "3",
                      "--rnaseq-run-id", "42"])
    assert [c[2] for c in recorded] == [{"build_id": 3, "rnaseq_run_id": None},
                                        {"build_id": 3, "rnaseq_run_id": 42}]


# --------------------------------------------------------------------------- #
# Signing in
# --------------------------------------------------------------------------- #


def test_the_pipeline_credential_is_copied_into_a_private_home(tmp_path, monkeypatch):
    mounted = tmp_path / "etc-bloom.txt"
    mounted.write_text("BLOOM_EMAIL=x\n")
    monkeypatch.setenv("HOME", str(tmp_path / "root"))
    monkeypatch.setenv("BLOOM_CREDENTIALS", str(mounted))
    go_pathways.sign_in_home()
    copied = Path.home() / ".bloom" / "credentials.txt"
    assert copied.read_text() == "BLOOM_EMAIL=x\n"
    assert oct(copied.stat().st_mode)[-3:] == "600"
    assert not str(copied).startswith(str(tmp_path / "root"))


def test_an_existing_bloomctl_login_is_used_as_it_is(tmp_path, monkeypatch):
    own = tmp_path / "home" / ".bloom" / "credentials.txt"
    own.parent.mkdir(parents=True)
    own.write_text("mine")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("BLOOM_CREDENTIALS", str(tmp_path / "missing"))
    go_pathways.sign_in_home()
    assert Path.home() == tmp_path / "home"


def test_without_any_credential_the_step_exits_16(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("BLOOM_CREDENTIALS", str(tmp_path / "missing"))
    with pytest.raises(StepError, match="no Bloom credentials") as exc:
        go_pathways.sign_in_home()
    assert exc.value.code == 16
