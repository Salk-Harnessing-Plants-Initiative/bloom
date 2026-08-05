## 1. Storage backend: `get_object_size`

- [ ] 1.1 Confirm the pinned `storage3==2.31.0`'s `info(path)` response shape by calling it
      against the real dev stack (or a recorded fixture) — **do not assume a flat `size`
      key**. Reading `.venv/lib/python3.11/site-packages/storage3/_sync/file_api.py:376`
      shows `info()` returns an untyped `dict[str, Any]`; the client's only typed sibling
      object (`SearchV2Object` in `types.py`) nests metadata under a `metadata` key, which
      is the more likely real shape (see design.md Decision 2). Record the actual shape
      found here before writing 1.2/1.3.
- [ ] 1.2 Write `bloommcp/tests/test_storage_backend.py` tests first (red against today's code):
      - `SupabaseStorageBackend.get_object_size(key)` calls `client.info(key)` and extracts
        the byte size from a mocked response using the *actual* shape confirmed in 1.1
        (test both a flat and a `metadata`-nested response if the extraction supports
        both, per design.md's best-effort extraction).
      - A mocked response missing the resolved size field raises rather than returning a
        fabricated `0` (distinct test from: a response that is present but not parseable —
        cover both).
      - `client.info(key)` itself raising (e.g. the object was deleted from storage) SHALL
        propagate a clear, storage-layer error rather than being swallowed.
      - `LocalStorageBackend.get_object_size(key)` returns the real `Path.stat().st_size` for
        an existing file under the root, and raises the same redacted not-found error
        `download_file`/`read_json` already raise for a missing key (reuse `_resolve`).
      - `StorageBackend` Protocol: `get_object_size` is part of the `runtime_checkable`
        interface (both concrete backends satisfy `isinstance(..., StorageBackend)`).
- [ ] 1.3 Implement `get_object_size(key: str) -> int` on the `StorageBackend` Protocol,
      `SupabaseStorageBackend`, and `LocalStorageBackend` in `storage_backend.py`. While
      editing this file's module docstring and the `StorageBackend` Protocol docstring,
      also fix the stale "six helpers" count (already wrong today — #581 made it seven;
      this makes it eight). Confirm 1.2 is green.
- [ ] 1.4 Add the eighth `bloom_mcp.supabase_client.get_object_size(key)` re-export, mirroring
      `create_signed_url`'s existing seventh (lazy `active_backend()` import + delegate); fix
      the same stale "six helpers" comment in this module. Add a matching test alongside the
      other seven in this module's existing test file.
- [ ] 1.5 Add `get_object_size` to `bloommcp/tests/conftest.py`'s `_InMemoryObjectStore` (a
      real `len(bytes)` lookup against the in-memory dict it already holds — not synthesized,
      since this fixture genuinely has the bytes) and to the `fake_supabase_storage` fixture's
      monkeypatch tuple, and to `test_storage_backend.py`'s separate `_FakeSbStorageClient`
      double (#581's own rollout found this third double needed the same update, not
      anticipated at that change's proposal time — checked here from the start).
- [ ] 1.6 Add at least one test that exercises `get_object_size` through genuine backend
      dispatch (`storage_backend.active_backend()`), not only through the always-faked
      `_sc`-module-level fixtures — mirroring `test_storage_backend.py`'s existing
      `test_local_store_roundtrip_matches_contract`-style real-dispatch coverage, so the
      actual `SupabaseStorageBackend`/`LocalStorageBackend` wiring (not just the fake) is
      exercised for this new method.

## 2. `ResultStore.get_download_links`

- [ ] 2.1 Write `ports.py` tests first: a new `CorruptRunLinksError(ResultStoreError)` exists
      in the existing error hierarchy (alongside `RunNotFoundError`, etc.), with a docstring
      distinguishing it from #598/PR #609's `_artifacts.py` `KeyScopeGuardError` (write-path,
      pre-upload) — this one is read-path, post-manifest-read (see design.md Decision 3).
      Note in a code comment (not a runtime check) that PR #609 may or may not have merged by
      implementation time; this task does not read or depend on its diff. **Also add
      `CorruptRunLinksError` to `result_store/__init__.py`'s imports and `__all__`** —
      every existing error type is re-exported there, and code/tests written the
      conventional way (`from bloom_mcp.result_store import CorruptRunLinksError`) would
      `ImportError` otherwise.
- [ ] 2.2 Write `test_store_parity.py`-style tests first (red) for `get_download_links`, run
      against both adapters via the shared parametrized-test convention:
      - Resolving `"latest"` and an explicit `run_ref` (e.g. `"v1"`) both return a `StoredRun`
        whose `output_links` carries a fresh `url`/`sha256`/`size_bytes` per output, with
        `sha256` equal to the persisted `output_sha256` and `size_bytes` from a live
        `get_object_size` call (assert the call was made — this always happens now, there is
        no persisted-size fast path).
      - A run whose `output_keys` is empty (a v2-shaped legacy entry, no key ever recorded)
        returns an empty `output_links` rather than raising.
      - A retired-but-historical `tool_class` (e.g. `"stats"` — still queryable per
        `list_existing_analyses.TOOL_CLASSES`) resolves and re-signs normally; this is a
        realistic use case for this exact tool, not just a discovery-tool concern.
      - An unknown `(experiment, tool_class, run_ref)` raises `RunNotFoundError` (reuses
        `get_run`'s existing resolution and error).
      - A deliberately mismatched persisted key (outside the freshly recomputed expected
        prefix for this run) raises `CorruptRunLinksError` — for **both** the
        `create_signed_url` and `get_object_size` call paths — rather than looking it up, via
        a thin test-only injection point, mirroring how #598's own guard test exercises an
        otherwise-unreachable case.
      - A single output's live `get_object_size`/`create_signed_url` call raising (e.g. the
        object was deleted from storage since commit) aborts the whole call — asserted to
        raise, never to return a partially-populated `output_links` (design.md Decision 4).
      - `FakeResultStore.get_download_links` never calls anything on `StorageBackend` for any
        run it recorded itself — assert this directly (e.g. via a spy/mock asserting zero
        calls), not just implied by the size value matching (design.md Decision 6).
- [ ] 2.3 Implement `get_download_links(experiment, tool_class, run_ref="latest") -> StoredRun`
      on the `ResultStore` Protocol, `SupabaseResultStore`, and `FakeResultStore`: resolve via
      the same manifest lookup `get_run` uses, recompute the expected key prefix fresh from
      `(experiment, tool_class, resolved version_dir)`, guard every `output_key` against it for
      both per-key calls (`CorruptRunLinksError` on mismatch), skip entirely (empty
      `output_links`) when `output_keys` is empty, and otherwise build one `OutputLink` per
      output — `sha256` from the persisted `output_sha256`, `size_bytes` from a live
      `get_object_size` call (Supabase adapter: `storage_backend.active_backend()`; Fake
      adapter: its own private per-run size bookkeeping recorded at commit time, per design.md
      Decision 6 — no `StorageBackend` call), `url` from `create_signed_url`. Any per-output
      failure propagates immediately (no partial result). Confirm 2.2 is green.

## 3. MCP tool

- [ ] 3.1 Write `bloommcp/tests/tools/test_get_download_links_tool.py` tests first (red),
      mirroring `test_qc_tools_discovery.py`'s style for the other core tools:
      - Happy path: returns JSON with `experiment`, `tool_class`, resolved `run_ref`,
        `version_dir`, `outputs`, `output_links`.
      - Unknown experiment: same `{"error": ..., "available_experiments": ...}` shape
        `list_existing_analyses` already returns.
      - Unknown run/tool_class: `{"error": ...}` from the caught `RunNotFoundError` (no raw
        traceback).
      - A live-lookup failure (`StorageKeyNotFound`/`StorageBackendError`) surfaces as the same
        clean `{"error": ...}` JSON shape, not a raw traceback (design.md Decision 4).
      - Dispatches correctly through FastMCP by keyword (mirrors
        `test_list_existing_analyses_dispatches_through_fastmcp_by_keyword`).
- [ ] 3.2 Implement `sections/core/get_download_links.py`: `get_download_links(experiment,
      tool_class, run_ref="latest") -> str`, a thin shim over
      `_ports.store().get_download_links(...)`, reusing `list_existing_analyses.py`'s
      known-experiment check and `{"error": ...}` JSON style for
      `RunNotFoundError`/`ManifestReadError`/`ManifestIncompatibleError`/
      `CorruptRunLinksError`/`StorageKeyNotFound`/`StorageBackendError`. Confirm 3.1 is green.
- [ ] 3.3 Register it in `sections/core/__init__.py` alongside the other three core tools.
      **Deliberately do not** add it to `ALWAYS_INCLUDE_MCP_TOOLS`
      (`langchain/helpers/foundational_tools.py`) — see design.md Decision 5.
- [ ] 3.4 Add a dedicated `bloommcp-get-download-links-tool` capability requirement
      ("Tool Registration and Discovery", mirroring `add-bloommcp-qc-inspect-tool`'s
      precedent) covering `tools/list` discoverability — this proposal's first draft omitted
      a per-tool capability spec entirely, unlike every prior new-tool proposal.
- [ ] 3.5 Update `bloommcp/tests/test_sections_scaffold.py` and
      `bloommcp/tests/test_devendor_invariants.py::test_expected_tool_surface` to include
      `core_get_download_links` in the live/expected tool-name sets. **Note:**
      `test_expected_tool_surface`'s actual assertion (`live & relevant == expected`, where
      `relevant = expected | not_expected`) only catches a *lost* tool, not a newly-added but
      unlisted one — adding this tool without updating the set does NOT fail this particular
      test (verified by reading it directly), so do this for documentation/hygiene accuracy,
      not because the test would otherwise catch the omission.

## 4. Docs

- [ ] 4.1 Update `bloommcp/docs/storage-backends.md`'s "Downloading outputs: signed URLs"
      section: replace "browsing and downloading historical runs is not yet supported (tracked
      separately)" with a description of `get_download_links`, and disclose the
      same-key-immutability assumption its live `size_bytes` lookup relies on (design.md Risks).

## 5. Validation

- [ ] 5.1 `openspec validate add-bloommcp-get-download-links --strict`.
- [ ] 5.2 Full `bloommcp` unit suite green (both `SupabaseResultStore`/`FakeResultStore` paths,
      `test_store_parity.py`, `test_devendor_invariants.py`).
- [ ] 5.3 Re-confirm sequencing: check whether `add-bloommcp-signed-url-key-scoping` (#598,
      PR #609) has merged to `staging` by the time this change is implemented; if so, note in a
      code comment near `CorruptRunLinksError` that consolidating it with `KeyScopeGuardError`
      is available as a follow-up (per design.md's Decision 3 Non-Goal) rather than silently
      duplicating logic without acknowledging it.
