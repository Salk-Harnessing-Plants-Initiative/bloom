"""What every step shares: the run's layout, exit codes, and the part contract."""

from __future__ import annotations

import sys
from importlib import metadata

import numpy as np
import scipy.sparse as sp

MATRIX = "outs/filtered_feature_bc_matrix.h5"
BASE_DIR = "analysis/preprocess"
BASE = f"{BASE_DIR}/base.h5ad"
ANALYSIS_DIR = "analysis"
FINAL_DIR = "h5ad"
MARKER = "_SUCCESS"
PART = "part.h5ad"

EXIT_TOO_FEW_CELLS = 13
EXIT_NO_MATRIX = 14
EXIT_PARTS_DONT_FIT = 15

PACKAGES = ("scanpy", "anndata", "numpy", "scipy", "igraph", "leidenalg", "umap-learn")


class StepError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


def versions() -> dict[str, str]:
    found = {}
    for name in PACKAGES:
        try:
            found[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            continue
    found["python"] = sys.version.split()[0]
    return found


def part_of(base, name: str, *, obs=None, obsm=None, params=None):
    """An analysis's results as a part: its obs columns and obsm arrays, and its params in uns[name]."""
    import anndata as ad
    import pandas as pd

    part = ad.AnnData(
        X=sp.csr_matrix((base.n_obs, 0), dtype=np.float32),
        obs=pd.DataFrame(obs or {}, index=base.obs_names.copy()),
        obsm=dict(obsm or {}),
    )
    part.uns[name] = dict(params or {})
    return part
