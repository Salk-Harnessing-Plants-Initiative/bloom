"""RunLinks base model — importability, inheritance, validation, and round-trip.

Maps the spec "RunLinks Base Model" scenarios under bloommcp-tool-contract:
  1. RunLinks is importable from bloom_mcp.contract and listed in __all__
  2. Consumer result models (PCAAnalysisResult) inherit RunLinks without redeclaring fields
  3. RunLinks fields survive round-trip serialization
  4. Missing / wrong-typed run-link fields are rejected at construction

The PCAAnalysisResult inheritance tests (test_pca_result_inherits_run_links,
test_run_link_fields_not_redeclared) are regression guards that turn green at task 3.1.
"""

from __future__ import annotations

import bloom_mcp.contract as _contract
import pytest
from pydantic import ValidationError

from bloom_mcp.contract import RunLinks
from bloom_mcp.tools.pca_analysis_tool import PCAAnalysisResult

# ---------------------------------------------------------------------------
# Importability and __all__
# ---------------------------------------------------------------------------


def test_run_links_importable():
    assert RunLinks is not None


def test_run_links_in_all():
    assert "RunLinks" in _contract.__all__


# ---------------------------------------------------------------------------
# Field set
# ---------------------------------------------------------------------------

_EXPECTED_FIELDS = {"run_ref", "version_dir", "manifest_path", "outputs"}


def test_run_links_field_set():
    assert set(RunLinks.model_fields.keys()) == _EXPECTED_FIELDS


# ---------------------------------------------------------------------------
# PCAAnalysisResult inherits RunLinks (regression guard — turns green at task 3.1)
# ---------------------------------------------------------------------------


def test_pca_result_is_run_links_subclass():
    assert issubclass(PCAAnalysisResult, RunLinks)


def test_run_link_fields_not_redeclared_on_pca_result():
    """None of the four RunLinks fields should be declared directly on PCAAnalysisResult."""
    pca_own = vars(PCAAnalysisResult).get("__annotations__", {})
    for field in _EXPECTED_FIELDS:
        assert field not in pca_own, (
            f"{field!r} is redeclared directly on PCAAnalysisResult — "
            "it should come from RunLinks inheritance only"
        )


# ---------------------------------------------------------------------------
# Round-trip serialization
# ---------------------------------------------------------------------------


def _make_pca_result(**overrides) -> PCAAnalysisResult:
    base = dict(
        experiment="turface_19.csv",
        source="v1_cleaned",
        n_samples=100,
        n_features=5,
        n_components=3,
        feature_names=["a", "b", "c", "d", "e"],
        explained_variance_ratio=[0.5, 0.3, 0.2],
        cumulative_variance_ratio=[0.5, 0.8, 1.0],
        eigenvalues=[5.0, 3.0, 2.0],
        run_ref="abc123",
        version_dir="v1_pca",
        manifest_path="pca/v1_pca/manifest.json",
        outputs={"scores.csv": "scores.csv", "loadings.csv": "loadings.csv"},
    )
    base.update(overrides)
    return PCAAnalysisResult(**base)


def test_run_links_round_trip_via_pca_result():
    instance = _make_pca_result()
    restored = PCAAnalysisResult.model_validate(instance.model_dump())
    assert restored.run_ref == instance.run_ref
    assert restored.version_dir == instance.version_dir
    assert restored.manifest_path == instance.manifest_path
    assert restored.outputs == instance.outputs
    # Tool-specific fields also survive
    assert restored.experiment == instance.experiment
    assert restored.n_samples == instance.n_samples


# ---------------------------------------------------------------------------
# Validation rejection
# ---------------------------------------------------------------------------


def test_run_ref_required():
    with pytest.raises(ValidationError) as exc_info:
        RunLinks(version_dir="v1", manifest_path="m.json", outputs={})
    errors = {e["loc"][0] for e in exc_info.value.errors()}
    assert "run_ref" in errors


def test_outputs_must_be_string_valued():
    with pytest.raises(ValidationError):
        RunLinks(
            run_ref="abc",
            version_dir="v1",
            manifest_path="m.json",
            outputs={"key": 42},  # int value — should be rejected
        )
