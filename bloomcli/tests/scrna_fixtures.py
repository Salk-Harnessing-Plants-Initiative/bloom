"""Small AnnData files written with h5py in AnnData's own on-disk encoding.

The tests need neither anndata nor pandas this way. Both index layouts real files use are
written: anndata 0.8-0.10 stored an index as a string array, 0.11 on as a nullable string
array with a mask.
"""

from __future__ import annotations

import gzip
from pathlib import Path

import h5py
import numpy as np

STR = h5py.string_dtype()

NORMALIZATION = {
    "transform": "log1p",
    "scaling": "library_size",
    "target_sum": 10000,
    "counts_layer": "counts",
}

X = np.array(
    [
        [0.0, 1.5, 0.0, 2.0],
        [0.7, 0.0, 0.0, 0.0],
        [0.0, 0.0, 3.1, 0.2],
    ]
)

DEFAULT = object()


def _attrs(obj, kind: str, version: str = "0.2.0", **extra) -> None:
    obj.attrs["encoding-type"] = kind
    obj.attrs["encoding-version"] = version
    for key, value in extra.items():
        obj.attrs[key] = value


def _index(group, values, style: str, blanks) -> None:
    group.attrs["_index"] = "_index"
    data = np.array(values, dtype=object)
    if style == "plain":
        _attrs(group.create_dataset("_index", data=data, dtype=STR), "string-array")
        return
    node = group.create_group("_index")
    _attrs(node, "nullable-string-array", "0.1.0", **{"na-value": "NaN"})
    mask = np.zeros(len(values), dtype=bool)
    mask[list(blanks)] = True
    _attrs(node.create_dataset("mask", data=mask), "array")
    _attrs(node.create_dataset("values", data=data, dtype=STR), "string-array")


def _frame(f, key: str, ids, style: str, blanks) -> None:
    group = f.create_group(key)
    _attrs(group, "dataframe")
    _index(group, ids, style, blanks)


def _matrix(parent, key: str, values, sparse: bool) -> None:
    values = np.asarray(values, dtype=float)
    if not sparse:
        _attrs(parent.create_dataset(key, data=values), "array")
        return
    rows, cols = np.nonzero(values)
    indptr = np.searchsorted(rows, np.arange(values.shape[0] + 1))
    group = parent.create_group(key)
    _attrs(group, "csr_matrix", "0.1.0", shape=np.array(values.shape))
    group.create_dataset("data", data=values[rows, cols])
    group.create_dataset("indices", data=cols.astype(np.int32))
    group.create_dataset("indptr", data=indptr.astype(np.int32))


def _dict(parent, key: str):
    group = parent.create_group(key)
    _attrs(group, "dict", "0.1.0")
    return group


def _scalar(parent, key: str, value) -> None:
    if isinstance(value, str):
        _attrs(parent.create_dataset(key, data=value, dtype=STR), "string")
    else:
        _attrs(parent.create_dataset(key, data=value), "numeric-scalar")


def write_h5ad(
    path: Path,
    *,
    x=None,
    counts=DEFAULT,
    obs_ids=None,
    var_ids=None,
    index_style: str = "nullable",
    obs_blanks=(),
    var_blanks=(),
    normalization=DEFAULT,
    obsm=DEFAULT,
    sparse: bool = True,
    anndata: bool = True,
    with_x: bool = True,
) -> Path:
    """Write a three-cell, four-gene file that meets the format unless told otherwise.

    `counts=None` leaves the counts layer out; `normalization=None` leaves the block out;
    `obsm` maps an array's name to its (rows, columns).
    """
    x = X if x is None else np.asarray(x, dtype=float)
    n_cells, n_genes = x.shape
    obs_ids = [f"cell{i}" for i in range(n_cells)] if obs_ids is None else obs_ids
    var_ids = [f"gene{j}" for j in range(n_genes)] if var_ids is None else var_ids
    counts = np.round(np.expm1(x) * 3, 2) if counts is DEFAULT else counts
    normalization = dict(NORMALIZATION) if normalization is DEFAULT else normalization
    obsm = {"X_umap": (n_cells, 2)} if obsm is DEFAULT else obsm
    with h5py.File(path, "w") as f:
        if anndata:
            _attrs(f, "anndata", "0.1.0")
        if with_x:
            _matrix(f, "X", x, sparse)
        _frame(f, "obs", obs_ids, index_style, obs_blanks)
        _frame(f, "var", var_ids, index_style, var_blanks)
        arrays = _dict(f, "obsm")
        for name, shape in obsm.items():
            _attrs(arrays.create_dataset(name, data=np.zeros(shape, dtype=np.float32)), "array")
        layers = _dict(f, "layers")
        if counts is not None:
            _matrix(layers, "counts", counts, sparse)
        uns = _dict(f, "uns")
        if normalization is not None:
            block = _dict(uns, "normalization")
            for key, value in normalization.items():
                _scalar(block, key, value)
    return path


def gzipped(data: bytes) -> bytes:
    return gzip.compress(data, mtime=0)
