"""The browser-file recipe behind `bloomctl genome index`: the five files, the GTF's genes, and
what it refuses before anything is sent to Bloom."""

import builtins
import gzip

import click
import pytest

from bloomctl.genome import _files, _index

pysam = pytest.importorskip("pysam")

FASTA = b">Chr1 CHROMOSOME dumped from ADB\nACGTACGTAC\nGGGGCCCCAA\n>Chr2\nTTTTAAAAGG\n"
# Sorted: Chr1 by start, then Chr2.
GTF = (
    "#!genome-build TAIR10\n"
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
    out.mkdir()
    return _index.build(fasta, gtf, out, tmp_path / "work")


# --- the five files -------------------------------------------------------------------------


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


def test_a_sorted_gtf_is_recorded_as_sorted(tmp_path, sources):
    assert _build(tmp_path, *sources).gtf_was_sorted is True


def test_an_unsorted_gtf_is_sorted_before_indexing(tmp_path, sources):
    lines = GTF.splitlines(keepends=True)
    shuffled = lines[:1] + [lines[5], lines[3], lines[1], lines[4], lines[2]]
    built = _build(tmp_path, sources[0], _gz(tmp_path / "shuffled.gtf.gz", "".join(shuffled)))
    assert built.gtf_was_sorted is False
    gtf = pysam.TabixFile(str(built.files["gtf"].path))
    assert [line.split("\t")[3] for line in gtf.fetch("Chr1")] == ["3", "3", "12", "17"]
    assert len(list(gtf.fetch("Chr2"))) == 1


def test_a_derived_file_over_the_limit_is_refused_with_its_size(tmp_path, sources, monkeypatch):
    monkeypatch.setattr(_files, "MAX_OBJECT_BYTES", 20)
    with pytest.raises(_index.IndexProblem, match=r"genome\.fa\.gz is .* at most"):
        _build(tmp_path, *sources)


# --- genes ----------------------------------------------------------------------------------


def test_genes_come_from_gene_lines_or_their_exons(tmp_path, sources):
    assert _build(tmp_path, *sources).genes == [
        {"gene_id": "AT1G01010", "gene_name": "NAC001", "contig": "Chr1",
         "gene_start": 3, "gene_end": 9, "strand": "+"},
        {"gene_id": "AT1G01020", "gene_name": None, "contig": "Chr1",
         "gene_start": 12, "gene_end": 18, "strand": "-"},
        {"gene_id": "AT2G01010", "gene_name": None, "contig": "Chr2",
         "gene_start": 1, "gene_end": 4, "strand": "-"},
    ]


def test_a_gene_whose_exons_disagree_on_strand_has_no_strand(tmp_path, sources):
    gtf = GTF.replace('17\t18\t.\t-', '17\t18\t.\t+')
    genes = _build(tmp_path, sources[0], _gz(tmp_path / "mixed.gtf.gz", gtf)).genes
    assert next(g for g in genes if g["gene_id"] == "AT1G01020")["strand"] == "."


def test_a_gene_id_on_two_gene_lines_is_refused_by_name(tmp_path, sources):
    gtf = GTF + 'Chr2\tAraport11\tgene\t6\t9\t.\t+\t.\tgene_id "AT1G01010";\n'
    with pytest.raises(_index.IndexProblem, match="AT1G01010") as caught:
        _build(tmp_path, sources[0], _gz(tmp_path / "twice.gtf.gz", gtf))
    assert "more than once" in str(caught.value)


def test_a_gene_whose_exons_sit_on_two_contigs_is_refused(tmp_path, sources):
    gtf = GTF + 'Chr2\tAraport11\texon\t6\t9\t.\t-\t.\tgene_id "AT1G01020";\n'
    with pytest.raises(_index.IndexProblem, match="AT1G01020"):
        _build(tmp_path, sources[0], _gz(tmp_path / "split.gtf.gz", gtf))


def test_a_gene_on_a_contig_the_fasta_lacks_is_refused(tmp_path, sources):
    gtf = GTF + 'chr3\tAraport11\tgene\t1\t4\t.\t+\t.\tgene_id "AT3G01010";\n'
    with pytest.raises(_index.IndexProblem, match="chr3"):
        _build(tmp_path, sources[0], _gz(tmp_path / "contig.gtf.gz", gtf))


def test_a_gene_past_its_contigs_end_is_refused(tmp_path, sources):
    gtf = GTF.replace("Chr2\tAraport11\tgene\t1\t4", "Chr2\tAraport11\tgene\t1\t40")
    with pytest.raises(_index.IndexProblem, match=r"AT2G01010 ends at 40, .*\(10 bases"):
        _build(tmp_path, sources[0], _gz(tmp_path / "long.gtf.gz", gtf))


def test_a_gtf_with_no_genes_is_refused(tmp_path, sources):
    with pytest.raises(_index.IndexProblem, match="no genes"):
        _build(tmp_path, sources[0], _gz(tmp_path / "empty.gtf.gz", "#only a header\n"))


def test_genes_are_sent_in_batches_of_at_most_5000():
    genes = [{"gene_id": f"G{i}"} for i in range(12001)]
    assert [len(b) for b in _index.batches(genes)] == [5000, 5000, 2001]


# --- htslib ---------------------------------------------------------------------------------


def test_without_the_extra_the_install_instruction_is_given(monkeypatch):
    real_import = builtins.__import__

    def no_pysam(name, *args, **kwargs):
        if name == "pysam":
            raise ImportError("No module named 'pysam'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_pysam)
    with pytest.raises(click.ClickException, match=r"bloomctl\[genome-index\]"):
        _index.require_htslib()
