"""Guard for `ports.py`'s `TYPE_CHECKING`-only import.

`StoredRun.from_version_entry`'s `entry: "VersionEntry"` annotation is a
forward-ref string — Python never evaluates it at runtime, so a stale
`bloom_mcp.manifest.schema` path there would be invisible to every test that
merely imports and uses `ports.py` normally, and this repo has no mypy/pyright
CI gate to catch it statically either. This test imports the real class
(outside `TYPE_CHECKING`) and exercises `from_version_entry` with an actual
instance, so a broken annotation path fails a real assertion instead of
silently rotting.
"""

from __future__ import annotations

from bloom_mcp.manifest.schema import CodeVersions, VersionEntry
from bloom_mcp.result_store.ports import StoredRun


def test_stored_run_from_version_entry_accepts_the_real_version_entry_type():
    entry = VersionEntry(
        id="v1",
        created_at="2026-01-01T00:00:00Z",
        tool="qc_clean",
        params={},
        based_on_version="raw",
        code_versions=CodeVersions(bloommcp="0.1.0"),
        outputs={"cleaned": "_cleaned.csv"},
    )

    run = StoredRun.from_version_entry(
        entry,
        tool_class="qc",
        experiment="foo.csv",
        manifest_path="bloommcp_output/qc_foo/manifest.json",
    )

    assert run.run_ref == "v1"
    assert run.tool == "qc_clean"
    assert run.outputs == {"cleaned": "_cleaned.csv"}
    # bloom#581: from_version_entry (used by get_run/list_runs) never signs —
    # output_links defaults to empty regardless of what the entry carries.
    assert run.output_links == {}
