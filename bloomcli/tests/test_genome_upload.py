"""bloomctl genome upload — the command end to end, against fake storage and database functions."""

import gzip
import hashlib
import importlib

import httpx
import pytest
from click.testing import CliRunner
from postgrest import APIError
from scrna_fake_db import FakeClient

from bloomctl.cli import cli
from bloomctl.genome import _files
from bloomctl.scrna import _session

upload_module = importlib.import_module("bloomctl.genome.upload")

FASTA = b">Chr1 CHROMOSOME dumped from ADB\nACGTACGTNNACGT\nACGT\n>Chr2\nGGCC\n"
GTF = (
    b"#!genome-build TAIR10\n"
    b"Chr1\tAraport11\tgene\t1\t10\t.\t+\t.\tgene_id \"AT1G01010\";\n"
    b"Chr1\tAraport11\texon\t1\t10\t.\t+\t.\tgene_id \"AT1G01010\"; transcript_id \"t1\";\n"
)
SPECIES = [{"id": 7, "common_name": "Arabidopsis"}, {"id": 8, "common_name": "Rice"}]


class _Call:
    def __init__(self, run):
        self._run = run

    def execute(self):
        class Response:
            pass

        response = Response()
        response.data = self._run()
        return response


class GenomeDB:
    """The genome tables and their three upload functions, kept in memory."""

    def __init__(self, storage, genomes=()):
        self.storage = storage
        self.base = FakeClient({"species": SPECIES, "genome_references": list(genomes)})
        # The same dicts the status lookup reads, so a status change shows in both.
        self.versions: list[dict] = self.base.tables.setdefault("genome_reference_versions", [])
        self.calls: list[str] = []
        self.refuse: dict[str, str] = {}
        # Commit the finish, then fail as though its answer was lost on the way back.
        self.lose_finish_answer = False

    def table(self, name):
        return self.base.table(name)

    def rpc(self, name, args):
        def run():
            self.calls.append(name)
            if name in self.refuse:
                raise APIError({"message": self.refuse[name], "code": "22023"})
            return getattr(self, name)(**args)

        return _Call(run)

    def _genome(self, name):
        return next((g for g in self.base.tables["genome_references"] if g["name"] == name), None)

    def start_genome_version(self, p_genome, p_species_id, **sources):
        genome = self._genome(p_genome)
        if genome is None:
            genome = {"id": len(self.base.tables["genome_references"]) + 1, "name": p_genome,
                      "species_id": p_species_id}
            self.base.tables["genome_references"].append(genome)
        number = 1 + sum(v["genome_id"] == genome["id"] for v in self.versions)
        version_id = 100 + len(self.versions)
        version = {
            "id": version_id, "version_id": version_id, "version": number,
            "genome_id": genome["id"],
            "fasta_path": f"{p_genome}/v{number}/genome.fa.gz",
            "gtf_path": f"{p_genome}/v{number}/genes.gtf.gz",
            "status": "uploading", "sources": sources,
        }
        self.versions.append(version)
        return [{k: version[k] for k in ("version_id", "version", "fasta_path", "gtf_path")}]

    def finish_genome_version(self, p_version_id, p_fasta_sha256, p_fasta_bytes, p_gtf_sha256,
                              p_gtf_bytes):
        version = next(v for v in self.versions if v["version_id"] == p_version_id)
        for path, size in ((version["fasta_path"], p_fasta_bytes),
                           (version["gtf_path"], p_gtf_bytes)):
            stored = self.storage.objects.get(f"genome-references/{path}")
            if stored is None or len(stored) != size:
                raise APIError({"message": f"{path} has not been uploaded", "code": "22023"})
        version.update(status="ready", fasta_sha256=p_fasta_sha256, gtf_sha256=p_gtf_sha256)
        if self.lose_finish_answer:
            raise httpx.ReadTimeout("the answer was lost")
        return version["version"]

    def abandon_genome_version(self, p_version_id):
        version = next(v for v in self.versions if v["version_id"] == p_version_id)
        if version["status"] != "uploading":
            raise APIError({"message": f"is {version['status']}, not uploading", "code": "55000"})
        version["status"] = "abandoned"
        return True


@pytest.fixture
def db(env, storage):
    env["client"] = GenomeDB(storage)
    return env["client"]


@pytest.fixture
def files(tmp_path):
    fasta, gtf = tmp_path / "TAIR10.fa", tmp_path / "Araport11.gtf"
    fasta.write_bytes(FASTA)
    gtf.write_bytes(GTF)
    return fasta, gtf


def _run(*args, **kwargs):
    return CliRunner().invoke(cli, ["genome", "upload", *args], **kwargs)


def _upload(files, *extra, name="tair10_araport11", **kwargs):
    fasta, gtf = files
    return _run("--fasta", str(fasta), "--gtf", str(gtf), *extra, "--", name, **kwargs)


def _stored(storage, path):
    return storage.objects[f"genome-references/{path}"]


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --- a successful upload --------------------------------------------------------------------


def test_a_new_genome_is_uploaded_as_version_1(db, storage, files):
    result = _upload(files, "--species", "arabidopsis", "--assembly", "TAIR10", "--yes")
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "tair10_araport11 v1: ready"
    fasta = _stored(storage, "tair10_araport11/v1/genome.fa.gz")
    gtf = _stored(storage, "tair10_araport11/v1/genes.gtf.gz")
    assert gzip.decompress(fasta) == FASTA
    assert gzip.decompress(gtf) == GTF
    version = db.versions[0]
    assert version["status"] == "ready"
    assert (version["fasta_sha256"], version["gtf_sha256"]) == (_sha(fasta), _sha(gtf))
    assert version["sources"]["p_assembly"] == "TAIR10"
    assert db.calls == ["start_genome_version", "finish_genome_version"]


def test_the_species_is_sent_by_id(db, storage, files):
    _upload(files, "--species", "Arabidopsis", "--yes")
    assert db.base.tables["genome_references"][0]["species_id"] == 7


def test_the_next_upload_is_version_2_and_needs_no_species(db, storage, files):
    _upload(files, "--species", "Arabidopsis", "--yes")
    result = _upload(files, "--yes")
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "tair10_araport11 v2: ready"
    assert "genome-references/tair10_araport11/v2/genome.fa.gz" in storage.objects


def test_a_gzipped_file_is_sent_as_it_is(db, storage, tmp_path):
    fasta, gtf = tmp_path / "g.fa.gz", tmp_path / "a.gtf.gz"
    fasta.write_bytes(gzip.compress(FASTA, mtime=123))
    gtf.write_bytes(gzip.compress(GTF, mtime=456))
    result = _upload((fasta, gtf), "--species", "Arabidopsis", "--yes")
    assert result.exit_code == 0, result.output
    assert _stored(storage, "tair10_araport11/v1/genome.fa.gz") == fasta.read_bytes()
    assert _stored(storage, "tair10_araport11/v1/genes.gtf.gz") == gtf.read_bytes()


def test_the_original_files_are_left_unchanged(db, storage, files):
    _upload(files, "--species", "Arabidopsis", "--yes")
    assert files[0].read_bytes() == FASTA
    assert files[1].read_bytes() == GTF


def test_the_summary_and_question_go_to_the_terminal(db, storage, files, monkeypatch):
    monkeypatch.setattr(upload_module, "_interactive", lambda: True)
    result = _upload(files, "--species", "Arabidopsis", "--annotation", "Araport11", input="y\n")
    assert result.exit_code == 0, result.output
    for text in ("tair10_araport11 (new)", "Arabidopsis", "TAIR10.fa", "Araport11.gtf",
                 "Annotation  Araport11", "Create genome tair10_araport11"):
        assert text in result.stderr
    assert result.stdout.strip() == "tair10_araport11 v1: ready"


# --- refusals before anything is sent -------------------------------------------------------


def test_declining_sends_nothing(db, storage, files, monkeypatch):
    monkeypatch.setattr(upload_module, "_interactive", lambda: True)
    result = _upload(files, "--species", "Arabidopsis", input="n\n")
    assert result.exit_code != 0
    assert "Nothing was sent." in result.stderr
    assert db.calls == [] and storage.requests == []


def test_pressing_enter_declines(db, storage, files, monkeypatch):
    monkeypatch.setattr(upload_module, "_interactive", lambda: True)
    result = _upload(files, "--species", "Arabidopsis", input="\n")
    assert result.exit_code != 0
    assert db.calls == []


def test_without_a_terminal_it_refuses_before_signing_in(env, files, monkeypatch):
    monkeypatch.setattr(upload_module, "_interactive", lambda: False)
    monkeypatch.setattr(_session, "connect", lambda _p: pytest.fail("it signed in"))
    result = _upload(files, "--species", "Arabidopsis")
    assert result.exit_code != 0
    assert "--yes" in result.output


def test_a_new_genome_without_a_species_is_refused(db, storage, files):
    result = _upload(files, "--yes")
    assert result.exit_code != 0
    assert "--species" in result.output
    assert db.calls == [] and storage.requests == []


def test_an_unknown_species_is_refused(db, storage, files):
    result = _upload(files, "--species", "Maize", "--yes")
    assert result.exit_code != 0
    assert "no species named 'Maize'" in result.output
    assert db.calls == []


def test_a_different_species_for_an_existing_genome_is_refused(env, storage, files):
    env["client"] = db = GenomeDB(storage, [{"id": 1, "name": "tair10_araport11", "species_id": 7}])
    result = _upload(files, "--species", "Rice", "--yes")
    assert result.exit_code != 0
    assert "belongs to species 7" in result.output
    assert db.calls == []


@pytest.mark.parametrize(
    "name", ["a__b", "-lead", "has space", "g" * 65, "../etc", "TAIR10", "Tair10", "tair10.v2"]
)
def test_a_bad_genome_name_is_refused(env, files, monkeypatch, name):
    monkeypatch.setattr(_session, "connect", lambda _p: pytest.fail("it signed in"))
    result = _upload(files, "--species", "Arabidopsis", "--yes", name=name)
    assert result.exit_code != 0
    assert "NAME must be" in result.output


def test_a_file_that_is_not_a_fasta_is_refused_before_signing_in(env, files, monkeypatch):
    monkeypatch.setattr(_session, "connect", lambda _p: pytest.fail("it signed in"))
    files[0].write_text("Chr1\tnot a fasta\n")
    result = _upload(files, "--species", "Arabidopsis", "--yes")
    assert result.exit_code != 0
    assert "TAIR10.fa is not a FASTA file" in result.output


def test_a_gtf_with_the_wrong_columns_is_refused(env, files, monkeypatch):
    monkeypatch.setattr(_session, "connect", lambda _p: pytest.fail("it signed in"))
    files[1].write_text("#comment\nChr1\tAraport11\tgene\t1\t10\n")
    result = _upload(files, "--species", "Arabidopsis", "--yes")
    assert result.exit_code != 0
    assert "5 tab-separated columns, not 9" in result.output


def test_a_damaged_gzip_file_is_refused(db, storage, tmp_path, files):
    broken = tmp_path / "broken.fa.gz"
    broken.write_bytes(gzip.compress(FASTA * 50)[:-20])
    result = _upload((broken, files[1]), "--species", "Arabidopsis", "--yes")
    assert result.exit_code != 0
    assert "damaged gzip file" in result.output
    assert db.calls == []


def test_a_file_over_the_limit_is_refused_before_starting(db, storage, files, monkeypatch):
    monkeypatch.setattr(_files, "MAX_OBJECT_BYTES", 10)
    result = _upload(files, "--species", "Arabidopsis", "--yes")
    assert result.exit_code != 0
    assert "at most" in result.output and "Nothing was sent." in result.output
    assert db.calls == []


@pytest.mark.parametrize("role", ["bloom_user", "bloom_admin", "bloom_workflows"])
def test_only_a_writer_login_can_upload(env, db, storage, files, role):
    env["role"] = role
    result = _upload(files, "--species", "Arabidopsis", "--yes")
    assert result.exit_code != 0
    assert "writer login" in result.output
    assert db.calls == []


# --- failures after the version is started ---------------------------------------------------


def test_an_upload_that_fails_abandons_the_version(db, storage, files):
    storage.drop_after = 0
    result = _upload(files, "--species", "Arabidopsis", "--yes")
    assert result.exit_code != 0
    assert db.versions[0]["status"] == "abandoned"
    assert "tair10_araport11 v1 was abandoned" in result.stderr
    assert "finish_genome_version" not in db.calls


def test_a_finish_whose_answer_was_lost_is_reported_ready(db, storage, files):
    db.lose_finish_answer = True
    result = _upload(files, "--species", "Arabidopsis", "--yes")
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "tair10_araport11 v1: ready"
    assert db.versions[0]["status"] == "ready"
    assert "abandoned" not in result.stderr and "uploading" not in result.stderr


def test_a_version_that_cannot_be_abandoned_reports_its_real_status(db, storage, files):
    storage.drop_after = 0
    db.refuse["abandon_genome_version"] = "no"
    result = _upload(files, "--species", "Arabidopsis", "--yes")
    assert result.exit_code != 0
    assert "could not be marked abandoned; it is uploading" in result.stderr


def test_a_refused_finish_abandons_the_version(db, storage, files):
    db.refuse["finish_genome_version"] = "genome.fa.gz is 10 bytes in storage, not 11"
    result = _upload(files, "--species", "Arabidopsis", "--yes")
    assert result.exit_code != 0
    assert "Could not finish the upload" in result.output
    assert db.versions[0]["status"] == "abandoned"


def test_a_refused_start_sends_nothing(db, storage, files):
    db.refuse["start_genome_version"] = "genome tair10_araport11 already has a different description"
    result = _upload(files, "--species", "Arabidopsis", "--description", "x", "--yes")
    assert result.exit_code != 0
    assert "Could not start the upload" in result.output
    assert storage.requests == []
    assert "abandon_genome_version" not in db.calls


def test_a_session_that_runs_out_mid_upload_signs_in_again_and_resumes(
    db, storage, files, monkeypatch
):
    storage.expire_after = 0
    signed_in = []
    original = _session.connect

    def connect(profile):
        signed_in.append(profile)
        if len(signed_in) > 1:
            storage.expire_after = None  # the new token is accepted
        return original(profile)

    monkeypatch.setattr(_session, "connect", connect)
    result = _upload(files, "--species", "Arabidopsis", "--yes")
    assert result.exit_code == 0, result.output
    assert len(signed_in) == 2  # the first sign-in, then once more after the expiry
    assert gzip.decompress(_stored(storage, "tair10_araport11/v1/genome.fa.gz")) == FASTA


# --- preparing files ------------------------------------------------------------------------


def test_gzipping_the_same_file_twice_gives_the_same_bytes(tmp_path, files):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    first = _files.prepare(files[0], tmp_path / "a", "genome.fa.gz")
    second = _files.prepare(files[0], tmp_path / "b", "genome.fa.gz")
    assert first.sha256 == second.sha256


def test_a_fasta_with_leading_blank_lines_is_accepted(tmp_path):
    path = tmp_path / "g.fa"
    path.write_bytes(b"\r\n\n>Chr1\r\nACGT\r\n")
    _files.check_fasta(path)


def test_an_empty_gtf_is_refused(tmp_path):
    path = tmp_path / "a.gtf"
    path.write_text("#only comments\n")
    with pytest.raises(_files.FileProblem, match="has no records"):
        _files.check_gtf(path)
