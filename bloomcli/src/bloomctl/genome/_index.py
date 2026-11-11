"""Building a genome version's browser files from its stored FASTA and GTF.

The genome browser reads a genome by region, so it needs the FASTA in block gzip (bgzip) with its
.fai and .gzi indexes, and the GTF in position order, bgzipped and tabix-indexed. Those five files,
and every gene's position, make one browser build. Everything here runs before Bloom is told
anything, so a genome that can't be built starts no build.

Memory stays small for a large genome: the FASTA is streamed straight into bgzip, and the GTF is
sorted by keeping only each line's contig, start and place in an unzipped copy.
"""

from __future__ import annotations

import gzip
import zlib
from array import array
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import click

from . import _files, _gtf

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


class IndexProblem(ValueError):
    """The version's files can't make a browser build; nothing was sent."""


@dataclass(frozen=True)
class Built:
    files: dict[str, _files.Prepared]
    genes: list[dict[str, Any]]


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
    """The five files in `out` and the GTF's genes; `work` holds the unzipped GTF meanwhile."""
    pysam = require_htslib()
    work.mkdir(parents=True, exist_ok=True)
    try:
        lengths = _gtf.contig_lengths(fasta_gz)
        _bgzip_fasta(fasta_gz, out / ROLES["fasta"])
        pysam.faidx(str(out / ROLES["fasta"]))
        if _fai_lengths(out / ROLES["fasta_fai"]) != lengths:
            raise IndexProblem("the FASTA's index doesn't match its sequences")
        genes = _sorted_gtf(gtf_gz, lengths, work / "genes.gtf", out / ROLES["gtf"])
        pysam.tabix_index(str(out / ROLES["gtf"]), preset="gff", force=True)
    except _gtf.GtfProblem as exc:
        raise IndexProblem(str(exc)) from exc
    except (OSError, EOFError, zlib.error, pysam.utils.SamtoolsError) as exc:
        raise IndexProblem(f"htslib could not index the files: {exc}") from exc
    files = {role: _prepared(out / name) for role, name in ROLES.items()}
    return Built(files=files, genes=genes)


def _bgzip_fasta(source: Path, dest: Path) -> None:
    """Stream the stored FASTA into bgzip, refusing one that unpacks past the limit."""
    from pysam.libcbgzf import BGZFile

    read = 0
    with gzip.open(source, "rb") as src, BGZFile(str(dest), "wb") as dst:
        for block in iter(lambda: src.read(_files.READ_BYTES), b""):
            read += len(block)
            if read > _gtf.MAX_UNPACKED_BYTES:
                raise _gtf.GtfProblem(f"the FASTA unpacks to more than "
                                      f"{_gtf.MAX_UNPACKED_BYTES // 1024**3} GB")
            dst.write(block)


def _fai_lengths(fai: Path) -> dict[str, int]:
    return {row[0]: int(row[1]) for row in (line.split("\t") for line in
                                             fai.read_text().splitlines() if line)}


def _sorted_gtf(source: Path, lengths: dict[str, int], plain: Path,
                dest: Path) -> list[dict[str, Any]]:
    """Check the GTF, read its genes, and write it bgzipped in position order.

    One pass checks every record and copies it, unzipped, to `plain`, keeping per line only its
    contig's place in the FASTA, its start and where it sits in the copy. A stable sort of those
    keys gives the order; the lines are then copied from `plain` into `dest`. `#!` header lines
    stay at the top; other comments are dropped.
    """
    import numpy
    from pysam.libcbgzf import BGZFile

    rank = {contig: i for i, contig in enumerate(lengths)}
    ranks, starts, offsets, sizes = array("I"), array("Q"), array("Q"), array("I")
    headers: list[bytes] = []
    reader = _gtf.GeneReader()
    written = 0
    with _gtf.open_binary(source) as fh, plain.open("wb") as copy:
        for _, _, columns in _gtf.records(_keep_headers(_gtf.lines(fh, "GTF"), headers),
                                          lengths):
            reader.add(columns)
            line = ("\t".join(columns) + "\n").encode()
            copy.write(line)
            ranks.append(rank[columns[0]])
            starts.append(int(columns[3]))
            offsets.append(written)
            sizes.append(len(line))
            written += len(line)
    genes = reader.result()
    order = numpy.lexsort((numpy.frombuffer(starts, dtype=numpy.uint64),
                           numpy.frombuffer(ranks, dtype=numpy.uint32)))
    with plain.open("rb") as copy, BGZFile(str(dest), "wb") as out:
        for header in headers:
            out.write(header + b"\n")
        for i in order:
            copy.seek(offsets[i])
            out.write(copy.read(sizes[i]))
    plain.unlink()
    return genes


def _keep_headers(numbered: Iterator[tuple[int, int, bytes]], headers: list[bytes]
                  ) -> Iterator[tuple[int, int, bytes]]:
    """Pass every line on, keeping the `#!` header lines that come before the first record."""
    seen_record = False
    for item in numbered:
        raw = item[2]
        if not seen_record and raw.startswith(b"#!"):
            headers.append(raw)
        elif raw.strip() and not raw.startswith(b"#"):
            seen_record = True
        yield item


def _prepared(path: Path) -> _files.Prepared:
    size = path.stat().st_size
    if size > _files.MAX_OBJECT_BYTES:
        raise IndexProblem(
            f"{path.name} is {size / 1024**2:,.0f} MB; Bloom stores genome files of at most "
            f"{_files.MAX_OBJECT_BYTES / 1024**2:,.0f} MB"
        )
    return _files.Prepared(path=path, sha256=_files.sha256_of(path), size=size)
