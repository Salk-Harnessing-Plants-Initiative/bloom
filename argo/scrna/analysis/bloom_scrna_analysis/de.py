"""de: each Leiden cluster against the rest of the cells, on the base's normalised expression, as a
part holding scanpy's rank_genes_groups result in uns['de'] for `bloomctl --de-key de`."""

from __future__ import annotations

import json
from pathlib import Path

from .steps import ANALYSIS_DIR, BASE, MARKER, PART, part_of
from .store import Store

NAME = "de"
GROUPBY = "leiden"
METHOD = "wilcoxon"
CORRECTION = "benjamini-hochberg"
# scanpy can't test a cluster of one cell; such clusters are left out of the result.
MIN_CELLS = 2
CLUSTER_PART = f"{ANALYSIS_DIR}/cluster/{PART}"


def testable(labels) -> list[str]:
    """The clusters scanpy can compare with the rest: enough cells, and some cells outside."""
    counts = labels.value_counts()
    total = int(counts.sum())
    return sorted(
        (str(c) for c, n in counts.items() if n >= MIN_CELLS and total - n >= MIN_CELLS),
        key=lambda c: (len(c), c),
    )


def compute_part(base, leiden):
    """The part: uns['de'] holds the result, or is empty when fewer than two clusters can be
    compared (one cluster has nothing to be compared with)."""
    import anndata as ad
    import pandas as pd
    import scanpy as sc

    labels = pd.Series(pd.Categorical(leiden), index=base.obs_names, name=GROUPBY)
    groups = testable(labels)
    part = part_of(base, NAME)
    if len(groups) < 2:
        part.uns[NAME] = {}
        return part
    work = ad.AnnData(X=base.X, obs=pd.DataFrame({GROUPBY: labels}), var=base.var[[]].copy())
    # base.X is normalize_total + natural log1p; scanpy reads the base back for fold changes.
    work.uns["log1p"] = {"base": None}
    sc.tl.rank_genes_groups(
        work, groupby=GROUPBY, groups=groups, reference="rest", method=METHOD,
        corr_method=CORRECTION, pts=True, n_genes=work.n_vars, use_raw=False,
        key_added=NAME,
    )
    part.uns[NAME] = work.uns[NAME]
    return part


def run(store: Store, workdir: Path) -> None:
    import anndata as ad

    folder = f"{ANALYSIS_DIR}/{NAME}"
    if store.exists(f"{folder}/{MARKER}"):
        print(f"Already done: {store.uri(f'{folder}/{PART}')}")
        return
    base = ad.read_h5ad(store.get(BASE, workdir / "base.h5ad"))
    clusters = ad.read_h5ad(store.get(CLUSTER_PART, workdir / "cluster.h5ad"))
    part = compute_part(base, clusters[base.obs_names].obs[GROUPBY].values)
    local = workdir / PART
    part.write_h5ad(local)
    store.put(local, f"{folder}/{PART}")
    result = part.uns[NAME]
    tested = list(result["names"].dtype.names) if result else []
    summary = {"groupby": GROUPBY, "method": METHOD, "clusters_tested": tested}
    store.write_text(f"{folder}/{MARKER}", json.dumps(summary) + "\n")
    print(f"{len(tested)} clusters each against the rest over {part.n_obs} cells -> "
          f"{store.uri(folder)}" if tested else
          f"Fewer than two clusters can be compared, so there is no DE -> {store.uri(folder)}")
