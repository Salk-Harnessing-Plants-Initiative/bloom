"""@as_mcp_tool resolves, records, and propagates the seed — never global re-seed.

Maps the spec "Seed Recording And Propagation Without Global Re-Seed"
scenarios: a provided seed is forwarded as ``random_state=`` and recorded while
the global RNG is untouched; an absent seed is resolved to a concrete integer
and recorded (never null).
"""

from __future__ import annotations

import numpy as np

from bloom_mcp.contract import as_mcp_tool

from .conftest import StubInput, StubOutput


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
    assert before[0] == after[0]
    assert np.array_equal(before[1], after[1])
    assert before[2:] == after[2:]


def test_absent_seed_resolved_to_concrete_int(recorder):
    """No seed → a concrete integer is resolved, forwarded, and recorded."""
    stub_tool = _stub_with_recorder(recorder)

    stub_tool({"experiment": "turface_19"})  # no seed

    resolved = recorder["provenance"].seed
    assert isinstance(resolved, int)
    assert recorder["delegate_random_state"] == resolved
