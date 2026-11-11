"""Checking a genome's GTF against its FASTA, and reading each gene's position.

Both `genome upload` and `genome index` use this, so a GTF Bloom can't use is refused at upload
and an older version is refused before a browser build starts. Files are read a line at a time:
memory grows with the number of genes and contigs, not with the size of the file.
"""

from __future__ import annotations

import gzip
import re
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO, Iterable, Iterator

from . import _files

# Bloom's limits on a gene's ID, name and contig (genome_browser_genes' checks).
MAX_NAME_CHARS = 200
# A longer line is not a GTF record; reading stops instead of holding it.
MAX_LINE_BYTES = 1024 * 1024
# The most a stored file may unpack to before it is refused.
MAX_UNPACKED_BYTES = 8 * 1024**3
# How many problems a message lists before saying how many more there are.
NAMED_IN_MESSAGE = 10
GTF_COLUMNS = 9
STRANDS = ("+", "-", ".")
# Where a gene's name is read from, in order: Ensembl/Araport, NCBI RefSeq, GFF-style.
NAME_KEYS = ("gene_name", "gene", "Name")
_ATTRIBUTE = re.compile(r'\s*([^\s;]+)\s+"([^"]*)"\s*;?')
_POSITION = re.compile(r"[0-9]{1,12}")


class GtfProblem(_files.FileProblem):
    """The GTF or FASTA can't be used; the message names what is wrong."""


@dataclass
class Span:
    contigs: set[str] = field(default_factory=set)
    start: int = 0
    end: int = 0
    strands: set[str] = field(default_factory=set)
    name: str | None = None

    def add(self, contig: str, start: int, end: int, strand: str) -> None:
        self.contigs.add(contig)
        self.start = start if not self.start else min(self.start, start)
        self.end = max(self.end, end)
        self.strands.add(strand)

    def covers(self, other: Span) -> bool:
        return not other.start or self.start <= other.start and other.end <= self.end

    @property
    def strand(self) -> str:
        return next(iter(self.strands)) if len(self.strands) == 1 else "."


@dataclass
class Transcript:
    """A transcript's gene, the span of its `transcript` lines and that of its other lines."""
    genes: set[str] = field(default_factory=set)
    line: Span = field(default_factory=Span)
    parts: Span = field(default_factory=Span)

    @property
    def span(self) -> Span:
        return self.line if self.line.start else self.parts


def attributes(text: str) -> list[tuple[str, str]]:
    """A GTF line's `key "value";` pairs, in order; a key may repeat."""
    return _ATTRIBUTE.findall(text)


def open_binary(path: Path) -> BinaryIO:
    return gzip.open(path, "rb") if _files.is_gzipped(path) else path.open("rb")


def lines(fh: BinaryIO, what: str) -> Iterator[tuple[int, int, bytes]]:
    """(line number, byte offset, line without its ending), refusing an overlong line or a file
    that unpacks past MAX_UNPACKED_BYTES."""
    offset = 0
    try:
        for number, raw in enumerate(iter(lambda: fh.readline(MAX_LINE_BYTES + 1), b""), 1):
            if len(raw) > MAX_LINE_BYTES:
                raise GtfProblem(f"line {number} of the {what} is longer than "
                                 f"{MAX_LINE_BYTES:,} bytes")
            yield number, offset, raw.rstrip(b"\r\n")
            offset += len(raw)
            if offset > MAX_UNPACKED_BYTES:
                raise GtfProblem(f"the {what} unpacks to more than "
                                 f"{MAX_UNPACKED_BYTES // 1024**3} GB")
    except (OSError, EOFError, zlib.error) as exc:
        raise GtfProblem(f"the {what} could not be read: {exc}") from exc


def contig_lengths(fasta: Path) -> dict[str, int]:
    """Each contig's name and length, in the FASTA's order; refuses a repeated or blank name.

    Read in chunks, so a sequence on one very long line is fine; only a header line is limited.
    """
    lengths: dict[str, int] = {}
    repeated: list[str] = []
    name: str | None = None
    at_line_start, number, unpacked = True, 0, 0
    try:
        with open_binary(fasta) as fh:
            for chunk in iter(lambda: fh.readline(MAX_LINE_BYTES + 1), b""):
                unpacked += len(chunk)
                if unpacked > MAX_UNPACKED_BYTES:
                    raise GtfProblem(f"the FASTA unpacks to more than "
                                     f"{MAX_UNPACKED_BYTES // 1024**3} GB")
                starts_line = at_line_start
                at_line_start = chunk.endswith(b"\n")
                number += starts_line
                if starts_line and chunk.startswith(b">"):
                    if not at_line_start and len(chunk) > MAX_LINE_BYTES:
                        raise GtfProblem(f"line {number} of the FASTA is a header longer "
                                         f"than {MAX_LINE_BYTES:,} bytes")
                    header = _text(chunk[1:].rstrip(b"\r\n"), number, "FASTA").split(maxsplit=1)
                    if not header:
                        raise GtfProblem(f"line {number} of the FASTA is a '>' line with no name")
                    name = header[0]
                    if name in lengths:
                        repeated.append(name)
                    lengths[name] = 0
                    continue
                bases = len(chunk.translate(None, b" \t\r\n"))
                if name is None and bases:
                    raise GtfProblem("the FASTA does not start with a '>' line")
                if name is not None:
                    lengths[name] += bases
    except (OSError, EOFError, zlib.error) as exc:
        raise GtfProblem(f"the FASTA could not be read: {exc}") from exc
    if repeated:
        raise GtfProblem(f"contig names appear more than once in the FASTA: {names(repeated)}")
    if not lengths:
        raise GtfProblem("the FASTA has no sequences")
    return lengths


def genes(gtf: Path, lengths: dict[str, int]) -> list[dict[str, Any]]:
    """Every gene's position, checked against the FASTA; refuses a GTF Bloom can't use."""
    reader = GeneReader()
    with open_binary(gtf) as fh:
        for _, _, columns in records(lines(fh, "GTF"), lengths):
            reader.add(columns)
    return reader.result()


def records(numbered: Iterable[tuple[int, int, bytes]], lengths: dict[str, int]
            ) -> Iterator[tuple[int, int, list[str]]]:
    """(line number, byte offset, columns) of every record, each checked; comments skipped."""
    for number, offset, raw in numbered:
        if not raw.strip() or raw.startswith(b"#"):
            continue
        columns = _text(raw, number, "GTF").split("\t")
        if len(columns) != GTF_COLUMNS:
            raise GtfProblem(f"line {number} of the GTF has {len(columns)} tab-separated "
                             f"columns, not {GTF_COLUMNS}")
        contig, start, end, strand = columns[0], columns[3], columns[4], columns[6]
        if not (_POSITION.fullmatch(start) and _POSITION.fullmatch(end)):
            raise GtfProblem(f"line {number} of the GTF has a start or end that isn't a number")
        if not 1 <= int(start) <= int(end):
            raise GtfProblem(f"line {number} of the GTF runs from {start} to {end}; a start is "
                             "at least 1 and no later than its end")
        if contig not in lengths:
            raise GtfProblem(f"line {number} of the GTF is on {contig}, which the FASTA "
                             "doesn't have")
        if int(end) > lengths[contig]:
            raise GtfProblem(f"line {number} of the GTF ends at {end}, past the end of "
                             f"{contig} ({lengths[contig]} bases in the FASTA)")
        if strand not in STRANDS:
            raise GtfProblem(f"line {number} of the GTF has strand {strand!r}, not +, - or .")
        yield number, offset, columns


class GeneReader:
    """One position per gene ID: its `gene` lines' span, or the span of its other lines; and
    each transcript's gene and span. A `gene` or `transcript` line must cover the gene's or
    transcript's other lines."""

    def __init__(self) -> None:
        self.from_gene_lines: dict[str, Span] = {}
        self.from_others: dict[str, Span] = {}
        self.transcripts: dict[str, Transcript] = {}

    def add(self, columns: list[str]) -> None:
        pairs = dict(attributes(columns[8]))
        gene_id = pairs.get("gene_id", "").strip()
        if not gene_id:
            return
        place = (columns[0], int(columns[3]), int(columns[4]), columns[6])
        table = self.from_gene_lines if columns[2] == "gene" else self.from_others
        gene = table.setdefault(gene_id, Span())
        gene.add(*place)
        if gene.name is None:
            gene.name = next((pairs[k].strip() for k in NAME_KEYS
                              if pairs.get(k, "").strip()), None)
        transcript_id = pairs.get("transcript_id", "").strip()
        if transcript_id and columns[2] != "gene":
            transcript = self.transcripts.setdefault(transcript_id, Transcript())
            transcript.genes.add(gene_id)
            (transcript.line if columns[2] == "transcript" else transcript.parts).add(*place)

    def result(self) -> list[dict[str, Any]]:
        self._check_transcripts()
        found, split, too_long, outside = [], [], [], []
        for gene_id in sorted(self.from_gene_lines.keys() | self.from_others.keys()):
            gene = self.from_gene_lines.get(gene_id) or self.from_others[gene_id]
            other = self.from_others.get(gene_id, Span())
            contigs = gene.contigs | other.contigs
            if len(contigs) > 1:
                split.append(gene_id)
                continue
            if gene is not other and not gene.covers(other):
                outside.append(gene_id)
                continue
            contig = next(iter(contigs))
            name = gene.name or other.name
            if max(len(gene_id), len(name or ""), len(contig)) > MAX_NAME_CHARS:
                too_long.append(gene_id[:40])
                continue
            found.append({
                "gene_id": gene_id, "gene_name": name, "contig": contig,
                "gene_start": gene.start, "gene_end": gene.end,
                "strand": gene.strand,
            })
        if split:
            raise GtfProblem(f"genes sit on more than one contig in the GTF: {names(split)}; "
                             "Bloom holds one position per gene")
        if outside:
            raise GtfProblem(f"genes have lines outside their `gene` line in the GTF: "
                             f"{names(outside)}")
        if too_long:
            raise GtfProblem(f"genes have an ID, name or contig over {MAX_NAME_CHARS} "
                             f"characters: {names(too_long)}")
        if not found:
            raise GtfProblem("the GTF has no genes (no line carries a gene_id)")
        return sorted(found, key=lambda g: (g["contig"], g["gene_start"], g["gene_id"]))


    def _check_transcripts(self) -> None:
        shared = [t for t, v in self.transcripts.items() if len(v.genes) > 1]
        if shared:
            raise GtfProblem(f"transcripts belong to more than one gene in the GTF: "
                             f"{names(sorted(shared))}")
        outside = [t for t, v in self.transcripts.items()
                   if v.line.start and not v.line.covers(v.parts)]
        if outside:
            raise GtfProblem(f"transcripts have lines outside their `transcript` line in the "
                             f"GTF: {names(sorted(outside))}")


def names(values: Iterable[str]) -> str:
    values = list(values)
    shown = ", ".join(values[:NAMED_IN_MESSAGE])
    more = len(values) - NAMED_IN_MESSAGE
    return f"{shown} and {more} more" if more > 0 else shown


def _text(raw: bytes, number: int, what: str) -> str:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GtfProblem(f"line {number} of the {what} is not UTF-8 text") from exc
    if "\x00" in text:
        raise GtfProblem(f"line {number} of the {what} contains a NUL character")
    return text
