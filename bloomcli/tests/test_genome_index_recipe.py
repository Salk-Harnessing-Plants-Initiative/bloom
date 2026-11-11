"""The browser-file recipe behind `bloomctl genome index`: the five files, read back by region,
and what htslib refuses."""

import gzip

import pysam
import pytest

from bloomctl.genome import _files, _index

FASTA = b">Chr1 CHROMOSOME dumped from ADB\nACGTACGTAC\nGGGGCCCCAA\n>Chr2\nTTTTAAAAGG\n"
GTF = (
    "#!genome-build TAIR10\n"
    "#a plain comment\n"
    'Chr1\tAraport11\tgene\t3\t9\t.\t+\t.\tgene_id "AT1G01010"; gene_name "NAC001";\n'
    'Chr1\tAraport11\texon\t3\t5\t.\t+\t.\tgene_id "AT1G01010"; transcript_id "t1";\n'
    'Chr1\tAraport11\texon\t12\t15\t.\t-\t.\tgene_id "AT1G01020"; transcript_id "t2";\n'
    'Chr1\tAraport11\texon\t17\t18\t.\t-\t.\tgene_id "AT1G01020"; transcript_id "t2";\n'
    'Chr2\tAraport11\tgene\t1\t4\t.\t-\t.\tgene_id "AT2G01010"; gene_name "";\n'
)


def _gz(path, data):
    path.write_bytes(gzip.compress(data if isinstance(data, bytes) else data.encode()))
    return path


@pytest.fixture
def sources(tmp_path):
    return _gz(tmp_path / "genome.fa.gz", FASTA), _gz(tmp_path / "genes.gtf.gz", GTF)


def _build(tmp_path, fasta, gtf):
    out = tmp_path / "out"
    out.mkdir(parents=True, exist_ok=True)
    return _index.build(fasta, gtf, out, tmp_path / "work")


def _starts(built, contig):
    return [line.split("\t")[3] for line in
            pysam.TabixFile(str(built.files["gtf"].path)).fetch(contig)]


def test_the_five_files_are_made_and_can_be_read_by_region(tmp_path, sources):
    built = _build(tmp_path, *sources)
    assert sorted(built.files) == sorted(_index.ROLES)
    for role, prepared in built.files.items():
        assert prepared.path.name == _index.ROLES[role]
        assert prepared.size == prepared.path.stat().st_size > 0
        assert prepared.sha256 == _files.sha256_of(prepared.path)
    fasta = pysam.FastaFile(str(built.files["fasta"].path))
    assert list(fasta.references) == ["Chr1", "Chr2"]
    assert fasta.fetch("Chr1", 10, 14) == "GGGG"
    gtf = pysam.TabixFile(str(built.files["gtf"].path))
    assert [line.split("\t")[3] for line in gtf.fetch("Chr1", 10, 20)] == ["12", "17"]


def test_the_genes_are_returned_with_the_files(tmp_path, sources):
    assert [g["gene_id"] for g in _build(tmp_path, *sources).genes] == [
        "AT1G01010", "AT1G01020", "AT2G01010"]


def test_an_unsorted_gtf_is_written_in_position_order(tmp_path, sources):
    lines = GTF.splitlines(keepends=True)
    shuffled = lines[:2] + [lines[6], lines[4], lines[2], lines[5], lines[3]]
    built = _build(tmp_path, sources[0], _gz(tmp_path / "shuffled.gtf.gz", "".join(shuffled)))
    assert _starts(built, "Chr1") == ["3", "3", "12", "17"]
    assert _starts(built, "Chr2") == ["1"]


def test_the_header_lines_stay_and_plain_comments_go(tmp_path, sources):
    built = _build(tmp_path, *sources)
    with gzip.open(built.files["gtf"].path, "rt") as fh:
        text = fh.read()
    assert text.startswith("#!genome-build TAIR10\n")
    assert "#a plain comment" not in text
    assert len(text.splitlines()) == 6


def test_the_same_input_gives_the_same_bytes(tmp_path, sources):
    first = _build(tmp_path / "a", *sources)
    second = _build(tmp_path / "b", *sources)
    assert ({r: f.sha256 for r, f in first.files.items()}
            == {r: f.sha256 for r, f in second.files.items()})


def test_a_fasta_on_one_line_per_contig_is_fine(tmp_path, sources):
    fasta = _gz(tmp_path / "long.fa.gz", b">Chr1\n" + b"ACGT" * 5 + b"\n>Chr2\nTTTTAAAAGG\n")
    built = _build(tmp_path, fasta, sources[1])
    assert pysam.FastaFile(str(built.files["fasta"].path)).get_reference_length("Chr1") == 20


def test_a_fasta_htslib_cannot_index_is_refused_cleanly(tmp_path, sources):
    uneven = _gz(tmp_path / "uneven.fa.gz",
                 b">Chr1\nACGTACGTACGG\nGG\nCCCCAA\n>Chr2\nTTTTAAAAGG\n")
    with pytest.raises(_index.IndexProblem, match="htslib could not index"):
        _build(tmp_path, uneven, sources[1])


def test_a_gtf_problem_is_refused_before_anything_is_written(tmp_path, sources):
    bad = _gz(tmp_path / "bad.gtf.gz", GTF + 'chr9\tx\tgene\t1\t4\t.\t+\t.\tgene_id "X";\n')
    with pytest.raises(_index.IndexProblem, match="chr9"):
        _build(tmp_path, sources[0], bad)


@pytest.mark.parametrize("role", list(_index.ROLES))
def test_a_derived_file_over_the_limit_is_refused_with_its_size(tmp_path, monkeypatch, role):
    path = tmp_path / _index.ROLES[role]
    path.write_bytes(b"x" * 30)
    monkeypatch.setattr(_files, "MAX_OBJECT_BYTES", 20)
    with pytest.raises(_index.IndexProblem, match=rf"{_index.ROLES[role]} is .* at most"):
        _index._prepared(path)


def test_genes_are_sent_in_batches_of_at_most_5000():
    genes = [{"gene_id": f"G{i}"} for i in range(12001)]
    assert [len(b) for b in _index.batches(genes)] == [5000, 5000, 2001]


def test_the_tools_used_are_named_with_their_versions():
    from bloomctl import __version__

    assert _index.tool_versions() == {"bloomctl": __version__, "pysam": pysam.__version__,
                                      "htslib": pysam.version.__htslib_version__}
