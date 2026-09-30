"""Tests for argo/scrna/analysis: preprocess, cluster and build-h5ad on a small made-up 10x matrix.

The run's folder is a local directory standing in for s3://.../runs_output/<run>. Needs scanpy,
so it runs in the scrna-analysis image (see argo/scrna/README.md).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("scanpy")

import anndata as ad
import h5py
import scipy.sparse as sp

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "argo/scrna/analysis"))

from bloom_scrna_analysis import build, cluster, preprocess
from bloom_scrna_analysis.__main__ import main
from bloom_scrna_analysis.steps import part_of
from bloom_scrna_analysis.store import Store

N_GROUPS = 3
CELLS_PER_GROUP = 80
N_GENES = 900
MARKERS_PER_GROUP = 60


def write_10x_h5(
    path: Path, *, n_cells_per_group: int = CELLS_PER_GROUP, low_cells: int = 0
) -> None:
    """A Cell Ranger v3 filtered matrix: three groups with their own marker genes, plus near-empty cells."""
    rng = np.random.default_rng(1)
    rows = []
    for group in range(N_GROUPS):
        rate = np.full(N_GENES, 0.6)
        start = 20 + group * MARKERS_PER_GROUP
        rate[start : start + MARKERS_PER_GROUP] = 12.0
        rows.append(rng.poisson(rate, size=(n_cells_per_group, N_GENES)))
    if low_cells:
        rows.append(rng.poisson(0.05, size=(low_cells, N_GENES)))
    counts = sp.csc_matrix(
        np.vstack(rows).T.astype(np.int32)
    )  # genes x cells, as Cell Ranger stores it
    n_cells = counts.shape[1]
    ids = [f"AT1G{i:05d}" for i in range(N_GENES - 2)] + ["ATMG00010", "ATCG00020"]
    names = [f"GENE{i}" for i in range(N_GENES)]
    names[5] = names[4]  # Cell Ranger's gene names can repeat; IDs can't
    with h5py.File(path, "w") as f:
        m = f.create_group("matrix")
        m["barcodes"] = np.array([f"{i:016d}-1".encode() for i in range(n_cells)])
        m["data"], m["indices"], m["indptr"] = (
            counts.data,
            counts.indices,
            counts.indptr,
        )
        m["shape"] = np.array(counts.shape, dtype=np.int32)
        feats = m.create_group("features")
        feats["id"] = np.array([s.encode() for s in ids])
        feats["name"] = np.array([s.encode() for s in names])
        feats["feature_type"] = np.array([b"Gene Expression"] * N_GENES)
        feats["genome"] = np.array([b"TAIR10"] * N_GENES)
        feats["_all_tag_keys"] = np.array([b"genome"])


@pytest.fixture
def run_dir(tmp_path):
    root = tmp_path / "runs_output" / "42"
    (root / "outs").mkdir(parents=True)
    write_10x_h5(root / "outs/filtered_feature_bc_matrix.h5")
    return root


def pipeline(root: Path, *extra: str) -> list[int]:
    return [
        main(["preprocess", "--root", str(root), *extra]),
        main(["cluster", "--root", str(root)]),
        main(["build-h5ad", "--root", str(root), "--sample", "root_tip"]),
    ]


def final(root: Path):
    return ad.read_h5ad(root / "h5ad/root_tip.h5ad")


# --------------------------------------------------------------------------- #
# The final file
# --------------------------------------------------------------------------- #


def test_the_pipeline_writes_one_file_with_every_piece(run_dir):
    assert pipeline(run_dir) == [0, 0, 0]
    adata = final(run_dir)
    assert adata.n_obs == N_GROUPS * CELLS_PER_GROUP
    assert adata.obs_names[0] == "0000000000000000-1"
    assert adata.var_names[0] == "AT1G00000"
    assert adata.var["gene_symbols"].iloc[0] == "GENE0"
    assert adata.var_names.is_unique
    assert sp.issparse(adata.layers["counts"])
    assert adata.obsm["X_umap"].shape == (adata.n_obs, 2)
    assert adata.obsm["X_pca"].shape[0] == adata.n_obs
    assert {"n_genes", "total_counts", "leiden"} <= set(adata.obs.columns)


def test_raw_counts_are_whole_numbers_and_x_is_their_log_normalised_copy(run_dir):
    pipeline(run_dir)
    adata = final(run_dir)
    counts = adata.layers["counts"].toarray()
    assert np.array_equal(counts, np.round(counts))
    expected = np.log1p(counts / counts.sum(axis=1, keepdims=True) * 10_000)
    assert np.allclose(adata.X.toarray(), expected, atol=1e-4)
    assert np.allclose(adata.obs["total_counts"], counts.sum(axis=1))


def test_the_three_groups_come_out_as_separate_clusters(run_dir):
    pipeline(run_dir)
    adata = final(run_dir)
    truth = np.repeat(np.arange(N_GROUPS), CELLS_PER_GROUP)
    for group in range(N_GROUPS):
        labels = adata.obs["leiden"].values[truth == group]
        others = adata.obs["leiden"].values[truth != group]
        assert not set(labels) & set(others), (
            f"group {group} shares a cluster with another group"
        )


def bloomctl_format():
    """bloomctl's structure check, loaded on its own: it needs only h5py and numpy, not the CLI."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "bloomctl_scrna_format", REPO / "bloomcli/src/bloomctl/scrna/_format.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_file_passes_bloomctls_upload_check(run_dir):
    _format = bloomctl_format()
    pipeline(run_dir)
    summary = _format.check_structure(run_dir / "h5ad/root_tip.h5ad")
    assert summary.n_cells == N_GROUPS * CELLS_PER_GROUP
    assert summary.normalization is not None
    assert (
        _format.normalization_problem(summary.normalization, layers=summary.layers)
        is None
    )


def test_the_file_records_its_steps_settings_and_versions(run_dir):
    pipeline(run_dir, "--n-top-genes", "500")
    adata = final(run_dir)
    assert list(adata.uns["bloom_pipeline"]["steps"]) == ["preprocess", "cluster"]
    assert "scanpy" in adata.uns["bloom_pipeline"]["versions"]
    assert adata.uns["preprocess"]["n_top_genes"] == 500
    assert adata.uns["cluster"]["n_clusters"] == adata.obs["leiden"].nunique()
    summary = json.loads((run_dir / "h5ad/summary.json").read_text())
    assert summary["n_cells"] == adata.n_obs
    assert summary["steps"] == ["preprocess", "cluster"]


def test_the_variable_gene_count_defaults_to_2000_capped_at_the_genes_there_are(
    run_dir,
):
    pipeline(run_dir)
    assert final(run_dir).uns["preprocess"]["n_top_genes"] == N_GENES


# --------------------------------------------------------------------------- #
# Filtering and failures
# --------------------------------------------------------------------------- #


def test_near_empty_cells_are_dropped(tmp_path):
    root = tmp_path / "run"
    (root / "outs").mkdir(parents=True)
    write_10x_h5(root / "outs/filtered_feature_bc_matrix.h5", low_cells=25)
    assert pipeline(root) == [0, 0, 0]
    assert final(root).n_obs == N_GROUPS * CELLS_PER_GROUP


def test_too_few_cells_fails_with_13_and_writes_nothing(tmp_path, capsys):
    root = tmp_path / "run"
    (root / "outs").mkdir(parents=True)
    write_10x_h5(root / "outs/filtered_feature_bc_matrix.h5", n_cells_per_group=10)
    assert main(["preprocess", "--root", str(root)]) == 13
    assert "at least 50" in capsys.readouterr().err
    assert not (root / "analysis").exists()


def test_a_missing_matrix_fails_with_14(tmp_path):
    root = tmp_path / "run"
    root.mkdir()
    assert main(["preprocess", "--root", str(root)]) == 14
    assert not (root / "analysis").exists()


def test_build_refuses_a_bad_sample_name(run_dir):
    main(["preprocess", "--root", str(run_dir)])
    with pytest.raises(ValueError):
        build.run(Store(str(run_dir)), run_dir, sample="../elsewhere")


# --------------------------------------------------------------------------- #
# Adding analyses
# --------------------------------------------------------------------------- #


def add_part(root: Path, name: str, *, obs=None, obsm=None, cells=None):
    base = ad.read_h5ad(root / "analysis/preprocess/base.h5ad")
    if cells is not None:
        base = base[cells].copy()
    part = part_of(base, name, obs=obs, obsm=obsm, params={"made_up": True})
    folder = root / "analysis" / name
    folder.mkdir(parents=True, exist_ok=True)
    part.write_h5ad(folder / "part.h5ad")
    (folder / "_SUCCESS").write_text("{}\n")
    return base.n_obs


def test_a_new_analysis_is_merged_without_changing_build(run_dir):
    pipeline(run_dir)
    n = add_part(
        run_dir,
        "annotate",
        obs={"cell_type": np.array(["root hair"] * N_GROUPS * CELLS_PER_GROUP)},
    )
    assert main(["build-h5ad", "--root", str(run_dir), "--sample", "root_tip"]) == 0
    adata = final(run_dir)
    assert (adata.obs["cell_type"] == "root hair").sum() == n
    assert list(adata.uns["bloom_pipeline"]["steps"]) == [
        "preprocess",
        "annotate",
        "cluster",
    ]
    assert adata.uns["annotate"]["made_up"]


def test_a_part_in_another_cell_order_is_aligned_to_the_base(run_dir):
    pipeline(run_dir)
    base = ad.read_h5ad(run_dir / "analysis/preprocess/base.h5ad")
    reversed_cells = base.obs_names[::-1]
    # Each cell's position in the base, listed in reverse order.
    add_part(
        run_dir,
        "order",
        obs={"position": np.arange(base.n_obs)[::-1]},
        cells=reversed_cells,
    )
    main(["build-h5ad", "--root", str(run_dir), "--sample", "root_tip"])
    assert list(final(run_dir).obs["position"]) == list(range(base.n_obs))


def test_a_part_with_different_cells_fails_with_15(run_dir):
    pipeline(run_dir)
    base = ad.read_h5ad(run_dir / "analysis/preprocess/base.h5ad")
    add_part(
        run_dir,
        "partial",
        obs={"x": np.zeros(base.n_obs - 1)},
        cells=base.obs_names[1:],
    )
    before = (run_dir / "h5ad/root_tip.h5ad").stat().st_mtime_ns
    assert main(["build-h5ad", "--root", str(run_dir), "--sample", "root_tip"]) == 15
    assert (run_dir / "h5ad/root_tip.h5ad").stat().st_mtime_ns == before


@pytest.mark.parametrize(
    "obs, obsm",
    [({"leiden": None}, None), ({"n_genes": None}, None), (None, {"X_umap": None})],
    ids=["another-part's-obs", "the-base's-obs", "another-part's-obsm"],
)
def test_a_part_reusing_a_taken_key_fails_with_15(run_dir, obs, obsm):
    pipeline(run_dir)
    n = N_GROUPS * CELLS_PER_GROUP
    obs = {k: np.zeros(n) for k in obs} if obs else None
    obsm = {k: np.zeros((n, 2)) for k in obsm} if obsm else None
    add_part(run_dir, "zz_clash", obs=obs, obsm=obsm)
    assert main(["build-h5ad", "--root", str(run_dir), "--sample", "root_tip"]) == 15


def test_a_part_writing_someone_elses_uns_fails_with_15(run_dir):
    pipeline(run_dir)
    add_part(run_dir, "sneaky")
    part = ad.read_h5ad(run_dir / "analysis/sneaky/part.h5ad")
    part.uns["normalization"] = {"transform": "none"}
    part.write_h5ad(run_dir / "analysis/sneaky/part.h5ad")
    assert main(["build-h5ad", "--root", str(run_dir), "--sample", "root_tip"]) == 15


def test_a_part_without_its_marker_is_left_out(run_dir):
    pipeline(run_dir)
    add_part(run_dir, "unfinished", obs={"x": np.zeros(N_GROUPS * CELLS_PER_GROUP)})
    (run_dir / "analysis/unfinished/_SUCCESS").unlink()
    main(["build-h5ad", "--root", str(run_dir), "--sample", "root_tip"])
    assert "x" not in final(run_dir).obs.columns


# --------------------------------------------------------------------------- #
# Skipping what's done
# --------------------------------------------------------------------------- #


def test_every_step_skips_when_its_marker_is_there(run_dir, capsys):
    pipeline(run_dir)
    stamps = {p: p.stat().st_mtime_ns for p in run_dir.rglob("*.h5ad")}
    capsys.readouterr()
    assert pipeline(run_dir) == [0, 0, 0]
    assert capsys.readouterr().out.count("Already") == 3
    assert {p: p.stat().st_mtime_ns for p in run_dir.rglob("*.h5ad")} == stamps


def test_build_reruns_when_a_new_part_has_appeared(run_dir, capsys):
    pipeline(run_dir)
    add_part(
        run_dir,
        "annotate",
        obs={"cell_type": np.array(["x"] * N_GROUPS * CELLS_PER_GROUP)},
    )
    capsys.readouterr()
    main(["build-h5ad", "--root", str(run_dir), "--sample", "root_tip"])
    assert "Built" in capsys.readouterr().out
    marker = json.loads((run_dir / "h5ad/_SUCCESS").read_text())
    assert marker["steps"] == ["preprocess", "annotate", "cluster"]


def test_the_marker_is_written_after_the_file(run_dir, monkeypatch):
    main(["preprocess", "--root", str(run_dir)])
    main(["cluster", "--root", str(run_dir)])
    written = []
    real_put, real_text = Store.put, Store.write_text
    monkeypatch.setattr(
        Store,
        "put",
        lambda self, src, rel: (written.append(rel), real_put(self, src, rel))[1],
    )
    monkeypatch.setattr(
        Store,
        "write_text",
        lambda self, rel, t: (written.append(rel), real_text(self, rel, t))[1],
    )
    main(["build-h5ad", "--root", str(run_dir), "--sample", "root_tip"])
    assert written[-1] == "h5ad/_SUCCESS"
    assert written.index("h5ad/root_tip.h5ad") < written.index("h5ad/_SUCCESS")


# --------------------------------------------------------------------------- #
# The steps on their own
# --------------------------------------------------------------------------- #


def test_organelle_and_repeated_names_are_kept_by_id(run_dir):
    pipeline(run_dir)
    adata = final(run_dir)
    assert "ATMG00010" in adata.var_names
    assert (adata.var["gene_symbols"] == "GENE4").sum() == 2


def test_cluster_reads_only_the_pca(run_dir):
    main(["preprocess", "--root", str(run_dir)])
    base = ad.read_h5ad(run_dir / "analysis/preprocess/base.h5ad")
    part = cluster.compute_part(base)
    assert part.n_vars == 0
    assert set(part.obs.columns) == {"leiden"}
    assert set(part.obsm) == {"X_umap"}
    assert set(part.uns) == {"cluster"}


def test_the_same_input_gives_the_same_clusters_and_umap(run_dir, tmp_path):
    main(["preprocess", "--root", str(run_dir)])
    base = ad.read_h5ad(run_dir / "analysis/preprocess/base.h5ad")
    first, second = cluster.compute_part(base), cluster.compute_part(base)
    assert list(first.obs["leiden"]) == list(second.obs["leiden"])
    assert np.allclose(first.obsm["X_umap"], second.obsm["X_umap"])


def test_build_base_keeps_every_gene_in_x_and_marks_the_variable_ones(run_dir):
    import scanpy as sc

    adata = sc.read_10x_h5(
        run_dir / "outs/filtered_feature_bc_matrix.h5", gex_only=True
    )
    preprocess.build_base(adata, n_top_genes=100)
    assert adata.var["highly_variable"].sum() == 100
    assert adata.n_vars > 100
    assert adata.uns["normalization"]["counts_layer"] == "counts"


# --------------------------------------------------------------------------- #
# The shared folder and S3
# --------------------------------------------------------------------------- #


def test_only_the_final_file_goes_to_s3(run_dir, tmp_path):
    s3 = tmp_path / "s3" / "runs_output" / "42"
    main(["preprocess", "--root", str(run_dir)])
    main(["cluster", "--root", str(run_dir)])
    assert (
        main(
            [
                "build-h5ad",
                "--root",
                str(run_dir),
                "--sample",
                "root_tip",
                "--publish",
                str(s3),
            ]
        )
        == 0
    )
    published = sorted(str(p.relative_to(s3)) for p in s3.rglob("*") if p.is_file())
    assert published == ["h5ad/_SUCCESS", "h5ad/root_tip.h5ad", "h5ad/summary.json"]
    assert (s3 / "h5ad/root_tip.h5ad").read_bytes() == (
        run_dir / "h5ad/root_tip.h5ad"
    ).read_bytes()


def test_the_s3_copy_is_complete_before_its_marker(run_dir, tmp_path, monkeypatch):
    s3 = tmp_path / "s3"
    main(["preprocess", "--root", str(run_dir)])
    main(["cluster", "--root", str(run_dir)])
    written = []
    real_put, real_text = Store.put, Store.write_text
    monkeypatch.setattr(
        Store,
        "put",
        lambda self, src, rel: (
            written.append((self.root, rel)),
            real_put(self, src, rel),
        )[1],
    )
    monkeypatch.setattr(
        Store,
        "write_text",
        lambda self, rel, t: (
            written.append((self.root, rel)),
            real_text(self, rel, t),
        )[1],
    )
    main(
        [
            "build-h5ad",
            "--root",
            str(run_dir),
            "--sample",
            "root_tip",
            "--publish",
            str(s3),
        ]
    )
    to_s3 = [rel for root, rel in written if root == str(s3)]
    assert to_s3 == ["h5ad/root_tip.h5ad", "h5ad/summary.json", "h5ad/_SUCCESS"]


def test_two_runs_on_the_share_keep_their_own_files(tmp_path):
    shared = tmp_path / "shared" / "runs"
    for run, per_group in (("41", CELLS_PER_GROUP), ("42", CELLS_PER_GROUP + 20)):
        (shared / run / "outs").mkdir(parents=True)
        write_10x_h5(
            shared / run / "outs/filtered_feature_bc_matrix.h5",
            n_cells_per_group=per_group,
        )
    for step in ("preprocess", "cluster"):
        for run in ("41", "42"):
            assert main([step, "--root", str(shared / run)]) == 0
    for run in ("41", "42"):
        assert (
            main(["build-h5ad", "--root", str(shared / run), "--sample", f"s{run}"])
            == 0
        )
    assert ad.read_h5ad(shared / "41/h5ad/s41.h5ad").n_obs == N_GROUPS * CELLS_PER_GROUP
    assert ad.read_h5ad(shared / "42/h5ad/s42.h5ad").n_obs == N_GROUPS * (
        CELLS_PER_GROUP + 20
    )
    assert not (shared / "41/h5ad/s42.h5ad").exists()
