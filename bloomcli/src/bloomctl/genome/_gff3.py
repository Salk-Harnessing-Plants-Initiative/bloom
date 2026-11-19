"""Turning a checked GTF's lines into GFF3, the gene format the genome browser reads by region.

GFF3 links each line to its parent by ID: an exon or CDS to its transcript, a transcript to its
gene. Each gene and transcript gets one line, `ID=gene:<gene_id>` or `ID=transcript:<id>`,
placed and stranded as the GTF check found it; the GTF's own `gene` and `transcript` lines give
it their attributes. Every other line keeps its columns and attributes and gains its `Parent`.
Lines without a gene_id belong to no gene and are left out.

The browser shows attribute values in a gene's details, so `<` and `>` are removed from them:
nothing a GTF says can read as HTML there.
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
# Keys GFF3 gives a meaning a GTF's don't have; kept as gtf_<key>.
RENAMED = {"Target", "Gap", "Derives_from", "Is_circular"}
# IDs are written without the spaces a GTF may pad them with, as Bloom stores them.
IDS = {"gene_id", "transcript_id"}
# The most the gene and transcript lines' attributes may hold while the rest of the GTF is read.
MAX_HELD_BYTES = 512 * 1024**2
_KEY = re.compile(r"[A-Za-z0-9_.:\-]+")
# Characters GFF3 escapes in an attribute value, and in a column.
_RESERVED = re.compile(r"[\x00-\x1f\x7f%;=&,]")
_RESERVED_IN_COLUMN = re.compile(r"[\x00-\x1f\x7f%]")
_MARKUP = str.maketrans("", "", "<>")


def escape(value: str) -> str:
    return _RESERVED.sub(_percent, value.translate(_MARKUP))


def column(value: str) -> str:
    return _RESERVED_IN_COLUMN.sub(_percent, value.translate(_MARKUP))


def _percent(match: re.Match[str]) -> str:
    return f"%{ord(match.group()):02X}"


def _joined(ours: list[tuple[str, str]], rest: str) -> str:
    return ";".join([f"{key}={escape(value)}" for key, value in ours] + ([rest] if rest else []))


class Converter:
    """Converts lines one at a time, and remembers what the gene and transcript lines need: each
    one's attributes, already written as GFF3."""

    def __init__(self) -> None:
        self.gene_sources: dict[str, str] = {}
        self.transcript_sources: dict[str, str] = {}
        self.gene_attributes: dict[str, str] = {}
        self.transcript_attributes: dict[str, str] = {}
        self.transcript_names: dict[str, str] = {}
        self.held = 0
        self.dropped_keys = 0

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
            self._hold(self.gene_attributes, gene_id, pairs)
            return None
        if transcript_id:
            self.transcript_sources.setdefault(transcript_id, columns[1])
            if found.get("transcript_name", "").strip():
                self.transcript_names.setdefault(transcript_id, found["transcript_name"].strip())
            if columns[2] == "transcript":
                self._hold(self.transcript_attributes, transcript_id, pairs)
                return None
        parent = f"transcript:{transcript_id}" if transcript_id else f"gene:{gene_id}"
        return _row(columns[:8], _joined([("Parent", parent)], self._rest(pairs)))

    def parents(self, reader: _gtf.GeneReader, genes: list[dict[str, Any]]
                ) -> Iterator[tuple[str, int, int, bytes]]:
        """(contig, start, GENE or TRANSCRIPT, line) for every gene and transcript."""
        for gene in genes:
            gene_id = gene["gene_id"]
            ours = [("ID", f"gene:{gene_id}"), ("Name", gene["gene_name"] or gene_id)]
            rest = self.gene_attributes.get(gene_id, f"gene_id={escape(gene_id)}")
            yield gene["contig"], gene["gene_start"], GENE, _row(
                [gene["contig"], self.gene_sources[gene_id], "gene", str(gene["gene_start"]),
                 str(gene["gene_end"]), ".", gene["strand"], "."], _joined(ours, rest))
        for transcript_id, transcript in reader.transcripts.items():
            span = transcript.span
            contig = next(iter(span.contigs))
            ours = [("ID", f"transcript:{transcript_id}"),
                    ("Parent", f"gene:{next(iter(transcript.genes))}"),
                    ("Name", self.transcript_names.get(transcript_id, transcript_id))]
            rest = self.transcript_attributes.get(transcript_id,
                                                  f"transcript_id={escape(transcript_id)}")
            yield contig, span.start, TRANSCRIPT, _row(
                [contig, self.transcript_sources[transcript_id], "transcript", str(span.start),
                 str(span.end), ".", span.strand, "."], _joined(ours, rest))

    @property
    def notes(self) -> list[str]:
        if not self.dropped_keys:
            return []
        return [f"{self.dropped_keys:,} GTF attributes have a key GFF3 can't hold (only "
                "letters, digits and _ . : -) and are left out of the browser's genes."]

    def _hold(self, held: dict[str, str], key: str, pairs: list[tuple[str, str]]) -> None:
        """Keep the first such line's attributes, refusing a GTF whose held attributes grow past
        MAX_HELD_BYTES."""
        if key in held:
            return
        held[key] = rest = self._rest(pairs)
        self.held += len(rest)
        if self.held > MAX_HELD_BYTES:
            raise _gtf.GtfProblem(f"the GTF's gene and transcript lines carry more than "
                                  f"{MAX_HELD_BYTES // 1024**2} MB of attributes")

    def _rest(self, pairs: list[tuple[str, str]]) -> str:
        """The GTF's own attributes as GFF3; a repeated key's values joined with commas."""
        merged: dict[str, list[str]] = {}
        for key, value in pairs:
            if key in OURS:
                continue
            if not _KEY.fullmatch(key):
                self.dropped_keys += 1
                continue
            value = value.strip() if key in IDS else value
            if value.strip():
                merged.setdefault(f"gtf_{key}" if key in RENAMED else key, []).append(
                    escape(value))
        return ";".join(f"{key}={','.join(values)}" for key, values in merged.items())


def _row(columns: list[str], attributes: str) -> bytes:
    """A GFF3 line; the source and type are written as GFF3 escapes a column."""
    columns = [columns[0], column(columns[1]), column(columns[2]), *columns[3:]]
    return ("\t".join(columns + [attributes]) + "\n").encode()
