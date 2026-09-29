"""Cross-tool guards for the #748 sample-size disclosure.

These pin properties that must hold for **both** trait-plot tools, which is why they live here
rather than in either tool's own file: round 3 added them for `plot_trait_boxplots` only, and
round 4 found `PlotTraitHistogramsResult.low_sample_traits` uncovered as a result.

The pattern they exist to catch has now recurred in three consecutive review rounds: a fix lands
in the code and a field description keeps asserting the old behavior. A description is the
surface a downstream agent reading the tool schema trusts, so a stale one is a defect rather
than a typo — and prose cannot be type-checked, so the two things that have actually drifted
(a named `params` key, and a documented sort order) are pinned mechanically instead.
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd
import pytest

from bloom_mcp.data_access import FakeReader, SupabaseReader
from bloom_mcp.result_store import FakeResultStore, SupabaseResultStore
from bloom_mcp.tools import _ports
from bloom_mcp.sections.sleap_roots.analysis.plot_trait_boxplots import (
    PlotTraitBoxplotsParams,
    PlotTraitBoxplotsResult,
    plot_trait_boxplots,
)
from bloom_mcp.sections.sleap_roots.analysis.plot_trait_histograms import (
    PlotTraitHistogramsParams,
    PlotTraitHistogramsResult,
    plot_trait_histograms,
)

_EXPERIMENT = "invariants.csv"


def _frame() -> pd.DataFrame:
    """One frame that populates every ordered bucket in both tools.

    Counts are deliberately ANTI-correlated with name order (G0 gets the most, G3 the fewest)
    so that a bucket sorted by name instead of by count fails. Round 3's version had them
    aligned, which let a name-sorted small bucket pass.
    """
    rows: dict[str, list] = {"geno": []}
    n_geno, n_rows = 4, 8
    for key in ("t_small", "t_inf", "b_absent", "a_absent"):
        rows[key] = []
    for i in range(n_geno):
        rows["geno"].extend([f"G{i}"] * n_rows)
        # small bucket: G0 -> 4 finite, G3 -> 1. Descending with genotype order.
        n_small = 4 - i
        rows["t_small"].extend(
            [float(j) for j in range(n_small)] + [np.nan] * (n_rows - n_small)
        )
        # non-finite bucket: G0 -> 1 inf, G3 -> 4. Ascending with genotype order, so a
        # descending-by-count sort must reverse it.
        n_inf = i + 1
        rows["t_inf"].extend(
            [np.inf] * n_inf + [float(j) for j in range(n_rows - n_inf)]
        )
        # two absent traits, named so that insertion order (b_ before a_) differs from
        # sorted order -- round 3's single already-sorted trait could not detect that.
        for trait in ("b_absent", "a_absent"):
            rows[trait].extend(
                [np.nan] * n_rows if i < 2 else [float(j) for j in range(n_rows)]
            )
    return pd.DataFrame(rows)


@pytest.fixture
def ports():
    reader = FakeReader()
    reader.add_experiment(_EXPERIMENT, _frame())
    store = FakeResultStore()
    _ports.configure(reader=reader, store=store)
    try:
        yield reader, store
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())


_TOOLS = [
    pytest.param(
        PlotTraitBoxplotsResult,
        lambda: plot_trait_boxplots(PlotTraitBoxplotsParams(experiment=_EXPERIMENT)),
        "trait_boxplots",
        id="boxplots",
    ),
    pytest.param(
        PlotTraitHistogramsResult,
        # Excludes t_inf: plot_trait_histograms refuses a selection containing +/-inf
        # outright (its delegate cannot bin a non-finite range), so the shared frame's
        # non-finite trait is deselected rather than the frame being weakened for both tools.
        lambda: plot_trait_histograms(
            PlotTraitHistogramsParams(
                experiment=_EXPERIMENT,
                trait_columns=["t_small", "b_absent", "a_absent"],
            )
        ),
        "trait_histograms",
        id="histograms",
    ),
]


def _descriptions(model) -> dict[str, str]:
    return {n: (f.description or "") for n, f in model.model_fields.items()}


@pytest.mark.parametrize("model,run,tool_class", _TOOLS)
def test_no_field_description_names_a_literal_params_key_that_is_not_stamped(
    ports, model, run, tool_class
):
    """Every `params["..."]` a description points a reader at must really be stamped.

    Scope, stated rather than implied: this matches only the LITERAL `params["x"]` form. Prose
    like "stamped into the persisted run's params" -- which is how `sample_size_note` is now
    worded -- is not checked, because there is no key in it to check. The narrow form is the
    one that actually drifted (round 3: a description pointing at a
    `params["page_sample_size_notes"]` that round 2 had stopped writing).
    """
    _reader, store = ports
    run()
    params = store.get_run(_EXPERIMENT, tool_class, "latest").params

    missing = [
        (field, m.group(1))
        for field, description in _descriptions(model).items()
        for m in re.finditer(r"""params\[['"](\w+)['"]\]""", description)
        if m.group(1) not in params
    ]
    assert not missing, f"descriptions name params keys never stamped: {missing}"


@pytest.mark.parametrize("model,run,tool_class", _TOOLS)
def test_capped_list_descriptions_state_the_sort_order_the_code_uses(
    ports, model, run, tool_class
):
    """Pins each capped list's documented ordering against its observed ordering."""
    result = run()
    descriptions = _descriptions(model)

    if tool_class == "trait_histograms":
        counts = [t.n_plotted for t in result.low_sample_traits]
        names = [t.trait for t in result.low_sample_traits]
        assert counts == sorted(counts), counts
        # Ties must break by name, and the fixture supplies ties at 0.
        assert names == [n for _, n in sorted(zip(counts, names))]
        assert "ASCENDING by count then trait name" in descriptions["low_sample_traits"]
        return

    small = [g.n for g in result.small_sample_groups]
    small_names = [(g.trait, g.genotype) for g in result.small_sample_groups]
    assert small == sorted(small), small
    # The fixture's counts descend with genotype order, so a name-sorted bucket cannot pass.
    assert small_names != sorted(
        small_names
    ), "fixture no longer distinguishes the two sorts"
    assert "ASCENDING by count" in descriptions["small_sample_groups"]

    inf = [g.n_non_finite for g in result.non_finite_groups]
    assert inf == sorted(inf, reverse=True), inf
    assert "DESCENDING by n_non_finite" in descriptions["non_finite_groups"]

    absent = [(g.trait, g.genotype) for g in result.absent_genotype_groups]
    assert absent == sorted(absent), absent
    # Two absent traits inserted out of order, so sorted() is a real constraint here.
    assert len({t for t, _ in absent}) == 2, absent
    assert "Ordered by (trait, genotype)" in descriptions["absent_genotype_groups"]
