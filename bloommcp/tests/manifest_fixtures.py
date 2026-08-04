"""Manifest-building helpers for tests using the `local_manifest_backend` fixture.

Deliberately not named `conftest.py` (or defined inside it): `bloommcp/tests/smoke/`
has its own, unrelated `conftest.py`, and pytest's default (no-`__init__.py`) import
mode gives every same-named module one shared `sys.modules` slot across the whole
tree — a bare `from conftest import ...` from a test file outside `tests/smoke/`
would be ambiguous (and, depending on collection order, can silently resolve to the
*other* file's `conftest`). `local_manifest_backend` itself is a pytest fixture, so
it stays in `conftest.py` (fixture lookup is unaffected by this collision — it is
resolved through pytest's own plugin/fixture graph, not a plain Python import); only
these plain helper functions need an unambiguous, explicitly-imported home.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional


def write_cleaned_manifest(
    tmp_path: Path,
    stem: str,
    tool_class: str,
    version_id: str,
    created_at: str,
    content: bytes,
    *,
    tool: Optional[str] = None,
    based_on_version: Optional[str] = None,
) -> None:
    """Write a valid one-version manifest + its cleaned CSV under `<tool_class>_<stem>/`.

    `tool`/`based_on_version` default to the `qc`/`outliers` two-tool-class
    convention (`qc_clean` for `qc`, `remove_outliers` for anything else) that
    every pre-existing caller of this helper relies on; pass them explicitly to
    build a manifest with more than one authoring tool (e.g. the historical
    audit's own tests, which need a `qc`-class manifest containing entries from
    both tools).
    """
    from bloom_mcp.manifest import (
        ExperimentBlock,
        Manifest,
        VersionEntry,
        get_code_versions,
        write_manifest,
    )
    from bloom_mcp.supabase_client import upload_file

    prefix = f"bloommcp_output/{tool_class}_{stem}/"
    version_dir = f"{version_id}_2026-07-06"
    src = tmp_path / f"{tool_class}_{version_id}_seed.csv"
    src.write_bytes(content)
    upload_file(f"{prefix}{version_dir}/_cleaned.csv", src)

    resolved_tool = (
        tool
        if tool is not None
        else ("qc_clean" if tool_class == "qc" else "remove_outliers")
    )
    resolved_based_on = (
        based_on_version
        if based_on_version is not None
        else ("raw" if tool_class == "qc" else f"{version_id}_cleaned")
    )
    entry = VersionEntry(
        id=version_id,
        created_at=created_at,
        tool=resolved_tool,
        params={},
        based_on_version=resolved_based_on,
        code_versions=get_code_versions(),
        outputs={"_cleaned.csv": "_cleaned.csv"},
        version_dir=version_dir,
    )
    manifest = Manifest(
        experiment=ExperimentBlock(
            filename=f"{stem}.csv", source_path="", input_sha256=""
        ),
        versions=[entry],
        latest=version_id,
    )
    write_manifest(prefix, manifest)


def append_cleaned_version(
    tmp_path: Path,
    stem: str,
    tool_class: str,
    version_id: str,
    created_at: str,
    content: bytes,
    *,
    tool: str,
    based_on_version: str,
) -> None:
    """Append a new version to an existing `<tool_class>_<stem>/` manifest.

    Unlike `write_cleaned_manifest` (always a fresh, one-version manifest), this
    reads the existing manifest, appends one more `VersionEntry`, and advances
    `latest` — for building multi-version history within a single manifest (the
    audit script's own tests need this: a `qc`-class manifest with more than one
    authoring tool across its history).
    """
    from bloom_mcp.manifest import (
        AnalysisDir,
        VersionEntry,
        get_code_versions,
        write_manifest,
    )
    from bloom_mcp.supabase_client import upload_file

    prefix = f"bloommcp_output/{tool_class}_{stem}/"
    version_dir = f"{version_id}_2026-07-06"
    src = tmp_path / f"{tool_class}_{version_id}_seed.csv"
    src.write_bytes(content)
    upload_file(f"{prefix}{version_dir}/_cleaned.csv", src)

    manifest = AnalysisDir("bloommcp_output", f"{stem}.csv", tool_class).read_manifest()
    assert (
        manifest is not None
    ), f"append_cleaned_version requires an existing manifest for {stem!r}"
    entry = VersionEntry(
        id=version_id,
        created_at=created_at,
        tool=tool,
        params={},
        based_on_version=based_on_version,
        code_versions=get_code_versions(),
        outputs={"_cleaned.csv": "_cleaned.csv"},
        version_dir=version_dir,
    )
    manifest.versions.append(entry)
    manifest.latest = version_id
    write_manifest(prefix, manifest)


def write_invalid_schema_manifest(stem: str, tool_class: str) -> None:
    """Write a manifest.json whose schema version is newer than this code understands."""
    from bloom_mcp.supabase_client import write_json

    prefix = f"bloommcp_output/{tool_class}_{stem}/"
    write_json(f"{prefix}manifest.json", {"manifest_schema_version": 999})
