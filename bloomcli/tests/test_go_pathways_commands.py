"""bloomctl genome annotation upload|download and scrna enrichment upload, against a fake Bloom
that records every function call, upload and download."""

import gzip
import hashlib
import json

import pytest
from click.testing import CliRunner

from bloomctl import go_pathways
from bloomctl.cli import cli
from bloomctl.scrna import _session, _transfer

GENOME = "sbicolor.btx623.hpiv3"
PATH = f"{GENOME}/v1/annotation/eggnog-b1.json.gz"
RUN_PATH = "enrichment/25/run-9.json.gz"


class Refused(Exception):
    """Stands in for postgrest's APIError and storage3's StorageApiError."""


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
        return type("R", (), {"data": answer})()


class Bucket:
    def __init__(self, bloom, name):
        self.bloom, self.name = bloom, name

    def upload(self, path, data, options):
        if self.bloom.upload_error:
            raise self.bloom.upload_error
        self.bloom.stored[(self.name, path)] = bytes(data)

    def download(self, path):
        return self.bloom.stored[(self.name, path)]


class Bloom:
    def __init__(self):
        self.tables, self.answers, self.calls, self.stored = {}, {}, [], {}
        self.upload_error = None
        self.storage = type("S", (), {"from_": lambda _, name: Bucket(self, name)})()

    def table(self, name):
        return Query(self, name)

    def rpc(self, name, params):
        return Call(self, name, params)

    def called(self, name):
        return [p for n, p in self.calls if n == name]


@pytest.fixture
def bloom(monkeypatch):
    b = Bloom()
    b.tables["genome_references"] = [{"id": 7, "name": GENOME}]
    b.tables["genome_reference_versions"] = [{"id": 70, "genome_id": 7, "version": 1,
                                              "status": "ready"}]
    b.answers["start_genome_annotation_build"] = [{"build_id": 3, "build": 1, "file_path": PATH}]
    b.answers["finish_genome_annotation_build"] = 1
    b.answers["abandon_genome_annotation_build"] = True
    monkeypatch.setattr(_session, "connect", lambda profile: _session.Connection(
        client=b, endpoint=_transfer.Endpoint("http://api.test", "anon", "tok"),
        role="bloom_workflows"))
    monkeypatch.setattr(go_pathways, "_refusals", lambda: (Refused,))
    return b


def _gz(tmp_path, name, doc):
    path = tmp_path / name
    with gzip.open(path, "wt") as fh:
        json.dump(doc, fh)
    return path


def annotation_file(tmp_path, **extra):
    doc = {"format": "bloom-annotation/1", "genome": GENOME, "version": 1, "source": "eggnog",
           "tools": {"eggnog_mapper": "2.1.13"},
           "terms": {"GO:1": {"kind": "GO_BP", "name": "a", "parents": []},
                     "map00010": {"kind": "KEGG", "name": "b"}},
           "genes": {"G1": {"terms": ["GO:1", "map00010"], "kos": []},
                     "G2": {"terms": ["GO:1"], "kos": []}}}
    doc.update(extra)
    return _gz(tmp_path, "eggnog.json.gz", doc)


def enrichment_file(tmp_path, results=2, **extra):
    doc = {"format": "bloom-enrichment/1", "dataset_id": 25, "de_run_id": 5,
           "annotation": f"{GENOME}.v1/eggnog-b1", "params": {"top_n": 300},
           "background_genes": 21957, "annotated_genes": 11427,
           "results": [{"term": f"GO:{i}"} for i in range(results)]}
    doc.update(extra)
    return _gz(tmp_path, "enrichment.json.gz", doc)


def run(*args):
    return CliRunner().invoke(cli, list(args))


# --------------------------------------------------------------------------- #
# genome annotation upload
# --------------------------------------------------------------------------- #


def test_an_annotation_is_started_stored_and_finished(bloom, tmp_path):
    path = annotation_file(tmp_path)
    data = path.read_bytes()
    result = run("genome", "annotation", "upload", str(path))
    assert result.exit_code == 0, result.output
    assert "annotation build 1 (id 3)" in result.output
    assert bloom.called("start_genome_annotation_build") == [{
        "p_version_id": 70, "p_source": "eggnog", "p_tool_versions": {"eggnog_mapper": "2.1.13"}}]
    assert bloom.stored[("genome-references", PATH)] == data
    assert bloom.called("finish_genome_annotation_build") == [{
        "p_build_id": 3, "p_file_sha256": hashlib.sha256(data).hexdigest(),
        "p_file_bytes": len(data), "p_gene_count": 2, "p_term_count": 2}]


def test_a_refused_start_exits_3_and_sends_nothing(bloom, tmp_path):
    bloom.answers["start_genome_annotation_build"] = Refused("already has build 1 (ready)")
    result = run("genome", "annotation", "upload", str(annotation_file(tmp_path)))
    assert result.exit_code == go_pathways.EXIT_REFUSED
    assert "already has build 1" in result.output
    assert not bloom.stored


@pytest.mark.parametrize("where", ["upload", "finish"])
def test_a_refused_upload_or_finish_abandons_the_build_and_exits_3(bloom, tmp_path, where):
    if where == "upload":
        bloom.upload_error = Refused("row-level security")
    else:
        bloom.answers["finish_genome_annotation_build"] = Refused("10 bytes in storage, not 11")
    result = run("genome", "annotation", "upload", str(annotation_file(tmp_path)))
    assert result.exit_code == go_pathways.EXIT_REFUSED
    assert bloom.called("abandon_genome_annotation_build") == [{"p_build_id": 3}]


def test_a_network_error_abandons_the_build_and_exits_1(bloom, tmp_path):
    bloom.upload_error = OSError("connection reset")
    result = run("genome", "annotation", "upload", str(annotation_file(tmp_path)))
    assert result.exit_code == 1
    assert bloom.called("abandon_genome_annotation_build") == [{"p_build_id": 3}]


def test_a_build_that_cant_be_cancelled_keeps_the_error_and_says_to_ask_an_admin(bloom, tmp_path):
    bloom.answers["finish_genome_annotation_build"] = Refused("10 bytes in storage, not 11")
    bloom.answers["abandon_genome_annotation_build"] = OSError("connection reset")
    result = run("genome", "annotation", "upload", str(annotation_file(tmp_path)))
    assert result.exit_code == go_pathways.EXIT_REFUSED
    assert "10 bytes in storage, not 11" in result.output
    assert "ask a Bloom admin to clear it" in result.output


@pytest.mark.parametrize("extra, words", [
    ({"format": "bloom-annotation/0"}, "bloom-annotation/0"),
    ({"genes": {}}, "no genes"),
    ({"genes": {"G1": {"terms": ["GO:9"]}}}, "unknown terms GO:9"),
])
def test_a_file_that_isnt_a_good_annotation_is_refused_before_anything_starts(
        bloom, tmp_path, extra, words):
    result = run("genome", "annotation", "upload", str(annotation_file(tmp_path, **extra)))
    assert result.exit_code == go_pathways.EXIT_REFUSED
    assert words in result.output
    assert not bloom.calls


def test_a_genome_version_that_isnt_ready_is_refused(bloom, tmp_path):
    bloom.tables["genome_reference_versions"][0]["status"] = "uploading"
    result = run("genome", "annotation", "upload", str(annotation_file(tmp_path)))
    assert result.exit_code == go_pathways.EXIT_REFUSED
    assert "not ready" in result.output


# --------------------------------------------------------------------------- #
# genome annotation download
# --------------------------------------------------------------------------- #


def stored_build(bloom, tmp_path, status="ready", sha=None, extra_bytes=0):
    data = annotation_file(tmp_path).read_bytes()
    bloom.stored[("genome-references", PATH)] = data
    bloom.tables["genome_annotation_builds"] = [{
        "id": 3, "version_id": 70, "source": "eggnog", "build": 1, "status": status,
        "file_path": PATH, "file_sha256": sha or hashlib.sha256(data).hexdigest(),
        "file_bytes": len(data) + extra_bytes}]
    return data


def test_a_ready_build_is_saved_with_its_id(bloom, tmp_path):
    data = stored_build(bloom, tmp_path)
    out, ids = tmp_path / "a.json.gz", tmp_path / "id"
    result = run("genome", "annotation", "download", f"{GENOME}.v1", "--out", str(out),
                 "--id-file", str(ids))
    assert result.exit_code == 0, result.output
    assert out.read_bytes() == data
    assert ids.read_text() == "3\n"
    assert f"{GENOME}.v1/eggnog-b1 (id 3)" in result.output


def test_a_build_still_uploading_exits_4_so_a_caller_can_wait(bloom, tmp_path):
    stored_build(bloom, tmp_path, status="uploading")
    result = run("genome", "annotation", "download", f"{GENOME}.v1", "--out", str(tmp_path / "x"))
    assert result.exit_code == go_pathways.EXIT_IN_PROGRESS
    assert "still uploading" in result.output


def test_no_build_exits_3_so_a_caller_can_build_one(bloom, tmp_path):
    result = run("genome", "annotation", "download", f"{GENOME}.v1", "--out", str(tmp_path / "x"))
    assert result.exit_code == go_pathways.EXIT_REFUSED
    assert "has no eggnog annotation" in result.output


@pytest.mark.parametrize("change", [{"sha": "0" * 64}, {"extra_bytes": 1}])
def test_a_file_that_doesnt_match_its_record_is_refused_and_not_saved(bloom, tmp_path, change):
    stored_build(bloom, tmp_path, **change)
    out = tmp_path / "x"
    result = run("genome", "annotation", "download", f"{GENOME}.v1", "--out", str(out))
    assert result.exit_code == go_pathways.EXIT_REFUSED
    assert "checksum and size" in result.output
    assert not out.exists()


# --------------------------------------------------------------------------- #
# scrna enrichment upload
# --------------------------------------------------------------------------- #


@pytest.fixture
def ready_build(bloom, tmp_path):
    stored_build(bloom, tmp_path)
    bloom.tables["scrna_datasets"] = [{"id": 25, "genome_version_id": 70}]
    bloom.answers["start_scrna_enrichment_run"] = [{"run_id": 9, "file_path": RUN_PATH}]
    bloom.answers["finish_scrna_enrichment_run"] = None
    bloom.answers["abandon_scrna_enrichment_run"] = True
    bloom.answers["link_scrna_dataset_genome"] = None
    return bloom


def test_an_enrichment_is_recorded_with_its_job(ready_build, tmp_path):
    path = enrichment_file(tmp_path)
    data = path.read_bytes()
    result = run("scrna", "enrichment", "upload", str(path), "--build-id", "3", "--run-id", "42")
    assert result.exit_code == 0, result.output
    assert "enrichment run 9" in result.output
    assert ready_build.called("start_scrna_enrichment_run") == [{
        "p_dataset_id": 25, "p_de_run_id": 5, "p_annotation_build_id": 3,
        "p_params": {"top_n": 300}, "p_rnaseq_run_id": 42}]
    assert ready_build.stored[("scrna", RUN_PATH)] == data
    assert ready_build.called("finish_scrna_enrichment_run")[0]["p_term_count"] == 2
    assert not ready_build.called("link_scrna_dataset_genome"), "already linked"


def test_an_unlinked_dataset_is_linked_first(ready_build, tmp_path):
    ready_build.tables["scrna_datasets"][0]["genome_version_id"] = None
    result = run("scrna", "enrichment", "upload", str(enrichment_file(tmp_path)), "--build-id", "3")
    assert result.exit_code == 0, result.output
    names = [n for n, _ in ready_build.calls]
    assert names.index("link_scrna_dataset_genome") < names.index("start_scrna_enrichment_run")


def test_a_dataset_linked_to_another_version_is_refused(ready_build, tmp_path):
    ready_build.tables["scrna_datasets"][0]["genome_version_id"] = 71
    result = run("scrna", "enrichment", "upload", str(enrichment_file(tmp_path)), "--build-id", "3")
    assert result.exit_code == go_pathways.EXIT_REFUSED
    assert not ready_build.called("start_scrna_enrichment_run")


def test_a_refused_finish_abandons_the_run(ready_build, tmp_path):
    ready_build.answers["finish_scrna_enrichment_run"] = Refused("not uploaded")
    result = run("scrna", "enrichment", "upload", str(enrichment_file(tmp_path)), "--build-id", "3")
    assert result.exit_code == go_pathways.EXIT_REFUSED
    assert ready_build.called("abandon_scrna_enrichment_run") == [{"p_run_id": 9}]


def test_a_run_that_cant_be_cancelled_says_to_ask_an_admin(ready_build, tmp_path):
    ready_build.upload_error = OSError("connection reset")
    ready_build.answers["abandon_scrna_enrichment_run"] = OSError("connection reset")
    result = run("scrna", "enrichment", "upload", str(enrichment_file(tmp_path)), "--build-id", "3")
    assert result.exit_code == 1
    assert "Enrichment run 9 couldn't be cancelled" in result.output
    assert "ask a Bloom admin to clear it" in result.output


def test_an_unknown_build_is_refused(ready_build, tmp_path):
    result = run("scrna", "enrichment", "upload", str(enrichment_file(tmp_path)), "--build-id", "99")
    assert result.exit_code == go_pathways.EXIT_REFUSED
    assert "no annotation build 99" in result.output


def test_a_file_that_isnt_an_enrichment_is_refused_before_anything_starts(ready_build, tmp_path):
    path = enrichment_file(tmp_path, format="bloom-enrichment/0")
    result = run("scrna", "enrichment", "upload", str(path), "--build-id", "3")
    assert result.exit_code == go_pathways.EXIT_REFUSED
    assert not ready_build.calls
