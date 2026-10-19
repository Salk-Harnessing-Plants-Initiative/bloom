"""A genome version's annotation: every gene's GO terms (with their ancestors) and KEGG pathways,
built from eggNOG-mapper or TAIR's GO annotations, and the gzipped JSON file that holds it."""

from __future__ import annotations

import csv
import gzip
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from .go_terms import KEGG, Ontology
from .steps import EXIT_BAD_ANNOTATION, StepError

FORMAT = "bloom-annotation/1"
EGGNOG_HEADER = "#query"
NONE = "-"
AGI = re.compile(r"^AT[1-5CM]G\d{5}$", re.IGNORECASE)
_GENE_ID = re.compile(r'gene_id "([^"]+)"')
_TRANSCRIPT_ID = re.compile(r'transcript_id "([^"]+)"')


@dataclass
class Annotation:
    """Terms by ID ({kind, name, parents}) and genes by ID ({name, description, terms})."""

    terms: dict[str, dict] = field(default_factory=dict)
    genes: dict[str, dict] = field(default_factory=dict)
    date: str | None = None


def gtf_genes(lines: Iterable[str]) -> set[str]:
    """Every gene ID in a GTF."""
    found = set()
    for line in lines:
        if not line.startswith("#") and (match := _GENE_ID.search(line)):
            found.add(match.group(1))
    return found


def transcript_genes(lines: Iterable[str]) -> dict[str, str]:
    """Each transcript's gene in a GTF; gffread names the proteins it builds by transcript."""
    found = {}
    for line in lines:
        if line.startswith("#"):
            continue
        transcript, gene = _TRANSCRIPT_ID.search(line), _GENE_ID.search(line)
        if transcript and gene:
            found[transcript.group(1)] = gene.group(1)
    return found


def _built(ontology: Ontology, direct: dict[str, set[str]], pathways: dict[str, str],
           details: dict[str, dict]) -> Annotation:
    """Genes with their GO terms' ancestors added, and the terms they use."""
    genes, used = {}, set()
    for gene in sorted(direct):
        terms = set()
        for term in direct[gene]:
            if term in ontology.terms:
                terms |= ontology.ancestors(term) & ontology.terms.keys()
            elif term in pathways:
                terms.add(term)
        if terms:
            info = details.get(gene, {})
            genes[gene] = {"name": info.get("name"), "description": info.get("description"),
                           "terms": sorted(terms)}
            used |= terms
    terms = {}
    for term in sorted(used):
        if term in ontology.terms:
            kind, name = ontology.terms[term]
            parents = sorted(ontology.parents.get(term, set()) & used)
            terms[term] = {"kind": kind, "name": name, "parents": parents}
        else:
            terms[term] = {"kind": KEGG, "name": pathways[term]}
    return Annotation(terms=terms, genes=genes)


def from_eggnog(lines: Iterable[str], *, transcript_genes: dict[str, str], ontology: Ontology,
                pathways: dict[str, str]) -> Annotation:
    """eggNOG-mapper's annotations of a genome's proteins; each gene gets the union of its
    proteins' GO terms and KEGG pathways (map IDs, of the species' pathways only)."""
    rows = [line for line in lines if not line.startswith("##")]
    if not rows or not rows[0].startswith(EGGNOG_HEADER):
        raise StepError(EXIT_BAD_ANNOTATION, "eggNOG-mapper output has no #query header")
    direct: dict[str, set[str]] = {}
    details: dict[str, dict] = {}
    for row in csv.DictReader(rows, delimiter="\t"):
        protein = row[EGGNOG_HEADER]
        gene = transcript_genes.get(protein)
        if gene is None:
            raise StepError(EXIT_BAD_ANNOTATION,
                            f"protein {protein} is not a transcript of the genome's GTF")
        terms = direct.setdefault(gene, set())
        if row["GOs"] not in (NONE, ""):
            terms.update(row["GOs"].split(","))
        if row["KEGG_Pathway"] not in (NONE, ""):
            terms.update(p for p in row["KEGG_Pathway"].split(",") if p.startswith("map"))
        info = details.setdefault(gene, {})
        for key, column in (("name", "Preferred_name"), ("description", "Description")):
            if info.get(key) is None and row[column] not in (NONE, ""):
                info[key] = row[column]
    return _built(ontology, direct, pathways, details)


def from_tair_gaf(lines: Iterable[str], *, genome_genes: set[str],
                  ontology: Ontology) -> Annotation:
    """TAIR's curated GO (a GAF), genes by AGI code from its symbol and synonym columns;
    NOT-qualified lines and genes outside the genome are left out."""
    direct: dict[str, set[str]] = {}
    date = None
    for line in lines:
        if line.startswith("!"):
            if line.startswith(("!date-generated:", "!Generated:")):
                date = line.split(":", 1)[1].strip()
            continue
        cols = line.rstrip("\n").split("\t")
        if len(cols) < 11 or "NOT" in cols[3].split("|"):
            continue
        codes = {s.upper() for s in cols[10].split("|") + [cols[2]] if AGI.match(s)}
        for gene in codes & genome_genes:
            direct.setdefault(gene, set()).add(cols[4])
    built = _built(ontology, direct, {}, {})
    built.date = date
    return built


def write(path: Path, built: Annotation, *, genome: str, version: int, source: str,
          tools: dict[str, str]) -> None:
    """The annotation file: gzipped JSON in the bloom-annotation/1 format."""
    if not built.genes:
        raise StepError(EXIT_BAD_ANNOTATION, f"the {source} annotation has no gene with a term")
    doc = {"format": FORMAT, "genome": genome, "version": version, "source": source,
           "tools": tools, "terms": built.terms, "genes": built.genes}
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(doc, fh, separators=(",", ":"), sort_keys=True)


def read(path: Path) -> Annotation:
    """An annotation file, refused unless it is bloom-annotation/1 and its genes' terms exist."""
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        doc = json.load(fh)
    if doc.get("format") != FORMAT:
        raise StepError(EXIT_BAD_ANNOTATION,
                        f"{path.name} is {doc.get('format')!r}, not {FORMAT}")
    terms, genes = doc["terms"], doc["genes"]
    for gene, info in genes.items():
        unknown = [t for t in info["terms"] if t not in terms]
        if unknown:
            raise StepError(EXIT_BAD_ANNOTATION,
                            f"{path.name}: gene {gene} uses unknown terms {', '.join(unknown)}")
    return Annotation(terms=terms, genes=genes)
