"""RunLinks base model — importability, inheritance, validation, and round-trip.

Maps the spec "RunLinks Base Model" scenarios under bloommcp-tool-contract:
  1. RunLinks is importable from bloom_mcp.contract and listed in __all__
  2. Consumer result models inherit RunLinks without redeclaring fields
     (PCAAnalysisResult and RemoveOutliersResult — both must be guarded)
  3. RunLinks fields survive round-trip serialization
  4. Missing / wrong-typed run-link fields are rejected at construction
"""

from __future__ import annotations

import bloom_mcp.contract as _contract
import pytest
from pydantic import ValidationError

from bloom_mcp.contract import RunLinks
from bloom_mcp.tools.pca_analysis_tool import PCAAnalysisResult
from bloom_mcp.tools.remove_outliers_tool import RemoveOutliersResult

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
    """None of the four RunLinks fields should appear in PCAAnalysisResult.model_fields directly."""
    pca_own = set(PCAAnalysisResult.model_fields.keys()) - set(RunLinks.model_fields.keys())
    for field in _EXPECTED_FIELDS:
        assert field not in pca_own, (
            f"{field!r} is redeclared directly on PCAAnalysisResult — "
            "it should come from RunLinks inheritance only"
        )


# ---------------------------------------------------------------------------
# RemoveOutliersResult inherits RunLinks (parallel regression guard)
# ---------------------------------------------------------------------------


def test_remove_outliers_result_is_run_links_subclass():
    assert issubclass(RemoveOutliersResult, RunLinks)


def test_run_link_fields_not_redeclared_on_remove_outliers_result():
    """None of the four RunLinks fields should appear in RemoveOutliersResult.model_fields directly."""
    ro_own = set(RemoveOutliersResult.model_fields.keys()) - set(RunLinks.model_fields.keys())
    for field in _EXPECTED_FIELDS:
        assert field not in ro_own, (
            f"{field!r} is redeclared directly on RemoveOutliersResult — "
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


# ---------------------------------------------------------------------------
# RemoveOutliersResult field ordering (intentional change from BaseModel → RunLinks)
# ---------------------------------------------------------------------------


def test_remove_outliers_result_run_link_fields_appear_first_in_model_dump():
    """Pydantic v2 places base-class fields first in model_dump().

    RemoveOutliersResult(RunLinks) has run_ref/version_dir/manifest_path/outputs
    at the START of the serialized dict (inherited from RunLinks), followed by the
    tool-specific fields.  This is an intentional consequence of the inheritance
    refactor and differs from the old BaseModel order where they appeared last.
    This test locks in the new order as deliberate so any future reversion is caught.
    """
    result = RemoveOutliersResult(
        run_ref="r1",
        version_dir="v1",
        manifest_path="m.json",
        outputs={"cleaned.csv": "cleaned.csv"},
        experiment="exp.csv",
        source="v1_cleaned",
        method="mahalanobis",
        n_input_samples=100,
        n_outliers=3,
        n_output_samples=97,
        removal_fraction=0.03,
        outlier_barcodes=["b1", "b2", "b3"],
    )
    keys = list(result.model_dump().keys())
    run_link_positions = [keys.index(f) for f in _EXPECTED_FIELDS]
    tool_specific_positions = [keys.index("experiment"), keys.index("n_outliers")]
    assert max(run_link_positions) < min(tool_specific_positions), (
        "RunLinks fields must precede tool-specific fields in model_dump() key order"
    )
