## 1. Storage backend: `get_object_size`

- [x] 1.1 Confirm the pinned `storage3==2.31.0`'s `info(path)` response shape by calling it
      against the real dev stack (or a recorded fixture) — **do not assume a flat `size`
      key**. Reading `.venv/lib/python3.11/site-packages/storage3/_sync/file_api.py:376`
      shows `info()` returns an untyped `dict[str, Any]`; the client's only typed sibling
      object (`SearchV2Object` in `types.py`) nests metadata under a `metadata` key, which
      is the more likely real shape (see design.md Decision 2). Record the actual shape
      found here before writing 1.2/1.3.
      **Done:** no live dev stack available in this environment; implemented a best-effort
      extraction that tries `metadata.size` first, falling back to a flat `size` — matching
      design.md's decision. Documented as unverified-against-a-live-call in
      `_extract_object_size`'s docstring.
- [x] 1.2 Write `bloommcp/tests/test_storage_backend.py` tests first (red against today's code):
      - `SupabaseStorageBackend.get_object_size(key)` calls `client.info(key)` and extracts
        the byte size from a mocked response using the *actual* shape confirmed in 1.1
        (test both a flat and a `metadata`-nested response if the extraction supports
        both, per design.md's best-effort extraction).
      - A mocked response missing the resolved size field raises rather than returning a
        fabricated `0` (distinct test from: a response that is present but not parseable —
        cover both).
      - `client.info(key)` itself raising for a missing object propagates unmodified — matching
        `SupabaseStorageBackend.download_file`/`read_json`'s own existing behavior for this
        exact class (confirmed by reading them: neither wraps a missing-key failure into any
        bloommcp-defined type today). `get_object_size` does not introduce a new,
        Supabase-side not-found type that doesn't otherwise exist on this class.
      - `LocalStorageBackend.get_object_size(key)` returns the real `Path.stat().st_size` for
        an existing file under the root, and raises the same `StorageKeyNotFound`
        `download_file`/`read_json` already raise for a missing key (reuse `_resolve`).
      - `StorageBackend` Protocol: `get_object_size` is part of the `runtime_checkable`
        interface (both concrete backends satisfy `isinstance(..., StorageBackend)`).
      - `get_object_size` performs no ownership check: called with a syntactically valid key
        belonging to a different experiment/tool_class than the caller's own context, it
        succeeds and returns that object's real size (no authorization error) — matches the
        storage-backend spec's own "no ownership check" scenario, which had no task without
        this bullet.
      **Done:** 9 tests added to `test_storage_backend.py`'s new "9. Object byte size lookup"
      section (flat size, nested `metadata.size`, missing field, non-numeric, negative,
      client-raises-propagates, no-ownership-check, Local real-stat-size, Local
      `StorageKeyNotFound`), plus a Protocol-membership test and a `supabase_client`
      re-export test.
- [x] 1.3 Implement `get_object_size(key: str) -> int` on the `StorageBackend` Protocol,
      `SupabaseStorageBackend`, and `LocalStorageBackend` in `storage_backend.py`. While
      editing this file's module docstring and the `StorageBackend` Protocol docstring,
      also fix the stale "six helpers" count (already wrong today — #581 made it seven;
      this makes it eight). Confirm 1.2 is green.
      **Done:** all green. Also added a `create_signed_url` docstring to the Protocol
      (previously bare `...`), matching #581's own eventual intent.
- [x] 1.4 Add the eighth `bloom_mcp.supabase_client.get_object_size(key)` re-export, mirroring
      `create_signed_url`'s existing seventh (lazy `active_backend()` import + delegate); fix
      the same stale "six helpers" comment in this module. Add a matching test alongside the
      other seven in this module's existing test file.
      **Done.**
- [x] 1.5 Add `get_object_size` to `bloommcp/tests/conftest.py`'s `_InMemoryObjectStore` (a
      real `len(bytes)` lookup against the in-memory dict it already holds — not synthesized,
      since this fixture genuinely has the bytes) and to the `fake_supabase_storage` fixture's
      monkeypatch tuple, and to `test_storage_backend.py`'s separate `_FakeSbStorageClient`
      double (#581's own rollout found this third double needed the same update, not
      anticipated at that change's proposal time — checked here from the start).
      **Done:** all three updated; `_FakeSbStorageClient.info()` returns the realistic
      `{"metadata": {"size": ...}}` shape.
- [x] 1.6 Add at least one test that exercises `get_object_size` through genuine backend
      dispatch (`storage_backend.active_backend()`), not only through the always-faked
      `_sc`-module-level fixtures — mirroring `test_storage_backend.py`'s existing
      `test_local_store_roundtrip_matches_contract`-style real-dispatch coverage, so the
      actual `SupabaseStorageBackend`/`LocalStorageBackend` wiring (not just the fake) is
      exercised for this new method.
      **Done:** `test_get_object_size_real_dispatch_through_active_backend`.

## 2. `ResultStore.get_download_links`

- [x] 2.1 Write `ports.py` tests first: a new `CorruptRunLinksError(ResultStoreError)` exists
      in the existing error hierarchy (alongside `RunNotFoundError`, etc.), with a docstring
      distinguishing it from #598/PR #609's `_artifacts.py` `KeyScopeGuardError` (write-path,
      pre-upload) — this one is read-path, post-manifest-read (see design.md Decision 3).
      Note in a code comment (not a runtime check) that PR #609 may or may not have merged by
      implementation time; this task does not read or depend on its diff. **Also add
      `CorruptRunLinksError` to `result_store/__init__.py`'s imports and `__all__`** —
      every existing error type is re-exported there, and code/tests written the
      conventional way (`from bloom_mcp.result_store import CorruptRunLinksError`) would
      `ImportError` otherwise.
      **Done.** Added a shared `build_download_links` helper to `_artifacts.py` (mirroring
      `build_output_links`'s shape) rather than duplicating the guard/assembly logic in both
      adapters — imports `CorruptRunLinksError` from `.ports` (no import cycle: `ports.py`
      has no reverse dependency on `_artifacts.py`, confirmed by reading it).
- [x] 2.2 Write `test_store_parity.py`-style tests first (red) for `get_download_links` —
      see the (extensive) bullet list this task originally specified; confirmed against real
      code and implemented as originally planned, with two corrections found during
      implementation:
      - The "mismatched key" injection point could not literally mirror an #598 pattern (none
        exists on this unmerged-PR-609 branch) — built a new `FakeResultStore.seed_run_with_keys`
        test-only helper instead, and a direct `write_manifest` call (bypassing `commit()`,
        which can never itself produce a mismatched key) for the Supabase side.
      - The empty-`experiment`-string case resolves through `RunNotFoundError` on both
        backends (no separate tool-layer path needed for this at the `ResultStore` level).
      **Done:** 11 new test functions (some `@pytest.mark.parametrize("kind", ...)`'d over both
      adapters) added to `test_store_parity.py`, plus `get_download_links` added to the
      existing `_READ_CALL_SITES` parity dict for `test_manifest_read_failure_parity`.
- [x] 2.3 Implement `get_download_links(experiment, tool_class, run_ref="latest") -> StoredRun`
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
      **Done:** both adapters call `self.get_run(...)` internally rather than re-deriving the
      lookup — simpler than initially planned, and inherits `get_run`'s existing
      `RunNotFoundError`/`ManifestReadError` handling for free. `FakeResultStore` gained a
      `_output_sizes` registry (populated success-only, after the try/except, so a failed
      commit never leaves an orphaned entry) and a `seed_run_with_keys` test helper.

## 3. MCP tool

- [x] 3.1 Write `bloommcp/tests/tools/test_get_download_links_tool.py` tests first (red),
      mirroring `test_qc_tools_discovery.py`'s style for the other core tools.
      **Correction found during implementation:** the tool's catch list was broadened from a
      closed 6-type enumeration to `except Exception` — the live `create_signed_url`/
      `get_object_size` calls on the Supabase backend can raise whatever the underlying
      storage client raises (unlike `LocalStorageBackend`'s typed `StorageKeyNotFound`/
      `StorageBackendError`), so a closed list would be structurally incomplete; fixed in the
      spec too (`bloommcp-get-download-links-tool/spec.md`), mirroring
      `list_existing_analyses.py`'s own broad `except Exception` for the same reason.
      **Done:** 7 tests, including a parametrized one covering all 6 named types plus an
      arbitrary `RuntimeError`, an empty-experiment-string test, and the FastMCP-dispatch test.
- [x] 3.2 Implement `sections/core/get_download_links.py` as a thin shim over
      `_ports.store().get_download_links(...)`, reusing `list_existing_analyses.py`'s
      known-experiment check and `{"error": ...}` JSON style, with the broadened
      `except Exception` catch (see 3.1's correction). Confirm 3.1 is green.
      **Done.**
- [x] 3.3 Register it in `sections/core/__init__.py` alongside the other three core tools.
      **Deliberately do not** add it to `ALWAYS_INCLUDE_MCP_TOOLS`
      (`langchain/helpers/foundational_tools.py`) — see design.md Decision 5.
      **Done:** registered. The "not foundational" assertion (originally planned as an
      `is_foundational_tool()` call) instead extends
      `test_devendor_invariants.py::test_tool_name_lists_match_live_registry`'s existing
      `_parse_always_include_mcp_tools()`-based check with
      `assert "get_download_links" not in always_include` — `is_foundational_tool` itself
      lives in `langchain/`, a different service this suite cannot import directly, mirroring
      how the adjacent `"inspect_data_quality"` assertion already works the same way.
- [x] 3.4 Add a dedicated `bloommcp-get-download-links-tool` capability requirement
      ("Tool Registration and Discovery", mirroring `add-bloommcp-qc-inspect-tool`'s
      precedent) covering `tools/list` discoverability — this proposal's first draft omitted
      a per-tool capability spec entirely, unlike every prior new-tool proposal.
      **Done** (added during the review-openspec fix-up pass, before implementation started).
- [x] 3.5 Update `bloommcp/tests/test_sections_scaffold.py` and
      `bloommcp/tests/test_devendor_invariants.py::test_expected_tool_surface` to include
      `core_get_download_links` in the live/expected tool-name sets.
      **Done** for both, plus `test_persistence_import_guard.py`'s `_CONSUMERS` allowlist
      (found during implementation — not originally listed in this task, but the same kind of
      exhaustive enumeration that needed this tool added).

## 4. Docs

- [x] 4.1 Update `bloommcp/docs/storage-backends.md`'s "Downloading outputs: signed URLs"
      section per the three caller-visible facts + the same-key-immutability disclosure.
      **Done.**

## 5. Validation

- [x] 5.1 `openspec validate add-bloommcp-get-download-links --strict`. **Done — valid**,
      re-confirmed after every spec-text edit made during implementation.
- [x] 5.2 Full `bloommcp` unit suite green, plus lint. **Done:** 1040 passed, 29 skipped
      (pre-existing skips, unchanged) — up from the pre-change 1010 passed. `black`/`ruff`
      (pinned to the exact `.pre-commit-config.yaml` versions, `26.3.1`/`0.9.9`, via `uvx`
      since bare `black`/`ruff` aren't on PATH in this env) both clean on every file this
      change touches; black reformatting was scoped to only those files, not the several
      pre-existing repo-wide formatting deviations found unrelated to this change (left
      untouched, out of scope).
- [x] 5.3 Re-checked immediately before finishing: `gh pr view 609` shows `state: OPEN,
      mergedAt: null`, and `git log origin/staging -1` is still `47a94ba` — unchanged since
      this branch was cut. No rebase needed; the disclosed `storage_backend.py` textual
      overlap with PR #609 remains a future, not current, concern.
