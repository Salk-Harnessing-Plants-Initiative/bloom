"""Two-mode contract for the shared ``_validate_trait_subset`` (#309 BL-1).

The certified-set / empty / duplicate rejections that ``pca_analysis`` + ``clustering`` need
live behind a ``require_certified=True`` opt-in; the **default** must stay byte-identical to
what ``qc_clean`` / ``qc_inspect`` have always consumed. This pins both so promoting the strict
checks into the shared helper cannot silently change the raw-frame producers' behavior.
"""

from __future__ import annotations

import pytest

from bloom_mcp.contract import BloomMCPError
from bloom_mcp.data_access import FakeReader
from bloom_mcp.tools._qc_shared import _validate_trait_subset

_EXPERIMENT = "exp.csv"


@pytest.fixture
def frame():
    import pandas as pd

    reader = FakeReader()
    df = pd.DataFrame(
        {
            "Barcode": ["a", "b", "c", "d"],  # non-numeric metadata
            "Replicate": [1, 2, 1, 2],  # numeric, but a role column (not a trait)
            "traitA": [1.0, 2.0, 3.0, 4.0],
            "traitB": [4.0, 3.0, 2.0, 1.0],
        }
    )
    reader.add_cleaned_version(_EXPERIMENT, "v1", df, make_latest=True)
    return reader.load_experiment(_EXPERIMENT, require_clean=True)


# ── default mode (qc_clean / qc_inspect) — behavior must be preserved ───────


def test_default_mode_accepts_empty_and_duplicates_and_any_numeric_column(frame):
    # Empty list falls through to "all detected traits" — unchanged.
    _validate_trait_subset(frame, [], _EXPERIMENT)
    # Duplicates are harmless to those delegates — unchanged.
    _validate_trait_subset(frame, ["traitA", "traitA"], _EXPERIMENT)
    # A numeric non-trait column (Replicate) is accepted — only existence + numeric is checked.
    _validate_trait_subset(frame, ["Replicate"], _EXPERIMENT)


def test_default_mode_rejects_missing_and_non_numeric(frame):
    with pytest.raises(BloomMCPError) as missing:
        _validate_trait_subset(frame, ["nope"], _EXPERIMENT)
    assert missing.value.code == "invalid_input" and "nope" in missing.value.message

    with pytest.raises(BloomMCPError) as non_numeric:
        _validate_trait_subset(frame, ["Barcode"], _EXPERIMENT)
    assert (
        non_numeric.value.code == "invalid_input"
        and "Barcode" in non_numeric.value.message
    )


# ── require_certified mode (pca_analysis / clustering) — strict ─────────────


def test_certified_mode_accepts_a_valid_certified_subset(frame):
    _validate_trait_subset(
        frame, ["traitA", "traitB"], _EXPERIMENT, require_certified=True
    )


def test_certified_mode_rejects_empty_duplicate_and_non_certified(frame):
    with pytest.raises(BloomMCPError) as empty:
        _validate_trait_subset(frame, [], _EXPERIMENT, require_certified=True)
    assert empty.value.code == "invalid_input"

    with pytest.raises(BloomMCPError) as dup:
        _validate_trait_subset(
            frame, ["traitA", "traitA"], _EXPERIMENT, require_certified=True
        )
    assert dup.value.code == "invalid_input" and "traitA" in dup.value.message

    # Replicate is numeric + in the frame, but not a certified trait → rejected here.
    with pytest.raises(BloomMCPError) as outside:
        _validate_trait_subset(
            frame, ["Replicate"], _EXPERIMENT, require_certified=True
        )
    assert (
        outside.value.code == "invalid_input" and "Replicate" in outside.value.message
    )


# ── `certified` is presentation only (#582) ─────────────────────────────────
#
# A consumer's inline `csv_content` path runs the same three certified-mode
# checks — `frame.trait_cols` is populated by the same `resolve_columns` for an
# inline frame — but the wording "certified-clean traits of 'my.csv'" is false
# twice over on that path: there is no experiment, and no certification was ever
# made. The flag must change what the message *says* without changing what the
# validator *accepts*.


def _rejections(frame, **kwargs) -> dict[str, BloomMCPError]:
    """Every certified-mode rejection, keyed by case."""
    cases = {
        "empty": [],
        "duplicate": ["traitA", "traitA"],
        "outside": ["Replicate"],
    }
    out: dict[str, BloomMCPError] = {}
    for name, requested in cases.items():
        with pytest.raises(BloomMCPError) as exc:
            _validate_trait_subset(
                frame, requested, _EXPERIMENT, require_certified=True, **kwargs
            )
        out[name] = exc.value
    return out


def test_certified_flag_does_not_change_the_accepted_column_set(frame):
    """The load-bearing property: same columns in, same verdict out. Only the
    prose differs."""
    certified_errors = _rejections(frame, certified=True)
    inline_errors = _rejections(frame, certified=False)
    assert set(certified_errors) == set(inline_errors)
    for case in certified_errors:
        assert certified_errors[case].code == inline_errors[case].code

    # And a valid selection is accepted identically under both.
    _validate_trait_subset(
        frame, ["traitA"], _EXPERIMENT, require_certified=True, certified=True
    )
    _validate_trait_subset(
        frame, ["traitA"], _EXPERIMENT, require_certified=True, certified=False
    )


def test_inline_wording_claims_no_certification(frame):
    inline = _rejections(frame, certified=False)
    for err in inline.values():
        assert "certified" not in err.message.lower(), err.message
        assert "certified" not in err.remedy.lower(), err.remedy
    assert "detected trait columns" in inline["outside"].message


def test_certified_wording_is_unchanged_by_default(frame):
    """Eight call sites rely on the default; none of them may shift."""
    default = _rejections(frame)
    explicit = _rejections(frame, certified=True)
    for case in default:
        assert default[case].message == explicit[case].message
        assert default[case].remedy == explicit[case].remedy
    assert "certified-clean traits" in default["outside"].message


def test_default_mode_is_unaffected_by_the_certified_flag(frame):
    """`certified` only reaches the require_certified branch; the producers'
    default path has no certified-set language to swap."""
    with pytest.raises(BloomMCPError) as strict:
        _validate_trait_subset(frame, ["nope"], _EXPERIMENT, certified=True)
    with pytest.raises(BloomMCPError) as inline:
        _validate_trait_subset(frame, ["nope"], _EXPERIMENT, certified=False)
    assert strict.value.message == inline.value.message
