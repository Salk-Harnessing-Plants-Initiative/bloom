"""Tests for argo/scrna/analysis: recording annotation builds and enrichment runs in Bloom
(start, upload the file, finish; abandon on failure), and fetching a build's file back with its
checksum checked. Bloom is a fake client recording every call. No scanpy needed."""

from __future__ import annotations

import gzip
import hashlib
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "argo/scrna/analysis"))

from bloom_scrna_analysis import catalogue  # noqa: E402
from bloom_scrna_analysis.steps import (  # noqa: E402
    EXIT_BAD_ANNOTATION,
    EXIT_BAD_ENRICHMENT_INPUT,
    StepError,
)


class Refused(Exception):
    """Stands in for postgrest's APIError: Bloom answered and said no."""

    def __init__(self, message):
        super().__init__(message)
        self.message = message


class Query:
    def __init__(self, bloom, table):
        self.bloom, self.table, self.filters = bloom, table, {}

    def select(self, *_):
        return self

    def eq(self, column, value):
        self.filters[column] = value
        return self

    def execute(self):
        rows = [r for r in self.bloom.tables.get(self.table, [])
                if all(r.get(k) == v for k, v in self.filters.items())]
        return type("R", (), {"data": rows})()


class Call:
    def __init__(self, bloom, name, params):
        self.bloom, self.name, self.params = bloom, name, params

    def execute(self):
        self.bloom.calls.append((self.name, self.params))
        answer = self.bloom.answers.get(self.name)
        if isinstance(answer, Exception):
            raise answer
        return type("R", (), {"data": answer(self.params) if callable(answer) else answer})()


class Bucket:
    def __init__(self, bloom, name):
        self.bloom, self.name = bloom, name

    def upload(self, path, data, options):
        if self.bloom.fail_upload:
            raise OSError("connection reset")
        self.bloom.stored[(self.name, path)] = (bytes(data), options)

    def download(self, path):
        return self.bloom.stored[(self.name, path)][0]


class Storage:
    def __init__(self, bloom):
        self.bloom = bloom

    def from_(self, name):
        return Bucket(self.bloom, name)


class Bloom:
    def __init__(self):
        self.tables, self.answers, self.calls, self.stored = {}, {}, [], {}
        self.fail_upload = False
        self.storage = Storage(self)

    def table(self, name):
        return Query(self, name)

    def rpc(self, name, params):
        return Call(self, name, params)

    def called(self, name):
        return [p for n, p in self.calls if n == name]


GENOME = "sbicolor.btx623.hpiv3"
PATH = f"{GENOME}/v1/annotation/eggnog-b1.json.gz"


def annotation_file(tmp_path, **extra):
    doc = {"format": "bloom-annotation/1", "genome": GENOME, "version": 1, "source": "eggnog",
           "tools": {"eggnog_mapper": "2.1.13", "go_basic": "releases/2026-07-26"},
           "terms": {"GO:1": {"kind": "GO_BP", "name": "a", "parents": []},
                     "map00010": {"kind": "KEGG", "name": "b"}},
           "genes": {"G1": {"name": None, "description": None, "terms": ["GO:1", "map00010"]},
                     "G2": {"name": None, "description": None, "terms": ["GO:1"]}}}
    doc.update(extra)
    path = tmp_path / "eggnog.json.gz"
    with gzip.open(path, "wt") as fh:
        json.dump(doc, fh)
    return path


def enrichment_file(tmp_path, results=1):
    doc = {"format": "bloom-enrichment/1", "dataset_id": 25, "de_run_id": 5,
           "annotation": f"{GENOME}.v1/eggnog-b1",
           "params": {"top_n": 300, "fdr_max": 0.05},
           "background_genes": 21957, "annotated_genes": 11427,
           "results": [{"term": f"GO:{i}"} for i in range(results)]}
    path = tmp_path / "enrichment.json.gz"
    with gzip.open(path, "wt") as fh:
        json.dump(doc, fh)
    return path


@pytest.fixture
def bloom():
    b = Bloom()
    b.tables["genome_references"] = [{"id": 7, "name": GENOME}]
    b.tables["genome_reference_versions"] = [{"id": 70, "genome_id": 7, "version": 1,
                                              "status": "ready"}]
    b.answers["start_genome_annotation_build"] = [{"build_id": 3, "build": 1, "file_path": PATH}]
    b.answers["finish_genome_annotation_build"] = 1
    b.answers["abandon_genome_annotation_build"] = True
    return b


# --------------------------------------------------------------------------- #
# Annotation builds
# --------------------------------------------------------------------------- #


def test_a_version_is_found_by_its_name_and_number(bloom):
    assert catalogue.version_id(bloom, f"{GENOME}.v1") == 70


@pytest.mark.parametrize("label, match", [
    ("nope.v1", "no genome"), (f"{GENOME}.v2", "no v2"), (GENOME, "<genome>.v<N>"),
])
def test_an_unknown_version_is_refused(bloom, label, match):
    with pytest.raises(StepError, match=match) as exc:
        catalogue.version_id(bloom, label)
    assert exc.value.code == EXIT_BAD_ANNOTATION


def test_a_version_that_isnt_ready_is_refused(bloom):
    bloom.tables["genome_reference_versions"][0]["status"] = "uploading"
    with pytest.raises(StepError, match="uploading"):
        catalogue.version_id(bloom, f"{GENOME}.v1")


def test_an_annotation_is_started_uploaded_and_finished(bloom, tmp_path):
    path = annotation_file(tmp_path)
    data = path.read_bytes()
    build = catalogue.upload_annotation(bloom, path)
    assert build == {"build_id": 3, "build": 1, "file_path": PATH}
    assert bloom.called("start_genome_annotation_build") == [{
        "p_version_id": 70, "p_source": "eggnog",
        "p_tool_versions": {"eggnog_mapper": "2.1.13", "go_basic": "releases/2026-07-26"},
    }]
    stored, options = bloom.stored[("genome-references", PATH)]
    assert stored == data
    assert options == {"content-type": "application/gzip", "upsert": "false"}
    assert bloom.called("finish_genome_annotation_build") == [{
        "p_build_id": 3, "p_file_sha256": hashlib.sha256(data).hexdigest(),
        "p_file_bytes": len(data), "p_gene_count": 2, "p_term_count": 2,
    }]


def test_a_failed_upload_abandons_the_build_and_says_so(bloom, tmp_path):
    bloom.fail_upload = True
    with pytest.raises(OSError):
        catalogue.upload_annotation(bloom, annotation_file(tmp_path))
    assert bloom.called("abandon_genome_annotation_build") == [{"p_build_id": 3}]
    assert not bloom.called("finish_genome_annotation_build")


def test_a_refused_start_is_the_steps_own_error_and_nothing_is_sent(bloom, tmp_path):
    bloom.answers["start_genome_annotation_build"] = Refused("already has eggnog annotation build 1")
    with pytest.raises(StepError, match="already has") as exc:
        catalogue.upload_annotation(bloom, annotation_file(tmp_path), refused=Refused)
    assert exc.value.code == EXIT_BAD_ANNOTATION
    assert not bloom.stored


def test_a_file_that_isnt_an_annotation_is_refused_before_anything_starts(bloom, tmp_path):
    with pytest.raises(StepError):
        catalogue.upload_annotation(bloom, annotation_file(tmp_path, format="other/1"))
    assert not bloom.calls


# --------------------------------------------------------------------------- #
# Fetching a build back
# --------------------------------------------------------------------------- #


def stored_build(bloom, tmp_path, status="ready", sha=None):
    data = annotation_file(tmp_path).read_bytes()
    bloom.stored[("genome-references", PATH)] = (data, {})
    bloom.tables["genome_annotation_builds"] = [{
        "id": 3, "version_id": 70, "source": "eggnog", "build": 1, "status": status,
        "file_path": PATH, "file_sha256": sha or hashlib.sha256(data).hexdigest(),
        "file_bytes": len(data),
    }]
    return data


def test_a_ready_build_is_fetched_and_named(bloom, tmp_path):
    data = stored_build(bloom, tmp_path)
    out = tmp_path / "fetched.json.gz"
    build = catalogue.fetch_annotation(bloom, 3, out)
    assert out.read_bytes() == data
    assert (build["id"], build["version_id"]) == (3, 70)
    assert catalogue.label(bloom, build) == f"{GENOME}.v1/eggnog-b1"


def test_a_build_that_isnt_ready_is_not_fetched(bloom, tmp_path):
    stored_build(bloom, tmp_path, status="withdrawn")
    with pytest.raises(StepError, match="withdrawn"):
        catalogue.fetch_annotation(bloom, 3, tmp_path / "x")


def test_a_file_that_doesnt_match_its_build_is_refused(bloom, tmp_path):
    stored_build(bloom, tmp_path, sha="0" * 64)
    out = tmp_path / "x"
    with pytest.raises(StepError, match="checksum"):
        catalogue.fetch_annotation(bloom, 3, out)
    assert not out.exists()


# --------------------------------------------------------------------------- #
# Enrichment runs
# --------------------------------------------------------------------------- #

RUN_PATH = "enrichment/25/run-9.json.gz"


@pytest.fixture
def ready_build(bloom, tmp_path):
    stored_build(bloom, tmp_path)
    bloom.tables["scrna_datasets"] = [{"id": 25, "genome_version_id": 70}]
    bloom.answers["start_scrna_enrichment_run"] = [{"run_id": 9, "file_path": RUN_PATH}]
    bloom.answers["finish_scrna_enrichment_run"] = None
    bloom.answers["abandon_scrna_enrichment_run"] = True
    return bloom


def test_an_enrichment_is_started_uploaded_and_finished(ready_build, tmp_path):
    bloom = ready_build
    path = enrichment_file(tmp_path, results=4)
    data = path.read_bytes()
    assert catalogue.upload_enrichment(bloom, path, build_id=3) == {"run_id": 9,
                                                                    "file_path": RUN_PATH}
    assert bloom.called("start_scrna_enrichment_run") == [{
        "p_dataset_id": 25, "p_de_run_id": 5, "p_annotation_build_id": 3,
        "p_params": {"top_n": 300, "fdr_max": 0.05}, "p_rnaseq_run_id": None,
    }]
    assert bloom.stored[("scrna", RUN_PATH)][0] == data
    assert bloom.called("finish_scrna_enrichment_run") == [{
        "p_run_id": 9, "p_file_sha256": hashlib.sha256(data).hexdigest(),
        "p_file_bytes": len(data), "p_term_count": 4, "p_background_genes": 21957,
        "p_annotated_genes": 11427,
    }]
    assert not bloom.called("link_scrna_dataset_genome"), "already linked"


def test_an_unlinked_dataset_is_linked_to_the_builds_genome_version_first(ready_build, tmp_path):
    bloom = ready_build
    bloom.tables["scrna_datasets"][0]["genome_version_id"] = None
    bloom.answers["link_scrna_dataset_genome"] = None
    catalogue.upload_enrichment(bloom, enrichment_file(tmp_path), build_id=3)
    assert bloom.called("link_scrna_dataset_genome") == [{"p_dataset_id": 25, "p_version_id": 70}]
    names = [n for n, _ in bloom.calls]
    assert names.index("link_scrna_dataset_genome") < names.index("start_scrna_enrichment_run")


def test_a_dataset_linked_to_another_version_is_refused(ready_build, tmp_path):
    ready_build.tables["scrna_datasets"][0]["genome_version_id"] = 71
    with pytest.raises(StepError, match="version 71") as exc:
        catalogue.upload_enrichment(ready_build, enrichment_file(tmp_path), build_id=3)
    assert exc.value.code == EXIT_BAD_ENRICHMENT_INPUT
    assert not ready_build.called("start_scrna_enrichment_run")


def test_a_failed_enrichment_upload_abandons_the_run(ready_build, tmp_path):
    ready_build.fail_upload = True
    with pytest.raises(OSError):
        catalogue.upload_enrichment(ready_build, enrichment_file(tmp_path), build_id=3)
    assert ready_build.called("abandon_scrna_enrichment_run") == [{"p_run_id": 9}]


def test_the_pipeline_job_is_recorded_when_given(ready_build, tmp_path):
    catalogue.upload_enrichment(ready_build, enrichment_file(tmp_path), build_id=3,
                                rnaseq_run_id=42)
    assert ready_build.called("start_scrna_enrichment_run")[0]["p_rnaseq_run_id"] == 42


# --------------------------------------------------------------------------- #
# Each check on its own
# --------------------------------------------------------------------------- #


def test_a_file_of_the_right_checksum_but_another_size_is_refused(bloom, tmp_path):
    stored_build(bloom, tmp_path)
    bloom.tables["genome_annotation_builds"][0]["file_bytes"] += 1
    out = tmp_path / "x"
    with pytest.raises(StepError, match="checksum and size"):
        catalogue.fetch_annotation(bloom, 3, out)
    assert not out.exists()


def test_a_refused_finish_abandons_the_build(bloom, tmp_path):
    bloom.answers["finish_genome_annotation_build"] = Refused("is 10 bytes in storage, not 11")
    with pytest.raises(Refused):
        catalogue.upload_annotation(bloom, annotation_file(tmp_path), refused=Refused)
    assert bloom.called("abandon_genome_annotation_build") == [{"p_build_id": 3}]


def test_a_refused_finish_abandons_the_run(ready_build, tmp_path):
    ready_build.answers["finish_scrna_enrichment_run"] = Refused("not uploaded")
    with pytest.raises(Refused):
        catalogue.upload_enrichment(ready_build, enrichment_file(tmp_path), build_id=3,
                                    refused=Refused)
    assert ready_build.called("abandon_scrna_enrichment_run") == [{"p_run_id": 9}]


def test_a_file_that_isnt_an_enrichment_is_refused_before_anything_starts(ready_build, tmp_path):
    path = tmp_path / "old.json.gz"
    with gzip.open(path, "wt") as fh:
        json.dump({"format": "bloom-enrichment/0", "dataset_id": 25}, fh)
    with pytest.raises(StepError, match="bloom-enrichment/0") as exc:
        catalogue.upload_enrichment(ready_build, path, build_id=3)
    assert exc.value.code == EXIT_BAD_ENRICHMENT_INPUT
    assert not ready_build.calls


def test_a_refused_link_is_the_steps_own_error_and_nothing_starts(ready_build, tmp_path):
    ready_build.tables["scrna_datasets"][0]["genome_version_id"] = None
    ready_build.answers["link_scrna_dataset_genome"] = Refused("is for species 2")
    with pytest.raises(StepError, match="refused the link") as exc:
        catalogue.upload_enrichment(ready_build, enrichment_file(tmp_path), build_id=3,
                                    refused=Refused)
    assert exc.value.code == EXIT_BAD_ENRICHMENT_INPUT
    assert not ready_build.called("start_scrna_enrichment_run")


def test_an_unknown_dataset_is_refused(ready_build, tmp_path):
    ready_build.tables["scrna_datasets"] = []
    with pytest.raises(StepError, match="no dataset 25"):
        catalogue.upload_enrichment(ready_build, enrichment_file(tmp_path), build_id=3)
    assert not ready_build.called("start_scrna_enrichment_run")
