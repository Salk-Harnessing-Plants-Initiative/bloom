"""bloomctl genome index — the command end to end, against fake storage and Bloom's browser-build
functions kept in memory."""

import base64
import gzip
import hashlib
import importlib

import httpx
import pytest
from click.testing import CliRunner
from postgrest import APIError
from scrna_fake_db import FakeClient

from bloomctl.cli import cli
from bloomctl.genome import _index

pytest.importorskip("pysam")
index_module = importlib.import_module("bloomctl.genome.index")

GENOME = "tair10_araport11"
FASTA_GZ = gzip.compress(b">Chr1\nACGTACGTACGGGGCCCCAA\n>Chr2\nTTTTAAAAGG\n", mtime=0)
GTF_GZ = gzip.compress(
    b'Chr1\tAraport11\tgene\t3\t9\t.\t+\t.\tgene_id "AT1G01010"; gene_name "NAC001";\n'
    b'Chr1\tAraport11\texon\t12\t18\t.\t-\t.\tgene_id "AT1G01020"; transcript_id "t2";\n'
    b'Chr2\tAraport11\tgene\t1\t4\t.\t-\t.\tgene_id "AT2G01010";\n',
    mtime=0,
)
FOLDER = f"{GENOME}/v1/browser/b1"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class _Call:
    def __init__(self, run):
        self._run = run

    def execute(self):
        return type("R", (), {"data": self._run()})()


class BrowserDB:
    """The genome tables and the browser-build functions, kept in memory."""

    def __init__(self, storage, version_status="ready"):
        self.storage = storage
        version = {
            "id": 101, "genome_id": 1, "version": 1, "status": version_status,
            "withdrawn_reason": None,
            "fasta_path": f"{GENOME}/v1/genome.fa.gz", "fasta_sha256": _sha(FASTA_GZ),
            "fasta_bytes": len(FASTA_GZ),
            "gtf_path": f"{GENOME}/v1/genes.gtf.gz", "gtf_sha256": _sha(GTF_GZ),
            "gtf_bytes": len(GTF_GZ),
        }
        self.base = FakeClient({
            "genome_references": [{"id": 1, "name": GENOME}],
            "genome_reference_versions": [version],
            "genome_browser_builds": [],
        })
        self.builds = self.base.tables["genome_browser_builds"]
        self.genes: dict[int, dict[str, dict]] = {}
        self.calls: list[tuple[str, dict]] = []
        self.refuse: dict[str, str] = {}
        # Each name: how many calls fail as a lost connection after taking effect.
        self.lose_answer: dict[str, int] = {}

    def table(self, name):
        return self.base.table(name)

    def rpc(self, name, args):
        def run():
            self.calls.append((name, args))
            if name in self.refuse:
                raise APIError({"message": self.refuse[name], "code": "22023"})
            result = getattr(self, name)(**args)
            if self.lose_answer.get(name, 0) > 0:
                self.lose_answer[name] -= 1
                raise httpx.ReadTimeout("the answer was lost")
            return result

        return _Call(run)

    def called(self, name):
        return [a for n, a in self.calls if n == name]

    def start_genome_browser_build(self, p_version_id, p_tool_versions):
        number = 1 + sum(b["version_id"] == p_version_id for b in self.builds)
        build = {"id": 7 + len(self.builds), "version_id": p_version_id, "build": number,
                 "status": "uploading", "folder": f"{GENOME}/v1/browser/b{number}",
                 "tool_versions": p_tool_versions}
        self.builds.append(build)
        self.genes[build["id"]] = {}
        return [{"build_id": build["id"], "build": number, "folder": build["folder"]}]

    def add_genome_browser_genes(self, p_build_id, p_genes):
        held, added = self.genes[p_build_id], 0
        for gene in p_genes:
            if gene["gene_id"] in held:
                assert held[gene["gene_id"]] == gene
                continue
            held[gene["gene_id"]] = gene
            added += 1
        return added

    def finish_genome_browser_build(self, p_build_id, p_files, p_gtf_was_sorted, p_gene_count):
        build = next(b for b in self.builds if b["id"] == p_build_id)
        for role, name in _index.ROLES.items():
            stored = self.storage.objects.get(f"genome-references/{build['folder']}/{name}")
            if stored is None or len(stored) != p_files[role]["bytes"]:
                raise APIError({"message": f"{name} has not been uploaded", "code": "22023"})
        if p_gene_count != len(self.genes[p_build_id]):
            raise APIError({"message": "gene count", "code": "22023"})
        build.update(status="ready", files=p_files, gtf_was_sorted=p_gtf_was_sorted,
                     gene_count=p_gene_count)
        return True

    def abandon_genome_browser_build(self, p_build_id):
        build = next(b for b in self.builds if b["id"] == p_build_id)
        if build["status"] != "uploading":
            raise APIError({"message": f"is {build['status']}, not uploading", "code": "55000"})
        build["status"] = "abandoned"
        self.genes[p_build_id] = {}
        return True


@pytest.fixture
def db(env, storage):
    env["client"] = BrowserDB(storage)
    storage.objects[f"genome-references/{GENOME}/v1/genome.fa.gz"] = FASTA_GZ
    storage.objects[f"genome-references/{GENOME}/v1/genes.gtf.gz"] = GTF_GZ
    return env["client"]


def _run(*args):
    return CliRunner().invoke(cli, ["genome", "index", *args])


def _content_types(storage):
    types = {}
    for request in storage.requests:
        if request.method == "POST" and request.url.path == "/storage/v1/upload/resumable":
            meta = dict(i.split(" ") for i in request.headers["upload-metadata"].split(","))
            name = base64.b64decode(meta["objectName"]).decode().rsplit("/", 1)[-1]
            types[name] = base64.b64decode(meta["contentType"]).decode()
    return types


# --- a build that works ---------------------------------------------------------------------


def test_a_version_is_built_uploaded_and_finished(db, storage):
    result = _run(f"{GENOME}.v1")
    assert result.exit_code == 0, result.output
    assert result.stdout == f"{GENOME}.v1 browser build b1 (id 7): {FOLDER}, 3 genes\n"
    (finish,) = db.called("finish_genome_browser_build")
    for role, name in _index.ROLES.items():
        data = storage.objects[f"genome-references/{FOLDER}/{name}"]
        assert finish["p_files"][role] == {"sha256": _sha(data), "bytes": len(data)}
    assert finish["p_gtf_was_sorted"] is True
    assert finish["p_gene_count"] == 3
    assert db.builds[0]["status"] == "ready"
    assert sorted(db.genes[7]) == ["AT1G01010", "AT1G01020", "AT2G01010"]


def test_the_tools_used_are_recorded(db):
    _run(f"{GENOME}.v1")
    (start,) = db.called("start_genome_browser_build")
    assert set(start["p_tool_versions"]) == {"bloomctl", "pysam", "htslib"}


def test_each_file_is_sent_with_its_own_content_type(db, storage):
    _run(f"{GENOME}.v1")
    assert _content_types(storage) == {
        "genome.fa.gz": "application/gzip", "genome.fa.gz.fai": "text/plain",
        "genome.fa.gz.gzi": "application/octet-stream", "genes.gtf.gz": "application/gzip",
        "genes.gtf.gz.tbi": "application/octet-stream",
    }


def test_genes_are_sent_in_batches(db, monkeypatch):
    monkeypatch.setattr(_index, "GENE_BATCH", 2)
    assert _run(f"{GENOME}.v1").exit_code == 0
    assert [len(c["p_genes"]) for c in db.called("add_genome_browser_genes")] == [2, 1]


def test_a_batch_whose_answer_is_lost_is_sent_again(db):
    db.lose_answer["add_genome_browser_genes"] = 1
    result = _run(f"{GENOME}.v1")
    assert result.exit_code == 0, result.output
    assert len(db.called("add_genome_browser_genes")) == 2
    assert db.builds[0]["gene_count"] == 3


def test_a_finish_whose_answer_is_lost_but_went_through_counts(db):
    db.lose_answer["finish_genome_browser_build"] = 1
    result = _run(f"{GENOME}.v1")
    assert result.exit_code == 0, result.output
    assert db.builds[0]["status"] == "ready"
    assert not db.called("abandon_genome_browser_build")


# --- refused before anything starts ---------------------------------------------------------


@pytest.mark.parametrize("genome", [GENOME, f"{GENOME}.v0", "Bad Name.v1"])
def test_a_genome_version_must_be_named_exactly(env, genome):
    result = _run(genome)
    assert result.exit_code == 2
    assert "NAME.vN" in result.output


def test_a_version_that_is_not_ready_is_refused(env, storage):
    env["client"] = BrowserDB(storage, version_status="uploading")
    result = _run(f"{GENOME}.v1")
    assert result.exit_code == 3
    assert not env["client"].called("start_genome_browser_build")


def test_a_version_with_a_ready_build_is_left_alone(db):
    db.builds.append({"id": 5, "version_id": 101, "build": 1, "status": "ready",
                      "folder": FOLDER})
    result = _run(f"{GENOME}.v1")
    assert result.exit_code == 0
    assert "already has browser build b1" in result.output
    assert not db.called("start_genome_browser_build")


def test_a_version_with_a_build_in_progress_exits_4(db):
    db.builds.append({"id": 5, "version_id": 101, "build": 1, "status": "uploading",
                      "folder": FOLDER})
    result = _run(f"{GENOME}.v1")
    assert result.exit_code == 4
    assert not db.called("start_genome_browser_build")


def test_a_source_file_that_does_not_match_bloom_is_refused(db, storage):
    storage.objects[f"genome-references/{GENOME}/v1/genes.gtf.gz"] = gzip.compress(b"x")
    result = _run(f"{GENOME}.v1")
    assert result.exit_code == 3
    assert "doesn't match Bloom's record" in result.output
    assert not db.called("start_genome_browser_build")


def test_a_gtf_with_a_repeated_gene_is_refused_by_name_before_starting(db, storage):
    gtf = gzip.decompress(GTF_GZ) + b'Chr2\tx\tgene\t6\t9\t.\t+\t.\tgene_id "AT1G01010";\n'
    data = gzip.compress(gtf, mtime=0)
    storage.objects[f"genome-references/{GENOME}/v1/genes.gtf.gz"] = data
    db.base.tables["genome_reference_versions"][0].update(gtf_sha256=_sha(data),
                                                          gtf_bytes=len(data))
    result = _run(f"{GENOME}.v1")
    assert result.exit_code == 3
    assert "AT1G01010" in result.output and "Nothing was started" in result.output
    assert not db.called("start_genome_browser_build")


def test_without_the_extra_it_says_how_to_install_and_signs_in_to_nothing(env, monkeypatch):
    def missing():
        raise index_module.click.ClickException(f"needs htslib: {_index.INSTALL_HINT}")

    monkeypatch.setattr(_index, "require_htslib", missing)
    monkeypatch.setattr(index_module._session, "connect",
                        lambda profile: pytest.fail("signed in"))
    result = _run(f"{GENOME}.v1")
    assert result.exit_code == 1
    assert "bloomctl[genome-index]" in result.output


# --- a failure after the build started ------------------------------------------------------


def test_a_failed_upload_abandons_the_build(db, storage):
    storage.drop_after = 0
    result = _run(f"{GENOME}.v1")
    assert result.exit_code != 0
    assert db.builds[0]["status"] == "abandoned"
    assert "abandoned" in result.output


def test_a_refused_finish_abandons_the_build_and_exits_3(db):
    db.refuse["finish_genome_browser_build"] = "genome.fa.gz.gzi is not stored"
    result = _run(f"{GENOME}.v1")
    assert result.exit_code == 3
    assert "gzi is not stored" in result.output
    assert db.builds[0]["status"] == "abandoned"
