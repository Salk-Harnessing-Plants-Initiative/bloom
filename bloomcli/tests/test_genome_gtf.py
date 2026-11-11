"""Checking a GTF against its FASTA and reading each gene's position — what `genome upload` and
`genome index` both refuse, with the line, gene or contig named."""

import gzip

import pytest

from bloomctl.genome import _gtf

FASTA = b">Chr1 CHROMOSOME dumped from ADB\nACGTACGTAC\nGGGGCCCCAA\n>Chr2\nTTTTAAAAGG\n"
GTF = (
    "#!genome-build TAIR10\n"
    'Chr1\tAraport11\tgene\t3\t9\t.\t+\t.\tgene_id "AT1G01010"; gene_name "NAC001";\n'
    'Chr1\tAraport11\texon\t3\t5\t.\t+\t.\tgene_id "AT1G01010"; transcript_id "t1";\n'
    'Chr1\tAraport11\texon\t12\t15\t.\t-\t.\tgene_id "AT1G01020"; transcript_id "t2";\n'
    'Chr1\tAraport11\texon\t17\t18\t.\t-\t.\tgene_id "AT1G01020"; transcript_id "t2";\n'
    'Chr2\tAraport11\tgene\t1\t4\t.\t-\t.\tgene_id "AT2G01010"; gene_name "";\n'
)
LENGTHS = {"Chr1": 20, "Chr2": 10}


def _file(tmp_path, data, name="f", zipped=True):
    data = data if isinstance(data, bytes) else data.encode()
    path = tmp_path / (name + (".gz" if zipped else ""))
    path.write_bytes(gzip.compress(data) if zipped else data)
    return path


def _genes(tmp_path, gtf):
    return _gtf.genes(_file(tmp_path, gtf, "genes.gtf"), LENGTHS)


def _refused(tmp_path, gtf, match):
    with pytest.raises(_gtf.GtfProblem, match=match):
        _genes(tmp_path, gtf)


def _line(contig="Chr1", feature="exon", start="1", end="4", strand="+",
          attributes='gene_id "X";'):
    return f"{contig}\tsrc\t{feature}\t{start}\t{end}\t.\t{strand}\t.\t{attributes}\n"


# --- the FASTA ------------------------------------------------------------------------------


@pytest.mark.parametrize("zipped", [True, False])
def test_contig_lengths_are_read_in_order(tmp_path, zipped):
    assert _gtf.contig_lengths(_file(tmp_path, FASTA, zipped=zipped)) == LENGTHS


def test_a_sequence_on_one_long_line_is_counted(tmp_path, monkeypatch):
    monkeypatch.setattr(_gtf, "MAX_LINE_BYTES", 8)
    fasta = _file(tmp_path, b">Chr1\n" + b"ACGT" * 50 + b"\r\n>Chr2\nGG\n")
    assert _gtf.contig_lengths(fasta) == {"Chr1": 200, "Chr2": 2}


def test_a_repeated_contig_name_is_refused(tmp_path):
    with pytest.raises(_gtf.GtfProblem, match="more than once in the FASTA: Chr1"):
        _gtf.contig_lengths(_file(tmp_path, FASTA + b">Chr1\nAC\n"))


@pytest.mark.parametrize("fasta, match", [
    (b"ACGT\n>Chr1\nAC\n", "does not start with a '>'"),
    (b">\nAC\n", "no name"),
    (b"", "no sequences"),
    (b">Chr\xff1\nAC\n", "not UTF-8"),
])
def test_a_fasta_bloom_cannot_use_is_refused(tmp_path, fasta, match):
    with pytest.raises(_gtf.GtfProblem, match=match):
        _gtf.contig_lengths(_file(tmp_path, fasta))


def test_a_fasta_that_unpacks_too_far_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(_gtf, "MAX_UNPACKED_BYTES", 20)
    with pytest.raises(_gtf.GtfProblem, match="unpacks to more than"):
        _gtf.contig_lengths(_file(tmp_path, FASTA))


# --- genes ----------------------------------------------------------------------------------


def test_genes_come_from_gene_lines_or_the_span_of_their_other_lines(tmp_path):
    assert _genes(tmp_path, GTF) == [
        {"gene_id": "AT1G01010", "gene_name": "NAC001", "contig": "Chr1",
         "gene_start": 3, "gene_end": 9, "strand": "+"},
        {"gene_id": "AT1G01020", "gene_name": None, "contig": "Chr1",
         "gene_start": 12, "gene_end": 18, "strand": "-"},
        {"gene_id": "AT2G01010", "gene_name": None, "contig": "Chr2",
         "gene_start": 1, "gene_end": 4, "strand": "-"},
    ]


@pytest.mark.parametrize("attributes, name", [
    ('gene_id "X"; gene_name "NAC1"; gene "nac1";', "NAC1"),
    ('gene_id "X"; gene "nac1"; Name "N1";', "nac1"),
    ('gene_id "X"; Name "N1";', "N1"),
    ('gene_id "X"; note "a; b";', None),
])
def test_a_genes_name_is_read_from_gene_name_then_gene_then_name(tmp_path, attributes, name):
    assert _genes(tmp_path, _line(attributes=attributes))[0]["gene_name"] == name


def test_trans_spliced_gene_lines_on_one_contig_become_one_span(tmp_path):
    gtf = (_line(feature="gene", start="2", end="4", strand="+", attributes='gene_id "rps12";')
           + _line(feature="gene", start="15", end="19", strand="-",
                   attributes='gene_id "rps12";'))
    (gene,) = _genes(tmp_path, gtf)
    assert (gene["gene_start"], gene["gene_end"], gene["strand"]) == (2, 19, ".")


def test_a_gene_on_two_contigs_is_refused_by_name(tmp_path):
    _refused(tmp_path, GTF + _line(contig="Chr2", attributes='gene_id "AT1G01020";'),
             "more than one contig in the GTF: AT1G01020")


def test_a_gene_whose_lines_disagree_on_strand_has_no_strand(tmp_path):
    gtf = _line(strand="+") + _line(start="6", end="8", strand="-")
    assert _genes(tmp_path, gtf)[0]["strand"] == "."


@pytest.mark.parametrize("attributes", [f'gene_id "{"A" * 201}";',
                                        f'gene_id "X"; gene_name "{"N" * 201}";'])
def test_an_id_or_name_over_bloom_s_limit_is_refused(tmp_path, attributes):
    _refused(tmp_path, _line(attributes=attributes), "over 200 characters")


def test_a_gtf_with_no_genes_is_refused(tmp_path):
    _refused(tmp_path, "#only a header\n" + _line(attributes='transcript_id "t";'), "no genes")


# --- each line ------------------------------------------------------------------------------


@pytest.mark.parametrize("line, match", [
    (_line(contig="chr3"), "line 1 .* on chr3, which the FASTA doesn't have"),
    (_line(contig="Chr2", end="40"), r"line 1 .* ends at 40, past the end of Chr2 \(10 bases"),
    (_line(start="0"), "line 1 .* runs from 0 to 4"),
    (_line(start="5", end="3"), "line 1 .* runs from 5 to 3"),
    (_line(start="²"), "line 1 .* isn't a number"),
    (_line(strand="x"), "line 1 .* strand 'x'"),
    ("Chr1\tsrc\texon\t1\t4\n", "line 1 .* has 5 tab-separated columns"),
    ("track name=genes\n", "line 1 .* has 1 tab-separated columns"),
])
def test_a_line_bloom_cannot_use_is_refused_by_number(tmp_path, line, match):
    _refused(tmp_path, line, match)


def test_every_line_is_checked_not_just_genes(tmp_path):
    gtf = (_line(feature="gene", end="9", attributes='gene_id "G";')
           + _line(contig="Chr2", end="11", attributes='gene_id "G";'))
    _refused(tmp_path, gtf, "line 2 .* past the end of Chr2")


def test_crlf_line_endings_are_read(tmp_path):
    assert len(_genes(tmp_path, GTF.replace("\n", "\r\n"))) == 3


@pytest.mark.parametrize("raw, match", [(b"Chr1\tsrc\texon\t1\t4\t.\t+\t.\tgene_id \"\xe9\";\n",
                                         "not UTF-8"),
                                        (b"Chr1\tsrc\texon\t1\t4\t.\t+\t.\tgene_id \"\x00\";\n",
                                         "NUL")])
def test_bytes_bloom_cannot_store_are_refused(tmp_path, raw, match):
    _refused(tmp_path, raw, match)


def test_an_overlong_line_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(_gtf, "MAX_LINE_BYTES", 30)
    _refused(tmp_path, GTF, "line 2 of the GTF is longer than 30 bytes")


def test_a_gtf_that_unpacks_too_far_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(_gtf, "MAX_UNPACKED_BYTES", 100)
    _refused(tmp_path, GTF, "unpacks to more than")


def test_a_damaged_gzip_is_refused(tmp_path):
    path = _file(tmp_path, GTF, "genes.gtf")
    path.write_bytes(path.read_bytes()[:-12])
    with pytest.raises(_gtf.GtfProblem, match="could not be read"):
        _gtf.genes(path, LENGTHS)


def test_a_long_list_of_problems_says_how_many_more():
    assert _gtf.names([f"G{i}" for i in range(13)]).endswith("G9 and 3 more")


# --- transcripts ----------------------------------------------------------------------------


def test_a_transcript_in_two_genes_is_refused_by_name(tmp_path):
    _refused(tmp_path, _line(attributes='gene_id "A"; transcript_id "T";')
             + _line(attributes='gene_id "B"; transcript_id "T";'),
             "transcripts belong to more than one gene in the GTF: T")


def test_a_line_outside_its_gene_line_is_refused_by_gene(tmp_path):
    _refused(tmp_path, _line(feature="gene", start="2", end="6", attributes='gene_id "G";')
             + _line(start="5", end="9", attributes='gene_id "G"; transcript_id "T";'),
             "genes have lines outside their `gene` line in the GTF: G")


def test_a_line_outside_its_transcript_line_is_refused_by_transcript(tmp_path):
    _refused(tmp_path, _line(feature="transcript", start="2", end="6",
                             attributes='gene_id "G"; transcript_id "T";')
             + _line(start="1", end="3", attributes='gene_id "G"; transcript_id "T";'),
             "transcripts have lines outside their `transcript` line in the GTF: T")


def test_gene_and_transcript_lines_that_cover_their_parts_are_fine(tmp_path):
    gtf = (_line(feature="gene", start="1", end="9", attributes='gene_id "G";')
           + _line(feature="transcript", start="2", end="8",
                   attributes='gene_id "G"; transcript_id "T";')
           + _line(start="2", end="4", attributes='gene_id "G"; transcript_id "T";')
           + _line(start="6", end="8", attributes='gene_id "G"; transcript_id "T";'))
    assert [(g["gene_start"], g["gene_end"]) for g in _genes(tmp_path, gtf)] == [(1, 9)]
