"""Unit tests for the shared ephemeral CSV-parsing helper (#582 / qc_clean slice).

``parse_inline_csv_frame`` / ``compute_input_sha256`` are the shared surface every
consumer tool's future ``csv_content`` path will import — this pins the helper's
own contract (parsing, size guard, malformed-input mapping, BOM stripping,
no-persistence) independent of any one tool.
"""

from __future__ import annotations

import hashlib
import io
import os
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
    """A real ``pandas.read_csv(io.StringIO(...))`` call cannot actually return a
    non-empty, zero-column frame — any content that would produce one instead
    raises ``EmptyDataError`` first (verified: blank/whitespace/comma-only
    strings all take that path). The ``df.shape[1] == 0`` guard is kept as
    defense-in-depth against a pandas behavior change; mock the return value
    directly so this test exercises that specific branch rather than relying on
    a real string that cannot actually reach it."""
    helper = _import_helper()
    with patch("pandas.read_csv", return_value=pd.DataFrame(index=[0, 1])):
        with pytest.raises(BloomMCPError) as exc:
            helper.parse_inline_csv_frame(_VALID_CSV)
    assert exc.value.code == "invalid_input"
    assert "column" in exc.value.message.lower()


def test_decode_failure_is_a_structured_error():
    helper = _import_helper()
    with patch(
        "pandas.read_csv", side_effect=UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad")
    ):
        with pytest.raises(BloomMCPError) as exc:
            helper.parse_inline_csv_frame(_VALID_CSV)
    assert exc.value.code == "invalid_input"


def test_lone_surrogate_encode_failure_is_a_structured_error():
    """A lone UTF-16 surrogate (e.g. from a lossy upstream decode) raises
    UnicodeEncodeError on `.encode("utf-8")` — must be mapped explicitly, since
    it happens before pandas.read_csv is ever called and would otherwise become
    an opaque internal_error."""
    helper = _import_helper()
    content = "Barcode,geno,traitA\nS1,\ud800,1.0\n"
    with pytest.raises(BloomMCPError) as exc:
        helper.parse_inline_csv_frame(content)
    assert exc.value.code == "invalid_input"


def test_compute_input_sha256_lone_surrogate_is_a_structured_error():
    helper = _import_helper()
    with pytest.raises(BloomMCPError) as exc:
        helper.compute_input_sha256("\ud800")
    assert exc.value.code == "invalid_input"


# ── 1.9 leading BOM ──────────────────────────────────────────────────────────


def test_leading_utf8_bom_is_stripped_before_parsing():
    helper = _import_helper()
    bom_content = "﻿" + _VALID_CSV
    frame = helper.parse_inline_csv_frame(bom_content)
    assert "Barcode" in frame.df.columns
    assert "﻿Barcode" not in frame.df.columns


def test_repeated_leading_boms_are_all_stripped():
    """A double-encoded/re-saved file can carry more than one leading BOM —
    stripping only the first would leave a mangled column name and break role
    detection for it."""
    helper = _import_helper()
    repeated_bom_content = "﻿﻿﻿" + _VALID_CSV
    frame = helper.parse_inline_csv_frame(repeated_bom_content)
    assert "Barcode" in frame.df.columns
    assert not any(col.startswith("﻿") for col in frame.df.columns)
    assert frame.sample_id_col == "Barcode"


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


def test_duplicate_headers_match_direct_pandas_behavior():
    helper = _import_helper()
    content = "Barcode,geno,geno,traitA\nS1,g1,g1dup,1.0\n"
    direct = pd.read_csv(io.StringIO(content))
    frame = helper.parse_inline_csv_frame(content)
    assert list(frame.df.columns) == list(direct.columns)


def test_whitespace_only_header_matches_direct_pandas_behavior():
    """A genuinely blank/whitespace-only column name — distinct from a duplicate
    name — pinned separately so the two edge cases aren't conflated."""
    helper = _import_helper()
    content = "Barcode,geno, ,traitA\nS1,g1,x,1.0\n"
    direct = pd.read_csv(io.StringIO(content))
    frame = helper.parse_inline_csv_frame(content)
    assert list(frame.df.columns) == list(direct.columns)


# ── column-count guard ───────────────────────────────────────────────────────


def test_too_many_columns_is_rejected_before_parsing():
    """The DoS-relevant case: a wide-but-short CSV can sit comfortably under
    MAX_INLINE_CSV_BYTES while still costing real CPU in pandas.read_csv's
    per-column overhead (measured externally: ~480k columns, 4.69 MB, ~7.7s
    CPU). The guard must reject via the cheap header estimate BEFORE
    pandas.read_csv ever runs — a post-parse-only check would already have
    paid that cost by the time it fires."""
    helper = _import_helper()
    n_cols = helper.MAX_INLINE_CSV_COLUMNS + 1
    header = ",".join(f"c{i}" for i in range(n_cols))
    row = ",".join("1" for _ in range(n_cols))
    content = f"{header}\n{row}\n"

    with patch("pandas.read_csv") as mock_read_csv:
        with pytest.raises(BloomMCPError) as exc:
            helper.parse_inline_csv_frame(content)
        mock_read_csv.assert_not_called()
    assert exc.value.code == "invalid_input"
    assert str(helper.MAX_INLINE_CSV_COLUMNS) in exc.value.message


def test_wide_csv_dos_repro_is_rejected_fast():
    """Reproduces the exact adversarial shape the review's DoS finding used —
    a single header + single data row of ~480,000 narrow columns, under the
    byte cap — and asserts it is rejected in well under a second, not after
    paying the ~7.7s CPU cost a post-parse-only check would incur."""
    import time

    helper = _import_helper()
    n = 480_000
    header = ",".join(f"c{i}" for i in range(n))
    row = ",".join("1" for _ in range(n))
    content = f"{header}\n{row}\n"
    assert len(content.encode("utf-8")) < helper.MAX_INLINE_CSV_BYTES

    start = time.perf_counter()
    with pytest.raises(BloomMCPError) as exc:
        helper.parse_inline_csv_frame(content)
    elapsed = time.perf_counter() - start

    assert exc.value.code == "invalid_input"
    assert (
        elapsed < 1.0
    ), f"rejection took {elapsed:.2f}s — the pre-parse guard didn't fire"


def test_embedded_newline_in_header_cell_does_not_bypass_the_guard():
    """Regression: a naive `csv_content.split("\\n", 1)[0]` (the round-1 fix's
    implementation) cuts a row short the moment any field contains a literal
    newline inside quotes (valid CSV) — reproduced directly: a header whose
    first cell is a quoted value with one embedded newline made the estimate
    say "1 column" for a real ~480,000-column row, letting pandas.read_csv run
    anyway (~5.5s CPU measured) before the post-parse backstop finally caught
    it. The fix must count the true width via the header row's real extent,
    not a line fragment, and reject fast — not after paying the parse cost."""
    import time

    helper = _import_helper()
    n = 480_000
    headers = ['"h0\nrest_of_h0"'] + [f"c{i}" for i in range(1, n)]
    header_line = ",".join(headers)
    row = ",".join("1" for _ in range(n))
    content = f"{header_line}\n{row}\n"
    assert len(content.encode("utf-8")) < helper.MAX_INLINE_CSV_BYTES

    with patch("pandas.read_csv") as mock_read_csv:
        start = time.perf_counter()
        with pytest.raises(BloomMCPError) as exc:
            helper.parse_inline_csv_frame(content)
        elapsed = time.perf_counter() - start
        # The load-bearing assertion: pandas.read_csv must never be reached,
        # regardless of which specific pre-parse guard (column-count-over-
        # limit, or the header-scan cap firing first because the true row is
        # itself too large to cheaply finish counting) ends up firing first —
        # both are safe, fast outcomes; only reaching pandas.read_csv is not.
        mock_read_csv.assert_not_called()

    assert exc.value.code == "invalid_input"
    assert (
        elapsed < 2.0
    ), f"rejection took {elapsed:.2f}s — a pre-parse guard didn't fire"


def test_embedded_newline_past_legitimate_looking_columns_is_still_counted_correctly():
    """The reviewer's exact concern: an attacker pads legitimate-looking
    columns first (to look like a normal call) before the embedded-newline
    trick. Row is small enough (well under the header-scan cap) that this
    must be resolved by genuinely counting through the embedded newline, not
    merely by the scan cap giving up — proving the fix counts correctly, not
    just fails safe when it can't."""
    helper = _import_helper()
    n_cols = helper.MAX_INLINE_CSV_COLUMNS + 500
    headers = [f"c{i}" for i in range(n_cols - 1)] + ['"last\nwith_newline"']
    header_line = ",".join(headers)
    row = ",".join("1" for _ in range(n_cols))
    content = f"{header_line}\n{row}\n"
    assert len(content.encode("utf-8")) < helper._MAX_HEADER_SCAN_BYTES

    with patch("pandas.read_csv") as mock_read_csv:
        with pytest.raises(BloomMCPError) as exc:
            helper.parse_inline_csv_frame(content)
        mock_read_csv.assert_not_called()
    assert exc.value.code == "invalid_input"
    assert str(n_cols) in exc.value.message


def test_legitimate_embedded_newline_in_header_cell_is_counted_correctly():
    """The positive counterpart: a genuinely small, well-formed header whose
    one cell contains an embedded newline must still be counted and accepted
    correctly — csv.reader's multi-line-aware tokenization, not a naive
    single-line split, must recover the true row, not just reject anything
    with a newline in a quoted field."""
    helper = _import_helper()
    content = '"Barcode with\nnote",geno,traitA\nS1,g1,1.0\nS2,g2,2.0\n'
    frame = helper.parse_inline_csv_frame(content)
    assert frame.df.shape[1] == 3
    assert list(frame.df.columns)[0] == "Barcode with\nnote"


def test_unterminated_quote_in_header_is_rejected_without_scanning_everything():
    """The residual pathological case the bounded scan exists for: an
    unterminated quote would otherwise force csv.reader to consume the entire
    payload looking for a closing quote that never comes — re-introducing the
    same CPU-cost problem this whole guard exists to avoid, just one level
    deeper. Must reject outright, fast, rather than scan the whole payload."""
    import time

    helper = _import_helper()
    # One huge, never-closed quoted field followed by a lot of filler — large
    # enough that scanning it all would itself be slow, but comfortably under
    # MAX_INLINE_CSV_BYTES.
    filler = "x" * (2 * helper.MAX_INLINE_CSV_BYTES // 3)
    content = f'"unterminated{filler}\n'

    start = time.perf_counter()
    with pytest.raises(BloomMCPError) as exc:
        helper.parse_inline_csv_frame(content)
    elapsed = time.perf_counter() - start

    assert exc.value.code == "invalid_input"
    assert elapsed < 1.0, f"rejection took {elapsed:.2f}s — the scan cap didn't fire"


def test_quoted_comma_in_header_does_not_cause_a_false_positive_rejection():
    """A naive `.count(",")` estimate would overcount the moment a single header
    name contains a quoted comma, falsely rejecting an otherwise-fine payload.
    Construct exactly MAX_INLINE_CSV_COLUMNS real columns, with one header
    value containing a quoted comma (which would push a naive count 1 over the
    limit) — the csv.reader-based estimate must count it correctly as one
    column and accept the content."""
    helper = _import_helper()
    n_cols = helper.MAX_INLINE_CSV_COLUMNS
    headers = ['"h0, extra"'] + [f"h{i}" for i in range(1, n_cols)]
    header_line = ",".join(headers)
    row = ",".join("1" for _ in range(n_cols))
    content = f"{header_line}\n{row}\n"

    # A naive comma-count would see n_cols + 1 "columns" (the quoted comma adds
    # one) and wrongly reject; assert that doesn't happen.
    frame = helper.parse_inline_csv_frame(content)
    assert frame.df.shape[1] == n_cols


def test_column_count_at_the_limit_is_accepted():
    helper = _import_helper()
    n_cols = helper.MAX_INLINE_CSV_COLUMNS
    header = ",".join(f"c{i}" for i in range(n_cols))
    row = ",".join("1" for _ in range(n_cols))
    content = f"{header}\n{row}\n"

    frame = helper.parse_inline_csv_frame(content)
    assert frame.df.shape[1] == n_cols


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


# ═══════════════════════════════════════════════════════════════════════════
# #582 rollout — the shared resolver, the row cap, the serializer, the flag
#
# `resolve_inline_or_experiment` is what stops ten tools from growing ten
# copies of "exactly one is required". Tested here once, and then *used* by
# every tool rather than reimplemented — the per-tool suites assert the
# behavior reaches them; these assert what the behavior is.
# ═══════════════════════════════════════════════════════════════════════════


_ROLE_CSV = _VALID_CSV


def _reader_call_returning(frame):
    """A stand-in for a tool's own reader call, recording whether it ran."""
    calls: list[int] = []

    def _call():
        calls.append(1)
        return frame

    return _call, calls


# ── exactly one of experiment / csv_content ─────────────────────────────────


def test_resolver_rejects_both_inputs_without_reading_or_parsing():
    helper = _import_helper()
    reader_call, calls = _reader_call_returning(object())

    with patch.object(helper.pd, "read_csv") as read_csv:
        with pytest.raises(BloomMCPError) as exc:
            helper.resolve_inline_or_experiment(
                experiment="turface_19.csv",
                csv_content=_ROLE_CSV,
                reader_call=reader_call,
            )

    assert exc.value.code == "invalid_input"
    assert "exactly one" in exc.value.message.lower()
    read_csv.assert_not_called()
    assert calls == [], "the reader must not run when the call is already invalid"


def test_resolver_rejects_neither_input():
    helper = _import_helper()
    reader_call, calls = _reader_call_returning(object())

    with pytest.raises(BloomMCPError) as exc:
        helper.resolve_inline_or_experiment(
            experiment=None, csv_content=None, reader_call=reader_call
        )

    assert exc.value.code == "invalid_input"
    assert "exactly one" in exc.value.message.lower()
    assert calls == []


def test_resolver_reports_the_input_conflict_before_a_parameter_conflict():
    """Ordering is specified, not incidental: a call that is wrong in two ways
    names the input conflict. Without this, a per-tool assertion like "the error
    names version_2 only" would depend on check order and flake."""
    helper = _import_helper()

    with pytest.raises(BloomMCPError) as exc:
        helper.resolve_inline_or_experiment(
            experiment="turface_19.csv",
            csv_content=_ROLE_CSV,
            registered_only={"version": "v2"},
        )

    assert "exactly one" in exc.value.message.lower()
    assert "version" not in exc.value.message


# ── the two resolved shapes ─────────────────────────────────────────────────


def test_resolver_inline_path_matches_the_parse_helper_and_hash_helper():
    helper = _import_helper()
    reader_call, calls = _reader_call_returning(object())

    resolved = helper.resolve_inline_or_experiment(
        experiment=None, csv_content=_ROLE_CSV, reader_call=reader_call
    )

    expected = helper.parse_inline_csv_frame(_ROLE_CSV)
    assert resolved.is_inline is True
    assert resolved.label == "csv_content"
    assert resolved.input_sha256 == helper.compute_input_sha256(_ROLE_CSV)
    assert resolved.frame.df.equals(expected.df)
    assert resolved.frame.trait_cols == expected.trait_cols
    assert resolved.frame.source == "inline"
    assert calls == [], "the inline path must bypass the reader entirely"


def test_resolver_registered_path_returns_the_tools_own_frame():
    helper = _import_helper()
    sentinel = object()
    reader_call, calls = _reader_call_returning(sentinel)

    resolved = helper.resolve_inline_or_experiment(
        experiment="turface_19.csv", csv_content=None, reader_call=reader_call
    )

    assert resolved.frame is sentinel, (
        "the registered path must return the tool's own read, so require_clean, "
        "version pinning and read-error mapping stay in the tool"
    )
    assert resolved.is_inline is False
    assert resolved.input_sha256 is None
    assert resolved.label == "turface_19.csv"
    assert calls == [1]


def test_resolver_registered_path_requires_a_reader_call():
    """A tool that forgets to pass its own read must fail loudly here rather
    than returning a frameless result that explodes later."""
    helper = _import_helper()
    with pytest.raises(ValueError):
        helper.resolve_inline_or_experiment(
            experiment="turface_19.csv", csv_content=None, reader_call=None
        )


# ── one vocabulary, parameterized by the registered field's name ────────────


def test_resolver_names_the_tools_own_registered_parameter():
    """`load_experiment_data` pairs csv_content with `filename`, and
    cross_experiment_correlations resolves per side. One vocabulary still has to
    produce all of them, so the resolver takes the parameter's name."""
    helper = _import_helper()

    with pytest.raises(BloomMCPError) as exc:
        helper.resolve_inline_or_experiment(
            experiment=None,
            csv_content=None,
            registered_field="filename",
            csv_content_field="csv_content",
        )
    assert "filename" in exc.value.message
    assert "experiment" not in exc.value.message

    with pytest.raises(BloomMCPError) as side2:
        helper.resolve_inline_or_experiment(
            experiment="a",
            csv_content="b",
            registered_field="experiment_2",
            csv_content_field="csv_content_2",
        )
    assert "experiment_2" in side2.value.message
    assert "csv_content_2" in side2.value.message


def test_resolver_message_is_identical_modulo_the_parameter_names():
    """The anti-drift property: substituting the field names makes two tools'
    messages equal. This is what task 11.1 asserts across the roster."""
    helper = _import_helper()

    def _message(registered_field, csv_field):
        with pytest.raises(BloomMCPError) as exc:
            helper.resolve_inline_or_experiment(
                experiment=None,
                csv_content=None,
                registered_field=registered_field,
                csv_content_field=csv_field,
            )
        return exc.value.message, exc.value.remedy

    msg_a, rem_a = _message("experiment", "csv_content")
    msg_b, rem_b = _message("filename", "csv_content")
    assert msg_a == msg_b.replace("filename", "experiment")
    assert rem_a == rem_b.replace("filename", "experiment")


# ── registered-only parameters: reject, never ignore ────────────────────────


def test_registered_only_parameter_is_rejected_on_the_inline_path():
    helper = _import_helper()

    with pytest.raises(BloomMCPError) as exc:
        helper.resolve_inline_or_experiment(
            experiment=None,
            csv_content=_ROLE_CSV,
            registered_only={"version": "v2"},
        )

    assert exc.value.code == "invalid_input"
    assert "version" in exc.value.message
    assert "csv_content" in exc.value.message


def test_registered_only_parameter_that_is_none_is_a_no_op():
    helper = _import_helper()
    resolved = helper.resolve_inline_or_experiment(
        experiment=None,
        csv_content=_ROLE_CSV,
        registered_only={"version": None, "user_label": None},
    )
    assert resolved.is_inline is True


def test_registered_only_parameters_are_untouched_on_the_registered_path():
    helper = _import_helper()
    reader_call, _calls = _reader_call_returning(object())
    resolved = helper.resolve_inline_or_experiment(
        experiment="turface_19.csv",
        csv_content=None,
        registered_only={"version": "v2", "user_label": "x"},
        reader_call=reader_call,
    )
    assert resolved.is_inline is False


def test_registered_only_rejection_names_every_offender_not_just_the_first():
    helper = _import_helper()
    with pytest.raises(BloomMCPError) as exc:
        helper.resolve_inline_or_experiment(
            experiment=None,
            csv_content=_ROLE_CSV,
            registered_only={"version": "v2", "user_label": "x"},
        )
    assert "user_label" in exc.value.message
    assert "version" in exc.value.message


def test_registered_only_false_is_still_a_supplied_value():
    """`include_plots=False` is the default, not a request — it must not trip the
    guard. `include_plots=True` must. A truthiness test on the *value* is wrong
    for `version="latest"` (a real pin) but right for a bool flag, so the caller
    passes only what it means; this pins that a False flag is filtered out by the
    caller, not silently accepted as a rejection-worthy value."""
    helper = _import_helper()
    resolved = helper.resolve_inline_or_experiment(
        experiment=None,
        csv_content=_ROLE_CSV,
        registered_only={"include_plots": None},
    )
    assert resolved.is_inline is True


# ── row cap (design.md Decision 9) ──────────────────────────────────────────


def test_row_count_above_the_cap_is_rejected():
    helper = _import_helper()
    rows = helper.MAX_INLINE_CSV_ROWS + 1
    body = "".join(f"S{i},g1,1.0,2.0\n" for i in range(rows))
    with pytest.raises(BloomMCPError) as exc:
        helper.parse_inline_csv_frame("Barcode,geno,traitA,traitB\n" + body)
    assert exc.value.code == "invalid_input"
    assert str(rows) in exc.value.message
    assert str(helper.MAX_INLINE_CSV_ROWS) in exc.value.message


def test_row_count_at_the_cap_is_accepted():
    helper = _import_helper()
    rows = helper.MAX_INLINE_CSV_ROWS
    body = "".join(f"S{i},g1,1.0,2.0\n" for i in range(rows))
    frame = helper.parse_inline_csv_frame("Barcode,geno,traitA,traitB\n" + body)
    assert len(frame.df) == rows


def test_row_cap_is_well_under_what_the_byte_cap_admits():
    """The point of the row cap: a payload can sit under MAX_INLINE_CSV_BYTES and
    still carry hundreds of thousands of rows, which is a quadratic-time problem
    for hierarchical clustering and an all-pairs problem for correlations."""
    helper = _import_helper()
    narrow_row = "S,g,1,2\n"
    rows_the_byte_cap_admits = helper.MAX_INLINE_CSV_BYTES // len(narrow_row)
    assert rows_the_byte_cap_admits > 10 * helper.MAX_INLINE_CSV_ROWS


# ── table serialization for the opt-in producer returns ─────────────────────


def test_serialize_table_csv_round_trips_without_an_index_column():
    helper = _import_helper()
    df = pd.DataFrame({"Barcode": ["S1", "S2"], "traitA": [1.0, 2.0]})
    text = helper.serialize_table_csv(df)
    restored = pd.read_csv(io.StringIO(text))
    assert list(restored.columns) == ["Barcode", "traitA"]
    assert restored["traitA"].tolist() == [1.0, 2.0]


def test_serialize_table_csv_pins_the_line_terminator():
    """pandas defaults `lineterminator` to os.linesep, which would make every
    returned digest platform-dependent. The repo already forces LF on CSVs via
    .gitattributes for the same class of bug."""
    helper = _import_helper()
    df = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
    text = helper.serialize_table_csv(df)
    assert "\r" not in text
    assert text.count("\n") == 3  # header + two rows


def test_serialize_table_csv_is_stable_across_calls():
    helper = _import_helper()
    df = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
    first = hashlib.sha256(helper.serialize_table_csv(df).encode()).hexdigest()
    second = hashlib.sha256(helper.serialize_table_csv(df).encode()).hexdigest()
    assert first == second


def test_serialize_table_csv_rejects_an_oversized_table():
    helper = _import_helper()
    wide = "x" * 4096
    df = pd.DataFrame({"a": [wide] * 2048})
    with pytest.raises(BloomMCPError) as exc:
        helper.serialize_table_csv(df, field="cleaned_csv")
    assert exc.value.code == "invalid_input"
    assert "cleaned_csv" in exc.value.message
    assert str(helper.MAX_INLINE_CSV_BYTES) in exc.value.message


# ── kill switch (design.md Decision 10) ─────────────────────────────────────


def test_inline_path_is_rejected_when_the_kill_switch_is_off(monkeypatch):
    helper = _import_helper()
    monkeypatch.setenv("BLOOMMCP_INLINE_CSV_ENABLED", "false")

    with pytest.raises(BloomMCPError) as exc:
        helper.resolve_inline_or_experiment(experiment=None, csv_content=_ROLE_CSV)

    assert exc.value.code == "invalid_input"
    assert "experiment" in exc.value.remedy


def test_registered_path_is_untouched_when_the_kill_switch_is_off(monkeypatch):
    helper = _import_helper()
    monkeypatch.setenv("BLOOMMCP_INLINE_CSV_ENABLED", "false")
    reader_call, calls = _reader_call_returning(object())

    resolved = helper.resolve_inline_or_experiment(
        experiment="turface_19.csv", csv_content=None, reader_call=reader_call
    )
    assert resolved.is_inline is False
    assert calls == [1]


def test_kill_switch_defaults_to_enabled(monkeypatch):
    helper = _import_helper()
    monkeypatch.delenv("BLOOMMCP_INLINE_CSV_ENABLED", raising=False)
    resolved = helper.resolve_inline_or_experiment(
        experiment=None, csv_content=_ROLE_CSV
    )
    assert resolved.is_inline is True


def test_kill_switch_is_read_per_call_not_at_import(monkeypatch):
    """Read at call time so an operator can flip it with a container restart
    rather than needing a rebuild — and so tests can toggle it."""
    helper = _import_helper()
    monkeypatch.setenv("BLOOMMCP_INLINE_CSV_ENABLED", "false")
    with pytest.raises(BloomMCPError):
        helper.resolve_inline_or_experiment(experiment=None, csv_content=_ROLE_CSV)
    monkeypatch.setenv("BLOOMMCP_INLINE_CSV_ENABLED", "true")
    assert helper.resolve_inline_or_experiment(
        experiment=None, csv_content=_ROLE_CSV
    ).is_inline


def test_a_real_near_cap_payload_is_rejected_by_the_row_cap_not_the_byte_cap():
    """The scenario MAX_INLINE_CSV_ROWS exists for, built for real rather than
    argued arithmetically.

    A payload sized just under MAX_INLINE_CSV_BYTES parses in well under a second
    and yields hundreds of thousands of rows — which is a quadratic-time problem
    for hierarchical clustering and an all-pairs problem for correlations. This
    asserts the row cap is what stops it, and names the row count so a future
    reader can see the gap between the two limits."""
    helper = _import_helper()
    header = "Barcode,geno,traitA,traitB\n"
    row = "S,g,1.0,2.0\n"
    rows = (helper.MAX_INLINE_CSV_BYTES - len(header)) // len(row)
    payload = header + row * rows

    size = len(payload.encode("utf-8"))
    assert size <= helper.MAX_INLINE_CSV_BYTES, "must be under the byte cap"
    assert rows > 10 * helper.MAX_INLINE_CSV_ROWS, (
        f"a compliant payload should carry far more rows than the row cap; "
        f"got {rows} vs a cap of {helper.MAX_INLINE_CSV_ROWS}"
    )

    with pytest.raises(BloomMCPError) as exc:
        helper.parse_inline_csv_frame(payload)

    # The row cap, not the byte cap, is what rejected it.
    assert str(helper.MAX_INLINE_CSV_ROWS) in exc.value.message
    assert "row" in exc.value.message
    assert str(rows) in exc.value.message


def test_serialize_table_csv_ignores_the_platform_line_separator(monkeypatch):
    """Direct evidence for the platform-independence claim: even with os.linesep
    reporting CRLF, the serialized text is LF. Asserting "no \\r on this machine"
    would pass vacuously on Linux and CI, which are the only places it runs."""
    helper = _import_helper()
    monkeypatch.setattr(os, "linesep", "\r\n")
    text = helper.serialize_table_csv(pd.DataFrame({"a": [1, 2]}))
    assert "\r" not in text
    assert text == "a\n1\n2\n"


def test_round_trip_guard_accepts_a_table_that_re_resolves_correctly():
    helper = _import_helper()
    frame = helper.parse_inline_csv_frame(_VALID_CSV)
    text = helper.serialize_table_csv(
        frame.df, field="cleaned_csv", verify_trait_cols=frame.trait_cols
    )
    assert "traitA" in text


def test_round_trip_guard_rejects_a_table_missing_a_certified_trait():
    helper = _import_helper()
    frame = helper.parse_inline_csv_frame(_VALID_CSV)
    with pytest.raises(BloomMCPError) as exc:
        helper.serialize_table_csv(
            frame.df.drop(columns=["traitA"]),
            field="cleaned_csv",
            verify_trait_cols=frame.trait_cols,
        )
    assert exc.value.code == "assumption_violated"
    assert "traitA" in exc.value.message
    assert "would be lost" in exc.value.message


def test_round_trip_guard_rejects_a_table_that_gains_an_undeclared_trait():
    """The mirror failure: a column the producer did not certify being detected as
    a trait by the consumer. Just as wrong as losing one — the next call would
    analyze a column this one never reported on."""
    helper = _import_helper()
    frame = helper.parse_inline_csv_frame(_VALID_CSV)
    extra = frame.df.assign(traitC=[7.0, 8.0, 9.0])
    with pytest.raises(BloomMCPError) as exc:
        helper.serialize_table_csv(
            extra, field="cleaned_csv", verify_trait_cols=frame.trait_cols
        )
    assert exc.value.code == "assumption_violated"
    assert "traitC" in exc.value.message
    assert "would be picked up" in exc.value.message


def test_round_trip_guard_is_off_by_default():
    """Producers opt in; the generic serializer stays generic."""
    helper = _import_helper()
    text = helper.serialize_table_csv(pd.DataFrame({"only_metadata": ["x", "y"]}))
    assert text == "only_metadata\nx\ny\n"


# ── registered-only rejection wording is accurate per parameter ─────────────


def test_user_label_rejection_explains_labels_not_source_pins():
    """A generic "only applies to a registered experiment's stored versions and
    sources" is true of the pins and plainly wrong for user_label, which is about
    writing rather than reading. An inaccurate rejection message is the drift this
    module exists to prevent."""
    helper = _import_helper()
    with pytest.raises(BloomMCPError) as exc:
        helper.resolve_inline_or_experiment(
            experiment=None,
            csv_content=_ROLE_CSV,
            registered_only={"user_label": "my-run"},
        )
    message = exc.value.message
    assert "version directory" in message
    assert "no run is created" in message
    assert "sources" not in message


def test_source_pin_rejection_explains_reading_not_labelling():
    helper = _import_helper()
    with pytest.raises(BloomMCPError) as exc:
        helper.resolve_inline_or_experiment(
            experiment=None, csv_content=_ROLE_CSV, registered_only={"source_id": 9}
        )
    assert "pins which stored raw source to read" in exc.value.message
    assert "version directory" not in exc.value.message


def test_each_offender_gets_its_own_reason_when_several_are_supplied():
    helper = _import_helper()
    with pytest.raises(BloomMCPError) as exc:
        helper.resolve_inline_or_experiment(
            experiment=None,
            csv_content=_ROLE_CSV,
            registered_only={"source_id": 9, "user_label": "x"},
        )
    message = exc.value.message
    assert "pins which stored raw source to read" in message
    assert "version directory" in message


def test_plot_companion_parameters_inherit_the_plot_reason_by_prefix():
    """A new plot_* knob on any tool should get correct wording without anyone
    remembering to add it to the table."""
    helper = _import_helper()
    for name in ("include_plots", "plots", "plot_font_family", "plot_alpha"):
        with pytest.raises(BloomMCPError) as exc:
            helper.resolve_inline_or_experiment(
                experiment=None, csv_content=_ROLE_CSV, registered_only={name: "x"}
            )
        assert "persisted as run artifacts" in exc.value.message, name


def test_every_rostered_parameter_has_a_specific_reason():
    """No parameter this rollout rejects should fall through to the generic
    clause — that fallback exists for safety, not for the known roster."""
    helper = _import_helper()
    roster = [
        "source_id",
        "run_id",
        "version",
        "version_1",
        "version_2",
        "user_label",
        "include_plots",
        "plots",
        "plot_font_family",
        "plot_font_size",
        "plot_alpha",
        "plot_cmap",
        "plot_point_size",
    ]
    generic = "only applies to a registered experiment"
    for name in roster:
        reason = helper._registered_only_reason(name, "csv_content")
        assert generic not in reason, f"{name} fell through to the generic clause"
