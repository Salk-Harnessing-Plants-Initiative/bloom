"""Unit tests for the workflow scaffolding: response shape contract,
build_writer construction, and CANONICAL_TOOL_CLASSES extension.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

# bloommcp ships with its packages (source, tools, storage) directly under
# bloommcp/ rather than nested under a top-level package. The `tools`
# namespace collides with langchain/tools/ when both are on sys.path, so
# the workflow modules are loaded by file path here instead of via import.
# `storage` (no langchain counterpart) is safe to import normally.
_BLOOMMCP_DIR = Path(__file__).resolve().parents[2] / "bloommcp"
if str(_BLOOMMCP_DIR) not in sys.path:
    sys.path.insert(0, str(_BLOOMMCP_DIR))

# experiment_utils._validate_dirs() runs at import time; satisfy it with a
# tmp dir so collection doesn't crash when the env vars aren't set.
_TMP_BASE = tempfile.mkdtemp(prefix="bloommcp_workflow_scaffolding_")
os.environ.setdefault("BLOOM_TRAITS_DIR", _TMP_BASE)
os.environ.setdefault("BLOOM_OUTPUT_DIR", _TMP_BASE)
os.environ.setdefault("BLOOM_PLOTS_DIR", _TMP_BASE)
os.environ.setdefault("BLOOM_PLOTS_URL", "http://localhost:5002/plots")

import importlib.util  # noqa: E402

from storage import AnalysisWriter, CANONICAL_TOOL_CLASSES  # noqa: E402


def _load_module_by_path(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_workflows_dir = _BLOOMMCP_DIR / "tools" / "workflows"
_helpers_mod = _load_module_by_path("workflow_helpers_under_test", _workflows_dir / "_helpers.py")
_response_mod = _load_module_by_path("workflow_response_under_test", _workflows_dir / "_response.py")

build_writer = _helpers_mod.build_writer
WorkflowResponse = _response_mod.WorkflowResponse
FollowupAction = _response_mod.FollowupAction
REQUIRED_KEYS = _response_mod.REQUIRED_KEYS
OPTIONAL_KEYS = _response_mod.OPTIONAL_KEYS


# ===================== WorkflowResponse contract =====================


def test_required_keys_are_the_canonical_five():
    assert REQUIRED_KEYS == (
        "version_id",
        "version_dir",
        "manifest_path",
        "summary",
        "outputs",
    )


def test_optional_keys_include_plot_and_followup():
    assert "plot_url" in OPTIONAL_KEYS
    assert "plot_layout" in OPTIONAL_KEYS
    assert "followup_actions" in OPTIONAL_KEYS


def test_required_and_optional_keys_do_not_overlap():
    assert set(REQUIRED_KEYS).isdisjoint(set(OPTIONAL_KEYS))


def test_workflow_response_typeddict_accepts_minimal_payload():
    # TypedDict is structural; a plain dict matching the shape is valid.
    payload: WorkflowResponse = {
        "version_id": "v1",
        "version_dir": "/tmp/x/qc/v1_2026-05-26",
        "manifest_path": "/tmp/x/qc/manifest.json",
        "summary": {"n_rows": 100},
        "outputs": {"cleaned_csv": "v1_2026-05-26/cleaned.csv"},
    }
    for key in REQUIRED_KEYS:
        assert key in payload


def test_workflow_response_typeddict_accepts_full_payload():
    payload: WorkflowResponse = {
        "version_id": "v1",
        "version_dir": "/tmp/x/clustering/v1_2026-05-26",
        "manifest_path": "/tmp/x/clustering/manifest.json",
        "summary": {"silhouette_score": 0.42},
        "outputs": {"labels_csv": "v1_2026-05-26/labels.csv"},
        "plot_url": "http://localhost:5002/plots/clustering_abcd.png",
        "plot_layout": "scatter",
        "followup_actions": [
            {"label": "Compare clusters by trait X", "prompt": "..."},
        ],
    }
    assert payload["plot_url"].endswith(".png")
    assert payload["followup_actions"][0]["label"].startswith("Compare")


def test_followup_action_typeddict_shape():
    action: FollowupAction = {"label": "Re-run with k=5", "prompt": "..."}
    assert set(action.keys()) == {"label", "prompt"}


# ===================== build_writer =====================


def test_build_writer_returns_analysis_writer_instance():
    w = build_writer("alfalfa_gwas_wave2.csv", "qc")
    assert isinstance(w, AnalysisWriter)


def test_build_writer_threads_tool_class_through_to_analysis_dir():
    w = build_writer("alfalfa_gwas_wave2.csv", "clustering")
    # AnalysisDir.path includes <tool_class>_<stem> as the directory name
    assert "clustering_alfalfa_gwas_wave2" in str(w.analysis_dir.path)


def test_build_writer_threads_experiment_filename_through():
    w = build_writer("rice_2024.csv", "outlier")
    assert w.analysis_dir.experiment_filename == "rice_2024.csv"


def test_build_writer_accepts_optional_source_csv():
    src = Path("/data/raw/rice_2024.csv")
    w = build_writer("rice_2024.csv", "outlier", source_csv=src)
    assert w.source_csv == src


def test_build_writer_uses_output_dir_env_var():
    # _TMP_BASE was set as BLOOM_OUTPUT_DIR at module load time
    w = build_writer("rice_2024.csv", "outlier")
    assert str(w.analysis_dir.path).startswith(_TMP_BASE)


# ===================== CANONICAL_TOOL_CLASSES =====================


def test_canonical_tool_classes_includes_originals():
    """Phase 1 must not drop the original 7 tool classes."""
    for original in ("qc", "stats", "dimred", "clustering", "outlier", "viz", "correlation"):
        assert original in CANONICAL_TOOL_CLASSES, f"missing original tool class {original!r}"


def test_canonical_tool_classes_includes_two_new_workflows():
    """Two workflow tool classes added on top of the original seven."""
    for new_class in ("heritability", "anova"):
        assert new_class in CANONICAL_TOOL_CLASSES, f"missing new tool class {new_class!r}"


def test_canonical_tool_classes_count_is_nine():
    assert len(CANONICAL_TOOL_CLASSES) == 9


def test_canonical_tool_classes_has_no_duplicates():
    assert len(CANONICAL_TOOL_CLASSES) == len(set(CANONICAL_TOOL_CLASSES))
