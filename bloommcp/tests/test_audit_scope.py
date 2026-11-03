"""Unit tests for `bloom_mcp.audit_scope` -- the pure experiment-filter helper the
outlier audit scripts share (bloom#919).

The parity table pins stem derivation to `AnalysisDir` itself (the oracle), so the
prefix a scoped audit resolves can never drift from the prefix a tool wrote.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from bloom_mcp.audit_scope import (
    ScopedExperiment,
    all_requested_failed,
    experiment_scope_record,
    resolve_experiment_scope,
    summary_prefix,
)
from bloom_mcp.manifest import AnalysisDir


def test_none_means_full_sweep():
    assert resolve_experiment_scope(None) is None


def test_empty_sequence_is_rejected_not_a_silent_full_sweep():
    with pytest.raises(ValueError, match="empty"):
        resolve_experiment_scope([])


def test_bare_string_is_rejected_rather_than_iterated_per_character():
    with pytest.raises(TypeError):
        resolve_experiment_scope("exp_a.csv")


@pytest.mark.parametrize(
    "value", ["exp_a.csv", "exp_a", "exp.v2.csv", "exp.v2", "123", "EXP.CSV"]
)
def test_stem_matches_analysis_dir(value):
    (resolved,) = resolve_experiment_scope([value])
    assert resolved.stem == AnalysisDir("bloommcp_output", value, "qc").stem
    assert resolved.requested == value


def test_report_stem_round_trips_via_dot_csv():
    # A full sweep rebuilds AnalysisDir(f"{stem}.csv"), so a dotted stem copied
    # from a report resolves back to itself when passed as `<stem>.csv`.
    (resolved,) = resolve_experiment_scope(["exp.v2.csv"])
    assert resolved.stem == "exp.v2"


def test_duplicates_collapse_first_seen():
    scope = resolve_experiment_scope(["exp_a.csv", "exp_a", "exp_b"])
    assert scope == (
        ScopedExperiment(requested="exp_a.csv", stem="exp_a"),
        ScopedExperiment(requested="exp_b", stem="exp_b"),
    )


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        ".",
        "..",
        "../x.csv",
        "a/b.csv",
        "a\\b.csv",
        "/abs.csv",
        "exp.csv/",
        "a\x00b",
    ],
)
def test_prefix_escaping_or_empty_values_are_rejected(value):
    with pytest.raises(ValueError):
        resolve_experiment_scope([value])


def test_non_string_element_is_rejected():
    with pytest.raises(ValueError):
        resolve_experiment_scope([123])


def test_one_bad_value_rejects_the_whole_scope():
    with pytest.raises(ValueError, match=r"\.\./x\.csv"):
        resolve_experiment_scope(["exp_a.csv", "../x.csv"])


def test_scope_record_for_full_sweep():
    assert experiment_scope_record(None) == {"mode": "all"}


def test_scope_record_for_scoped_run():
    scope = resolve_experiment_scope(["exp_a.csv", "exp_b"])
    assert experiment_scope_record(scope) == {
        "mode": "experiments",
        "experiments": [
            {"requested": "exp_a.csv", "stem": "exp_a"},
            {"requested": "exp_b", "stem": "exp_b"},
        ],
    }


def test_summary_prefix():
    assert summary_prefix(None) == ""
    scope = resolve_experiment_scope(["exp_a.csv", "exp_b"])
    assert summary_prefix(scope) == "scoped to 2 experiment(s): "


def test_all_requested_failed():
    scope = resolve_experiment_scope(["exp_a", "exp_b"])
    both = {"errors": [{"stem": "exp_a"}, {"stem": "exp_b"}]}
    one = {"errors": [{"stem": "exp_a"}]}
    assert all_requested_failed(both, scope) is True
    assert all_requested_failed(one, scope) is False
    assert all_requested_failed({"errors": [{"stem": "x"}]}, None) is False


def test_module_imports_with_no_environment():
    # It ships in the wheel; the release workflow's walk_packages smoke imports
    # every bloom_mcp module with no service configuration present.
    env = {"PATH": os.environ.get("PATH", "")}
    result = subprocess.run(
        [sys.executable, "-c", "import bloom_mcp.audit_scope"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
