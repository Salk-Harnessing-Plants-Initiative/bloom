"""@as_mcp_tool resolves, records, and propagates the seed — never global re-seed.

Maps the spec "Seed Recording And Propagation Without Global Re-Seed"
scenarios: a provided seed is forwarded as ``random_state=`` and recorded while
the global RNG is untouched; an absent seed is resolved to a concrete integer
and recorded (never null).
"""

from __future__ import annotations

import numpy as np
import pytest

from bloom_mcp.contract import BloomMCPError, as_mcp_tool

from .conftest import StubInput, StubOutput


def _rng_state_equal(before, after) -> bool:
    """True if two numpy global RNG states are byte-identical."""
    return (
        before[0] == after[0]
        and np.array_equal(before[1], after[1])
        and before[2:] == after[2:]
    )


def _stub_with_recorder(recorder):
    """A decorated stub that forwards random_state to a fake perform_* and
    records what the decorator injected."""

    def fake_perform(df=None, *, random_state):
        recorder["delegate_random_state"] = random_state
        return random_state

    @as_mcp_tool(input_model=StubInput, output_model=StubOutput)
    def stub_tool(
        params: StubInput, *, random_state=None, provenance=None
    ) -> StubOutput:
        fake_perform(random_state=random_state)
        recorder["provenance"] = provenance
        return StubOutput(n_components=3)

    return stub_tool


def test_provided_seed_recorded_forwarded_rng_untouched(recorder):
    """A given seed reaches the delegate + Provenance; global RNG is unchanged."""
    stub_tool = _stub_with_recorder(recorder)

    before = np.random.get_state()
    stub_tool({"experiment": "turface_19", "seed": 1234})
    after = np.random.get_state()

    assert recorder["delegate_random_state"] == 1234
    assert recorder["provenance"].seed == 1234
    # Byte-identical global RNG state: the decorator never called np.random.seed.
    assert _rng_state_equal(before, after)


def test_absent_seed_resolved_to_concrete_int(recorder):
    """No seed → a concrete integer is resolved, forwarded, recorded; RNG untouched."""
    stub_tool = _stub_with_recorder(recorder)

    before = np.random.get_state()
    stub_tool({"experiment": "turface_19"})  # no seed
    after = np.random.get_state()

    resolved = recorder["provenance"].seed
    assert isinstance(resolved, int)
    assert recorder["delegate_random_state"] == resolved
    assert _rng_state_equal(before, after)


def test_non_stochastic_tool_records_no_seed(recorder):
    """A tool whose delegate takes no random_state records seed=None."""

    @as_mcp_tool(input_model=StubInput, output_model=StubOutput)
    def stub_tool(params: StubInput, *, provenance=None) -> StubOutput:
        recorder["provenance"] = provenance
        return StubOutput(n_components=3)

    stub_tool({"experiment": "turface_19"})  # no seed, no random_state param
    assert recorder["provenance"].seed is None


def test_seed_provided_to_non_stochastic_tool_is_internal_error():
    """A seed the delegate can't apply is a wiring bug, not a silent lie."""

    @as_mcp_tool(input_model=StubInput, output_model=StubOutput)
    def stub_tool(params: StubInput, *, provenance=None) -> StubOutput:
        return StubOutput(n_components=3)

    with pytest.raises(BloomMCPError) as exc:
        stub_tool({"experiment": "turface_19", "seed": 7})
    assert exc.value.code == "internal_error"


def test_out_of_range_seed_rejected_as_invalid_input(recorder):
    """An out-of-range seed is rejected at input validation, not recorded."""
    stub_tool = _stub_with_recorder(recorder)

    with pytest.raises(BloomMCPError) as exc:
        stub_tool({"experiment": "turface_19", "seed": 2**32})  # == SEED_MAX (excl.)
    assert exc.value.code == "invalid_input"
    assert "provenance" not in recorder
