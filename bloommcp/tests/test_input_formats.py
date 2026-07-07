"""Format-registry oracle + edge cases (bloom_mcp.input_formats)."""

from __future__ import annotations

import dataclasses
import io

import pandas as pd
import pytest

from bloom_mcp import input_formats as fmt


def _frame(rows: int = 3) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "genotype": [f"g{i % 2}" for i in range(rows)],
            "trait_x": [float(i) for i in range(rows)],
            "trait_y": [float(i) * 2 for i in range(rows)],
        }
    )


def _to_bytes(df: pd.DataFrame, fmt_id: str) -> bytes:
    buf = io.BytesIO()
    if fmt_id == "csv":
        return df.to_csv(index=False).encode()
    if fmt_id == "tsv":
        return df.to_csv(sep="\t", index=False).encode()
    if fmt_id == "excel":
        df.to_excel(buf, index=False)
    elif fmt_id == "parquet":
        df.to_parquet(buf)
    elif fmt_id == "feather":
        df.to_feather(buf)
    elif fmt_id == "json":
        return df.to_json(orient="records").encode()
    else:  # pragma: no cover - guard
        raise AssertionError(fmt_id)
    return buf.getvalue()


ALL_FORMATS = ["csv", "tsv", "excel", "parquet", "feather", "json"]
ROW_ORIENTED = ["csv", "tsv", "excel", "json"]
COLUMNAR = ["parquet", "feather"]


# ─── Registry membership ──────────────────────────────────────────────────────


def test_registered_extensions_cover_the_set_and_exclude_pickle():
    exts = fmt.registered_extensions()
    for ext in (".csv", ".tsv", ".xlsx", ".parquet", ".feather", ".json"):
        assert ext in exts
    assert ".pkl" not in exts and ".pickle" not in exts


@pytest.mark.parametrize("ext", [".pkl", ".pickle", ".txt", ".h5", ""])
def test_unregistered_extension_is_rejected(ext):
    with pytest.raises(fmt.UnsupportedFormatError):
        fmt.validate_upload(f"data{ext}", b"anything")
    with pytest.raises(fmt.UnsupportedFormatError):
        fmt.load_frame(f"data{ext}", b"anything")


# ─── Load round-trips ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("fmt_id", ALL_FORMATS)
def test_load_frame_round_trips(fmt_id):
    df = _frame(3)
    loaded = fmt.load_frame(f"exp.{'xlsx' if fmt_id == 'excel' else fmt_id}", _to_bytes(df, fmt_id))
    assert list(loaded.columns) == list(df.columns)
    assert len(loaded) == len(df)
    assert loaded["genotype"].tolist() == df["genotype"].tolist()


# ─── Bounded validation ───────────────────────────────────────────────────────


@pytest.mark.parametrize("fmt_id", ROW_ORIENTED)
def test_row_oriented_peek_is_bounded(fmt_id):
    df = _frame(fmt.PEEK_ROWS * 4)
    peek = fmt.validate_upload(
        f"big.{'xlsx' if fmt_id == 'excel' else fmt_id}", _to_bytes(df, fmt_id)
    )
    assert list(peek.columns) == list(df.columns)
    assert len(peek) <= fmt.PEEK_ROWS


@pytest.mark.parametrize("fmt_id", COLUMNAR)
def test_columnar_peek_reads_schema_only(fmt_id):
    df = _frame(500)
    peek = fmt.validate_upload(f"big.{fmt_id}", _to_bytes(df, fmt_id))
    # schema-only: correct columns, zero data rows read
    assert list(peek.columns) == list(df.columns)
    assert len(peek) == 0


@pytest.mark.parametrize("fmt_id", COLUMNAR)
def test_invalid_bytes_raise_invalid_format(fmt_id):
    with pytest.raises(fmt.InvalidFormatError):
        fmt.validate_upload(f"corrupt.{fmt_id}", b"this is not a valid columnar file")


def test_oversize_upload_is_rejected(monkeypatch):
    tiny = dataclasses.replace(fmt.get_format("csv"), max_size=10)
    monkeypatch.setitem(fmt._BY_EXT, ".csv", tiny)
    with pytest.raises(fmt.FileTooLargeError):
        fmt.validate_upload("big.csv", b"genotype,trait_x\n" + b"a,1\n" * 100)


def test_within_size_limit_passes(monkeypatch):
    tiny = dataclasses.replace(fmt.get_format("csv"), max_size=10_000)
    monkeypatch.setitem(fmt._BY_EXT, ".csv", tiny)
    peek = fmt.validate_upload("ok.csv", _to_bytes(_frame(3), "csv"))
    assert len(peek) == 3
