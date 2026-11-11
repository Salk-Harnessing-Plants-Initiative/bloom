"""Turning a checked GTF's lines into GFF3, the gene format the genome browser reads by region.

GFF3 links each line to its parent by ID: an exon or CDS to its transcript, a transcript to its
gene. Each gene and transcript gets one line, `ID=gene:<gene_id>` or `ID=transcript:<id>`,
placed and stranded as the GTF check found it; the GTF's own `gene` and `transcript` lines give
it their attributes. Every other line keeps its columns and attributes and gains its `Parent`.
Lines without a gene_id belong to no gene and are left out.
"""

from __future__ import annotations

import re
from typing import Any, Iterator

from . import _gtf

HEADER = b"##gff-version 3\n"
# Where a line sorts among lines with the same start: a parent before its children.
GENE, TRANSCRIPT, PART = 0, 1, 2
# GFF3 sets these from the gene and transcript links; the GTF's own are not copied.
OURS = {"ID", "Parent", "Name"}
_KEY = re.compile(r"[A-Za-z0-9_.:\-]+")
# Characters GFF3 escapes in an attribute value.
_RESERVED = re.compile(r"[\x00-\x1f\x7f%;=&,]")


def escape(value: str) -> str:
    return _RESERVED.sub(lambda m: f"%{ord(m.group()):02X}", value)


def _attributes(first: list[tuple[str, str]], pairs: list[tuple[str, str]]) -> str:
    """`first` (ours), then the GTF's pairs; a repeated key's values joined with commas."""
    merged: dict[str, list[str]] = {}
    for key, value in pairs:
        if key not in OURS and _KEY.fullmatch(key) and value.strip():
            merged.setdefault(key, []).append(escape(value))
    ours = [f"{key}={escape(value)}" for key, value in first]
    return ";".join(ours + [f"{key}={','.join(values)}" for key, values in merged.items()])


class Converter:
    """Converts lines one at a time, and remembers what the gene and transcript lines need."""

    def __init__(self) -> None:
        self.gene_sources: dict[str, str] = {}
        self.transcript_sources: dict[str, str] = {}
        self.gene_pairs: dict[str, list[tuple[str, str]]] = {}
        self.transcript_pairs: dict[str, list[tuple[str, str]]] = {}
        self.transcript_names: dict[str, str] = {}

    def line(self, columns: list[str]) -> bytes | None:
        """The GFF3 line for a part (exon, CDS, ...); None for a gene or transcript line, which
        `parents` writes, or for a line with no gene."""
        pairs = _gtf.attributes(columns[8])
        found = dict(pairs)
        gene_id = found.get("gene_id", "").strip()
        if not gene_id:
            return None
        transcript_id = found.get("transcript_id", "").strip()
        self.gene_sources.setdefault(gene_id, columns[1])
        if columns[2] == "gene":
            self.gene_pairs.setdefault(gene_id, pairs)
            return None
        if transcript_id:
            self.transcript_sources.setdefault(transcript_id, columns[1])
            if found.get("transcript_name", "").strip():
                self.transcript_names.setdefault(transcript_id, found["transcript_name"].strip())
            if columns[2] == "transcript":
                self.transcript_pairs.setdefault(transcript_id, pairs)
                return None
        parent = f"transcript:{transcript_id}" if transcript_id else f"gene:{gene_id}"
        return _row(columns[:8], _attributes([("Parent", parent)], pairs))

    def parents(self, reader: _gtf.GeneReader, genes: list[dict[str, Any]]
                ) -> Iterator[tuple[str, int, int, bytes]]:
        """(contig, start, GENE or TRANSCRIPT, line) for every gene and transcript."""
        for gene in genes:
            gene_id = gene["gene_id"]
            ours = [("ID", f"gene:{gene_id}"), ("Name", gene["gene_name"] or gene_id)]
            yield gene["contig"], gene["gene_start"], GENE, _row(
                [gene["contig"], self.gene_sources[gene_id], "gene", str(gene["gene_start"]),
                 str(gene["gene_end"]), ".", gene["strand"], "."],
                _attributes(ours, self.gene_pairs.get(gene_id, [("gene_id", gene_id)])))
        for transcript_id, transcript in reader.transcripts.items():
            span = transcript.span
            contig = next(iter(span.contigs))
            ours = [("ID", f"transcript:{transcript_id}"),
                    ("Parent", f"gene:{next(iter(transcript.genes))}"),
                    ("Name", self.transcript_names.get(transcript_id, transcript_id))]
            pairs = self.transcript_pairs.get(transcript_id, [("transcript_id", transcript_id)])
            yield contig, span.start, TRANSCRIPT, _row(
                [contig, self.transcript_sources[transcript_id], "transcript", str(span.start),
                 str(span.end), ".", span.strand, "."], _attributes(ours, pairs))


def _row(columns: list[str], attributes: str) -> bytes:
    return ("\t".join(columns + [attributes]) + "\n").encode()
