"""The structure check for Bloom's h5ad format: the parts that need no declared names.

Which column holds the cell type or the genotype is declared when the dataset is loaded, so
those checks stay with the load. Everything else is checked here, before a file is stored:
a stored object cannot be replaced, so a broken one would stay until an admin removed it.

h5py and numpy come from the optional `scrna` extra and are imported only when a check runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

TRANSFORMS = ("log1p", "log2p", "none")
SCALINGS = ("library_size", "none", "other")

# Values read at a time when scanning a matrix, so a large file is never read whole.
SCAN_VALUES = 4 * 1024 * 1024

INSTALL = "pip install 'bloomctl[scrna]'"


class FormatError(ValueError):
    """The file does not meet the format; the message names the first problem."""


class MissingExtra(ImportError):
    """h5py or numpy is not installed."""


@dataclass(frozen=True)
class Summary:
    n_cells: int
    n_genes: int
    normalization: dict[str, Any] | None
    layers: frozenset[str]


def _modules():
    try:
        import h5py
        import numpy
    except ImportError as exc:
        raise MissingExtra(f"the structure check needs h5py and numpy: {INSTALL}") from exc
    return h5py, numpy


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def check_structure(path: Path) -> Summary:
    """Check ``path`` against the format; raise :class:`FormatError` naming the first problem.

    A missing `uns['normalization']` is reported, not refused: whether it is allowed depends
    on a dataset loaded from this exact file, which the caller looks up.
    """
    h5py, np = _modules()
    try:
        f = h5py.File(path, "r")
    except OSError as exc:
        raise FormatError("not an HDF5 file") from exc
    with f:
        if _text(f.attrs.get("encoding-type")) != "anndata":
            raise FormatError(
                "not an AnnData file: it carries no AnnData encoding; save it with anndata 0.8 "
                "or later"
            )
        if "X" not in f:
            raise FormatError("no X: the file holds no expression matrix")
        n_cells, n_genes = _shape(h5py, f["X"], "X")
        if not n_cells or not n_genes:
            raise FormatError(f"X is empty ({n_cells} x {n_genes})")
        _scan(h5py, np, f["X"], "X")
        _ids(h5py, f, "obs", "cell", n_cells, "rows")
        _ids(h5py, f, "var", "gene", n_genes, "columns")
        layers = frozenset(f["layers"].keys()) if "layers" in f else frozenset()
        normalization = _fields(h5py, f, "uns/normalization")
        if normalization is not None:
            problem = normalization_problem(normalization, layers=layers)
            if problem:
                raise FormatError(problem)
        _umap(h5py, f, n_cells)
        if "counts" in layers:
            shape = _shape(h5py, f["layers/counts"], "layers['counts']")
            if shape != (n_cells, n_genes):
                raise FormatError(
                    f"layers['counts'] is {shape[0]} x {shape[1]}; X is {n_cells} x {n_genes}"
                )
            _scan(h5py, np, f["layers/counts"], "layers['counts']", non_negative=True)
    return Summary(n_cells, n_genes, normalization, layers)


def normalization_problem(block: dict[str, Any], *, layers) -> str | None:
    """What is wrong with a normalization block, or None. ``layers`` are the file's layer names."""
    where = "uns['normalization']"
    transform = block.get("transform")
    if transform not in TRANSFORMS:
        return f"{where} transform is {transform!r}; use one of {', '.join(TRANSFORMS)}"
    scaling = block.get("scaling")
    if scaling not in SCALINGS:
        return f"{where} scaling is {scaling!r}; use one of {', '.join(SCALINGS)}"
    target = block.get("target_sum")
    if scaling == "library_size" and (
        isinstance(target, bool) or not isinstance(target, (int, float)) or not target > 0
    ):
        return f"{where} uses library_size scaling but gives no positive target_sum"
    description = block.get("description")
    if description is not None and not isinstance(description, str):
        return f"{where} description is not text"
    if scaling == "other" and not (description or "").strip():
        return f"{where} uses 'other' scaling but gives no description of it"
    layer = block.get("counts_layer")
    if layer is not None and layer not in layers:
        return f"{where} names counts_layer {layer!r}, which the file does not hold"
    return None


def _shape(h5py, node, what: str) -> tuple[int, int]:
    if isinstance(node, h5py.Group):
        kind = _text(node.attrs.get("encoding-type"))
        if kind not in ("csr_matrix", "csc_matrix"):
            raise FormatError(f"{what} is stored as {kind or 'an unknown encoding'}")
        shape = tuple(int(v) for v in node.attrs["shape"])
    else:
        shape = tuple(int(v) for v in node.shape)
    if len(shape) != 2:
        raise FormatError(f"{what} is not two-dimensional")
    return shape


def _scan(h5py, np, node, what: str, *, non_negative: bool = False) -> None:
    """Every stored value is finite (and not negative, when asked), read in bounded slices."""
    values = node["data"] if isinstance(node, h5py.Group) else node
    rows = values.shape[0]
    per_slice = SCAN_VALUES if values.ndim == 1 else max(1, SCAN_VALUES // max(1, values.shape[1]))
    for start in range(0, rows, per_slice):
        block = values[start:start + per_slice]
        if not np.isfinite(block).all():
            raise FormatError(f"{what} holds a value that is not finite")
        if non_negative and (block < 0).any():
            raise FormatError(f"{what} holds a negative value")


def _index(h5py, group) -> list[str | None]:
    """An index's values; None where a nullable index marks a value missing."""
    name = _text(group.attrs.get("_index")) or "_index"
    if name not in group:
        raise FormatError(f"{group.name.strip('/')} has no index")
    node = group[name]
    if isinstance(node, h5py.Group):  # nullable string array: values plus a missing mask
        values = node["values"][:]
        mask = node["mask"][:] if "mask" in node else [False] * len(values)
        return [None if bool(missing) else _text(v) for v, missing in zip(values, mask)]
    return [_text(v) for v in node[:]]


def _ids(h5py, f, key: str, noun: str, expected: int, axis: str) -> None:
    if key not in f:
        raise FormatError(f"no {key}: the file lists no {noun}s")
    ids = _index(h5py, f[key])
    if len(ids) != expected:
        raise FormatError(f"{len(ids)} {noun} IDs for {expected} {axis} of X")
    seen: set[str] = set()
    for number, value in enumerate(ids, start=1):
        if value is None or not value.strip():
            raise FormatError(f"{noun} {number} has no ID")
        if value in seen:
            raise FormatError(f"{noun} ID {value!r} appears more than once")
        seen.add(value)


def _fields(h5py, f, path: str) -> dict[str, Any] | None:
    """A group of single values, such as `uns['normalization']`, as a dict; None if absent."""
    if path not in f:
        return None
    node = f[path]
    where = "uns['normalization']"
    if not isinstance(node, h5py.Group):
        raise FormatError(f"{where} is not a set of fields")
    out: dict[str, Any] = {}
    for key, child in node.items():
        if isinstance(child, h5py.Group):
            raise FormatError(f"{where}['{key}'] is not a single value")
        value = child[()]
        if isinstance(value, bytes):
            value = value.decode("utf-8", errors="replace")
        elif hasattr(value, "item"):
            value = value.item()
        out[key] = value
    return out


def _umap(h5py, f, n_cells: int) -> None:
    arrays = f["obsm"].values() if "obsm" in f else ()
    for node in arrays:
        if isinstance(node, h5py.Dataset) and node.ndim == 2 and node.shape == (n_cells, 2):
            return
    raise FormatError(
        f"no obsm array has two columns and one row per cell ({n_cells}); the UMAP "
        "coordinates belong there"
    )
