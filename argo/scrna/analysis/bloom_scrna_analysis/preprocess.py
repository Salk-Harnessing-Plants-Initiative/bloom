"""preprocess: Cell Ranger's filtered matrix -> the base (filtered, normalised, PCA)."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import numpy as np
import scipy.sparse as sp

from .steps import (
    BASE,
    BASE_DIR,
    CHEMISTRY_QC,
    EXIT_NO_MATRIX,
    EXIT_TOO_FEW_CELLS,
    MARKER,
    MATRIX,
    METRICS,
    StepError,
)
from .store import Store

MIN_GENES_PER_CELL = 200
MIN_CELLS_PER_GENE = 3
MIN_CELLS = 50
TARGET_SUM = 10_000
MAX_PCS = 50
HVG_FLAVOR = "seurat"
RANDOM_STATE = 0
DEFAULT_N_TOP_GENES = 2000


def build_base(adata, *, n_top_genes: int = DEFAULT_N_TOP_GENES):
    """Filter, keep raw counts, normalise, pick variable genes and run the PCA, in place."""
    import scanpy as sc

    adata.var["gene_symbols"] = adata.var_names.astype(str)
    adata.var_names = adata.var.pop("gene_ids").astype(str)
    adata.var_names_make_unique()
    adata.X = sp.csr_matrix(adata.X, dtype=np.float32)

    sc.pp.filter_cells(adata, min_genes=MIN_GENES_PER_CELL)
    sc.pp.filter_genes(adata, min_cells=MIN_CELLS_PER_GENE)
    if adata.n_obs < MIN_CELLS:
        raise StepError(
            EXIT_TOO_FEW_CELLS,
            f"only {adata.n_obs} cells have at least {MIN_GENES_PER_CELL} genes; "
            f"at least {MIN_CELLS} are needed to analyse",
        )
    adata.obs["n_genes"] = (
        np.asarray((adata.X > 0).sum(axis=1)).ravel().astype(np.int32)
    )
    adata.obs["total_counts"] = np.asarray(adata.X.sum(axis=1)).ravel()
    adata.var = adata.var.drop(columns=["n_cells"], errors="ignore")

    adata.layers["counts"] = adata.X.copy()
    sc.pp.normalize_total(adata, target_sum=TARGET_SUM)
    sc.pp.log1p(adata)

    n_top = min(n_top_genes, adata.n_vars)
    sc.pp.highly_variable_genes(adata, flavor=HVG_FLAVOR, n_top_genes=n_top)
    n_pcs = min(MAX_PCS, adata.n_obs - 1, int(adata.var["highly_variable"].sum()) - 1)
    sc.tl.pca(
        adata, n_comps=n_pcs, mask_var="highly_variable", random_state=RANDOM_STATE
    )

    for key in ("log1p", "hvg", "pca"):
        adata.uns.pop(key, None)
    adata.uns["normalization"] = {
        "transform": "log1p",
        "scaling": "library_size",
        "target_sum": TARGET_SUM,
        "counts_layer": "counts",
        "description": "scanpy normalize_total then log1p",
    }
    adata.uns["preprocess"] = {
        "min_genes_per_cell": MIN_GENES_PER_CELL,
        "min_cells_per_gene": MIN_CELLS_PER_GENE,
        "target_sum": TARGET_SUM,
        "hvg_flavor": HVG_FLAVOR,
        "n_top_genes": n_top,
        "n_pcs": n_pcs,
        "random_state": RANDOM_STATE,
    }
    return adata


def cellranger_metrics(text: str) -> dict[str, str]:
    """metrics_summary.csv's one row, by column name, values as Cell Ranger wrote them."""
    rows = list(csv.reader(io.StringIO(text)))
    if len(rows) < 2:
        return {}
    return {name.replace("/", "_"): value for name, value in zip(rows[0], rows[1])}


def add_run_records(adata, store: Store) -> None:
    """Cell Ranger's metrics and the chemistry check, when the count step kept them."""
    if store.exists(METRICS):
        adata.uns["cellranger_metrics"] = cellranger_metrics(store.read_text(METRICS))
    if store.exists(CHEMISTRY_QC):
        adata.uns["qc_summary"] = store.read_text(CHEMISTRY_QC)


def run(store: Store, workdir: Path, *, n_top_genes: int = DEFAULT_N_TOP_GENES) -> None:
    import scanpy as sc

    if store.exists(f"{BASE_DIR}/{MARKER}"):
        print(f"Already done: {store.uri(BASE)}")
        return
    if not store.exists(MATRIX):
        raise StepError(EXIT_NO_MATRIX, f"no count matrix at {store.uri(MATRIX)}")
    adata = sc.read_10x_h5(store.get(MATRIX, workdir / "matrix.h5"), gex_only=True)
    print(f"Read {adata.n_obs} cells x {adata.n_vars} genes")
    build_base(adata, n_top_genes=n_top_genes)
    add_run_records(adata, store)
    local = workdir / "base.h5ad"
    adata.write_h5ad(local, compression="gzip")
    store.put(local, BASE)
    store.write_text(
        f"{BASE_DIR}/{MARKER}", json.dumps(dict(adata.uns["preprocess"])) + "\n"
    )
    print(f"Base: {adata.n_obs} cells x {adata.n_vars} genes -> {store.uri(BASE)}")
