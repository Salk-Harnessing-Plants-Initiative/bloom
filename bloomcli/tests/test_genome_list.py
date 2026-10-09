"""bloomctl genome list — every genome with its species and versions, against a fake database."""

import json

import pytest
from click.testing import CliRunner
from scrna_fake_db import FakeClient

from bloomctl.cli import cli

SPECIES = [{"id": 7, "common_name": "Arabidopsis"}, {"id": 8, "common_name": "Rice"}]
GENOMES = [
    {"id": 1, "name": "tair10_araport11", "species_id": 7, "description": "Col-0"},
    {"id": 2, "name": "irgsp1", "species_id": 8, "description": None},
]
VERSIONS = [
    {"genome_id": 1, "version": 1, "status": "withdrawn", "assembly": "TAIR10",
     "annotation": "Araport11-old", "fasta_bytes": 37_000_000, "gtf_bytes": 9_000_000,
     "ready_at": "2026-10-01T10:00:00Z", "withdrawn_reason": "wrong annotation"},
    {"genome_id": 1, "version": 2, "status": "ready", "assembly": "TAIR10",
     "annotation": "Araport11", "fasta_bytes": 37_500_000, "gtf_bytes": 9_100_000,
     "ready_at": "2026-10-08T12:00:00Z", "withdrawn_reason": None},
    {"genome_id": 1, "version": 3, "status": "uploading", "assembly": None, "annotation": None,
     "fasta_bytes": None, "gtf_bytes": None, "ready_at": None, "withdrawn_reason": None},
    {"genome_id": 2, "version": 1, "status": "abandoned", "assembly": "IRGSP-1.0",
     "annotation": None, "fasta_bytes": None, "gtf_bytes": None, "ready_at": None,
     "withdrawn_reason": None},
]


@pytest.fixture
def genomes(env):
    env["client"] = FakeClient({
        "species": SPECIES, "genome_references": GENOMES,
        "genome_reference_versions": VERSIONS,
    })
    return env["client"]


def _run(*args):
    # Wide enough that the table doesn't fold a name across lines.
    return CliRunner().invoke(cli, ["genome", "list", *args], env={"COLUMNS": "200"})


def test_every_version_is_listed_with_its_genome_species_and_status(genomes):
    result = _run()
    assert result.exit_code == 0, result.output
    for text in ("tair10_araport11", "Arabidopsis", "irgsp1", "Rice", "withdrawn", "ready",
                 "uploading", "abandoned", "Araport11", "TAIR10"):
        assert text in result.output


def test_json_has_one_record_per_version_in_genome_then_version_order(genomes):
    result = _run("--output", "json")
    assert result.exit_code == 0, result.output
    records = json.loads(result.stdout)
    assert [(r["genome"], r["version"]) for r in records] == [
        ("irgsp1", 1), ("tair10_araport11", 1), ("tair10_araport11", 2), ("tair10_araport11", 3),
    ]
    ready = records[2]
    assert ready == {
        "genome": "tair10_araport11", "species": "Arabidopsis", "version": 2,
        "status": "ready", "assembly": "TAIR10", "annotation": "Araport11",
        "fasta_bytes": 37_500_000, "gtf_bytes": 9_100_000, "ready_at": "2026-10-08T12:00:00Z",
        "withdrawn_reason": None,
    }
    assert records[1]["withdrawn_reason"] == "wrong annotation"


def test_csv_has_a_header_and_one_row_per_version(genomes):
    result = _run("--output", "csv")
    assert result.exit_code == 0, result.output
    lines = result.stdout.strip().splitlines()
    assert lines[0].startswith("genome,species,version,status")
    assert len(lines) == 1 + len(VERSIONS)


def test_a_genome_with_no_versions_yet_is_still_listed(env):
    env["client"] = FakeClient({
        "species": SPECIES, "genome_references": [GENOMES[1]], "genome_reference_versions": [],
    })
    result = _run("--output", "json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == [{
        "genome": "irgsp1", "species": "Rice", "version": None, "status": None,
        "assembly": None, "annotation": None, "fasta_bytes": None, "gtf_bytes": None,
        "ready_at": None, "withdrawn_reason": None,
    }]


def test_no_genomes_says_so(env):
    env["client"] = FakeClient({
        "species": SPECIES, "genome_references": [], "genome_reference_versions": [],
    })
    result = _run()
    assert result.exit_code == 0, result.output
    assert "Bloom holds no reference genomes yet" in result.output


def test_any_login_can_list(genomes, env):
    env["role"] = "bloom_user"
    assert _run().exit_code == 0
