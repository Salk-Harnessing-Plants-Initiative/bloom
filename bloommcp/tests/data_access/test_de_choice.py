"""de_choice: which DE runs and comparisons count, as Bloom's DE pages choose them."""

from __future__ import annotations

from bloom_mcp.data_access import de_choice
from bloom_mcp.data_access.fake_table_reader import FakeTableReader
from bloom_mcp.data_access.gene_reads import Reads


def _run(id_, status, completed_at, dataset=14):
    return {"id": id_, "dataset_id": dataset, "status": status,
            "completed_at": completed_at}  # fmt: skip


def _comparison(id_, run, cluster, n1=50, n2=500, contrast=None):
    return {"id": id_, "run_id": run, "cluster_id": cluster, "contrast": contrast,
            "n_group1": n1, "n_group2": n2}  # fmt: skip


RUNS = [
    _run(1, "complete", "2026-01-01"),
    _run(2, "complete", "2026-02-01"),
    _run(3, "failed", None),
    _run(4, "running", None),
    _run(5, "complete", "2026-03-01", dataset=15),
]


def _newest_first():
    return de_choice.newest_first(
        Reads(FakeTableReader({"scrna_de_runs": RUNS})), [14, 15]
    )


def test_complete_runs_newest_first_per_dataset():
    assert _newest_first() == {14: [2, 1], 15: [5]}


def test_failed_and_running_runs_are_not_chosen():
    chosen = _newest_first()
    assert 3 not in chosen[14] and 4 not in chosen[14]


def test_newer_run_replaces_only_the_cell_types_it_reran():
    # Run 1 has c4 and c7, run 2 re-ran c4 only: c4 from run 2, c7 from run 1.
    comparisons = [
        _comparison(1, 1, "c4"),
        _comparison(2, 1, "c7"),
        _comparison(3, 2, "c4"),
    ]
    kept = de_choice.genotype(comparisons, [2, 1])
    assert {c["cluster_id"]: c["id"] for c in kept} == {"c4": 3, "c7": 2}


def test_each_contrast_is_chosen_on_its_own():
    comparisons = [
        _comparison(1, 1, "c4", contrast="a"),
        _comparison(2, 2, "c4", contrast="b"),
    ]
    kept = de_choice.genotype(comparisons, [2, 1])
    assert sorted(c["id"] for c in kept) == [1, 2]


def test_one_vs_rest_comes_from_the_newest_run_that_has_one():
    comparisons = [_comparison(1, 1, "c3"), _comparison(2, 2, "c4")]
    chosen = de_choice.one_vs_rest(comparisons, [2, 1])
    assert {k: v["id"] for k, v in chosen.items()} == {"c3": 1, "c4": 2}


def test_one_vs_rest_over_the_most_cells_then_lowest_id():
    comparisons = [
        _comparison(1, 2, "c4", n1=10, n2=100),
        _comparison(2, 2, "c4", n1=60, n2=490),
        _comparison(3, 2, "c4", n1=60, n2=490),
        _comparison(4, 1, "c4", n1=99, n2=999),
    ]
    assert de_choice.one_vs_rest(comparisons, [2, 1])["c4"]["id"] == 2
