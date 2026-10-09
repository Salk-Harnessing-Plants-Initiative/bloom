"""bloomctl genome download — a version's FASTA and GTF, checked against Bloom's record, against
fake storage and a fake database."""

import gzip
import hashlib

import pytest
from click.testing import CliRunner
from scrna_fake_db import FakeClient

from bloomctl.cli import cli

FASTA = b">Chr1\nACGTACGTNNACGT\n>Chr2\nGGCC\n"
GTF = b"Chr1\tAraport11\tgene\t1\t10\t.\t+\t.\tgene_id \"AT1G01010\";\n"
FASTA_GZ = gzip.compress(FASTA, mtime=0)
GTF_GZ = gzip.compress(GTF, mtime=0)
GENOME = {"id": 1, "name": "tair10_araport11", "species_id": 7, "description": None}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _version(number, status="ready", reason=None):
    return {
        "id": 100 + number, "genome_id": 1, "version": number, "status": status,
        "fasta_path": f"tair10_araport11/v{number}/genome.fa.gz",
        "gtf_path": f"tair10_araport11/v{number}/genes.gtf.gz",
        "fasta_sha256": _sha(FASTA_GZ), "fasta_bytes": len(FASTA_GZ),
        "gtf_sha256": _sha(GTF_GZ), "gtf_bytes": len(GTF_GZ),
        "withdrawn_reason": reason,
    }


@pytest.fixture
def genome(env, storage):
    """v1 withdrawn, v2 ready, v3 still uploading; v2's files stored."""
    versions = [_version(1, "withdrawn", "wrong annotation"), _version(2), _version(3, "uploading")]
    env["client"] = FakeClient({
        "genome_references": [GENOME], "genome_reference_versions": versions,
    })
    for number in (1, 2):
        storage.objects[f"genome-references/tair10_araport11/v{number}/genome.fa.gz"] = FASTA_GZ
        storage.objects[f"genome-references/tair10_araport11/v{number}/genes.gtf.gz"] = GTF_GZ
    # The fake's own rows, so a test can change a version's status.
    return env["client"].tables["genome_reference_versions"]


def _run(*args):
    return CliRunner().invoke(cli, ["genome", "download", *args])


def _reads(storage):
    return [r for r in storage.requests if "/object/authenticated/" in r.url.path]


# --- which version ----------------------------------------------------------------------------


def test_the_newest_ready_version_is_downloaded_by_default(genome, storage, tmp_path):
    result = _run("tair10_araport11", "--to", str(tmp_path))
    assert result.exit_code == 0, result.output
    assert (tmp_path / "genome.fa.gz").read_bytes() == FASTA_GZ
    assert (tmp_path / "genes.gtf.gz").read_bytes() == GTF_GZ
    assert "tair10_araport11 v2" in result.stdout
    assert all("/v2/" in r.url.path for r in _reads(storage)), "never the uploading v3"


def test_a_named_ready_version_is_downloaded(genome, storage, tmp_path):
    genome[0]["status"] = "ready"
    result = _run("tair10_araport11.v1", "--to", str(tmp_path))
    assert result.exit_code == 0, result.output
    assert "tair10_araport11 v1" in result.stdout


@pytest.mark.parametrize("named, words", [
    ("tair10_araport11.v1", "v1 was withdrawn: wrong annotation"),
    ("tair10_araport11.v3", "v3 is still uploading"),
])
def test_a_version_that_isnt_ready_is_refused_and_nothing_written(genome, tmp_path, named, words):
    result = _run(named, "--to", str(tmp_path))
    assert result.exit_code == 1
    assert words in result.output
    assert list(tmp_path.iterdir()) == []


def test_an_abandoned_version_is_refused(genome, tmp_path):
    genome[2]["status"] = "abandoned"
    result = _run("tair10_araport11.v3", "--to", str(tmp_path))
    assert result.exit_code == 1 and "v3's upload failed" in result.output


def test_a_version_that_doesnt_exist_is_refused(genome, tmp_path):
    result = _run("tair10_araport11.v9", "--to", str(tmp_path))
    assert result.exit_code == 1 and "has no v9" in result.output


def test_an_unknown_genome_is_refused(genome, tmp_path):
    result = _run("rice_v7", "--to", str(tmp_path))
    assert result.exit_code == 1 and "No genome is named 'rice_v7'" in result.output


def test_a_genome_with_no_ready_version_is_refused(genome, tmp_path):
    genome[1]["status"] = "abandoned"
    result = _run("tair10_araport11", "--to", str(tmp_path))
    assert result.exit_code == 1 and "has no ready version" in result.output
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("bad", ["Tair10", "a/b", "x__y"])
def test_a_name_bloom_cannot_hold_is_a_usage_error(genome, bad):
    assert _run(bad).exit_code == 2


# --- checked against Bloom's record ------------------------------------------------------------


def test_a_file_that_doesnt_match_bloom_is_removed_and_fails(genome, storage, tmp_path):
    storage.objects["genome-references/tair10_araport11/v2/genes.gtf.gz"] = gzip.compress(b"x")
    result = _run("tair10_araport11", "--to", str(tmp_path))
    assert result.exit_code == 1
    assert "genes.gtf.gz" in result.output and "doesn't match" in result.output
    assert not (tmp_path / "genes.gtf.gz").exists()
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".")], "no partial left"


def test_a_file_missing_from_storage_says_so(genome, storage, tmp_path):
    del storage.objects["genome-references/tair10_araport11/v2/genome.fa.gz"]
    result = _run("tair10_araport11", "--to", str(tmp_path))
    assert result.exit_code == 1 and "isn't stored" in result.output
    assert not (tmp_path / "genome.fa.gz").exists()


# --- unzipped, for building a reference ------------------------------------------------------


def test_unzip_writes_the_plain_files_after_the_check(genome, tmp_path):
    result = _run("tair10_araport11", "--to", str(tmp_path), "--unzip")
    assert result.exit_code == 0, result.output
    assert (tmp_path / "genome.fa").read_bytes() == FASTA
    assert (tmp_path / "genes.gtf").read_bytes() == GTF
    assert not (tmp_path / "genome.fa.gz").exists()


def test_unzip_of_a_mismatched_file_writes_nothing(genome, storage, tmp_path):
    storage.objects["genome-references/tair10_araport11/v2/genome.fa.gz"] = gzip.compress(b">x\n")
    result = _run("tair10_araport11", "--to", str(tmp_path), "--unzip")
    assert result.exit_code == 1
    assert not (tmp_path / "genome.fa").exists()


# --- safe to run again -----------------------------------------------------------------------


def test_a_retry_keeps_the_right_file_and_fetches_only_the_missing_one(genome, storage, tmp_path):
    (tmp_path / "genome.fa.gz").write_bytes(FASTA_GZ)
    result = _run("tair10_araport11", "--to", str(tmp_path))
    assert result.exit_code == 0, result.output
    assert [r.url.path.rsplit("/", 1)[-1] for r in _reads(storage)] == ["genes.gtf.gz"]


def test_an_unzipped_retry_downloads_nothing(genome, storage, tmp_path):
    assert _run("tair10_araport11", "--to", str(tmp_path), "--unzip").exit_code == 0
    before = len(_reads(storage))
    result = _run("tair10_araport11", "--to", str(tmp_path), "--unzip")
    assert result.exit_code == 0, result.output
    assert len(_reads(storage)) == before


@pytest.mark.parametrize("unzip, name", [(False, "genes.gtf.gz"), (True, "genes.gtf")])
def test_a_different_file_at_the_destination_is_never_overwritten(genome, tmp_path, unzip, name):
    (tmp_path / name).write_bytes(b"someone else's file")
    result = _run("tair10_araport11", "--to", str(tmp_path), *(["--unzip"] if unzip else []))
    assert result.exit_code == 1 and name in result.output
    assert (tmp_path / name).read_bytes() == b"someone else's file"


# --- the exact version, for the workflow ------------------------------------------------------


def test_the_version_file_names_the_exact_version(genome, tmp_path):
    version_file = tmp_path / "VERSION"
    result = _run("tair10_araport11", "--to", str(tmp_path / "ref"), "--unzip",
                  "--version-file", str(version_file))
    assert result.exit_code == 0, result.output
    assert version_file.read_text() == "tair10_araport11.v2\n"


def test_the_pipelines_login_can_download(genome, env, tmp_path):
    env["role"] = "bloom_workflows"
    assert _run("tair10_araport11", "--to", str(tmp_path)).exit_code == 0


def test_the_files_are_named_as_cell_ranger_expects(genome, tmp_path):
    assert _run("tair10_araport11", "--to", str(tmp_path)).exit_code == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == ["genes.gtf.gz", "genome.fa.gz"]


def test_an_unzip_that_stops_part_way_leaves_nothing(genome, storage, tmp_path):
    cut = FASTA_GZ[: len(FASTA_GZ) // 2]
    storage.objects["genome-references/tair10_araport11/v2/genome.fa.gz"] = cut
    genome[1].update(fasta_sha256=_sha(cut), fasta_bytes=len(cut))
    result = _run("tair10_araport11", "--to", str(tmp_path), "--unzip")
    assert result.exit_code == 1 and "is not a whole gzipped file" in result.output
    assert not (tmp_path / "genome.fa").exists()
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".genome")], "no partial left"
