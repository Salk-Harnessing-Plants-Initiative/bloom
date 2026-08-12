"""Direct unit tests for the shared `_artifacts.py` helpers (#598).

`build_output_links` is shared by `SupabaseResultStore.commit()` and
`FakeResultStore.commit()`; this file pins its own contract (including the
key-scoping guard added for #598) independent of either adapter's end-to-end
test suite.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from bloom_mcp.result_store._artifacts import (
    CorruptRunLinksError,
    KeyScopeGuardError,
    build_download_links,
    build_output_links,
)

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
    with pytest.raises(KeyScopeGuardError) as exc:
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
    with pytest.raises(KeyScopeGuardError):
        _links(output_keys, url_for=url_for)
    url_for.assert_not_called()


def test_sibling_version_is_rejected_because_of_the_trailing_slash():
    """`v10` vs `v1` is the one case that actually depends on the trailing
    '/' in expected_prefix: str.startswith("bloommcp_output/qc_experiment/v1")
    (no trailing slash) would be True for a `v10/...` key too, since "v10"
    starts with "v1". Only appending '/' makes the comparison require the
    version segment to end exactly where expected_prefix says it does."""
    with pytest.raises(KeyScopeGuardError):
        _links({"cleaned": "bloommcp_output/qc_experiment/v10/_cleaned.csv"})


def test_sibling_stem_is_rejected_regardless_of_the_trailing_slash():
    """`qc_experiment2` is rejected by the prefix diverging earlier in the
    string (at "experiment" vs "experiment2", well before the "/v1" segment
    even starts) — unlike the sibling-version case above, this one would be
    rejected the same way even if expected_prefix had no trailing '/' at
    all."""
    with pytest.raises(KeyScopeGuardError):
        _links({"cleaned": "bloommcp_output/qc_experiment2/v1/_cleaned.csv"})


def test_empty_expected_prefix_is_rejected_not_treated_as_match_everything():
    """str.startswith("") is always True, so an empty expected_prefix would
    silently defeat the guard — every key would appear "in scope" and every
    existing test above would stay green even if a future refactor
    accidentally passed "" through. Guarded explicitly so that
    misconfiguration raises instead of no-op'ing."""
    with pytest.raises(KeyScopeGuardError):
        _links({"cleaned": "anything/at/all.csv"}, expected_prefix="")


def test_empty_output_keys_does_not_crash():
    assert _links({}) == {}


# ── path_for: local backend surfaces a direct path, not a URL (#642 follow-up) ──


def test_path_for_populates_path_and_leaves_url_none(tmp_path):
    output_keys = {"cleaned": f"{_PREFIX}_cleaned.csv"}
    output_sha256 = {"cleaned": "sha-cleaned"}
    output_size_bytes = {"cleaned": 9}
    resolved = tmp_path / f"{_PREFIX}_cleaned.csv"
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_bytes(b"cleaned-!")
    links = build_output_links(
        output_keys,
        output_sha256,
        output_size_bytes,
        path_for=lambda key: str(tmp_path / key),
        expected_prefix=_PREFIX,
    )
    assert links["cleaned"].path == str(resolved)
    assert links["cleaned"].url is None
    assert links["cleaned"].key == f"{_PREFIX}_cleaned.csv"
    assert links["cleaned"].sha256 == "sha-cleaned"
    assert links["cleaned"].size_bytes == 9


def test_path_for_returning_a_missing_path_raises():
    with pytest.raises(ValueError, match="non-existent path"):
        build_output_links(
            {"cleaned": f"{_PREFIX}_cleaned.csv"},
            {"cleaned": "sha-cleaned"},
            {"cleaned": 9},
            path_for=lambda key: "/does/not/exist",
            expected_prefix=_PREFIX,
        )


def test_url_for_leaves_path_none():
    links = _links({"cleaned": f"{_PREFIX}_cleaned.csv"})
    assert links["cleaned"].url == f"fake://signed/{_PREFIX}_cleaned.csv"
    assert links["cleaned"].path is None


def test_neither_url_for_nor_path_for_raises():
    with pytest.raises(ValueError, match="exactly one"):
        build_output_links(
            {"cleaned": f"{_PREFIX}_cleaned.csv"},
            {"cleaned": "sha"},
            {"cleaned": 1},
            expected_prefix=_PREFIX,
        )


def test_both_url_for_and_path_for_raises():
    with pytest.raises(ValueError, match="exactly one"):
        build_output_links(
            {"cleaned": f"{_PREFIX}_cleaned.csv"},
            {"cleaned": "sha"},
            {"cleaned": 1},
            url_for=lambda key: f"fake://signed/{key}",
            path_for=lambda key: f"/root/{key}",
            expected_prefix=_PREFIX,
        )


def test_path_for_key_scope_guard_still_applies():
    """The #598 key-scoping guard runs before either closure is called —
    confirm it still gates the path_for branch, not just url_for."""
    path_for = MagicMock(side_effect=lambda key: f"/root/{key}")
    with pytest.raises(KeyScopeGuardError):
        build_output_links(
            {"cleaned": "bloommcp_output/qc_someone_else/v1/_cleaned.csv"},
            {"cleaned": "sha"},
            {"cleaned": 1},
            path_for=path_for,
            expected_prefix=_PREFIX,
        )
    path_for.assert_not_called()


# ── build_download_links: read-path twin of build_output_links (#642 follow-up) ──


def test_download_links_url_for_populates_url_and_leaves_path_none():
    links = build_download_links(
        {"cleaned": f"{_PREFIX}_cleaned.csv"},
        {"cleaned": "sha-cleaned"},
        url_for=lambda key: f"fake://signed/{key}",
        size_for=lambda key: 9,
        expected_prefix=_PREFIX,
    )
    assert links["cleaned"].url == f"fake://signed/{_PREFIX}_cleaned.csv"
    assert links["cleaned"].path is None
    assert links["cleaned"].size_bytes == 9


def test_download_links_path_for_populates_path_and_leaves_url_none(tmp_path):
    resolved = tmp_path / f"{_PREFIX}_cleaned.csv"
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_bytes(b"cleaned-!")
    links = build_download_links(
        {"cleaned": f"{_PREFIX}_cleaned.csv"},
        {"cleaned": "sha-cleaned"},
        path_for=lambda key: str(tmp_path / key),
        size_for=lambda key: 9,
        expected_prefix=_PREFIX,
    )
    assert links["cleaned"].path == str(resolved)
    assert links["cleaned"].url is None
    assert links["cleaned"].size_bytes == 9


def test_download_links_path_for_returning_a_missing_path_raises():
    with pytest.raises(ValueError, match="non-existent path"):
        build_download_links(
            {"cleaned": f"{_PREFIX}_cleaned.csv"},
            {"cleaned": "sha-cleaned"},
            path_for=lambda key: "/does/not/exist",
            size_for=lambda key: 9,
            expected_prefix=_PREFIX,
        )


def test_download_links_neither_url_for_nor_path_for_raises():
    with pytest.raises(ValueError, match="exactly one"):
        build_download_links(
            {"cleaned": f"{_PREFIX}_cleaned.csv"},
            {"cleaned": "sha"},
            size_for=lambda key: 1,
            expected_prefix=_PREFIX,
        )


def test_download_links_both_url_for_and_path_for_raises():
    with pytest.raises(ValueError, match="exactly one"):
        build_download_links(
            {"cleaned": f"{_PREFIX}_cleaned.csv"},
            {"cleaned": "sha"},
            url_for=lambda key: f"fake://signed/{key}",
            path_for=lambda key: f"/root/{key}",
            size_for=lambda key: 1,
            expected_prefix=_PREFIX,
        )


def test_download_links_path_for_key_scope_guard_still_applies():
    path_for = MagicMock(side_effect=lambda key: f"/root/{key}")
    with pytest.raises(CorruptRunLinksError):
        build_download_links(
            {"cleaned": "bloommcp_output/qc_someone_else/v1/_cleaned.csv"},
            {"cleaned": "sha"},
            path_for=path_for,
            size_for=lambda key: 1,
            expected_prefix=_PREFIX,
        )
    path_for.assert_not_called()
