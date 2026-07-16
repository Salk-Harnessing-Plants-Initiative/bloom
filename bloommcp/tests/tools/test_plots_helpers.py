"""Unit tests for the tool-agnostic ``_plots`` helpers.

These tests exercise ``validate_plot_keys``, ``generate_figures``, and
``close_figures`` in isolation — no live stack, no Supabase, no matplotlib
import on the validation path.
"""

from __future__ import annotations

import pytest

from bloom_mcp.contract import BloomMCPError
from bloom_mcp.tools._plots import close_figures, generate_figures, validate_plot_keys

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


# ── generate_figures ─────────────────────────────────────────────────────────


def test_generate_figures_populates_caller_dict():
    figures: dict = {}
    generate_figures({"a": lambda: "fig_a", "b": lambda: "fig_b"}, figures)
    assert figures == {"a": "fig_a", "b": "fig_b"}


def test_generate_figures_partial_failure_leaves_prior_results_in_caller_dict():
    """Regression: a mid-generation exception must not discard figures already
    produced by earlier calls — the caller's dict (passed in, not returned) is
    the only thing ``close_figures`` can reach in ``finally``."""
    figures: dict = {}

    def _boom():
        raise RuntimeError("second plotter blew up")

    with pytest.raises(RuntimeError):
        generate_figures(
            {
                "first": lambda: "fig_first",
                "second": _boom,
                "third": lambda: "fig_third",
            },
            figures,
        )
    # "first" ran and was recorded before "second" raised; "third" never ran.
    assert figures == {"first": "fig_first"}


def test_generate_figures_partial_failure_then_close_leaves_no_open_figures():
    """End-to-end with real matplotlib figures: after a mid-generation failure,
    ``close_figures`` on the caller's dict must close everything that leaked in."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figures: dict = {}

    def _boom():
        raise RuntimeError("second plotter blew up")

    with pytest.raises(RuntimeError):
        generate_figures({"first": lambda: plt.figure(), "second": _boom}, figures)

    assert list(figures) == ["first"]
    assert plt.get_fignums() != []  # the first figure is open before cleanup
    close_figures(figures)
    assert (
        plt.get_fignums() == []
    )  # closed via the same dict generate_figures populated


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
