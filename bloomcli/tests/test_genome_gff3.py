"""Turning GTF lines into GFF3 for the genome browser: each line's parent, the gene and
transcript lines, and how attribute values are carried over."""

import pytest

from bloomctl.genome import _gff3, _gtf


def _cols(feature="exon", start="1", end="4", strand="+", attributes='gene_id "G";',
          contig="Chr1"):
    return [contig, "src", feature, start, end, ".", strand, ".", attributes]


def _convert(*rows):
    reader, converter = _gtf.GeneReader(), _gff3.Converter()
    parts = []
    for columns in rows:
        reader.add(columns)
        line = converter.line(columns)
        if line is not None:
            parts.append(line.decode())
    parents = [line.decode() for *_, line in converter.parents(reader, reader.result())]
    return parents, parts


def _attrs(line):
    return line.rstrip("\n").split("\t")[8]


def test_a_part_belongs_to_its_transcript_and_the_transcript_to_its_gene():
    parents, parts = _convert(_cols(attributes='gene_id "G"; transcript_id "T";'))
    assert [_attrs(p) for p in parents] == [
        "ID=gene:G;Name=G;gene_id=G",
        "ID=transcript:T;Parent=gene:G;Name=T;transcript_id=T",
    ]
    assert _attrs(parts[0]) == "Parent=transcript:T;gene_id=G;transcript_id=T"


def test_a_part_without_a_transcript_belongs_to_its_gene():
    _, parts = _convert(_cols(attributes='gene_id "G";'))
    assert _attrs(parts[0]) == "Parent=gene:G;gene_id=G"


def test_a_line_without_a_gene_is_left_out():
    assert _gff3.Converter().line(_cols(attributes='transcript_id "T";')) is None


def test_the_gtf_gene_and_transcript_lines_give_their_attributes_and_are_not_copied():
    parents, parts = _convert(
        _cols("gene", "1", "9", attributes='gene_id "G"; gene_name "NAC1"; gene_type "pc";'),
        _cols("transcript", "1", "9",
              attributes='gene_id "G"; transcript_id "T"; transcript_name "NAC1-201";'),
        _cols("exon", "2", "4", attributes='gene_id "G"; transcript_id "T";'))
    assert [p.split("\t")[2:5] for p in parents] == [["gene", "1", "9"],
                                                     ["transcript", "1", "9"]]
    assert _attrs(parents[0]) == "ID=gene:G;Name=NAC1;gene_id=G;gene_name=NAC1;gene_type=pc"
    assert _attrs(parents[1]) == ("ID=transcript:T;Parent=gene:G;Name=NAC1-201;gene_id=G;"
                                  "transcript_id=T;transcript_name=NAC1-201")
    assert [p.split("\t")[2] for p in parts] == ["exon"]


def test_a_gene_and_transcript_without_their_own_lines_span_their_parts():
    parents, _ = _convert(
        _cols(start="2", end="4", strand="-", attributes='gene_id "G"; transcript_id "T";'),
        _cols(start="8", end="9", strand="-", attributes='gene_id "G"; transcript_id "T";'))
    assert [p.split("\t")[3:7] for p in parents] == [["2", "9", ".", "-"]] * 2


def test_the_parts_columns_are_kept():
    _, parts = _convert(["Chr1", "Araport11", "CDS", "5", "9", "0.5", "-", "2",
                         'gene_id "G"; transcript_id "T";'])
    assert parts[0].split("\t")[:8] == ["Chr1", "Araport11", "CDS", "5", "9", "0.5", "-", "2"]


def test_a_repeated_key_becomes_one_list():
    _, parts = _convert(_cols(attributes='gene_id "G"; tag "basic"; tag "CCDS";'))
    assert _attrs(parts[0]) == "Parent=gene:G;gene_id=G;tag=basic,CCDS"


def test_the_gtf_s_own_id_parent_and_name_are_not_copied():
    _, parts = _convert(_cols(attributes='gene_id "G"; ID "x"; Parent "y"; Name "z";'))
    assert _attrs(parts[0]) == "Parent=gene:G;gene_id=G"


def test_empty_values_and_keys_gff3_cannot_hold_are_left_out():
    _, parts = _convert(_cols(attributes='gene_id "G"; note ""; a=b "c"; ok "1";'))
    assert _attrs(parts[0]) == "Parent=gene:G;gene_id=G;ok=1"


@pytest.mark.parametrize("value, written", [
    ("a;b", "a%3Bb"), ("a=b", "a%3Db"), ("a,b", "a%2Cb"), ("a&b", "a%26b"),
    ("50%", "50%25"), ("tab\there", "tab%09here"), ("plain text", "plain text"),
])
def test_characters_gff3_reserves_are_escaped(value, written):
    assert _gff3.escape(value) == written


def test_an_escaped_value_is_carried_over():
    _, parts = _convert(_cols(attributes='gene_id "G"; note "a; b=c";'))
    assert _attrs(parts[0]) == "Parent=gene:G;gene_id=G;note=a%3B b%3Dc"


def test_an_unknown_strand_stays_on_its_line_and_its_parents_have_none():
    parents, parts = _convert(_cols(strand="?", attributes='gene_id "G"; transcript_id "T";'))
    assert [p.split("\t")[6] for p in parents] == [".", "."]
    assert parts[0].split("\t")[6] == "?"
