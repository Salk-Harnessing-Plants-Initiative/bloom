"""GO and KEGG pathway over-representation of a dataset's differential expression against one
annotation, and the gzipped JSON file that holds the significant terms.

Per comparison and direction, the list is the genes with fdr < fdr_max and |log2fc| > lfc_min,
strongest first, up to top_n. Each kind (GO_BP, GO_MF, GO_CC, KEGG) is tested against its own
background: the dataset's genes with a term of that kind. Every term with min_term..max_term
background genes and at least one list gene gets a hypergeometric test; terms covering the same
genes are merged into the most specific; Benjamini-Hochberg over all of them within a
comparison, direction and kind; then terms with fdr < fdr_max and at least min_list list genes
are kept. min_list only hides terms, after the correction: filtering on the list-gene count
before it would leave out the tests least likely to pass, and the FDRs would come out too low. A KEGG result also lists its list
genes' KEGG orthologs, to colour them on the pathway's map.
"""

from __future__ import annotations

import gzip
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from scipy.stats import hypergeom

from .annotation import Annotation
from .go_terms import KEGG
from .steps import EXIT_BAD_ENRICHMENT_INPUT, StepError

FORMAT = "bloom-enrichment/1"
DIRECTIONS = ("up", "down")
# How many unknown genes a refusal names.
SHOWN = 5


@dataclass
class Params:
    top_n: int = 300
    fdr_max: float = 0.05
    lfc_min: float = 0.25
    min_term: int = 5
    max_term: int = 500
    min_list: int = 3


@dataclass
class Result:
    results: list[dict]
    background_genes: int
    annotated_genes: int


def gene_lists(rows: list[dict], params: Params) -> dict[str, list[str]]:
    """A comparison's up and down lists, strongest fold change first (ties by gene)."""
    significant = [r for r in rows if r["log2fc"] is not None and r["fdr"] is not None
                   and r["fdr"] < params.fdr_max]
    picked = {
        "up": [r for r in significant if r["log2fc"] > params.lfc_min],
        "down": [r for r in significant if r["log2fc"] < -params.lfc_min],
    }
    return {direction: [r["gene"] for r in sorted(rows_, key=lambda r: (-abs(r["log2fc"]),
                                                                         r["gene"]))][: params.top_n]
            for direction, rows_ in picked.items()}


def p_value(*, k: int, background: int, term: int, listed: int) -> float:
    """P(at least k of `listed` genes fall in a term of `term` genes, out of `background`)."""
    return float(hypergeom.sf(k - 1, background, term, listed))


def bh(pvalues: list[float]) -> list[float]:
    """Benjamini-Hochberg adjusted p-values, in the given order."""
    n = len(pvalues)
    adjusted, running = [0.0] * n, 1.0
    for rank, i in reversed(list(enumerate(sorted(range(n), key=lambda i: pvalues[i]), 1))):
        running = min(running, pvalues[i] * n / rank)
        adjusted[i] = running
    return adjusted


def _ancestors(terms: dict[str, dict]) -> dict[str, set[str]]:
    """Each term's ancestors (itself excluded) through the file's parents."""
    found: dict[str, set[str]] = {}
    for term in terms:
        above, todo = set(), list(terms[term].get("parents", []))
        while todo:
            parent = todo.pop()
            if parent not in above:
                above.add(parent)
                todo.extend(terms.get(parent, {}).get("parents", []))
        found[term] = above
    return found


def _most_specific(tested: list[dict], members: dict[str, frozenset[str]],
                   ancestors: dict[str, set[str]]) -> list[dict]:
    """One term per set of covered genes: one no other term in the set lies below."""
    groups: dict[frozenset[str], list[dict]] = {}
    for row in tested:
        groups.setdefault(members[row["term"]], []).append(row)
    kept = []
    for same in groups.values():
        ids = {r["term"] for r in same}
        specific = [r for r in same
                    if not any(r["term"] in ancestors.get(other, ()) for other in ids)]
        kept.append(min(specific or same, key=lambda r: r["term"]))
    return kept


def run(annotation: Annotation, *, dataset_genes: list[str], comparisons: list[dict],
        params: Params) -> Result:
    """The significant terms of every comparison and direction."""
    dataset = set(dataset_genes)
    annotated = dataset & annotation.genes.keys()
    if not annotated:
        raise StepError(EXIT_BAD_ENRICHMENT_INPUT,
                        "the dataset shares no gene with the annotation; check its genome")
    by_kind: dict[str, dict[str, set[str]]] = {}
    for gene in annotated:
        for term in annotation.genes[gene]["terms"]:
            kind = annotation.terms[term]["kind"]
            by_kind.setdefault(kind, {}).setdefault(term, set()).add(gene)
    backgrounds = {kind: set().union(*terms.values()) for kind, terms in by_kind.items()}
    members = {term: frozenset(genes) for terms in by_kind.values() for term, genes in terms.items()}
    ancestors = _ancestors(annotation.terms)

    results = []
    for comparison in comparisons:
        unknown = sorted({r["gene"] for r in comparison["genes"]} - dataset)
        if unknown:
            raise StepError(EXIT_BAD_ENRICHMENT_INPUT,
                            f"{comparison['comparison']} has genes outside the dataset: "
                            f"{', '.join(unknown[:SHOWN])}")
        lists = gene_lists(comparison["genes"], params)
        for direction in DIRECTIONS:
            for kind in sorted(by_kind):
                background = backgrounds[kind]
                hits = set(lists[direction]) & background
                tested = []
                for term, genes in by_kind[kind].items():
                    in_list = genes & hits
                    if not in_list:
                        continue
                    if not params.min_term <= len(genes) <= params.max_term:
                        continue
                    tested.append({
                        "comparison": comparison["comparison"], "direction": direction,
                        "kind": kind, "term": term, "name": annotation.terms[term]["name"],
                        "list_genes_in_term": len(in_list), "list_size": len(hits),
                        "term_size": len(genes), "background_size": len(background),
                        "p": p_value(k=len(in_list), background=len(background),
                                     term=len(genes), listed=len(hits)),
                        "genes": sorted(in_list),
                    })
                    if kind == KEGG:
                        tested[-1]["kos"] = sorted({ko for gene in in_list for ko in
                                                    annotation.genes[gene].get("kos", [])})
                tested = _most_specific(tested, members, ancestors)
                for row, fdr in zip(tested, bh([r["p"] for r in tested])):
                    if fdr < params.fdr_max and row["list_genes_in_term"] >= params.min_list:
                        row["fdr"] = fdr
                        results.append(row)
    results.sort(key=lambda r: (r["comparison"], r["direction"], r["kind"], r["fdr"], r["term"]))
    return Result(results=results, background_genes=len(dataset), annotated_genes=len(annotated))


def write(path: Path, result: Result, *, dataset_id: int, de_run_id: int, annotation: str,
          params: Params) -> None:
    """The enrichment file: gzipped JSON in the bloom-enrichment/1 format."""
    doc = {"format": FORMAT, "dataset_id": dataset_id, "de_run_id": de_run_id,
           "annotation": annotation, "params": asdict(params),
           "background_genes": result.background_genes,
           "annotated_genes": result.annotated_genes, "results": result.results}
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(doc, fh, separators=(",", ":"))
