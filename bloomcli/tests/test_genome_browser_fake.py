"""The fake Bloom behind the `genome index` tests refuses what the browser-build migrations
refuse, so a request real Bloom would turn down can't pass the tests."""

import genome_browser_fake
import pytest
from genome_browser_fake import stock
from postgrest import APIError

from bloomctl.genome import _index


@pytest.fixture
def db(env, storage):
    return stock(env, storage)


@pytest.mark.parametrize("gene", [
    {"gene_name": ""}, {"gene_name": " "}, {"contig": "Chr 1"}, {"gene_end": 2},
    {"strand": "?"}, {"gene_start": "3"},
])
def test_the_fake_refuses_genes_bloom_refuses(db, gene):
    build = db.add_build("uploading")
    good = {"gene_id": "G", "gene_name": None, "contig": "Chr1", "gene_start": 3,
            "gene_end": 9, "strand": "+"}
    with pytest.raises(APIError, match="check constraint"):
        db.add_genome_browser_genes(build["id"], [{**good, **gene}])


@pytest.mark.parametrize("files", [
    lambda f: {**f, "extra": f["fasta"]},
    lambda f: {**f, "fasta": {**f["fasta"], "bytes": str(f["fasta"]["bytes"])}},
    lambda f: {**f, "fasta": {**f["fasta"], "sha256": "A" * 64}},
    lambda f: {**f, "fasta": {**f["fasta"], "note": "x"}},
])
def test_the_fake_refuses_a_files_record_bloom_refuses(files):
    good = {role: {"sha256": "a" * 64, "bytes": 10} for role in _index.ROLES}
    assert genome_browser_fake.files_ok(good)
    assert not genome_browser_fake.files_ok(files(good))


def test_the_fake_refuses_a_batch_that_repeats_a_gene(db):
    build = db.add_build("uploading")
    gene = {"gene_id": "G", "gene_name": None, "contig": "Chr1", "gene_start": 3,
            "gene_end": 9, "strand": "+"}
    with pytest.raises(APIError, match="appears more than once"):
        db.add_genome_browser_genes(build["id"], [gene, gene])
