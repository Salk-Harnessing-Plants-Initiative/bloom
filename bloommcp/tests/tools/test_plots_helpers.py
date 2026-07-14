"""Unit tests for the tool-agnostic ``_plots`` helpers.

These tests exercise ``validate_plot_keys`` and ``close_figures`` in isolation —
no live stack, no Supabase, no matplotlib import on the validation path.
"""

from __future__ import annotations

import pytest

from bloom_mcp.contract import BloomMCPError
from bloom_mcp.tools._plots import close_figures, validate_plot_keys

_VALID = {"key_a", "key_b", "key_c"}


# ── validate_plot_keys ───────────────────────────────────────────────────────


def test_none_is_accepted_without_error():
    validate_plot_keys(None, _VALID)  # no exception


def test_valid_subset_is_accepted():
    validate_plot_keys(["key_a", "key_b"], _VALID)  # no exception


def test_full_list_is_accepted():
    validate_plot_keys(list(_VALID), _VALID)  # no exception


def test_unknown_key_raises_invalid_input_naming_it():
    with pytest.raises(BloomMCPError) as exc:
        validate_plot_keys(["not_real"], _VALID)
    assert exc.value.code == "invalid_input"
    assert "not_real" in exc.value.message


def test_multiple_unknown_keys_named_in_error():
    with pytest.raises(BloomMCPError) as exc:
        validate_plot_keys(["bad_a", "bad_b"], _VALID)
    assert exc.value.code == "invalid_input"
    assert "bad_a" in exc.value.message or "bad_b" in exc.value.message


def test_duplicate_key_raises_invalid_input_naming_duplicate():
    with pytest.raises(BloomMCPError) as exc:
        validate_plot_keys(["key_a", "key_a"], _VALID)
    assert exc.value.code == "invalid_input"
    assert "key_a" in exc.value.message


def test_empty_list_raises_invalid_input():
    with pytest.raises(BloomMCPError) as exc:
        validate_plot_keys([], _VALID)
    assert exc.value.code == "invalid_input"


# ── close_figures ────────────────────────────────────────────────────────────


def test_close_figures_empty_dict_does_not_raise():
    close_figures({})  # no exception, no matplotlib import


def test_close_figures_does_not_raise_on_already_closed_figure():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig = plt.figure()
    plt.close(fig)  # pre-close
    close_figures({"k": fig})  # best-effort; must not raise


def test_close_figures_clears_open_figures():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig = plt.figure()
    assert fig.number in plt.get_fignums()
    close_figures({"k": fig})
    assert fig.number not in plt.get_fignums()
