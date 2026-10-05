"""Reading a dataset's cells out of an h5ad: coordinates, cell types, samples and labels.

Every check runs before a single row is written, because a dataset that is half loaded looks
to the explorer exactly like one that is complete. anndata is imported only when a file is read.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ._format import (
    FLOAT32_MAX,
    MAX_DUPLICATE_POINT_SHARE,
    MissingDependency,
    missing_dependency_message,
)
from ._text import listed, visible
from ._writer import LoadError

# Cluster colours, one per ordinal, so a cell type is the same colour for every user.
PALETTE = [
    "#4E79A7", "#F28E2B", "#E15759", "#76B7B2", "#59A14F",
    "#EDC948", "#B07AA1", "#FF9DA7", "#9C755F", "#BAB0AC",
    "#86BCB6", "#F1CE63", "#D37295", "#A0CBE8", "#FFBE7D",
    "#8CD17D", "#B6992D", "#499894", "#FABFD2", "#D4A6C8",
    "#79706E", "#D7B5A6", "#6B4C9A",
]

# A label becomes a row of toggles, so it has to be a handful of values. More is a
# measurement, and would reach the browser as hundreds of buttons.
MAX_FACET_VALUES = 12

# The database's limits on a cell's labels (scrna_facets_are_flat_text) and on a genotype
# (scrna_genotypes_lengths).
MAX_FACETS = 32
MAX_FACET_KEY = 64
MAX_FACET_VALUE = 200
MAX_FACETS_JSON = 1024
MAX_GENOTYPE_NAME = 100
MAX_CONSTRUCT = 200
MAX_SAMPLE_NAME = 100  # scrna_cells_replicate_length

# What a missing value looks like once something upstream has called astype(str) on it.
# Stored as-is, each would become a real cell type in the legend, or a barcode naming no cell.
NOT_A_VALUE = {"", "nan", "none", "na", "<na>", "null"}


def read_cells(
    path: Path,
    annotation: str,
    sample_column: str,
    umap_key: str,
    expect_cells: int | None = None,
    source_column: str | None = None,
    genotype_column: str | None = None,
    facet_columns: tuple[str, ...] = (),
) -> dict:
    """Everything the explorer needs from the file, or a LoadError naming what is wrong.

    The coordinates are taken as given: they come out of the same file as the labels, in the
    row order anndata keeps, so nothing here can pair them up wrongly.
    """
    try:
        import h5py
        import numpy as np
        read_elem = _read_elem()
    except ImportError as exc:
        raise MissingDependency(missing_dependency_message()) from exc

    cells = _open(h5py, read_elem, path, umap_key)
    return _read(np, cells, path, annotation, sample_column, umap_key, expect_cells,
                 source_column, genotype_column, facet_columns)


@dataclass(frozen=True)
class _Cells:
    """The parts of a file the load reads: the cell table, the UMAP and the gene count."""

    obs: Any
    obsm: dict  # every obsm name; only the UMAP's array is read
    n_vars: int

    @property
    def n_obs(self) -> int:
        return len(self.obs)

    @property
    def obs_names(self):
        return self.obs.index


def _read_elem():
    """anndata's reader for one part of a file; it moved from experimental in 0.11."""
    try:
        from anndata.io import read_elem
    except ImportError:
        from anndata.experimental import read_elem
    return read_elem


# What anndata raises on a file it cannot read; anything else is a fault here.
READ_FAILURES = (OSError, KeyError, IndexError, TypeError, ValueError, AttributeError,
                 NotImplementedError)


def _read_failures() -> tuple:
    """What anndata raises on a file it cannot read, including its own registry error."""
    try:
        from anndata._io.specs.registry import IORegistryError
    except ImportError:  # a private path, so another anndata may have moved it
        return READ_FAILURES
    return (*READ_FAILURES, IORegistryError)


def _open(h5py, read_elem, path: Path, umap_key: str) -> _Cells:
    """Only the cell table, the UMAP and the matrix's shape: no matrix or layer is read, so the
    memory this needs does not grow with the expression data."""
    try:
        with h5py.File(path, "r") as f:
            obsm = {key: None for key in f["obsm"]} if "obsm" in f else {}
            if umap_key in obsm:
                obsm[umap_key] = read_elem(f["obsm"][umap_key])
            return _Cells(read_elem(f["obs"]), obsm, _n_vars(f["X"]))
    except _read_failures() as exc:
        raise LoadError(
            f"{path.name} could not be read by anndata: {visible(str(exc) or type(exc).__name__)}"
            ". Re-saving it with a current anndata usually fixes this"
        ) from exc


def _n_vars(x) -> int:
    """Genes, from the matrix's recorded shape rather than its values."""
    shape = x.attrs["shape"] if "shape" in x.attrs else x.shape
    return int(shape[1])


def _read(np, adata: _Cells, path: Path, annotation: str, sample_column: str, umap_key: str,
          expect_cells: int | None, source_column: str | None, genotype_column: str | None,
          facet_columns: tuple[str, ...]) -> dict:
    if adata.n_obs == 0:
        raise LoadError(f"{path.name} holds no cells")
    if umap_key not in adata.obsm:
        raise LoadError(
            f"{path.name} has no obsm[{umap_key!r}]; loading needs the UMAP, the explorer "
            f"plots stored coordinates and never computes them. Found: "
            f"{listed(sorted(adata.obsm)) or 'nothing'}"
        )
    coords = _coordinates(np, adata.obsm[umap_key], umap_key)

    named = (("--annotation", annotation), ("--sample-column", sample_column),
             ("--source-column", source_column), ("--genotype-column", genotype_column))
    for flag, column in named:
        if column and column not in adata.obs:
            raise LoadError(
                f"{path.name} has no obs[{visible(column)!r}], the column {flag} names. Found: "
                f"{listed(sorted(adata.obs.columns))}"
            )
    if expect_cells is not None and adata.n_obs != expect_cells:
        raise LoadError(f"expected {expect_cells} cells, the file holds {adata.n_obs}")

    labels = _text_column(adata, annotation)
    samples = _text_column(adata, sample_column)
    sources = _text_column(adata, source_column) if source_column else None
    genotypes = _text_column(adata, genotype_column) if genotype_column else None
    if genotypes is not None:
        _refuse_long_names(genotypes, MAX_GENOTYPE_NAME, "genotype")
    _refuse_long_names(samples, MAX_SAMPLE_NAME, "sample")
    levels = sorted(set(labels))
    if len(levels) > len(PALETTE):
        raise LoadError(
            f"obs[{annotation!r}] has {len(levels)} cell types and there are {len(PALETTE)} "
            "colours to tell them apart; two would be drawn identically"
        )
    return {
        "n_cells": int(adata.n_obs),
        "n_genes": int(adata.n_vars),
        "x": [float(v) for v in coords[:, 0]],
        "y": [float(v) for v in coords[:, 1]],
        "labels": labels,
        "samples": samples,
        "levels": levels,
        "barcodes": _barcodes(adata),
        "sources": label_sources(labels, sources) if sources else {},
        "genotypes": genotypes,
        "facets": read_facets(adata, facet_columns) if facet_columns else None,
    }


def _coordinates(np, array, umap_key: str):
    """The UMAP as an (n, 2) float array, refused when it cannot be plotted as stored."""
    try:
        coords = np.asarray(array, dtype=float)
    except (TypeError, ValueError) as exc:
        raise LoadError(f"obsm[{umap_key!r}] does not read as numbers: {exc}") from exc
    if coords.ndim != 2 or coords.shape[1] != 2:
        raise LoadError(
            f"obsm[{umap_key!r}] has shape {coords.shape}; the UMAP needs exactly two columns"
        )
    if not np.isfinite(coords).all():
        raise LoadError(f"obsm[{umap_key!r}] holds values that are not finite")
    largest = float(np.abs(coords).max())
    if largest > FLOAT32_MAX:
        raise LoadError(
            f"obsm[{umap_key!r}] holds coordinates too large to store; the largest is "
            f"{largest:.3g}"
        )
    _, piles = np.unique(coords, axis=0, return_counts=True)
    if piles.max() > max(1, len(coords) * MAX_DUPLICATE_POINT_SHARE):
        raise LoadError(
            f"obsm[{umap_key!r}] puts {piles.max()} of {len(coords)} cells on a single point; "
            "the array is unfilled, partly unfilled, or rounded so coarsely that cells collide"
        )
    return coords


def _refuse_long_names(values: list[str], limit: int, noun: str) -> None:
    """A name the database cannot hold, refused with the longest one shortened."""
    longest = max(values, key=len, default="")
    if len(longest) <= limit:
        return
    shown = visible(longest[:40]) + "…"
    raise LoadError(
        f"{noun.capitalize()} name too long ({len(longest)} characters): {shown!r}. Limit {noun} names to "
        f"{limit} characters in the file and upload again"
    )


def read_facets(adata, columns: tuple[str, ...]) -> list[dict[str, str]]:
    """Each cell's labels to filter the map by, as {column: value}."""
    if len(columns) > MAX_FACETS:
        raise LoadError(f"{len(columns)} label columns; a cell holds at most {MAX_FACETS}")
    per_column = {}
    for column in columns:
        if not column.strip():
            raise LoadError(
                "--facet was given a blank column name; give the name of an obs column, e.g. "
                "--facet treatment"
            )
        if column not in adata.obs:
            raise LoadError(
                f"no obs[{visible(column)!r}] to use as a label. Found: "
                f"{listed(sorted(adata.obs.columns))}"
            )
        if len(column) > MAX_FACET_KEY:
            raise LoadError(
                f"label column {visible(column)!r} is longer than {MAX_FACET_KEY} characters")
        values = _text_column(adata, column)
        levels = sorted(set(values))
        if len(levels) > MAX_FACET_VALUES:
            raise LoadError(
                f"obs[{visible(column)!r}] has {len(levels)} values, more than the {MAX_FACET_VALUES} a "
                "row of toggles can show; it looks like a measurement rather than a label"
            )
        long = [v for v in levels if len(v) > MAX_FACET_VALUE]
        if long:
            raise LoadError(
                f"obs[{visible(column)!r}] has values longer than {MAX_FACET_VALUE} characters, "
                f"e.g. {visible(long[0][:40])!r}"
            )
        per_column[column] = values
    facets = [{c: per_column[c][i] for c in columns} for i in range(adata.n_obs)]
    for i, cell in enumerate(facets):
        if len(json.dumps(cell)) > MAX_FACETS_JSON:
            raise LoadError(
                f"cell {i}'s labels come to more than {MAX_FACETS_JSON} characters; label "
                "fewer columns"
            )
    return facets


def parse_constructs(pairs: tuple[str, ...] | list[str]) -> dict[str, str]:
    """`GENOTYPE=NAME` arguments, refused rather than ignored when malformed."""
    out = {}
    for pair in pairs:
        genotype, sep, construct = pair.partition("=")
        if not sep or not genotype.strip() or not construct.strip():
            raise LoadError(f"--construct wants GENOTYPE=NAME, got {pair!r}")
        if len(construct.strip()) > MAX_CONSTRUCT:
            raise LoadError(
                f"the construct for {genotype.strip()!r} is longer than {MAX_CONSTRUCT} "
                "characters"
            )
        out[genotype.strip()] = construct.strip()
    return out


def genotype_rows(
    genotypes: list[str], control: str | None, constructs: dict[str, str]
) -> list[dict]:
    """One row per genotype in the file: which is the control, and each line's construct."""
    names = sorted(set(genotypes))
    if control is None:
        raise LoadError(
            "--genotype-column needs --control, naming which genotype is the control; it is "
            "not guessed"
        )
    if control not in names:
        raise LoadError(
            f"--control names {visible(control)!r}, which is not a genotype in this file: "
            f"{listed(names)}"
        )
    unknown = sorted(set(constructs) - set(names))
    if unknown:
        raise LoadError(
            f"--construct names {listed(unknown)}, not a genotype in this file: {listed(names)}"
        )
    return [
        {"name": n, "is_control": n == control, "construct": constructs.get(n)}
        for n in names
    ]


def label_sources(labels: list[str], sources: list[str]) -> dict[str, str]:
    """Per cell type, where its label came from: one source, or each source's share."""
    per_type: dict[str, Counter] = {}
    for label, source in zip(labels, sources):
        per_type.setdefault(label, Counter())[source] += 1
    summary = {}
    for label, counts in per_type.items():
        if len(counts) == 1:
            summary[label] = next(iter(counts))
        else:
            total = sum(counts.values())
            summary[label] = ", ".join(
                f"{name} ({round(n * 100 / total)}%)" for name, n in counts.most_common()
            )
    return summary


def _barcodes(adata) -> list[str]:
    """The cell barcodes, refusing any that cannot identify one cell.

    anndata.concat leaves 10x barcodes repeated across samples unless given index_unique.
    """
    text = [str(v).strip() for v in adata.obs_names]
    blank = sum(1 for v in text if v.lower() in NOT_A_VALUE)
    if blank:
        raise LoadError(f"{blank} of {len(text)} cells have no barcode; every cell needs one")
    repeated = [b for b, n in Counter(text).items() if n > 1]
    if repeated:
        shown = ", ".join(repr(b) for b in sorted(repeated)[:3])
        raise LoadError(
            f"{len(text) - len(set(text))} of {len(text)} barcodes are duplicates "
            f"({len(repeated)} repeated, e.g. {shown}). Cells concatenated without "
            "index_unique do this; a barcode has to name one cell"
        )
    return text


def _text_column(adata, column: str) -> list[str]:
    """A column as text, refusing a missing value rather than storing it as "nan"."""
    values = adata.obs[column]
    missing = int(values.isna().sum())
    if missing:
        raise LoadError(
            f"obs[{visible(column)!r}] has {missing} missing value(s); every cell needs one")
    text = [str(v) for v in values]
    blank = sum(1 for v in text if v.strip().lower() in NOT_A_VALUE)
    if blank:
        raise LoadError(
            f"obs[{visible(column)!r}] has {blank} value(s) that are blank or read as a missing "
            "value; "
            "every cell needs a name"
        )
    return text
