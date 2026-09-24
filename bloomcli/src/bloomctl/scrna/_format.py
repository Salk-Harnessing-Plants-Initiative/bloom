"""The structure check for Bloom's h5ad format: the parts that need no declared names.

Which column holds the cell type or the genotype is declared when the dataset is loaded, so
those checks stay with the load. Everything else is checked here, before a file is stored:
a stored object cannot be replaced, so a broken one would stay until an admin removed it.

h5py and numpy come from the optional `scrna` extra and are imported only when a check runs.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TRANSFORMS = ("log1p", "log2p", "none")
SCALINGS = ("library_size", "none", "other")

# Where the coordinates are read from, by name; the loader's --umap-key can name
# another, so the check takes one too.
UMAP_KEY = "X_umap"

# scrna_cell_arrays casts x and y to REAL on the way out, so a coordinate above this stores
# fine and then fails for every reader of the dataset. The loader's own limit.
FLOAT32_MAX = 3.4028235e38

# A real embedding gives essentially every cell its own point, so cells stacked on one mean
# the obsm was allocated and never filled. The loader's own share.
MAX_DUPLICATE_POINT_SHARE = 0.001

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


def check_structure(path: Path, *, umap_key: str = UMAP_KEY) -> Summary:
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
        with _reading("the file's encoding"):
            encoding = _text(f.attrs.get("encoding-type"))
        if encoding != "anndata":
            raise FormatError(
                "not an AnnData file: it carries no AnnData encoding; save it with anndata 0.8 "
                "or later"
            )
        with _reading("the file's structure"):
            _refuse_links(h5py, f)
        if "X" not in f:
            raise FormatError("no X: the file holds no expression matrix")
        with _reading("X"):
            n_cells, n_genes = _shape(h5py, f["X"], "X")
            if not n_cells or not n_genes:
                raise FormatError(f"X is empty ({n_cells} x {n_genes})")
            _scan(h5py, np, f["X"], "X")
        with _reading("obs"):
            _ids(h5py, f, "obs", "cell", n_cells, "rows")
        with _reading("var"):
            _ids(h5py, f, "var", "gene", n_genes, "columns")
        with _reading("layers"):
            layers = frozenset(f["layers"].keys()) if "layers" in f else frozenset()
        with _reading("uns['normalization']"):
            normalization = _fields(h5py, f, "uns/normalization")
            if normalization is not None:
                problem = normalization_problem(normalization, layers=layers)
                if problem:
                    raise FormatError(problem)
        with _reading(f"obsm['{umap_key}']"):
            _umap(h5py, np, f, n_cells, umap_key)
        if "counts" in layers:
            with _reading("layers['counts']"):
                shape = _shape(h5py, f["layers/counts"], "layers['counts']")
                if shape != (n_cells, n_genes):
                    raise FormatError(
                        f"layers['counts'] is {shape[0]} x {shape[1]}; X is {n_cells} x {n_genes}"
                    )
                _scan(h5py, np, f["layers/counts"], "layers['counts']", non_negative=True)
    return Summary(n_cells, n_genes, normalization, layers)


# What a malformed file makes h5py and numpy raise. Anything else -- MemoryError, a bug in
# this module -- is not a fact about the file and must not be reported as one.
READ_FAILURES = (OSError, KeyError, IndexError, TypeError, ValueError, AttributeError,
                 ArithmeticError)


@contextmanager
def _reading(what: str):
    """What h5py raises while reading ``what`` becomes a FormatError naming it.

    A file this command refuses is one it was handed to check; a KeyError from a missing
    member is a fact about the file, not a bug to report as a traceback. The exception's type
    stands in for an empty message, so a refusal never ends in a bare colon.
    """
    try:
        yield
    except FormatError:
        raise
    except READ_FAILURES as exc:
        raise FormatError(f"{what} could not be read: {str(exc) or type(exc).__name__}") from exc


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


def _refuse_links(h5py, root) -> None:
    """Refuse a file that does not carry its own contents.

    HDF5 can put a dataset's bytes somewhere else -- a virtual dataset reads them from other
    datasets, external storage keeps them in a plain file on disk -- and a link can name
    something outside the file, or nothing at all. All of those read correctly on the machine
    that wrote them and are empty or broken anywhere else, which an object named by its own
    fingerprint and impossible to replace must never be.

    Walked with a stack rather than recursion, so a deeply nested file is refused, not a crash.
    """
    seen: set[int] = {h5py.h5o.get_info(root.id).addr}
    pending = [(root, frozenset(seen))]
    while pending:
        group, ancestors = pending.pop()
        for name in group:
            where = name if group.name == "/" else f"{group.name.strip('/')}/{name}"
            if not isinstance(group.get(name, getlink=True), h5py.HardLink):
                raise FormatError(
                    f"{where} is a link rather than data held here; re-save the file so every "
                    f"array carries its own values"
                )
            member = group[name]
            if isinstance(member, h5py.Dataset):
                _refuse_outside_storage(h5py, member, where)
            elif isinstance(member, h5py.Group):
                address = h5py.h5o.get_info(member.id).addr
                if address in ancestors:
                    raise FormatError(f"{where} links back into the file; it cannot be read through")
                if address in seen:
                    continue  # reached again by a second hard link, and already walked through
                seen.add(address)
                pending.append((member, ancestors | {address}))


def _refuse_outside_storage(h5py, dataset, where: str) -> None:
    """Whether this dataset's bytes are in this file. HDF5 has two ways for them not to be."""
    layout = dataset.id.get_create_plist()
    if layout.get_layout() == h5py.h5d.VIRTUAL:
        raise FormatError(
            f"{where} reads its values from outside this file; it is a virtual dataset, so the "
            f"file holds only a pointer. Re-save it so the values travel with it"
        )
    if layout.get_external_count() > 0:
        raise FormatError(
            f"{where} keeps its values in another file on disk. Re-save it so the values "
            f"travel with it"
        )


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
        # isfinite is only defined for numbers; a text matrix is a fact about the file.
        if block.dtype.kind not in "fiub":
            raise FormatError(f"{what} holds {block.dtype} values, not numbers")
        if block.dtype.kind == "f" and not np.isfinite(block).all():
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


def _umap(h5py, np, f, n_cells: int, umap_key: str = UMAP_KEY) -> None:
    """The coordinates the explorer plots, read by name and checked as the loader checks them."""
    arrays = f["obsm"] if "obsm" in f else None
    node = arrays.get(umap_key) if arrays is not None else None
    if not isinstance(node, h5py.Dataset) or node.ndim != 2 or tuple(node.shape) != (n_cells, 2):
        found = ", ".join(sorted(arrays)) if arrays is not None and len(arrays) else "nothing"
        raise FormatError(
            f"no obsm['{umap_key}'] with two columns and one row per cell ({n_cells}), which is "
            f"where the UMAP coordinates are read from; obsm holds: {found}"
        )
    coordinates = node[:]
    # isfinite is only defined for numbers.
    if coordinates.dtype.kind not in "fiub":
        raise FormatError(f"obsm['{umap_key}'] holds {coordinates.dtype} values, not numbers")
    if not np.isfinite(coordinates).all():
        raise FormatError(f"obsm['{umap_key}'] holds a coordinate that is not finite")
    largest = float(np.abs(coordinates).max()) if n_cells else 0.0
    if largest > FLOAT32_MAX:
        raise FormatError(
            f"obsm['{umap_key}'] holds coordinates too large to store; the largest is "
            f"{largest:.3g}"
        )
    _, piles = np.unique(coordinates, axis=0, return_counts=True)
    most = int(piles.max()) if len(piles) else 0
    if most > max(1, n_cells * MAX_DUPLICATE_POINT_SHARE):
        raise FormatError(
            f"obsm['{umap_key}'] puts {most} of {n_cells} cells on a single point. Real "
            f"coordinates give essentially every cell its own; this array is unfilled, partly "
            f"unfilled, or rounded so coarsely that cells collide"
        )
