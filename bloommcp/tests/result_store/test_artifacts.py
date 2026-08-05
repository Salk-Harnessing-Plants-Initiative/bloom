"""Direct unit tests for the shared `_artifacts.py` helpers (#598).

`build_output_links` is shared by `SupabaseResultStore.commit()` and
`FakeResultStore.commit()`; this file pins its own contract (including the
key-scoping guard added for #598) independent of either adapter's end-to-end
test suite.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from bloom_mcp.result_store._artifacts import build_output_links

_PREFIX = "bloommcp_output/qc_experiment/v1/"


def _links(output_keys, url_for=None, expected_prefix=_PREFIX):
    output_sha256 = {name: f"sha-{name}" for name in output_keys}
    output_size_bytes = {name: len(name) for name in output_keys}
    return build_output_links(
        output_keys,
        output_sha256,
        output_size_bytes,
        url_for=url_for or (lambda key: f"fake://signed/{key}"),
        expected_prefix=expected_prefix,
    )


def test_in_scope_single_key_builds_a_link():
    links = _links({"cleaned": f"{_PREFIX}_cleaned.csv"})
    assert links["cleaned"].key == f"{_PREFIX}_cleaned.csv"
    assert links["cleaned"].url == f"fake://signed/{_PREFIX}_cleaned.csv"
    assert links["cleaned"].sha256 == "sha-cleaned"
    assert links["cleaned"].size_bytes == len("cleaned")


def test_multiple_in_scope_keys_all_build_links():
    """Mirrors a real multi-output commit (e.g. qc_clean's _cleaned.csv +
    cleanup_log.json) — not just a single-key case."""
    output_keys = {
        "cleaned": f"{_PREFIX}_cleaned.csv",
        "log": f"{_PREFIX}cleanup_log.json",
    }
    url_for = MagicMock(side_effect=lambda key: f"fake://signed/{key}")
    links = _links(output_keys, url_for=url_for)
    assert set(links) == {"cleaned", "log"}
    assert url_for.call_count == 2


def test_out_of_scope_key_raises_before_any_signing_call():
    url_for = MagicMock(side_effect=lambda key: f"fake://signed/{key}")
    with pytest.raises(RuntimeError) as exc:
        _links(
            {"cleaned": "bloommcp_output/qc_someone_else/v1/_cleaned.csv"},
            url_for=url_for,
        )
    assert "bloommcp_output/qc_someone_else/v1/_cleaned.csv" in str(exc.value)
    assert _PREFIX in str(exc.value)
    url_for.assert_not_called()


def test_first_out_of_scope_key_stops_before_signing_any_key():
    """Guard fires before any signing call, not partway through a multi-output
    commit — even when an earlier key in iteration order is in-scope."""
    url_for = MagicMock(side_effect=lambda key: f"fake://signed/{key}")
    output_keys = {
        "in_scope": f"{_PREFIX}a.csv",
        "out_of_scope": "bloommcp_output/qc_someone_else/v1/b.csv",
    }
    with pytest.raises(RuntimeError):
        _links(output_keys, url_for=url_for)
    url_for.assert_not_called()


@pytest.mark.parametrize(
    "confusable_key",
    [
        "bloommcp_output/qc_experiment2/v1/_cleaned.csv",  # sibling stem
        "bloommcp_output/qc_experiment/v10/_cleaned.csv",  # sibling version
    ],
)
def test_confusable_prefix_is_rejected_not_accepted(confusable_key):
    """Pins that the trailing '/' in expected_prefix is load-bearing — without
    it, a naive str.startswith would accept a sibling experiment/version."""
    with pytest.raises(RuntimeError):
        _links({"cleaned": confusable_key})


def test_empty_output_keys_does_not_crash():
    assert _links({}) == {}
