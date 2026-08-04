"""Unit tests for the shared ephemeral CSV-parsing helper (#582 / qc_clean slice).

``parse_inline_csv_frame`` / ``compute_input_sha256`` are the shared surface every
consumer tool's future ``csv_content`` path will import — this pins the helper's
own contract (parsing, size guard, malformed-input mapping, BOM stripping,
no-persistence) independent of any one tool.
"""

from __future__ import annotations

import hashlib
import io
from unittest.mock import patch

import pandas as pd
import pytest

from bloom_mcp.contract import BloomMCPError
from bloom_mcp.data_access import ExperimentFrame
from bloom_mcp.data_access.columns import resolve_columns

_VALID_CSV = "Barcode,geno,traitA,traitB\nS1,g1,1.0,2.0\nS2,g2,3.0,4.0\nS3,g1,5.0,6.0\n"


def _import_helper():
    from bloom_mcp.tools import _inline_input

    return _inline_input


# ── 1.2 valid content ───────────────────────────────────────────────────────


def test_valid_csv_parses_into_a_frame_with_resolved_roles():
    helper = _import_helper()
    frame = helper.parse_inline_csv_frame(_VALID_CSV)

    assert isinstance(frame, ExperimentFrame)
    assert frame.source == "inline"

    expected = resolve_columns(pd.read_csv(io.StringIO(_VALID_CSV)))
    assert frame.genotype_col == expected.genotype
    assert frame.sample_id_col == expected.sample_id
    assert frame.replicate_col == expected.replicate
    assert frame.trait_cols == expected.trait_cols
    assert list(frame.df["Barcode"]) == ["S1", "S2", "S3"]


# ── 1.3 / 1.4 size guard ────────────────────────────────────────────────────


def test_oversized_content_is_rejected_before_parsing():
    helper = _import_helper()
    # One row whose single trait value is padded to push total encoded bytes
    # comfortably over the limit.
    padding = "9" * (helper.MAX_INLINE_CSV_BYTES + 1024)
    oversized = f"Barcode,geno,traitA\nS1,g1,{padding}\n"
    assert len(oversized.encode("utf-8")) > helper.MAX_INLINE_CSV_BYTES

    with patch("pandas.read_csv") as mock_read_csv:
        with pytest.raises(BloomMCPError) as exc:
            helper.parse_inline_csv_frame(oversized)
        mock_read_csv.assert_not_called()

    assert exc.value.code == "invalid_input"
    assert str(helper.MAX_INLINE_CSV_BYTES) in exc.value.message


def test_content_at_the_limit_is_accepted():
    helper = _import_helper()
    header = "Barcode,geno,traitA\n"
    row_prefix = "S1,g1,"
    # Pad exactly to the byte limit with digits, keeping the CSV well-formed.
    filler_len = helper.MAX_INLINE_CSV_BYTES - len(
        (header + row_prefix + "\n").encode("utf-8")
    )
    content = header + row_prefix + ("1" * max(filler_len, 1)) + "\n"
    assert len(content.encode("utf-8")) <= helper.MAX_INLINE_CSV_BYTES

    frame = helper.parse_inline_csv_frame(content)
    assert isinstance(frame, ExperimentFrame)


def test_byte_vs_character_size_guard_uses_encoded_bytes():
    """Multi-byte UTF-8 chars: char count under a naive limit, byte count over it."""
    helper = _import_helper()
    # Each CJK character below is 3 bytes in UTF-8.
    n_chars = helper.MAX_INLINE_CSV_BYTES // 2
    huge_value = "日" * n_chars
    content = f"Barcode,geno,traitA\nS1,g1,{huge_value}\n"
    assert len(content) < helper.MAX_INLINE_CSV_BYTES  # under by character count
    assert len(content.encode("utf-8")) > helper.MAX_INLINE_CSV_BYTES  # over by bytes

    with pytest.raises(BloomMCPError) as exc:
        helper.parse_inline_csv_frame(content)
    assert exc.value.code == "invalid_input"


# ── 1.5 malformed CSV ───────────────────────────────────────────────────────


def test_malformed_csv_is_a_structured_error_not_a_raw_parser_error():
    helper = _import_helper()
    # Inconsistent field counts across rows — the C parser tokenizer rejects this.
    malformed = "a,b,c\n1,2\n3,4,5,6\n"
    with pytest.raises(BloomMCPError) as exc:
        helper.parse_inline_csv_frame(malformed)
    assert exc.value.code == "invalid_input"


# ── 1.6 / 1.7 empty content ──────────────────────────────────────────────────


def test_empty_string_is_rejected():
    helper = _import_helper()
    with pytest.raises(BloomMCPError) as exc:
        helper.parse_inline_csv_frame("")
    assert exc.value.code == "invalid_input"


def test_header_only_zero_data_rows_is_rejected():
    helper = _import_helper()
    with pytest.raises(BloomMCPError) as exc:
        helper.parse_inline_csv_frame("Barcode,geno,traitA\n")
    assert exc.value.code == "invalid_input"
    assert "no data rows" in exc.value.message.lower() or "0 rows" in exc.value.message


def test_zero_columns_is_rejected():
    helper = _import_helper()
    with pytest.raises(BloomMCPError) as exc:
        helper.parse_inline_csv_frame("\n\n\n")
    assert exc.value.code == "invalid_input"


def test_decode_failure_is_a_structured_error():
    helper = _import_helper()
    with patch(
        "pandas.read_csv", side_effect=UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad")
    ):
        with pytest.raises(BloomMCPError) as exc:
            helper.parse_inline_csv_frame(_VALID_CSV)
    assert exc.value.code == "invalid_input"


# ── 1.9 leading BOM ──────────────────────────────────────────────────────────


def test_leading_utf8_bom_is_stripped_before_parsing():
    helper = _import_helper()
    bom_content = "﻿" + _VALID_CSV
    frame = helper.parse_inline_csv_frame(bom_content)
    assert "Barcode" in frame.df.columns
    assert "﻿Barcode" not in frame.df.columns


# ── 1.10 CRLF ────────────────────────────────────────────────────────────────


def test_crlf_content_parses_identically_to_lf():
    helper = _import_helper()
    crlf_content = _VALID_CSV.replace("\n", "\r\n")
    lf_frame = helper.parse_inline_csv_frame(_VALID_CSV)
    crlf_frame = helper.parse_inline_csv_frame(crlf_content)
    pd.testing.assert_frame_equal(lf_frame.df, crlf_frame.df)


# ── 1.11 non-ASCII content ───────────────────────────────────────────────────


def test_non_ascii_content_survives_parsing_intact():
    helper = _import_helper()
    content = "Barcode,geno,traitA\nS1,Köln-1,1.0\nS2,日本晴,2.0\n"
    frame = helper.parse_inline_csv_frame(content)
    assert list(frame.df["geno"]) == ["Köln-1", "日本晴"]


# ── 1.12 duplicate / whitespace headers ─────────────────────────────────────


def test_duplicate_and_whitespace_headers_match_direct_pandas_behavior():
    helper = _import_helper()
    content = "Barcode,geno,geno,traitA\nS1,g1,g1dup,1.0\n"
    direct = pd.read_csv(io.StringIO(content))
    frame = helper.parse_inline_csv_frame(content)
    assert list(frame.df.columns) == list(direct.columns)


# ── 1.13 sha256 ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("text", ["", "a,b,c\n1,2,3\n", "Köln-1 日本晴", "x" * 10_000])
def test_compute_input_sha256_matches_independent_computation(text):
    helper = _import_helper()
    expected = hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert helper.compute_input_sha256(text) == expected


# ── 1.14 no persistence ──────────────────────────────────────────────────────


def test_parsing_touches_no_persistence_port():
    helper = _import_helper()
    from bloom_mcp.tools import _ports

    with patch.object(_ports, "store") as mock_store:
        helper.parse_inline_csv_frame(_VALID_CSV)
        mock_store.assert_not_called()
