## 1. Port error types

- [x] 1.1 Add `ManifestReadError(ResultStoreError)` and `ManifestIncompatibleError(ManifestReadError)`
      to `result_store/ports.py`; export both from `result_store/__init__.py`
      (`__all__` + import)

## 2. SupabaseResultStore guards

- [x] 2.1 Guard `create_run`'s `adir.read_manifest()` call only (not the surrounding
      `next_version_id(...)` call): catch `ManifestSchemaError` first → log
      (`logger.error(..., exc_info=True)`) → raise `ManifestIncompatibleError` with a
      message reading "unsupported" (not "newer than," since `ManifestSchemaError` also
      covers a missing schema-version field) including the underlying schema-error text;
      then catch bare `Exception` → log (`logger.exception(...)`) → raise
      `ManifestReadError` with an exc-free message that does not claim the failure is
      transient (the branch also covers a corrupt/shape-invalid manifest or a permanent
      permission denial — see design.md)
- [x] 2.2 Guard `list_runs`'s `adir.list_versions()` call the same way
- [x] 2.3 Guard `get_run`'s `adir.get_version()` call the same way

## 3. FakeResultStore parity

- [x] 3.1 Add `fail_next_read(experiment, tool_class)` one-shot hook to
      `FakeResultStore`: checked at the top of `create_run`, `list_runs`, and `get_run`
      (before any other logic in each), keyed by `(experiment, tool_class)`, consumed by
      whichever of the three is called first for that key, raising `ManifestReadError`.
      The check-then-discard on the shared set is guarded by its own `threading.Lock`
      (flagged during review: without it, two threads racing the same armed key could
      both observe it set before either discarded it)

## 4. Tests

- [x] 4.1 `test_supabase_result_store.py`: force the underlying read to raise a generic
      exception at each of the three call sites by monkeypatching
      `bloom_mcp.manifest.analysis_dir.read_manifest` (the name as imported into
      `analysis_dir.py`'s own namespace — patching `bloom_mcp.manifest.manifest.read_manifest`
      is a no-op here since `AnalysisDir.read_manifest`/`list_versions`/`get_version` call
      the already-bound local reference, not the definition site); assert
      `ManifestReadError` (never the raw exception) propagates, with a message pinning
      the fixed template and never the injected exception's own text (no-leak assertion,
      mirroring `test_commit_failure_is_retryable_and_does_not_leak` — this pins the
      message-template design decision, not a dynamic redaction step, since the template
      never interpolates `{exc}` in the first place)
- [x] 4.2 `test_supabase_result_store.py`: force a `ManifestSchemaError` at each call
      site (same monkeypatch point); assert `ManifestIncompatibleError` (which `isinstance`
      also satisfies `ManifestReadError`) with a message containing the underlying
      schema-error text
- [x] 4.3 `test_store_parity.py`: parametrized `fail_next_read` scenario across
      `fake`/`supabase`, asserting both raise `ManifestReadError` for `create_run`,
      `list_runs`, and `get_run`
- [x] 4.4 `test_fake_result_store.py`: unit test for `fail_next_read`'s one-shot /
      scoped-to-`(experiment, tool_class)` behavior (arm it, call the targeted method
      once — raises; call again — succeeds; a different key is unaffected)
- [x] 4.5 Confirm `list_existing_analyses.py`'s existing `except Exception` around
      `store.list_runs` still passes unmodified (it catches by type, not message; no
      change expected, verify not assumed)
- [x] 4.6 `test_fake_result_store.py`: ordering test proving `fail_next_read` is
      consumed by whichever of `create_run`/`list_runs`/`get_run` is called *first* for
      the armed key (e.g. arm the hook, call `list_runs` first — it raises and clears;
      a subsequent `create_run` for the same key succeeds normally), and that arming one
      key never fires for a different `(experiment, tool_class)`

## 5. Validate

- [x] 5.1 `openspec validate fix-bloommcp-resultstore-manifest-guard --strict`
