"""_ports.load_frame's source_id/run_id threading (#626).

load_frame is the legacy 4-tuple read adapter load_experiment_data (and
summarize_trait) call through — it has no way to request a version other
than the default, so a source pin must force version="raw" itself (a pin
cannot apply to a cleaned read; see design.md Decision 6) rather than
raising AmbiguousSourceSelectionError on any experiment that already has a
cleaned version.
"""

from __future__ import annotations

import pandas as pd
import pytest

from bloom_mcp.data_access import FakeReader, SupabaseReader
from bloom_mcp.result_store import FakeResultStore, SupabaseResultStore
from bloom_mcp.tools import _ports

_EXPERIMENT = "exp.csv"


def _raw() -> pd.DataFrame:
    return pd.DataFrame({"Genotype": ["g1", "g2"], "trait_x": [1.0, 2.0]})


# The multi-source test double (FakeReader + a bolted-on SourceSelectable
# surface) lives in the root tests/conftest.py as make_multi_source_fake_reader
# — it was duplicated near-verbatim across this file, test_qc_clean_tool.py,
# and test_qc_inspect_tool.py before being consolidated there.


@pytest.fixture
def multi_source_ports(make_multi_source_fake_reader):
    # resolve_when_unpinned=False: this fixture's whole point is proving an
    # UNPINNED call leaves the cleaned-version resolution alone (resolves
    # "v1_cleaned", never touching a source) -- the opposite of qc_clean/
    # qc_inspect's fixtures, which need an unpinned call to resolve "latest"
    # so their source_note advisory can populate.
    reader = make_multi_source_fake_reader([9, 10], resolve_when_unpinned=False)
    reader.add_experiment(_EXPERIMENT, _raw())
    # A committed cleaned version exists — the case that would spuriously
    # raise AmbiguousSourceSelectionError without the raw-tier forcing.
    reader.add_cleaned_version(_EXPERIMENT, "v1", _raw())
    store = FakeResultStore()
    _ports.configure(reader=reader, store=store)
    try:
        yield reader, store
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())


def test_load_frame_accepts_source_id_and_run_id_kwargs():
    """Before this change, load_frame(filename, source_id=...) is a TypeError
    (only `filename` is accepted)."""
    import inspect

    sig = inspect.signature(_ports.load_frame)
    assert "source_id" in sig.parameters
    assert "run_id" in sig.parameters


def test_omitting_both_preserves_todays_default_resolution(multi_source_ports):
    """No pin given -> resolves the cleaned version (today's version="latest"
    default), not forced to raw."""
    df, trait_cols, config, source = _ports.load_frame(_EXPERIMENT)
    assert df is not None
    assert source == "v1_cleaned"


def test_a_source_pin_forces_the_raw_tier_even_with_a_cleaned_version(
    multi_source_ports,
):
    """Decision 6: a pin only ever means anything against the raw tier, and
    this seam has no other way to request it — so a pin must force
    version="raw" itself, rather than colliding with the cleaned-version
    resolution."""
    df, trait_cols, config, source = _ports.load_frame(_EXPERIMENT, source_id=9)
    assert df is not None
    assert source == "raw"


def test_both_source_id_and_run_id_returns_the_ambiguous_error_string(
    multi_source_ports,
):
    df, trait_cols, config, source = _ports.load_frame(
        _EXPERIMENT, source_id=9, run_id="p10"
    )
    assert df is None
    assert "source_id" in source.lower() or "run_id" in source.lower()


def test_source_pinning_unsupported_on_a_reader_with_no_source_concept():
    reader = FakeReader()
    reader.add_experiment(_EXPERIMENT, _raw())
    _ports.configure(reader=reader)
    try:
        df, trait_cols, config, source = _ports.load_frame(_EXPERIMENT, source_id=7)
        assert df is None
    finally:
        _ports.configure(reader=SupabaseReader())
