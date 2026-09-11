"""Schema-driven guard on the registered-only parameter roster (#582).

A field that cannot apply to inline content declares itself registered-only in
its own schema (``json_schema_extra={REGISTERED_ONLY: True}``). Both halves read
that one marker: ``registered_only_fields`` builds the rejection from it, and
this module walks every inline-capable tool's ``*Params`` model and asserts each
marked field really is rejected when combined with ``csv_content``.

An earlier design had each tool hand ``resolve_inline_or_experiment`` a
hand-written dict. That worked and `qc_clean` listed its fields correctly, but a
tool could simply forget one and then silently accept-but-ignore it — precisely
the failure class ``_inline_input``'s own docstring says must never happen (a
caller who supplied a pin, got a successful result, and believes it took
effect). With nine more consumers due in PR 2 and PR 3, that is nine fresh
chances to forget. Marking the field is now the only step, and a newly-added
field is covered the moment the tool declares it.

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
from bloom_mcp.tools._inline_input import REGISTERED_ONLY

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


# A "supplied" value per field *type*, so a marked field can be exercised without
# anyone maintaining a parallel list of which fields are marked. Which fields are
# registered-only comes from the schema itself (see `_registered_only_fields`);
# this only answers "what is a non-default value for a field of this shape".
_SAMPLE_BY_NAME: dict[str, object] = {
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
    """The registered-only fields this tool declares, read from the schema.

    Derived from each field's own `json_schema_extra={REGISTERED_ONLY: True}`
    marker rather than from a list kept here. A tool that adds a registered-only
    field marks it once; the rejection (via `registered_only_fields`) and this
    test both follow from that single marker, so there is no second place to
    forget. Falls back to nothing if a tool marks no fields — which the first
    assertion below turns into a failure rather than a silent pass.
    """
    return [
        name
        for name, field in params_model.model_fields.items()
        if isinstance(field.json_schema_extra, dict)
        and field.json_schema_extra.get(REGISTERED_ONLY)
    ]


def _sample_value(params_model, field: str) -> object:
    """A value that counts as "supplied" for `field`."""
    if field in _SAMPLE_BY_NAME:
        return _SAMPLE_BY_NAME[field]
    annotation = params_model.model_fields[field].annotation
    for kind, value in ((str, "x"), (int, 7), (float, 1.5), (bool, True)):
        if kind.__name__ in str(annotation):
            return value
    raise AssertionError(
        f"{params_model.__name__}.{field} is marked registered-only but this "
        f"test has no sample value for it — add one to _SAMPLE_BY_NAME"
    )


@pytest.mark.parametrize("tool,params_model,base_kwargs", _INLINE_CAPABLE_TOOLS)
def test_every_registered_only_field_the_tool_declares_is_rejected(
    tool, params_model, base_kwargs, injected_ports
):
    """The load-bearing assertion: derived from the model, not from a hand-written
    list, so a field added in a later PR is covered without anyone remembering."""
    declared = _registered_only_fields(params_model)
    assert declared, (
        f"{params_model.__name__} marks no fields with "
        f"json_schema_extra={{{REGISTERED_ONLY!r}: True}} — either the tool "
        f"genuinely has none (fine, remove it from _INLINE_CAPABLE_TOOLS) or a "
        f"field that should be rejected on the inline path was never marked"
    )

    for field in declared:
        kwargs = {**base_kwargs, field: _sample_value(params_model, field)}
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


@pytest.mark.parametrize("tool,params_model,base_kwargs", _INLINE_CAPABLE_TOOLS)
def test_no_known_registered_only_field_is_left_unmarked(
    tool, params_model, base_kwargs
):
    """The other direction: a field that *should* be marked but wasn't.

    The marker drives the rejection, so an unmarked field is silently accepted
    and ignored — and the schema-derived test above cannot catch that, because it
    only checks fields the tool actually marked. This closes the loop from the
    other side using the same vocabulary `_SAMPLE_BY_NAME` already maintains:
    these names are registered-only wherever they appear, so any tool declaring
    one must have marked it.

    Belt and braces on purpose. The marker is the mechanism; this is the net that
    catches forgetting to attach it, which is the one step a tool author still
    has to remember as PR 2 and PR 3 add nine more consumers.
    """
    marked = set(_registered_only_fields(params_model))
    declared_known = {f for f in params_model.model_fields if f in _SAMPLE_BY_NAME}

    unmarked = sorted(declared_known - marked)
    assert not unmarked, (
        f"{params_model.__name__} declares {unmarked} but did not mark "
        f"{'them' if len(unmarked) > 1 else 'it'} with "
        f"json_schema_extra={{{REGISTERED_ONLY!r}: True}}, so "
        f"{'they are' if len(unmarked) > 1 else 'it is'} silently accepted and "
        f"ignored on the csv_content path"
    )
