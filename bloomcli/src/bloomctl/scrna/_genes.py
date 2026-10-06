"""Reading a dataset's genes, and each gene's value in every cell, out of an h5ad.

The matrix is never read whole. Genes are taken a block at a time, each block sized so its
values fit in a fixed budget, so the memory this needs does not grow with the dataset.
"""

from __future__ import annotations

import re
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

from ._text import listed, visible
from ._writer import DOTS_ONLY, LoadError

# Non-zero values kept at once while reading a block of genes. Each costs about 20 bytes with
# its cell and gene, so a block stays near 50 MB whatever the dataset's size.
BLOCK_VALUES = 2 * 1024 * 1024

# Non-zero values read per slice while scanning a cells-down matrix for a block of genes.
SLICE_VALUES = 512 * 1024

# The annotation release some exports append to a gene name, e.g. AT1G01010.Araport11.447.
RELEASE_SUFFIX = re.compile(r"\.Araport11\.\d+$")

# A gene name becomes part of an object path, so it is held to accession characters.
SAFE_GENE_NAME = re.compile(r"[A-Za-z0-9._-]+")


def strip_release(gene: str) -> str:
    return RELEASE_SUFFIX.sub("", gene)


def parse_expectations(pairs) -> dict[str, int]:
    """`GENE=COUNT` arguments, refused rather than ignored when malformed."""
    out: dict[str, int] = {}
    for pair in pairs:
        name, sep, count = pair.partition("=")
        if not sep or not name.strip() or not count.strip().isdigit():
            raise LoadError(f"--expect-nonzero wants GENE=COUNT, got {visible(pair)!r}")
        out[strip_release(name.strip())] = int(count)
    return out


def read_names(path: Path) -> list[str]:
    """The file's gene names, as plain accessions, refused unless each can name one object."""
    import h5py

    from ._format import _index

    with _readable(path):
        with h5py.File(path, "r") as f:
            names = [strip_release(str(v)) for v in _index(h5py, f["var"])]
    if not names:
        raise LoadError(f"{path.name} holds no genes")
    blank = sum(1 for g in names if not g.strip())
    if blank:
        raise LoadError(f"{blank} gene names are blank")
    unsafe = sorted({g for g in names if not SAFE_GENE_NAME.fullmatch(g) or DOTS_ONLY.fullmatch(g)})
    if unsafe:
        raise LoadError(
            f"{len(unsafe)} gene names cannot be part of an object path, e.g. "
            f"{listed(unsafe[:5])}; they may hold letters, digits, '.', '_' and '-'"
        )
    # Two genes with one name would write to one object, the second replacing the first.
    twice = sorted(g for g, n in Counter(names).items() if n > 1)
    if twice:
        raise LoadError(
            f"{len(twice)} gene names appear more than once and would share one object, e.g. "
            f"{listed(twice[:5])}"
        )
    return names


def check_expectations(path: Path, names: list[str], expectations: dict[str, int]) -> None:
    """Each pinned gene is non-zero in exactly the cells said, e.g. a transgene's."""
    if not expectations:
        return
    position = {g: i for i, g in enumerate(names)}
    absent = sorted(g for g in expectations if g not in position)
    if absent:
        raise LoadError(f"--expect-nonzero names {listed(absent)}, not a gene in this file")
    wanted = sorted(position[g] for g in expectations)
    for column, values in gene_values(path, names, only=wanted):
        gene = names[column]
        if len(values) != expectations[gene]:
            raise LoadError(
                f"{visible(gene)} is non-zero in {len(values):,} cells, expected "
                f"{expectations[gene]:,}"
            )


def gene_values(
    path: Path, names: list[str], *, only: list[int] | None = None,
    after_block: Callable[[], None] | None = None,
) -> Iterator[tuple[int, dict[str, float]]]:
    """Each gene's value in every cell that has one, keyed by the cell's position as text.

    Single-cell expression is mostly zeros, so only non-zero cells are kept and an absent
    key reads as zero. The key is `scrna_cells.cell_number`: 0-based, in file order. A value
    that is not finite is refused naming the gene, since the explorer cannot colour by it
    and JSON cannot spell it. ``after_block`` runs once each block is read and before any of
    it is handed on, so a check that the file has not changed covers every value given out.
    """
    import h5py
    import numpy as np

    # In file order, so each block is a run of ascending columns.
    columns = list(range(len(names))) if only is None else sorted(set(only))
    with _readable(path), h5py.File(path, "r") as f:
        matrix = f["X"]
        for block in _blocks(h5py, np, matrix, columns, len(names)):
            read = list(_read_block(h5py, np, matrix, block))
            if after_block:
                after_block()
            for column, cells, values in read:
                if not np.isfinite(values).all():
                    raise LoadError(
                        f"{visible(names[column])} holds a value that is not finite (NaN or "
                        "infinite)"
                    )
                yield column, {str(int(c)): float(v) for c, v in zip(cells, values) if v != 0}


@contextmanager
def _readable(path: Path):
    """What reading the file raises becomes a refusal naming it, not a traceback."""
    from ._format import READ_FAILURES

    try:
        yield
    except READ_FAILURES as exc:
        raise LoadError(
            f"{visible(path.name)} could not be read: {visible(str(exc) or type(exc).__name__)}"
        ) from exc


def _blocks(h5py, np, matrix, columns: list[int], n_genes: int) -> Iterator[list[int]]:
    """The columns cut into blocks whose non-zero values fit the budget, gene by gene."""
    sizes = _values_per_gene(h5py, np, matrix, n_genes)
    block: list[int] = []
    held = 0
    for column in columns:
        size = int(sizes[column])
        if block and held + size > BLOCK_VALUES:
            yield block
            block, held = [], 0
        block.append(column)
        held += size
    if block:
        yield block


def _values_per_gene(h5py, np, matrix, n_genes: int):
    """How many values each gene holds: every cell for a dense matrix, its non-zeros otherwise."""
    if isinstance(matrix, h5py.Dataset):
        return np.full(n_genes, int(matrix.shape[0]))
    encoding = matrix.attrs.get("encoding-type")
    if isinstance(encoding, bytes):
        encoding = encoding.decode()
    if encoding == "csc_matrix":
        return np.diff(matrix["indptr"][:])
    counts = np.zeros(n_genes, dtype=np.int64)
    indices, total = matrix["indices"], int(matrix["indptr"][-1])
    for start in range(0, total, SLICE_VALUES):
        counts += np.bincount(indices[start:start + SLICE_VALUES], minlength=n_genes)
    return counts


def _read_block(h5py, np, matrix, block: list[int]):
    """(column, cell positions, values) for each gene in the block, in block order."""
    encoding = matrix.attrs.get("encoding-type")
    if isinstance(encoding, bytes):
        encoding = encoding.decode()
    if isinstance(matrix, h5py.Dataset):
        contiguous = block[-1] - block[0] + 1 == len(block)
        slab = matrix[:, block[0]:block[-1] + 1] if contiguous else None
        for column in block:
            values = slab[:, column - block[0]] if contiguous else matrix[:, column]
            cells = np.flatnonzero(values)
            yield column, cells, values[cells]
    elif encoding == "csc_matrix":
        indptr = matrix["indptr"]
        for column in block:
            lo, hi = int(indptr[column]), int(indptr[column + 1])
            yield column, matrix["indices"][lo:hi], matrix["data"][lo:hi]
    else:
        yield from _csr_block(np, matrix, block)


def _csr_block(np, matrix, block: list[int]):
    """A cells-down matrix scanned in row slices, keeping only this block's genes."""
    lo, hi = block[0], block[-1] + 1
    wanted = np.asarray(block)
    contiguous = hi - lo == len(block)
    indptr = matrix["indptr"][:]
    kept_cells, kept_genes, kept_values = [], [], []
    n_rows, row = len(indptr) - 1, 0
    while row < n_rows:
        # As many whole rows as fit the slice's values, and always at least one.
        end = int(np.searchsorted(indptr, indptr[row] + SLICE_VALUES, side="right")) - 1
        end = min(max(end, row + 1), n_rows)
        first, last = int(indptr[row]), int(indptr[end])
        genes = matrix["indices"][first:last]
        values = matrix["data"][first:last]
        cells = np.repeat(np.arange(row, end, dtype=np.int32), np.diff(indptr[row:end + 1]))
        # A scattered block, such as the genes --expect-nonzero names, keeps only those genes.
        keep = (genes >= lo) & (genes < hi) if contiguous else np.isin(genes, wanted)
        kept_cells.append(cells[keep])
        kept_genes.append(genes[keep])
        kept_values.append(values[keep])
        row = end
    cells = np.concatenate(kept_cells) if kept_cells else np.array([], dtype=np.int32)
    genes = np.concatenate(kept_genes) if kept_genes else np.array([], dtype=np.int32)
    values = np.concatenate(kept_values) if kept_values else np.array([])
    del kept_cells, kept_genes, kept_values
    # Rows were read in order, so a stable sort by gene keeps each gene's cells in order.
    order = np.argsort(genes, kind="stable")
    cells, genes, values = cells[order], genes[order], values[order]
    del order
    bounds = np.searchsorted(genes, np.arange(lo, hi + 1))
    for column in block:
        a, b = bounds[column - lo], bounds[column - lo + 1]
        yield column, cells[a:b], values[a:b]
