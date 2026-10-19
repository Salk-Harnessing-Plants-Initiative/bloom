"""Tests for argo/scrna/analysis: the GO ontology, KEGG pathway names, and building a genome's
annotation file from eggNOG-mapper or TAIR's GO annotations. No scanpy needed."""

from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "argo/scrna/analysis"))

from bloom_scrna_analysis import annotation, go_terms  # noqa: E402
from bloom_scrna_analysis.steps import StepError  # noqa: E402

# A small ontology: root process -> response to stimulus -> response to light (and a part_of
# link to a component), one function, and one obsolete term.
OBO = """format-version: 1.2
data-version: releases/2026-09-01

[Term]
id: GO:0008150
name: biological_process
namespace: biological_process

[Term]
id: GO:0050896
name: response to stimulus
namespace: biological_process
is_a: GO:0008150 ! biological_process

[Term]
id: GO:0009416
name: response to light stimulus
namespace: biological_process
is_a: GO:0050896 ! response to stimulus
relationship: part_of GO:0009314 ! response to radiation

[Term]
id: GO:0009314
name: response to radiation
namespace: biological_process
is_a: GO:0050896 ! response to stimulus

[Term]
id: GO:0003674
name: molecular_function
namespace: molecular_function

[Term]
id: GO:0005575
name: cellular_component
namespace: cellular_component

[Term]
id: GO:0000001
name: mitochondrion inheritance
namespace: biological_process
is_obsolete: true

[Typedef]
id: part_of
name: part of
"""

KEGG_LIST = (
    "sbi00010\tGlycolysis / Gluconeogenesis - Sorghum bicolor (sorghum)\n"
    "sbi04137\tMitophagy - animal - Sorghum bicolor (sorghum)\n"
)

GTF = "\n".join(
    [
        'Chr1\tx\tgene\t1\t9\t.\t+\t.\tgene_id "G1";',
        'Chr1\tx\ttranscript\t1\t9\t.\t+\t.\tgene_id "G1"; transcript_id "G1.t1";',
        'Chr1\tx\ttranscript\t1\t9\t.\t+\t.\tgene_id "G1"; transcript_id "G1.t2";',
        'Chr1\tx\tgene\t20\t29\t.\t+\t.\tgene_id "G2";',
        'Chr1\tx\ttranscript\t20\t29\t.\t+\t.\tgene_id "G2"; transcript_id "G2.t1";',
        'Chr1\tx\tgene\t40\t49\t.\t+\t.\tgene_id "AT1G01010";',
        "",
    ]
)

EMAPPER_HEADER = "\t".join(
    ["#query", "evalue", "max_annot_lvl", "COG_category", "Description", "Preferred_name",
     "GOs", "KEGG_ko", "KEGG_Pathway", "PFAMs"]
)


def emapper(*rows: tuple[str, ...]) -> str:
    """eggNOG-mapper output: (protein, description, preferred name, GOs, KEGG_Pathway[, KEGG_ko])
    per row."""
    lines = ["## emapper-2.1.13", EMAPPER_HEADER]
    for query, description, name, gos, pathways, *kos in rows:
        lines.append("\t".join([query, "1e-50", "33090|Viridiplantae", "S", description, name,
                                gos, kos[0] if kos else "-", pathways, "-"]))
    lines.append("## 3 queries scanned")
    return "\n".join(lines) + "\n"


@pytest.fixture
def ontology():
    return go_terms.parse_obo(OBO)


# --------------------------------------------------------------------------- #
# The GO ontology and KEGG names
# --------------------------------------------------------------------------- #


def test_the_ontology_reads_terms_kinds_and_its_release(ontology):
    assert ontology.version == "releases/2026-09-01"
    assert ontology.terms["GO:0009416"] == ("GO_BP", "response to light stimulus")
    assert ontology.terms["GO:0003674"] == ("GO_MF", "molecular_function")
    assert ontology.terms["GO:0005575"] == ("GO_CC", "cellular_component")


def test_obsolete_terms_and_typedefs_are_left_out(ontology):
    assert "GO:0000001" not in ontology.terms
    assert "part_of" not in ontology.terms


def test_ancestors_follow_is_a_and_part_of(ontology):
    assert ontology.ancestors("GO:0009416") == {
        "GO:0009416", "GO:0009314", "GO:0050896", "GO:0008150"
    }
    assert ontology.parents["GO:0009416"] == {"GO:0050896", "GO:0009314"}


def test_kegg_pathways_are_named_by_their_map_id_without_the_species():
    assert go_terms.parse_kegg_list(KEGG_LIST) == {
        "map00010": "Glycolysis / Gluconeogenesis",
        "map04137": "Mitophagy - animal",
    }


def test_genes_of_transcripts_come_from_the_gtf():
    assert annotation.transcript_genes(GTF.splitlines()) == {
        "G1.t1": "G1", "G1.t2": "G1", "G2.t1": "G2"
    }
    assert annotation.gtf_genes(GTF.splitlines()) == {"G1", "G2", "AT1G01010"}


# --------------------------------------------------------------------------- #
# eggNOG-mapper
# --------------------------------------------------------------------------- #


def eggnog(ontology, text):
    return annotation.from_eggnog(
        text.splitlines(),
        transcript_genes=annotation.transcript_genes(GTF.splitlines()),
        ontology=ontology,
        pathways=go_terms.parse_kegg_list(KEGG_LIST),
    )


def test_a_gene_gets_the_union_of_its_proteins_terms_with_ancestors(ontology):
    built = eggnog(ontology, emapper(
        ("G1.t1", "Light response protein", "LRP1", "GO:0009416", "-"),
        ("G1.t2", "-", "-", "GO:0003674", "ko00010,map00010"),
    ))
    assert built.genes["G1"]["terms"] == [
        "GO:0003674", "GO:0008150", "GO:0009314", "GO:0009416", "GO:0050896", "map00010"
    ]
    assert built.genes["G1"]["name"] == "LRP1"
    assert built.genes["G1"]["description"] == "Light response protein"


def test_a_gene_keeps_its_proteins_kegg_orthologs_without_the_ko_prefix(ontology):
    built = eggnog(ontology, emapper(
        ("G1.t1", "-", "-", "GO:0009416", "map00010", "ko:K00844"),
        ("G1.t2", "-", "-", "-", "-", "ko:K12811,ko:K00844"),
        ("G2.t1", "-", "-", "GO:0003674", "-"),
    ))
    assert built.genes["G1"]["kos"] == ["K00844", "K12811"]
    assert built.genes["G2"]["kos"] == []


def test_ko_ids_unknown_terms_and_other_species_pathways_are_dropped(ontology):
    built = eggnog(ontology, emapper(
        ("G2.t1", "-", "-", "GO:9999999,GO:0000001", "ko01100,map01100"),
    ))
    assert "G2" not in built.genes, "nothing usable is left"


def test_a_protein_not_in_the_gtf_is_refused(ontology):
    with pytest.raises(StepError, match="NOPE.t1"):
        eggnog(ontology, emapper(("NOPE.t1", "-", "-", "GO:0009416", "-")))


def test_eggnog_output_without_its_header_is_refused(ontology):
    with pytest.raises(StepError, match="header"):
        annotation.from_eggnog(
            ["G1.t1\t1e-5"], transcript_genes={"G1.t1": "G1"}, ontology=ontology, pathways={}
        )


def test_only_the_terms_used_are_kept_with_their_parents(ontology):
    built = eggnog(ontology, emapper(("G1.t1", "-", "-", "GO:0009416", "ko00010,map00010"),))
    assert set(built.terms) == {"GO:0009416", "GO:0009314", "GO:0050896", "GO:0008150", "map00010"}
    assert built.terms["GO:0009416"] == {
        "kind": "GO_BP", "name": "response to light stimulus",
        "parents": ["GO:0009314", "GO:0050896"],
    }
    assert built.terms["GO:0008150"]["parents"] == []
    assert built.terms["map00010"] == {"kind": "KEGG", "name": "Glycolysis / Gluconeogenesis"}


# --------------------------------------------------------------------------- #
# TAIR GO
# --------------------------------------------------------------------------- #


def gaf_line(symbol, synonyms, qualifier, go_id):
    cols = ["TAIR", "locus:1", symbol, qualifier, go_id, "TAIR:1", "IDA", "", "P", "", synonyms,
            "protein", "taxon:3702", "20260101", "TAIR", "", ""]
    return "\t".join(cols)


GAF = "\n".join(
    [
        "!gaf-version: 2.2",
        "!date-generated: 2026-09-15",
        gaf_line("NAC001", "AT1G01010|ANAC001", "involved_in", "GO:0009416"),
        gaf_line("NAC001", "AT1G01010", "NOT|involved_in", "GO:0003674"),
        gaf_line("XYZ", "AT5G99999", "enables", "GO:0003674"),
        "",
    ]
)


def test_tair_go_maps_genes_by_their_agi_code_and_drops_not_lines(ontology):
    built = annotation.from_tair_gaf(
        GAF.splitlines(), genome_genes=annotation.gtf_genes(GTF.splitlines()), ontology=ontology
    )
    assert built.date == "2026-09-15"
    assert set(built.genes) == {"AT1G01010"}, "a gene outside the genome is left out"
    assert built.genes["AT1G01010"]["terms"] == [
        "GO:0008150", "GO:0009314", "GO:0009416", "GO:0050896"
    ]
    assert built.genes["AT1G01010"]["kos"] == [], "TAIR has no KEGG orthologs"


# --------------------------------------------------------------------------- #
# The file
# --------------------------------------------------------------------------- #


def test_the_file_records_its_format_genome_source_and_tools(ontology, tmp_path):
    built = eggnog(ontology, emapper(("G1.t1", "-", "-", "GO:0009416", "-"),))
    path = tmp_path / "eggnog.json.gz"
    annotation.write(
        path, built, genome="sbicolor.btx623.hpiv3", version=1, source="eggnog",
        tools={"eggnog_mapper": "2.1.13", "eggnog_db": "5.0.2", "go_basic": ontology.version},
    )
    with gzip.open(path, "rt") as fh:
        doc = json.load(fh)
    assert doc["format"] == annotation.FORMAT == "bloom-annotation/1"
    assert (doc["genome"], doc["version"], doc["source"]) == ("sbicolor.btx623.hpiv3", 1, "eggnog")
    assert doc["tools"]["go_basic"] == "releases/2026-09-01"
    assert doc["genes"]["G1"]["terms"][0] == "GO:0008150"
    assert annotation.read(path).genes == built.genes, "it reads back the same"


def test_reading_another_format_is_refused(tmp_path):
    path = tmp_path / "old.json.gz"
    with gzip.open(path, "wt") as fh:
        json.dump({"format": "bloom-annotation/0", "terms": {}, "genes": {}}, fh)
    with pytest.raises(StepError, match="bloom-annotation/0"):
        annotation.read(path)


def test_a_file_whose_genes_use_unknown_terms_is_refused(tmp_path):
    path = tmp_path / "bad.json.gz"
    with gzip.open(path, "wt") as fh:
        json.dump({"format": annotation.FORMAT, "terms": {},
                   "genes": {"G1": {"name": None, "description": None, "terms": ["GO:1"]}}}, fh)
    with pytest.raises(StepError, match="GO:1"):
        annotation.read(path)


def test_an_annotation_with_no_genes_is_refused(ontology, tmp_path):
    built = eggnog(ontology, emapper(("G2.t1", "-", "-", "-", "-"),))
    with pytest.raises(StepError, match="no gene"):
        annotation.write(tmp_path / "x.json.gz", built, genome="g", version=1, source="eggnog",
                         tools={})
