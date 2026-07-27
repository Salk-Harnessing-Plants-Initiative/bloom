"""Tests for the phenotyping_segmentation demo tools (min / median / mode).

Covers the shared ``_demo_stats`` I/O helper (number parsing, input resolution,
result-file writing, structured errors) and each tool's happy path + error
propagation through the ``@as_mcp_tool`` contract. Runs with no live Supabase
(see tests/conftest.py); the input root (``resolve_experiment_local_root``) and
output root (``OUTPUT_DIR``) are redirected to a tmp dir per test so nothing
touches the real data directories.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bloom_mcp.contract import BloomMCPError
from bloom_mcp.sections.phenotyping_segmentation import (
    _demo_stats,
    compute_median,
    compute_min,
    compute_mode,
)

# Each tool + the result-model attribute that carries its computed value.
SCALAR_TOOLS = {
    "min": (compute_min.compute_min, "minimum"),
    "median": (compute_median.compute_median, "median"),
}
ALL_TOOL_FNS = {
    "min": compute_min.compute_min,
    "median": compute_median.compute_median,
    "mode": compute_mode.compute_mode,
}


@pytest.fixture
def demo_dirs(tmp_path, monkeypatch):
    """Point the helper's input/output dirs at an isolated tmp location."""
    traits = tmp_path / "traits"
    traits.mkdir()
    output = tmp_path / "out"
    results = output / "results"
    monkeypatch.setattr(_demo_stats, "resolve_experiment_local_root", lambda: traits)
    monkeypatch.setattr(_demo_stats, "OUTPUT_DIR", output)
    return traits, results


def _write(traits: Path, name: str, text: str) -> str:
    (traits / name).write_text(text)
    return name


# ── _demo_stats.read_numbers: parsing ────────────────────────────────────────


@pytest.mark.parametrize(
    "text, expected",
    [
        ("1 2 3", [1.0, 2.0, 3.0]),
        ("1\n2\n3\n", [1.0, 2.0, 3.0]),
        ("1\t2  3\n4", [1.0, 2.0, 3.0, 4.0]),
        ("1.5 2.5 3.0", [1.5, 2.5, 3.0]),
        ("-4 -1 0 2", [-4.0, -1.0, 0.0, 2.0]),
        ("42", [42.0]),
        ("  7   ", [7.0]),
        ("1e3 2.5e-1", [1000.0, 0.25]),
    ],
)
def test_read_numbers_parses_whitespace_separated_values(demo_dirs, text, expected):
    traits, _ = demo_dirs
    name = _write(traits, "nums.txt", text)
    assert _demo_stats.read_numbers(name) == expected


def test_read_numbers_resolves_relative_against_traits_dir(demo_dirs):
    traits, _ = demo_dirs
    _write(traits, "rel.txt", "5 10 15")
    assert _demo_stats.read_numbers("rel.txt") == [5.0, 10.0, 15.0]


def test_read_numbers_accepts_absolute_path(demo_dirs, tmp_path):
    abs_file = tmp_path / "elsewhere.txt"
    abs_file.write_text("8 9")
    assert _demo_stats.read_numbers(str(abs_file)) == [8.0, 9.0]


# ── _demo_stats.read_numbers: errors ─────────────────────────────────────────


def test_read_numbers_missing_file_raises_invalid_input(demo_dirs):
    with pytest.raises(BloomMCPError) as exc:
        _demo_stats.read_numbers("nope.txt")
    assert exc.value.code == "invalid_input"
    assert "not found" in exc.value.message.lower()


def test_read_numbers_non_numeric_raises_invalid_input(demo_dirs):
    traits, _ = demo_dirs
    _write(traits, "bad.txt", "1 2 banana 4")
    with pytest.raises(BloomMCPError) as exc:
        _demo_stats.read_numbers("bad.txt")
    assert exc.value.code == "invalid_input"
    assert "banana" in exc.value.message


@pytest.mark.parametrize("text", ["", "   ", "\n\n", "\t "])
def test_read_numbers_empty_raises_invalid_input(demo_dirs, text):
    traits, _ = demo_dirs
    _write(traits, "empty.txt", text)
    with pytest.raises(BloomMCPError) as exc:
        _demo_stats.read_numbers("empty.txt")
    assert exc.value.code == "invalid_input"


def test_read_numbers_error_carries_remedy(demo_dirs):
    with pytest.raises(BloomMCPError) as exc:
        _demo_stats.read_numbers("missing.txt")
    assert exc.value.remedy  # non-empty, actionable


# ── _demo_stats.write_result ─────────────────────────────────────────────────


def test_write_result_creates_results_dir_and_file(demo_dirs):
    _, results = demo_dirs
    assert not results.exists()
    out = _demo_stats.write_result("min", "sample.txt", "3.0")
    assert results.exists()
    assert Path(out) == results / "min_sample.txt"


def test_write_result_content_format(demo_dirs):
    out = _demo_stats.write_result("median", "sample.txt", "7.5")
    assert Path(out).read_text() == "median(sample.txt) = 7.5\n"


def test_write_result_names_file_by_stem_not_full_name(demo_dirs):
    _, results = demo_dirs
    out = _demo_stats.write_result("mode", "deep/path/exp.txt", "5.0")
    assert Path(out).name == "mode_exp.txt"


# ── compute_min / compute_median: happy path ─────────────────────────────────


@pytest.mark.parametrize(
    "stat, text, expected",
    [
        ("min", "3 1 4 1 5", 1.0),
        ("min", "-2 0 7", -2.0),
        ("min", "42", 42.0),
        ("median", "1 2 3", 2.0),
        ("median", "1 2 3 4", 2.5),  # even count -> mean of middle two
        ("median", "10", 10.0),
        ("median", "5 1 3", 3.0),  # unsorted input
    ],
)
def test_scalar_tool_values(demo_dirs, stat, text, expected):
    traits, _ = demo_dirs
    fn, attr = SCALAR_TOOLS[stat]
    name = _write(traits, "x.txt", text)
    result = fn({"filename": name})
    assert getattr(result, attr) == expected


@pytest.mark.parametrize("stat", ["min", "median"])
def test_scalar_tool_reports_count_and_source(demo_dirs, stat):
    traits, _ = demo_dirs
    fn, _attr = SCALAR_TOOLS[stat]
    name = _write(traits, "count.txt", "1 2 3 4 5 6")
    result = fn({"filename": name})
    assert result.n == 6
    assert result.source_file == name


@pytest.mark.parametrize("stat", ["min", "median"])
def test_scalar_tool_writes_result_file(demo_dirs, stat):
    traits, results = demo_dirs
    fn, attr = SCALAR_TOOLS[stat]
    name = _write(traits, "y.txt", "2 4 6")
    result = fn({"filename": name})
    out = Path(result.result_path)
    assert out == results / f"{stat}_y.txt"
    assert out.read_text() == f"{stat}(y.txt) = {getattr(result, attr)}\n"


# ── compute_mode: happy path (incl. ties) ────────────────────────────────────


def test_mode_single_most_frequent(demo_dirs):
    traits, _ = demo_dirs
    name = _write(traits, "m.txt", "5 5 5 1 2 3")
    result = compute_mode.compute_mode({"filename": name})
    assert result.modes == [5.0]
    assert result.n == 6


def test_mode_tie_returns_all_modes_in_first_seen_order(demo_dirs):
    traits, _ = demo_dirs
    name = _write(traits, "tie.txt", "3 1 3 1 2")
    result = compute_mode.compute_mode({"filename": name})
    assert result.modes == [3.0, 1.0]


def test_mode_all_unique_returns_every_value(demo_dirs):
    traits, _ = demo_dirs
    name = _write(traits, "uniq.txt", "1 2 3")
    result = compute_mode.compute_mode({"filename": name})
    assert result.modes == [1.0, 2.0, 3.0]


def test_mode_writes_comma_joined_result_file(demo_dirs):
    traits, results = demo_dirs
    name = _write(traits, "mm.txt", "7 7 4 4")
    result = compute_mode.compute_mode({"filename": name})
    out = Path(result.result_path)
    assert out == results / "mode_mm.txt"
    assert out.read_text() == "mode(mm.txt) = 7.0, 4.0\n"


# ── contract behaviours shared by all three tools ────────────────────────────


@pytest.mark.parametrize("name", ["min", "median", "mode"])
def test_tool_accepts_model_instance_and_dict(demo_dirs, name):
    traits, _ = demo_dirs
    fn = ALL_TOOL_FNS[name]
    fname = _write(traits, "both.txt", "1 2 3 4")
    by_dict = fn({"filename": fname})
    by_kw = fn(params={"filename": fname})
    assert by_dict.n == by_kw.n == 4


@pytest.mark.parametrize("name", ["min", "median", "mode"])
def test_tool_missing_file_raises_invalid_input(demo_dirs, name):
    fn = ALL_TOOL_FNS[name]
    with pytest.raises(BloomMCPError) as exc:
        fn({"filename": "does_not_exist.txt"})
    assert exc.value.code == "invalid_input"


@pytest.mark.parametrize("name", ["min", "median", "mode"])
def test_tool_non_numeric_raises_invalid_input(demo_dirs, name):
    traits, _ = demo_dirs
    fn = ALL_TOOL_FNS[name]
    fname = _write(traits, "bad.txt", "1 2 oops")
    with pytest.raises(BloomMCPError) as exc:
        fn({"filename": fname})
    assert exc.value.code == "invalid_input"


@pytest.mark.parametrize("name", ["min", "median", "mode"])
def test_tool_empty_file_raises_invalid_input(demo_dirs, name):
    traits, _ = demo_dirs
    fn = ALL_TOOL_FNS[name]
    fname = _write(traits, "empty.txt", "   \n  ")
    with pytest.raises(BloomMCPError) as exc:
        fn({"filename": fname})
    assert exc.value.code == "invalid_input"


@pytest.mark.parametrize("name", ["min", "median", "mode"])
def test_tool_missing_required_filename_is_rejected(demo_dirs, name):
    """Empty params violate the input model -> contract raises before the body."""
    fn = ALL_TOOL_FNS[name]
    with pytest.raises(BloomMCPError) as exc:
        fn({})
    assert exc.value.code == "invalid_input"


@pytest.mark.parametrize("name", ["min", "median", "mode"])
def test_tool_registered_on_section(name):
    """Each demo tool is registered on the section server under its own name."""
    import asyncio

    from bloom_mcp.sections import phenotyping_segmentation as ps

    tools = {t.name for t in asyncio.run(ps.section.list_tools())}
    assert f"compute_{name}" in tools


# ── BLOOM_LOCAL_ROOT-only mode (#479 regression) ─────────────────────────────
#
# BLOOM_TRAITS_DIR / BLOOM_OUTPUT_DIR can now be entirely unset when
# BLOOM_STORAGE_BACKEND=local and BLOOM_LOCAL_ROOT is set — these tools must
# not silently read/write relative to the process CWD in that combination.


def test_read_numbers_honors_local_root_only_mode(tmp_path, monkeypatch):
    import bloom_mcp.experiment_utils as eu
    import bloom_mcp.storage_backend as sb

    root = tmp_path / "local_root"
    (root / "input").mkdir(parents=True)
    monkeypatch.delenv("BLOOM_EXPERIMENT_LOCAL_ROOT", raising=False)
    monkeypatch.setattr(eu, "TRAITS_DIR", Path("/should-not-be-used"))
    monkeypatch.setenv("BLOOM_STORAGE_BACKEND", "local")
    monkeypatch.setenv("BLOOM_LOCAL_ROOT", str(root))
    sb.reset_backend_for_tests()

    (root / "input" / "nums.txt").write_text("1 2 3")
    assert _demo_stats.read_numbers("nums.txt") == [1.0, 2.0, 3.0]


def test_write_result_honors_local_root_only_mode(tmp_path, monkeypatch):
    import bloom_mcp.storage_backend as sb

    root = tmp_path / "local_root"
    root.mkdir()
    monkeypatch.delenv("BLOOM_OUTPUT_DIR", raising=False)
    monkeypatch.setattr(_demo_stats, "OUTPUT_DIR", Path("/should-not-be-used"))
    monkeypatch.setenv("BLOOM_STORAGE_BACKEND", "local")
    monkeypatch.setenv("BLOOM_LOCAL_ROOT", str(root))
    sb.reset_backend_for_tests()

    out = _demo_stats.write_result("min", "sample.txt", "3.0")
    assert Path(out) == root / "output" / "results" / "min_sample.txt"
    assert Path(out).read_text() == "min(sample.txt) = 3.0\n"
