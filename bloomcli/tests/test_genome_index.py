"""bloomctl genome index — the command end to end, against fake storage and the browser-build
functions in genome_browser_fake, which make the migrations' checks."""

import base64
import gzip
import importlib

import httpx
import pytest
from genome_browser_fake import GENOME, GTF_GZ, BrowserDB, stock
from genome_browser_fake import refusal as _refusal
from genome_browser_fake import replace_gtf as _replace_gtf
from genome_browser_fake import run_index as _run
from genome_browser_fake import sha as _sha

from bloomctl.genome import _index

index_module = importlib.import_module("bloomctl.genome.index")

FOLDER = f"{GENOME}/v1/browser/b1"


@pytest.fixture
def db(env, storage):
    return stock(env, storage)


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
    assert finish["p_gene_count"] == 3
    assert db.builds[0]["status"] == "ready"
    assert sorted(db.genes[7]) == ["AT1G01010", "AT1G01020", "AT2G01010"]


def test_the_tools_used_are_recorded(db):
    _run(f"{GENOME}.v1")
    (start,) = db.called("start_genome_browser_build")
    assert start["p_tool_versions"] == _index.tool_versions()


def test_each_file_is_sent_with_its_own_content_type(db, storage):
    _run(f"{GENOME}.v1")
    assert _content_types(storage) == {
        "genome.fa.gz": "application/gzip", "genome.fa.gz.fai": "text/plain",
        "genome.fa.gz.gzi": "application/octet-stream", "genes.gff3.gz": "application/gzip",
        "genes.gff3.gz.tbi": "application/octet-stream",
    }


def test_genes_are_sent_in_batches(db, monkeypatch):
    monkeypatch.setattr(_index, "GENE_BATCH", 2)
    assert _run(f"{GENOME}.v1").exit_code == 0
    assert [len(c["p_genes"]) for c in db.called("add_genome_browser_genes")] == [2, 1]


@pytest.mark.parametrize("lost", ["answer", "server error"])
def test_a_batch_that_fails_in_passing_is_sent_again(db, lost):
    if lost == "answer":
        db.lose_answer["add_genome_browser_genes"] = 1
    else:
        db.fail["add_genome_browser_genes"] = [_refusal("JSON could not be generated", "502")]
    result = _run(f"{GENOME}.v1")
    assert result.exit_code == 0, result.output
    assert len(db.called("add_genome_browser_genes")) == 2
    assert db.builds[0]["gene_count"] == 3


def test_a_batch_that_keeps_failing_abandons_the_build_and_exits_1(db):
    db.fail["add_genome_browser_genes"] = [_refusal("bad gateway", "502")] * 3
    result = _run(f"{GENOME}.v1")
    assert result.exit_code == 1
    assert "server error 502" in result.output
    assert db.builds[0]["status"] == "abandoned"


def test_a_finish_whose_answer_is_lost_but_went_through_counts(db):
    db.lose_answer["finish_genome_browser_build"] = 1
    result = _run(f"{GENOME}.v1")
    assert result.exit_code == 0, result.output
    assert db.builds[0]["status"] == "ready"
    assert not db.called("abandon_genome_browser_build")


def test_the_upload_signs_in_again_when_the_session_runs_out(db, storage, monkeypatch):
    storage.expire_after = 2  # two files land, then the token runs out
    signed_in = []
    real = index_module._session.connect

    def connect(profile):
        signed_in.append(profile)
        if len(signed_in) > 1:
            storage.expire_after = None  # the new token is accepted
        return real(profile)

    monkeypatch.setattr(index_module._session, "connect", connect)
    result = _run(f"{GENOME}.v1")
    assert result.exit_code == 0, result.output
    assert len(signed_in) == 2
    assert db.builds[0]["status"] == "ready"


def test_workdir_is_used_and_left_empty(db, tmp_path):
    work = tmp_path / "work"
    assert _run(f"{GENOME}.v1", "--workdir", str(work)).exit_code == 0
    assert work.is_dir() and not any(work.iterdir())


# --- refused before anything starts ---------------------------------------------------------


@pytest.mark.parametrize("genome", [GENOME, f"{GENOME}.v0", "Bad Name.v1"])
def test_a_genome_version_must_be_named_exactly(env, genome):
    result = _run(genome)
    assert result.exit_code == 2
    assert "NAME.vN" in result.output


def test_a_member_login_is_refused_before_downloading(db, env, storage):
    env["role"] = "bloom_user"
    result = _run(f"{GENOME}.v1")
    assert result.exit_code == 1
    assert "writer (bloom_writer) or pipeline (bloom_workflows)" in result.output
    assert not storage.requests


def test_the_pipeline_login_can_build(db, env):
    env["role"] = "bloom_workflows"
    assert _run(f"{GENOME}.v1").exit_code == 0


def test_a_version_that_is_not_ready_is_refused_in_this_commands_words(env, storage):
    env["client"] = BrowserDB(storage, version_status="uploading")
    result = _run(f"{GENOME}.v1")
    assert result.exit_code == 3
    assert "only a ready version can be built. Nothing was started." in result.output
    assert not env["client"].called("start_genome_browser_build")


def test_a_version_with_a_ready_build_prints_it_and_starts_nothing(db):
    db.add_build("ready", gene_count=3)
    result = _run(f"{GENOME}.v1")
    assert result.exit_code == 0
    assert result.stdout == f"{GENOME}.v1 browser build b1 (id 7): {FOLDER}, 3 genes\n"
    assert not db.called("start_genome_browser_build")


def test_an_unfinished_build_exits_4_and_says_how_to_replace_it(db):
    db.add_build("uploading")
    result = _run(f"{GENOME}.v1")
    assert result.exit_code == 4
    assert "--replace-unfinished" in result.output
    assert not db.called("start_genome_browser_build")


def test_replace_unfinished_abandons_your_build_and_makes_the_next(db):
    db.add_build("uploading")
    result = _run(f"{GENOME}.v1", "--replace-unfinished")
    assert result.exit_code == 0, result.output
    assert [b["status"] for b in db.builds] == ["abandoned", "ready"]
    assert "browser build b2" in result.stdout


def test_replace_unfinished_cannot_abandon_someone_elses_build(db):
    db.add_build("uploading", created_by="someone-else")
    result = _run(f"{GENOME}.v1", "--replace-unfinished")
    assert result.exit_code == 3
    assert "started by someone else" in result.output
    assert db.builds[0]["status"] == "uploading"


def test_a_start_that_loses_a_race_exits_4(db):
    db.fail["start_genome_browser_build"] = [
        _refusal("genome version 101 already has browser build 1 (uploading)", "55000")]
    assert _run(f"{GENOME}.v1").exit_code == 4


def test_a_start_whose_answer_is_lost_says_how_to_clear_it(db):
    db.lose_answer["start_genome_browser_build"] = 1
    result = _run(f"{GENOME}.v1")
    assert result.exit_code == 1
    assert "--replace-unfinished" in result.output


def test_a_source_file_that_does_not_match_bloom_is_refused(db, storage):
    storage.objects[f"genome-references/{GENOME}/v1/genes.gtf.gz"] = gzip.compress(b"x")
    result = _run(f"{GENOME}.v1")
    assert result.exit_code == 3
    assert "doesn't match Bloom's record" in result.output
    assert not db.called("start_genome_browser_build")


@pytest.mark.parametrize("line, named", [
    (b'Chr2\tx\tgene\t6\t9\t.\t+\t.\tgene_id "AT1G01010";\n', "AT1G01010"),
    (b'Chr1\tx\texon\t0\t4\t.\t+\t.\tgene_id "G0";\n', "runs from 0 to 4"),
    (b'Chr3\tx\tgene\t1\t4\t.\t+\t.\tgene_id "G3";\n', "Chr3"),
])
def test_a_gtf_bloom_cannot_use_is_refused_before_starting(db, storage, line, named):
    _replace_gtf(db, storage, gzip.decompress(GTF_GZ) + line)
    result = _run(f"{GENOME}.v1")
    assert result.exit_code == 3
    assert named in result.output and "Nothing was started" in result.output
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
    assert result.exit_code == 1
    assert db.builds[0]["status"] == "abandoned"
    assert "was abandoned" in result.output


def test_a_refused_batch_abandons_the_build_and_exits_3(db):
    db.fail["add_genome_browser_genes"] = [
        _refusal("gene AT1G01010 was already added with different details", "23505")]
    result = _run(f"{GENOME}.v1")
    assert result.exit_code == 3
    assert "different details" in result.output
    assert db.builds[0]["status"] == "abandoned"


def test_a_refused_finish_abandons_the_build_and_exits_3(db):
    db.fail["finish_genome_browser_build"] = [_refusal("genome.fa.gz.gzi is not stored")]
    result = _run(f"{GENOME}.v1")
    assert result.exit_code == 3
    assert "gzi is not stored" in result.output
    assert db.builds[0]["status"] == "abandoned"


def test_a_stop_partway_abandons_the_build(db):
    db.fail["add_genome_browser_genes"] = [KeyboardInterrupt()]
    result = _run(f"{GENOME}.v1")
    assert result.exit_code != 0
    assert db.builds[0]["status"] == "abandoned"


def test_an_abandon_that_fails_says_how_to_clear_the_build(db):
    db.fail["finish_genome_browser_build"] = [_refusal("refused")]
    db.fail["abandon_genome_browser_build"] = [httpx.ConnectError("down")]
    result = _run(f"{GENOME}.v1")
    assert result.exit_code == 3
    assert "could not be abandoned and is uploading" in result.output
    assert "--replace-unfinished" in result.output
