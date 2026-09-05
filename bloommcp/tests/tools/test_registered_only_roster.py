"""Schema-driven guard on the registered-only parameter roster (#582).

Rejecting a parameter that cannot apply to inline content is enforced per call
site: each tool hands ``resolve_inline_or_experiment`` a dict of the fields it
believes are registered-only. That works, and `qc_clean` lists every one of its
fields correctly today — but it is *manual enumeration*, and PR 2 and PR 3 add
nine more tools to the same pattern. A tool that simply forgets a field would
accept-but-ignore it, which is precisely the failure class
``_inline_input``'s own docstring says must never happen: a caller who supplied
a pin and got a successful result believing it took effect.

So instead of trusting each author to remember, this walks every inline-capable
tool's ``*Params`` model and asserts that every field known to be
registered-only is actually rejected when combined with ``csv_content``. It is
derived from the schema, so a newly-added field is covered the moment the tool
declares it — no test edit required.

PR 1 wires only ``qc_clean``; ``_INLINE_CAPABLE_TOOLS`` grows as PR 2 and PR 3
land, and the roster test grows with it automatically.
"""

from __future__ import annotations

import pytest

from bloom_mcp.contract import BloomMCPError
from bloom_mcp.data_access import FakeReader, SupabaseReader
from bloom_mcp.result_store import FakeResultStore, SupabaseResultStore
from bloom_mcp.sections.sleap_roots.analysis.qc_clean import QCCleanParams, qc_clean
from bloom_mcp.tools import _ports

_VALID_CSV = "Barcode,geno,traitA,traitB\nS1,g1,1.0,2.0\nS2,g2,3.0,4.0\nS3,g1,5.0,6.0\n"


@pytest.fixture
def injected_ports():
    """Ports seam, restored on teardown — `_ports.configure` is process-global, so
    a roster test spanning many tool modules must not leak state into them."""
    reader, store = FakeReader(), FakeResultStore()
    _ports.configure(reader=reader, store=store)
    try:
        yield reader, store
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())


# Field-name -> a value that is meaningfully "supplied" for that field. Anything
# a tool declares whose name matches one of these is registered-only by
# construction: it names stored state (a source, a committed version, a version
# directory) or configures an artifact only a persisted run can hold.
_REGISTERED_ONLY_FIELDS: dict[str, object] = {
    "source_id": 9,
    "run_id": "p9",
    "version": "v2",
    "version_1": "v2",
    "version_2": "v2",
    "user_label": "my-run",
    "include_plots": True,
    "plots": ["create_pca_biplot"],
    "plot_font_family": "serif",
    "plot_font_size": 12.0,
    "plot_alpha": 0.5,
    "plot_cmap": "viridis",
    "plot_point_size": 4.0,
}

# (tool callable, params model, kwargs that make a minimal valid inline call).
# Grows as PR 2/3 land; each entry is covered by every check below for free.
_INLINE_CAPABLE_TOOLS = [
    pytest.param(
        qc_clean,
        QCCleanParams,
        {"csv_content": _VALID_CSV, "min_samples_per_trait": 1},
        id="qc_clean",
    ),
]


def _registered_only_fields(params_model) -> list[str]:
    """The registered-only fields this tool actually declares."""
    return [f for f in params_model.model_fields if f in _REGISTERED_ONLY_FIELDS]


@pytest.mark.parametrize("tool,params_model,base_kwargs", _INLINE_CAPABLE_TOOLS)
def test_every_registered_only_field_the_tool_declares_is_rejected(
    tool, params_model, base_kwargs, injected_ports
):
    """The load-bearing assertion: derived from the model, not from a hand-written
    list, so a field added in a later PR is covered without anyone remembering."""
    declared = _registered_only_fields(params_model)
    assert declared, (
        f"{params_model.__name__} declares no registered-only fields — either the "
        f"tool genuinely has none (fine, remove it from _INLINE_CAPABLE_TOOLS) or "
        f"_REGISTERED_ONLY_FIELDS has drifted from the schema"
    )

    for field in declared:
        kwargs = {**base_kwargs, field: _REGISTERED_ONLY_FIELDS[field]}
        with pytest.raises(BloomMCPError) as exc:
            tool(params_model(**kwargs))
        assert exc.value.code == "invalid_input", field
        assert field in exc.value.message, (
            f"{field} was supplied alongside csv_content and the call failed, but "
            f"the message does not name it — a caller cannot tell what to remove"
        )


@pytest.mark.parametrize("tool,params_model,base_kwargs", _INLINE_CAPABLE_TOOLS)
def test_the_minimal_inline_call_actually_succeeds(
    tool, params_model, base_kwargs, injected_ports
):
    """Guards the guard: if the base call were itself invalid, every rejection
    above would pass for the wrong reason."""
    result = tool(params_model(**base_kwargs))
    assert result is not None


@pytest.mark.parametrize("tool,params_model,base_kwargs", _INLINE_CAPABLE_TOOLS)
def test_registered_only_fields_are_optional_so_omitting_them_is_valid(
    tool, params_model, base_kwargs
):
    """A registered-only field must never be *required*, or the inline path would
    be unreachable — you would have to supply something that is then rejected."""
    for field in _registered_only_fields(params_model):
        assert not params_model.model_fields[field].is_required(), (
            f"{field} is required on {params_model.__name__}, which makes the "
            f"csv_content path impossible to call"
        )
