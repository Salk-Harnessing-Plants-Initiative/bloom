"""The browser-file recipe behind `bloomctl genome index`: the five files, read back by region,
the genes written as GFF3, and what htslib refuses."""

import gzip
from pathlib import Path

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


def _rows(built, contig, *region):
    return [line.split("\t") for line in
            pysam.TabixFile(str(built.files["gff3"].path)).fetch(contig, *region)]


def _text(built):
    with gzip.open(built.files["gff3"].path, "rt") as fh:
        return fh.read()


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
    assert [(r[2], r[3]) for r in _rows(built, "Chr1", 10, 20)] == [
        ("gene", "12"), ("transcript", "12"), ("exon", "12"), ("exon", "17")]


def test_the_genes_are_written_as_gff3_with_each_line_linked_to_its_parent(tmp_path, sources):
    assert _text(_build(tmp_path, *sources)) == (
        "##gff-version 3\n"
        "#!genome-build TAIR10\n"
        "Chr1\tAraport11\tgene\t3\t9\t.\t+\t.\tID=gene:AT1G01010;Name=NAC001;"
        "gene_id=AT1G01010;gene_name=NAC001\n"
        "Chr1\tAraport11\ttranscript\t3\t5\t.\t+\t.\tID=transcript:t1;"
        "Parent=gene:AT1G01010;Name=t1;transcript_id=t1\n"
        "Chr1\tAraport11\texon\t3\t5\t.\t+\t.\tParent=transcript:t1;gene_id=AT1G01010;"
        "transcript_id=t1\n"
        "Chr1\tAraport11\tgene\t12\t18\t.\t-\t.\tID=gene:AT1G01020;Name=AT1G01020;"
        "gene_id=AT1G01020\n"
        "Chr1\tAraport11\ttranscript\t12\t18\t.\t-\t.\tID=transcript:t2;"
        "Parent=gene:AT1G01020;Name=t2;transcript_id=t2\n"
        "Chr1\tAraport11\texon\t12\t15\t.\t-\t.\tParent=transcript:t2;gene_id=AT1G01020;"
        "transcript_id=t2\n"
        "Chr1\tAraport11\texon\t17\t18\t.\t-\t.\tParent=transcript:t2;gene_id=AT1G01020;"
        "transcript_id=t2\n"
        "Chr2\tAraport11\tgene\t1\t4\t.\t-\t.\tID=gene:AT2G01010;Name=AT2G01010;"
        "gene_id=AT2G01010\n"
    )


def test_every_parent_is_a_line_of_the_file(tmp_path, sources):
    rows = [line.split("\t") for line in _text(_build(tmp_path, *sources)).splitlines()
            if not line.startswith("#")]
    fields = [dict(a.split("=", 1) for a in row[8].split(";")) for row in rows]
    ids = [f["ID"] for f in fields if "ID" in f]
    assert len(ids) == len(set(ids))
    assert {f["Parent"] for f in fields if "Parent" in f} <= set(ids)


def test_the_genes_are_returned_with_the_files(tmp_path, sources):
    assert [g["gene_id"] for g in _build(tmp_path, *sources).genes] == [
        "AT1G01010", "AT1G01020", "AT2G01010"]


def test_an_unsorted_gtf_is_written_in_position_order(tmp_path, sources):
    lines = GTF.splitlines(keepends=True)
    shuffled = lines[:2] + [lines[6], lines[4], lines[2], lines[5], lines[3]]
    built = _build(tmp_path, sources[0], _gz(tmp_path / "shuffled.gtf.gz", "".join(shuffled)))
    assert [(r[2], r[3]) for r in _rows(built, "Chr1")] == [
        ("gene", "3"), ("transcript", "3"), ("exon", "3"),
        ("gene", "12"), ("transcript", "12"), ("exon", "12"), ("exon", "17")]
    assert [r[3] for r in _rows(built, "Chr2")] == ["1"]


def test_the_header_lines_stay_and_plain_comments_go(tmp_path, sources):
    text = _text(_build(tmp_path, *sources))
    assert text.startswith("##gff-version 3\n#!genome-build TAIR10\n")
    assert "#a plain comment" not in text


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


# --- NCBI RefSeq's trans-spliced nad1, built and read back ---------------------------------

NAD1 = (Path(__file__).parent / "fixtures" / "refseq_nad1.gtf").read_bytes()
# The mitochondrion's length in TAIR10.1; its bases don't matter here.
MITOCHONDRION = b">NC_037304.1 Arabidopsis thaliana mitochondrion\n" + b"ACGT" * 91_952 + b"\n"


@pytest.fixture
def nad1(tmp_path):
    return _build(tmp_path, _gz(tmp_path / "mt.fa.gz", MITOCHONDRION),
                  _gz(tmp_path / "nad1.gtf.gz", NAD1))


def test_refseq_nad1_reads_back_by_region_as_one_gene(nad1):
    rows = _rows(nad1, "NC_037304.1", 82_900, 83_400)
    assert [(r[2], r[3], r[4], r[6]) for r in rows[:2]] == [
        ("gene", "58315", "234132", "+"), ("transcript", "58315", "234132", ".")]
    assert {r[2] for r in rows[2:]} == {"exon", "CDS", "start_codon"}
    assert nad1.genes == [{"gene_id": "DA397_mgp34", "gene_name": "nad1",
                           "contig": "NC_037304.1", "gene_start": 58315, "gene_end": 234132,
                           "strand": "+"}]


def test_refseq_nad1_s_parents_cover_their_children_and_exist(nad1):
    rows = _rows(nad1, "NC_037304.1")
    fields = [dict(a.split("=", 1) for a in row[8].rstrip("\n").split(";")) for row in rows]
    span = {f["ID"]: (int(r[3]), int(r[4])) for r, f in zip(rows, fields) if "ID" in f}
    for row, f in zip(rows, fields):
        if "Parent" in f:
            start, end = span[f["Parent"]]
            assert start <= int(row[3]) and int(row[4]) <= end
    assert [r[6] for r in rows if r[2] == "exon"] == ["+", "+", "-", "+", "+"]
    assert any("DA397_mgp34" in n for n in nad1.notes)


# --- refused or said ------------------------------------------------------------------------


def test_a_contig_too_long_for_the_gene_index_is_refused_up_front(tmp_path, sources,
                                                                  monkeypatch):
    monkeypatch.setattr(_index, "MAX_CONTIG_BASES", 15)
    with pytest.raises(_index.IndexProblem, match="contigs over 15 bases.*: Chr1$"):
        _build(tmp_path, *sources)
    assert not (tmp_path / "out" / _index.ROLES["fasta"]).exists()


def test_a_fasta_htslib_reads_differently_names_the_contig(tmp_path, sources, monkeypatch):
    monkeypatch.setattr(_index, "_fai_lengths", lambda _fai: {"Chr1": 20, "Chr2": 9})
    with pytest.raises(_index.IndexProblem, match="contig 'Chr2' as 9, the FASTA check as 10"):
        _build(tmp_path, *sources)


def test_header_lines_are_checked_and_lose_markup(tmp_path, sources):
    gtf = _gz(tmp_path / "h.gtf.gz", "#!note <b>x</b>\n" + GTF)
    assert _text(_build(tmp_path, sources[0], gtf)).startswith(
        "##gff-version 3\n#!note bx/b\n")
    bad = _gz(tmp_path / "bad.gtf.gz", b"#!note \xff\n" + GTF.encode())
    with pytest.raises(_index.IndexProblem, match="line 1 of the GTF is not UTF-8"):
        _build(tmp_path, sources[0], bad)


def test_what_is_worth_saying_comes_back_with_the_files(tmp_path, sources):
    gtf = _gz(tmp_path / "n.gtf.gz", GTF + 'Chr2\tx\texon\t1\t4\t.\t+\t.\ttranscript_id "t9";\n')
    assert _build(tmp_path, sources[0], gtf).notes == [
        "1 GTF lines have no gene_id and are left out."]


def test_bgzip_stops_at_the_unpacked_limit(tmp_path, monkeypatch):
    from bloomctl.genome import _gtf

    monkeypatch.setattr(_gtf, "MAX_UNPACKED_BYTES", 10)
    monkeypatch.setattr(_files, "READ_BYTES", 8)
    with pytest.raises(_gtf.GtfProblem, match="unpacks to more than"):
        _index._bgzip_fasta(_gz(tmp_path / "f.fa.gz", FASTA), tmp_path / "out.fa.gz")
