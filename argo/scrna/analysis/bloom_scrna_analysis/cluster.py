"""cluster: the base's PCA -> neighbour graph, Leiden clusters and UMAP, as a part."""

from __future__ import annotations

import json
from pathlib import Path

from .steps import ANALYSIS_DIR, BASE, MARKER, PART, part_of
from .store import Store

NAME = "cluster"
N_NEIGHBORS = 15
RESOLUTION = 1.0
LEIDEN_ITERATIONS = 2
RANDOM_STATE = 0


def compute_part(base):
    import anndata as ad
    import pandas as pd
    import scanpy as sc

    work = ad.AnnData(
        obs=pd.DataFrame(index=base.obs_names.copy()),
        obsm={"X_pca": base.obsm["X_pca"]},
    )
    n_neighbors = min(N_NEIGHBORS, base.n_obs - 1)
    sc.pp.neighbors(
        work, n_neighbors=n_neighbors, use_rep="X_pca", random_state=RANDOM_STATE
    )
    sc.tl.leiden(
        work,
        resolution=RESOLUTION,
        flavor="igraph",
        n_iterations=LEIDEN_ITERATIONS,
        directed=False,
        random_state=RANDOM_STATE,
    )
    sc.tl.umap(work, random_state=RANDOM_STATE)
    return part_of(
        base,
        NAME,
        obs={"leiden": work.obs["leiden"].values},
        obsm={"X_umap": work.obsm["X_umap"]},
        params={
            "n_neighbors": n_neighbors,
            "resolution": RESOLUTION,
            "leiden_iterations": LEIDEN_ITERATIONS,
            "random_state": RANDOM_STATE,
            "n_clusters": int(work.obs["leiden"].nunique()),
        },
    )


def run(store: Store, workdir: Path) -> None:
    import anndata as ad

    folder = f"{ANALYSIS_DIR}/{NAME}"
    if store.exists(f"{folder}/{MARKER}"):
        print(f"Already done: {store.uri(f'{folder}/{PART}')}")
        return
    base = ad.read_h5ad(store.get(BASE, workdir / "base.h5ad"))
    part = compute_part(base)
    local = workdir / PART
    part.write_h5ad(local)
    store.put(local, f"{folder}/{PART}")
    store.write_text(f"{folder}/{MARKER}", json.dumps(dict(part.uns[NAME])) + "\n")
    print(
        f"{part.uns[NAME]['n_clusters']} clusters over {part.n_obs} cells -> {store.uri(folder)}"
    )
