"""Building a genome version's browser files from its stored FASTA and GTF.

The genome browser reads a genome by region, so it needs the FASTA in block gzip (bgzip) with its
.fai and .gzi indexes, and the GTF sorted by position, bgzipped and tabix-indexed. Those five
files, and every gene's position read from the GTF, make one browser build. Everything here runs
before Bloom is told anything, so a genome that can't be built starts no build.
"""

from __future__ import annotations

import gzip
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator

import click

from . import _files

INSTALL_HINT = "pip install 'bloomctl[genome-index]'"
# Each file's role and its name in the build's folder, as genome_browser_build_roles() lists them.
ROLES = {
    "fasta": "genome.fa.gz",
    "fasta_fai": "genome.fa.gz.fai",
    "fasta_gzi": "genome.fa.gz.gzi",
    "gtf": "genes.gtf.gz",
    "gtf_tbi": "genes.gtf.gz.tbi",
}
# Bloom takes at most this many genes in one call.
GENE_BATCH = 5000
# How many refused gene IDs or contigs a message names before saying how many more there are.
NAMED_IN_MESSAGE = 10
_ATTRIBUTE = re.compile(r'\s*([^\s;]+)\s+"([^"]*)"\s*;?')
_GTF_COLUMNS = 9


class IndexProblem(ValueError):
    """The version's files can't make a browser build; nothing was sent."""


@dataclass(frozen=True)
class Built:
    files: dict[str, _files.Prepared]
    genes: list[dict[str, Any]]
    gtf_was_sorted: bool


def require_htslib() -> Any:
    """pysam, which carries htslib; a clear instruction when the extra isn't installed."""
    try:
        import pysam
    except ImportError as exc:
        raise click.ClickException(
            f"`genome index` needs htslib, which comes with an optional extra: {INSTALL_HINT}"
        ) from exc
    return pysam


def tool_versions() -> dict[str, str]:
    from .. import __version__

    pysam = require_htslib()
    return {"bloomctl": __version__, "pysam": pysam.__version__,
            "htslib": pysam.version.__htslib_version__}


def batches(genes: list[dict[str, Any]]) -> Iterator[list[dict[str, Any]]]:
    for start in range(0, len(genes), GENE_BATCH):
        yield genes[start:start + GENE_BATCH]


def build(fasta_gz: Path, gtf_gz: Path, out: Path, work: Path) -> Built:
    """The five files in `out` and the GTF's genes; `work` holds the unzipped sources."""
    pysam = require_htslib()
    work.mkdir(parents=True, exist_ok=True)
    plain_fasta = _gunzip(fasta_gz, work / "genome.fa")
    fasta = out / ROLES["fasta"]
    pysam.tabix_compress(str(plain_fasta), str(fasta), force=True)
    pysam.faidx(str(fasta))
    lengths = _contig_lengths(out / ROLES["fasta_fai"])

    lines = _gtf_records(gtf_gz)
    genes = _genes(lines)
    _check_against_fasta(genes, lengths)
    order = {contig: rank for rank, contig in enumerate(lengths)}
    ranked = sorted(lines, key=lambda f: (order.get(f[0], len(order)), f[0], int(f[3])))
    plain_gtf = work / "genes.gtf"
    plain_gtf.write_text("".join("\t".join(f) + "\n" for f in ranked))
    gtf = out / ROLES["gtf"]
    pysam.tabix_compress(str(plain_gtf), str(gtf), force=True)
    pysam.tabix_index(str(gtf), preset="gff", force=True)

    files = {role: _prepared(out / name) for role, name in ROLES.items()}
    return Built(files=files, genes=genes, gtf_was_sorted=ranked == lines)


def _gunzip(source: Path, dest: Path) -> Path:
    with gzip.open(source, "rb") as src, dest.open("wb") as dst:
        shutil.copyfileobj(src, dst, _files.READ_BYTES)
    return dest


def _contig_lengths(fai: Path) -> dict[str, int]:
    """Each contig's length, in the FASTA's order."""
    return {row[0]: int(row[1]) for row in (line.split("\t") for line in
                                             fai.read_text().splitlines() if line)}


def _gtf_records(gtf_gz: Path) -> list[list[str]]:
    """The GTF's records as columns; comments and blank lines dropped."""
    records = []
    with gzip.open(gtf_gz, "rt", errors="replace") as fh:
        for number, line in enumerate(fh, 1):
            line = line.rstrip("\r\n")
            if not line.strip() or line.startswith("#"):
                continue
            columns = line.split("\t")
            if len(columns) != _GTF_COLUMNS or not (columns[3].isdigit() and columns[4].isdigit()):
                raise IndexProblem(f"line {number} of the GTF is not a GTF record: {line[:80]!r}")
            records.append(columns)
    return records


def _attributes(column: str) -> dict[str, str]:
    return {key: value for key, value in _ATTRIBUTE.findall(column)}


def _genes(records: list[list[str]]) -> list[dict[str, Any]]:
    """One position per gene ID: its `gene` line, or the span of its other lines."""
    gene_lines: dict[str, list[list[str]]] = {}
    other_lines: dict[str, list[list[str]]] = {}
    for record in records:
        gene_id = _attributes(record[8]).get("gene_id", "").strip()
        if gene_id:
            target = gene_lines if record[2] == "gene" else other_lines
            target.setdefault(gene_id, []).append(record)
    repeated = sorted(g for g, lines in gene_lines.items() if len(lines) > 1)
    if repeated:
        raise IndexProblem(f"gene IDs appear more than once in the GTF: {_names(repeated)}; a "
                           "browser build holds one position per gene")
    genes, split = [], []
    for gene_id in sorted(gene_lines.keys() | other_lines.keys()):
        lines = gene_lines.get(gene_id) or other_lines[gene_id]
        contigs = {line[0] for line in gene_lines.get(gene_id, []) + other_lines.get(gene_id, [])}
        if len(contigs) > 1:
            split.append(gene_id)
            continue
        strands = {line[6] for line in lines}
        names = [_attributes(line[8]).get("gene_name", "").strip() for line in lines]
        genes.append({
            "gene_id": gene_id,
            "gene_name": next((n for n in names if n), None),
            "contig": lines[0][0],
            "gene_start": min(int(line[3]) for line in lines),
            "gene_end": max(int(line[4]) for line in lines),
            "strand": strands.pop() if len(strands) == 1 and strands <= {"+", "-"} else ".",
        })
    if split:
        raise IndexProblem(f"genes sit on more than one contig in the GTF: {_names(split)}; a "
                           "browser build holds one position per gene")
    if not genes:
        raise IndexProblem("the GTF has no genes (no line carries a gene_id)")
    return sorted(genes, key=lambda g: (g["contig"], g["gene_start"], g["gene_id"]))


def _check_against_fasta(genes: list[dict[str, Any]], lengths: dict[str, int]) -> None:
    missing = sorted({g["contig"] for g in genes} - lengths.keys())
    if missing:
        raise IndexProblem(f"the GTF names contigs the FASTA doesn't have: {_names(missing)}")
    for gene in genes:
        if gene["gene_end"] > lengths[gene["contig"]]:
            raise IndexProblem(
                f"gene {gene['gene_id']} ends at {gene['gene_end']}, past the end of "
                f"{gene['contig']} ({lengths[gene['contig']]} bases in the FASTA)"
            )


def _names(values: Iterable[str]) -> str:
    values = list(values)
    shown = ", ".join(values[:NAMED_IN_MESSAGE])
    more = len(values) - NAMED_IN_MESSAGE
    return f"{shown} and {more} more" if more > 0 else shown


def _prepared(path: Path) -> _files.Prepared:
    size = path.stat().st_size
    if size > _files.MAX_OBJECT_BYTES:
        raise IndexProblem(
            f"{path.name} is {size / 1024**2:,.0f} MB; Bloom stores genome files of at most "
            f"{_files.MAX_OBJECT_BYTES / 1024**2:,.0f} MB"
        )
    return _files.Prepared(path=path, sha256=_files.sha256_of(path), size=size)
