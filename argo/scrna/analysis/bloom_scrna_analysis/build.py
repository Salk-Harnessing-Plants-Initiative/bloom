"""build-h5ad: the base and every finished analysis part -> h5ad/<sample>.h5ad."""

from __future__ import annotations

import json
import re
from pathlib import Path

from .steps import (
    ANALYSIS_DIR,
    BASE,
    BASE_DIR,
    EXIT_PARTS_DONT_FIT,
    FINAL_DIR,
    MARKER,
    PART,
    StepError,
    versions,
)
from .store import Store

SAMPLE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
# Each cell's sample, which `bloomctl scrna hdf5 upload` reads (its --sample-column default).
SAMPLE_COLUMN = "sample"


def finished_parts(store: Store) -> list[str]:
    """Analyses with a part and a marker, in name order."""
    preprocess = BASE_DIR.rsplit("/", 1)[-1]
    return [
        name
        for name in store.children(ANALYSIS_DIR)
        if name != preprocess and store.exists(f"{ANALYSIS_DIR}/{name}/{MARKER}")
    ]


def merge(base, part, name: str) -> None:
    """Add a part's obs columns, obsm arrays and uns[name] to the base, refusing any clash."""
    if part.n_obs != base.n_obs or set(part.obs_names) != set(base.obs_names):
        raise StepError(
            EXIT_PARTS_DONT_FIT,
            f"part {name!r} does not hold the same cells as the base",
        )
    part = part[base.obs_names]
    extra_uns = sorted(set(part.uns) - {name})
    if extra_uns:
        raise StepError(
            EXIT_PARTS_DONT_FIT,
            f"part {name!r} writes uns keys other than its own: {extra_uns}",
        )
    taken = [
        f"obs[{c!r}]"
        for c in part.obs.columns
        if c in base.obs.columns or c == SAMPLE_COLUMN
    ]
    taken += [f"obsm[{k!r}]" for k in part.obsm if k in base.obsm]
    taken += [f"uns[{name!r}]"] if name in part.uns and name in base.uns else []
    if taken:
        raise StepError(
            EXIT_PARTS_DONT_FIT,
            f"part {name!r} holds keys already taken: {', '.join(taken)}",
        )
    for column in part.obs.columns:
        base.obs[column] = part.obs[column].values
    for key in part.obsm:
        base.obsm[key] = part.obsm[key]
    if name in part.uns:
        base.uns[name] = part.uns[name]


def compile_file(base, parts: dict[str, object], sample: str):
    """The final AnnData: the base with each part merged in name order, every cell marked
    with the run's sample."""
    import pandas as pd

    for name in sorted(parts):
        merge(base, parts[name], name)
    base.obs[SAMPLE_COLUMN] = pd.Categorical([sample] * base.n_obs)
    base.uns["bloom_pipeline"] = {
        "steps": ["preprocess", *sorted(parts)],
        "versions": versions(),
    }
    return base


def run(
    store: Store, workdir: Path, *, sample: str, publish: Store | None = None
) -> None:
    """Build into store's h5ad/, and copy the file and summary to publish (the run's S3 folder)."""
    import anndata as ad

    if not SAMPLE_NAME.match(sample) or "__" in sample:
        raise ValueError(f"bad sample name {sample!r}")
    names = finished_parts(store)
    # The sample is part of the record, so a file built before it was written in is rebuilt.
    record = {"steps": ["preprocess", *names], "sample": sample}
    marker = f"{FINAL_DIR}/{MARKER}"
    if store.exists(marker) and json.loads(store.read_text(marker)) == record:
        print(
            f"Already built from {', '.join(record['steps'])}: {store.uri(f'{FINAL_DIR}/{sample}.h5ad')}"
        )
        return

    base = ad.read_h5ad(store.get(BASE, workdir / "base.h5ad"))
    parts = {
        name: ad.read_h5ad(
            store.get(f"{ANALYSIS_DIR}/{name}/{PART}", workdir / name / PART)
        )
        for name in names
    }
    final = compile_file(base, parts, sample)

    local = workdir / f"{sample}.h5ad"
    final.write_h5ad(local, compression="gzip")
    summary_text = json.dumps(_summary(final, sample, record, local), indent=2) + "\n"
    for target in (publish, store):
        if target is None:
            continue
        target.put(local, f"{FINAL_DIR}/{sample}.h5ad")
        target.write_text(f"{FINAL_DIR}/summary.json", summary_text)
        target.write_text(marker, json.dumps(record) + "\n")
    where = publish or store
    print(
        f"Built {final.n_obs} cells x {final.n_vars} genes from {', '.join(record['steps'])} -> {where.uri(FINAL_DIR)}"
    )


def _summary(final, sample: str, record: dict, local: Path) -> dict:
    return {
        "sample": sample,
        "n_cells": final.n_obs,
        "n_genes": final.n_vars,
        "steps": record["steps"],
        "obs": list(final.obs.columns),
        "obsm": list(final.obsm),
        "bytes": local.stat().st_size,
    }
