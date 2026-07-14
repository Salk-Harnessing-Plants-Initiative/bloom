"""_build_output_frame and snapshot_frame — unit tests.

Scenarios covered:
  _build_output_frame
    1. With metadata_cols — identity columns prepended to payload, correct values
    2. With empty metadata_cols — payload returned as a copy (no identity prepend)
    3. Non-default index on frame.df — reset_index on identity side (regression guard)
    4. Non-default index on payload_df — reset_index on payload side (regression guard)
  snapshot_frame
    5. Normal path — CSV exists, yields a readable Path, temp dir cleaned up after exit
    6. Empty DataFrame — CSV written (zero data rows), context manager still works
    7. Exception inside the block — temp dir cleaned up even on error
"""

from __future__ import annotations

import pandas as pd
import pytest

from bloom_mcp.tools._consumer_utils import _build_output_frame, snapshot_frame

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_frame_df(metadata_cols: list[str], index_start: int = 0) -> pd.DataFrame:
    """Return a minimal frame.df substitute with the given metadata columns."""
    data = {
        "Genotype": ["A", "B", "C"],
        "SampleID": ["s1", "s2", "s3"],
    }
    df = pd.DataFrame(data)
    if index_start:
        df.index = range(index_start, index_start + len(df))
    return df


class _FakeFrame:
    """Minimal ExperimentFrame stand-in — only the two attributes the helper reads."""

    def __init__(self, metadata_cols: list[str], index_start: int = 0):
        self.metadata_cols = metadata_cols
        self.df = _make_frame_df(metadata_cols, index_start)


# ---------------------------------------------------------------------------
# _build_output_frame
# ---------------------------------------------------------------------------


def test_build_output_frame_with_metadata_cols():
    frame = _FakeFrame(["Genotype", "SampleID"])
    payload = pd.DataFrame({"PC1": [0.1, 0.2, 0.3], "PC2": [0.4, 0.5, 0.6]})
    result = _build_output_frame(frame, payload)

    # Identity columns are the first two columns
    assert list(result.columns[:2]) == ["Genotype", "SampleID"]
    # Payload columns follow
    assert list(result.columns[2:]) == ["PC1", "PC2"]
    # Row count preserved
    assert len(result) == 3
    # Correct values — wrong reset_index behavior produces NaN or misaligned rows
    assert list(result["Genotype"]) == ["A", "B", "C"]
    assert list(result["SampleID"]) == ["s1", "s2", "s3"]
    assert result["PC1"].tolist() == pytest.approx([0.1, 0.2, 0.3])


def test_build_output_frame_empty_metadata_cols():
    frame = _FakeFrame([])
    payload = pd.DataFrame({"PC1": [0.1, 0.2, 0.3]})
    result = _build_output_frame(frame, payload)

    # With no metadata_cols, result is just the payload
    assert list(result.columns) == ["PC1"]
    assert len(result) == 3


def test_build_output_frame_non_default_frame_index():
    """reset_index on identity side: frame.df has non-default index, payload has RangeIndex."""
    frame = _FakeFrame(["Genotype", "SampleID"], index_start=10)
    # payload has default RangeIndex(0..2); frame.df has RangeIndex(10..12)
    payload = pd.DataFrame({"PC1": [0.1, 0.2, 0.3]})
    result = _build_output_frame(frame, payload)

    assert not result.isnull().any().any()
    assert len(result) == 3
    assert list(result["Genotype"]) == ["A", "B", "C"]
    assert result["PC1"].tolist() == pytest.approx([0.1, 0.2, 0.3])


def test_build_output_frame_non_default_payload_index():
    """reset_index on payload side: payload has non-default index, frame.df has RangeIndex."""
    frame = _FakeFrame(["Genotype", "SampleID"])
    # payload has a non-default starting index — pd.concat without reset_index silently NaNs
    payload = pd.DataFrame({"PC1": [0.1, 0.2, 0.3]}, index=[5, 6, 7])
    result = _build_output_frame(frame, payload)

    assert not result.isnull().any().any()
    assert len(result) == 3
    assert list(result["Genotype"]) == ["A", "B", "C"]
    assert result["PC1"].tolist() == pytest.approx([0.1, 0.2, 0.3])


# ---------------------------------------------------------------------------
# snapshot_frame
# ---------------------------------------------------------------------------


def test_snapshot_frame_normal():
    df = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
    captured_path = None

    with snapshot_frame(df) as src:
        captured_path = src
        # CSV exists and is readable while inside the context
        assert src.exists()
        loaded = pd.read_csv(src)
        assert list(loaded.columns) == ["a", "b"]
        assert len(loaded) == 2

    # Temp directory cleaned up after exit
    assert not captured_path.exists()


def test_snapshot_frame_empty_df():
    df = pd.DataFrame({"a": [], "b": []})

    with snapshot_frame(df) as src:
        assert src.exists()
        loaded = pd.read_csv(src)
        assert list(loaded.columns) == ["a", "b"]
        assert len(loaded) == 0


def test_snapshot_frame_cleanup_on_exception():
    df = pd.DataFrame({"x": [1, 2, 3]})
    captured_path = None

    with pytest.raises(RuntimeError, match="intentional"):
        with snapshot_frame(df) as src:
            captured_path = src
            raise RuntimeError("intentional error inside snapshot block")

    # Even after exception the temp dir must be cleaned up
    assert not captured_path.exists()
