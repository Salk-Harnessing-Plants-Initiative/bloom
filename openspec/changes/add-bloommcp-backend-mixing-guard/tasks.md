## 1. Schema: `storage_backend` field

- [x] 1.1 Write the schema test first (extend `bloommcp/tests/contract/test_v2_backcompat.py`
      or add a sibling test): a `Manifest` round-trips `storage_backend`
      through `model_dump`/`model_validate`; a manifest dict without the
      field (simulating a pre-existing v2/v3/v4 manifest, per the checked-in
      `manifest_v2.json` fixture's shape) still validates with
      `storage_backend is None`. Confirm it fails against current
      `schema.py` (red).
- [x] 1.2 Add `storage_backend: Optional[str] = None` to `Manifest` in
      `bloommcp/src/bloom_mcp/manifest/schema.py`. Bump
      `CURRENT_SCHEMA_VERSION` from `4` to `5` and extend the module
      docstring with a "Schema version 5 is an additive bump over v4: it
      adds an optional `storage_backend` field to `Manifest`..." paragraph,
      matching the v3→v4 precedent (`source_id`/`source_name`) — the more
      recent and now-established convention of bumping the version number
      for every additive field, superseding the older no-bump
      `input_validation` (#403) precedent. Confirm 1.1 passes (green).

## 2. Sentinel stamping in `write_manifest`

- [x] 2.1 Write the test first: `write_manifest` stamps the currently active
      backend — assert both the default (`supabase`, env unset) and
      `BLOOM_STORAGE_BACKEND=local` cases via monkeypatch (reset the cached
      backend selection between cases the same way
      `test_local_store_roundtrip_matches_contract` in
      `bloommcp/tests/test_storage_backend.py` does via
      `sb.reset_backend_for_tests()`). Confirm it fails (red).
- [x] 2.2 Confirm no import cycle between `manifest.py` and
      `storage_backend.py` (both already lazy-import each other's sibling
      module in places — trace the actual import statements before adding a
      new module-level import). Confirmed: `storage_backend.py` has no
      top-level imports from `bloom_mcp`, so `manifest.py` importing
      `selected_backend_name` at module level introduces no cycle.
- [x] 2.3 In `bloommcp/src/bloom_mcp/manifest/manifest.py`, have
      `write_manifest` set `storage_backend` immediately before
      `validate_schema`/serialization, so every writer stamps correctly
      without per-call-site duty. Confirm 2.1 passes (green). (Revised in
      §7 below to derive the name from `active_backend()` rather than an
      independent env re-read, and to stamp a copy rather than mutate the
      caller's `Manifest` in place.)

## 3. Fresh-catalog log line in `SupabaseResultStore.commit`

- [x] 3.1 Write the tests first, via `caplog.at_level(logging.INFO)`
      (mirroring the existing `caplog` pattern in
      `bloommcp/tests/result_store/test_supabase_result_store.py`):
      (a) an INFO record is logged on the first commit for a fresh
      (experiment, tool_class) pair, and its message contains the
      experiment name, tool class, and active backend name — assert on the
      specific message content, not just "a record exists";
      (b) on a second commit against the same, now-existing manifest, no
      record contains that same fresh-catalog message substring — scope
      the negative assertion to the message content, not to "zero
      log records at this level," since `commit`'s cleanup-failure path
      (`supabase_store.py`) already logs its own unrelated `WARNING` in
      other tests and a blanket absence check would be fragile against
      future unrelated log lines.
      Confirm both fail against current `supabase_store.py` (red).
- [x] 3.2 In `SupabaseResultStore.commit`, in the `if fresh is None:`
      branch, add the `logger.info` call (not `logger.warning` — see
      design.md's Decisions for why: this fires on every brand-new
      experiment's first commit, the common non-mixing case, and
      `warning`-level would page on-call in any environment alerting on
      WARNING-and-above). Confirm 3.1 passes (green).

## 4. End-to-end parity check

- [x] 4.1 Add a test that commits the same run through both `supabase` and
      `local` and asserts the two serialized manifests are byte-identical
      **except** for `storage_backend`, which correctly differs
      (`"supabase"` vs `"local"`) — the exact claim the MODIFIED spec
      scenario makes, exercised through the real
      `SupabaseResultStore.create_run`/`commit`/`write_manifest` path (not a
      hand-built dict). Implemented via a `_FakeSbStorageClient` monkeypatched
      onto `get_storage_client` (not the `fake_supabase_storage` fixture,
      which monkeypatches `bloom_mcp.manifest.manifest`'s module-level
      helpers directly and so bypasses `storage_backend.active_backend()`
      dispatch — that would make `BLOOM_STORAGE_BACKEND` toggling within one
      test a no-op). One shared `Provenance` instance across both commits
      keeps `created_at`/`code_versions`/`environment` genuinely identical,
      not coincidentally so.

## 5. Regression check

- [x] 5.1 Existing suites stay green unmodified:
      `bloommcp/tests/result_store/test_store_parity.py`,
      `bloommcp/tests/result_store/test_supabase_result_store.py`,
      `bloommcp/tests/result_store/test_fake_result_store.py`,
      `bloommcp/tests/contract/test_v2_backcompat.py`,
      `bloommcp/tests/contract/test_schema_v3.py`,
      `bloommcp/tests/test_storage_backend.py`.
      (`test_workflow_persistence.py` no longer exists — confirmed removed in
      a prior change; not part of this check.) Two of the above needed
      version-number updates that are a normal consequence of the schema
      bump, not a regression: `test_schema_v3.py`'s
      `test_current_schema_version_is_4` → `_is_5`, and
      `test_v2_backcompat.py`'s `test_newer_schema_version_is_rejected`
      (hardcoded `5` → `6`, since `5` is now the known/current version); a
      new `test_recorded_v4_manifest_reads_under_v5` test was added
      alongside the existing v2→v3 and v3→v4 back-compat tests. Full
      non-integration suite run: 853 passed, 0 failed.
- [x] 5.2 Confirmed `test_store_parity.py`'s fake/Supabase parametrization
      needs no `storage_backend` assertion of its own, since
      `FakeResultStore` never constructs a real `Manifest` and is explicitly
      out of scope (design.md).

## 6. Docs

- [x] 6.1 Updated `bloommcp/docs/storage-backends.md`'s "⚠️ Do not mix
      backends for one experiment" section to describe the
      `storage_backend` sentinel field and the fresh-catalog INFO log line,
      including the known residual gap (flip A → B → A logs nothing on the
      return to A, since A's own manifest already exists — see design.md's
      Risks section) — while keeping the existing caution that mixing is
      still not automatically prevented or cross-checked.

## 7. Review-response fixes (5-subagent PR review on #572)

- [x] 7.1 **Blocking**: `bloommcp/tests/smoke/live_persistence_smoke.py`
      hardcodes `schema_version == 4` in 6 `Check(...)` assertions (a
      dev-stack-smoke CI job runs this for real) — missed by the original
      OpenSpec-review sweep, which only reached `test_schema_v3.py`/
      `test_v2_backcompat.py`. Bumped all 6 to `== 5`, plus the module's
      prose (`schema v4` / `v4 manifest` / `v4-provenance` → `v5`).
      `bloommcp/tests/scripts/test_live_persistence_smoke_logic.py` (the
      unit-level suite exercising this driver's pure logic with no live
      stack) needed the same bump in its "valid" fixture builders and two
      exact-label assertions, plus renaming `..._v4_entry` tests to `_v5_`.
- [x] 7.2 Fixed `bloommcp/docs/local-validation.md`'s 4 stale
      `manifest_schema_version == 3` references (already stale before this
      PR, fixed while in the area per reviewer's side note).
- [x] 7.3 **Important**: `selected_backend_name()` independently re-reads
      env on every call, unvalidated, while `active_backend()` builds once
      and memoizes — a durable trust anchor (the #395 sentinel) should not
      be independently derived. Added `active_backend_name()` to
      `storage_backend.py`, derived from the memoized backend object's own
      type (so it can never disagree with what's actually doing I/O) and
      validated (raises on an unrecognized value, same as any other storage
      call). `write_manifest` and the `commit` log line now use it instead
      of `selected_backend_name()`.
- [x] 7.4 **Important**: added `test_repeated_backend_flip_logs_once_not_on_return`
      (supabase → local → supabase, asserting the fresh-catalog log fires on
      the first two legs but not the return trip) — the spec's own
      "Repeated backend flips do not repeatedly signal" scenario had no test
      exercising the actual flip sequence with `caplog` before this.
- [x] 7.5 **Important**: filed #573 (qc_clean/pca_analysis `latest`
      resolution can silently pick a stale/wrong-backend result — the
      consumer-facing consequence design.md names but this PR doesn't close)
      and #574 (surface `storage_backend` in tool-facing provenance output,
      not just `manifest.json` + server logs) so both risks are tracked
      outside a design doc that gets archived on merge. Cross-referenced
      from design.md's Risks section.
- [x] 7.6 Suggestions: `write_manifest` now stamps a `model_copy` instead of
      mutating the caller's `Manifest` in place; the byte-identical parity
      test now also compares the raw serialized bytes each store actually
      received (not just a re-derived `model_dump()`); fixed design.md's
      triage-comment date (2026-07-29 → the actual 2026-07-30).
- [x] 7.7 Full non-integration suite green after all of the above: 854
      passed, 0 failed. `ruff check` / `ruff format --check` / `black
      --check` clean on every touched file.
