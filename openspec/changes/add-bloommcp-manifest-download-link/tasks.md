## 1. `ResultStore.get_download_links`: sign the manifest too

- [x] 1.1 Write `test_store_parity.py`-style tests first (red against today's code, which has
      no `manifest_url` field at all — expect `AttributeError`):
      - `get_download_links` returns a non-empty `manifest_url` for a run with populated
        `output_keys`, on both `SupabaseResultStore` and `FakeResultStore`.
      - `get_run`/`list_runs` continue to return `manifest_url is None` (regression guard for
        the "get_download_links-only" design decision — this field must never leak into the
        always-empty read paths the way `output_links` doesn't either).
      - A legacy run with empty `output_keys` (which today returns `output_links == {}` without
        raising) still gets a signed, non-`None` `manifest_url` on **both** adapters — this is
        not gated on `output_keys` being non-empty the way `output_links` is, since a run's
        manifest always exists once committed regardless of whether per-artifact keys were
        ever recorded for it. Extend the existing empty-`output_keys` test case(s) rather than
        writing a parallel one, so a regression can't accidentally leave one adapter checked
        and the other not.
      - **A manifest-signing failure aborts the whole call**, on both adapters: inject a
        failing `create_signed_url` targeted at the manifest key specifically (not an output
        key) — e.g. a `Mock(side_effect=...)` for Supabase, and a fake-adapter injection point
        analogous to the existing per-output failure injection — and assert
        `get_download_links` raises rather than returning a `StoredRun` with populated
        `output_links` but no `manifest_url`. This is a distinct scenario from the existing
        per-output failure test; do not assume it's covered by that test alone.
      - Extend the two existing parametrized tests that already call `get_download_links` but
        only assert on `output_links` today —
        `test_get_download_links_retired_tool_class_still_resolves_parity` and
        `test_fake_get_download_links_never_calls_storage_backend` — to also assert
        `resolved.manifest_url` is populated (the latter should also confirm the mocked
        `active_backend`/`create_signed_url` boom is never hit for the manifest key on the
        fake adapter, identically to how it already isn't hit for outputs).
      - Fake/real parity: `manifest_url` is bound to the correct key, mirroring
        `test_output_links_parity`'s existing `assert link.key in link.url` check — assert
        `stored.manifest_path in resolved.manifest_url` (or the adapter-appropriate
        equivalent) on both backends, catching a "wired every link to the same URL" class of
        bug.
      **Done:** 10 tests added/modified in `test_store_parity.py` (confirmed red via
      `AttributeError`/"DID NOT RAISE" before implementation). **Correction found during
      implementation:** the manifest-signing-failure test has **no meaningful Fake-adapter
      equivalent** — `FakeResultStore.manifest_url` is plain string interpolation over a field
      already present on `stored` (no external call, no lookup dict to corrupt the way the
      existing per-output failure test corrupts `_output_sizes`), so it structurally cannot
      fail the way a live `create_signed_url` call can. Written as a Supabase-only test
      (`test_manifest_url_signing_failure_aborts_whole_call_supabase`, not parametrized),
      documented as the mirror-image of `test_fake_get_download_links_never_calls_storage_backend`
      having no Supabase counterpart either.
- [x] 1.2 Add `manifest_url: Optional[str] = None` to `StoredRun` (`result_store/ports.py`),
      after `output_links`, with a docstring note mirroring `output_links`'s own: populated
      only by `get_download_links`, always `None` from `create_run`/`commit`/`get_run`/
      `list_runs`/`from_version_entry`. Confirm this is additive-only: the sole non-keyword
      `StoredRun` construction site (`fake_store.py`'s `_stub_stored_run`) uses all-keyword
      args, so a new defaulted field cannot break it — verify this directly (grep all
      `StoredRun(` call sites) rather than assuming.
      **Done:** grepped every `StoredRun(` call site (`from_version_entry`, `_stub_stored_run`,
      and both adapters' `commit`/`get_download_links`) — all keyword-only; confirmed safe.
- [x] 1.3 Implement in `SupabaseResultStore.get_download_links`
      (`result_store/supabase_store.py`): after resolving `stored` via `self.get_run(...)`,
      call `_sc.create_signed_url(stored.manifest_path, SIGNED_URL_EXPIRES_SECONDS)` and
      attach as `manifest_url` on the returned (possibly-`replace`d) `StoredRun` —
      **independent of, and not gated on, the existing `output_keys`-empty short-circuit**
      (the early `if not stored.output_keys: return stored` branch must not skip manifest
      signing). Confirm 1.1 green.
      **Done.**
- [x] 1.4 Implement in `FakeResultStore.get_download_links` (`result_store/fake_store.py`):
      synthesize `manifest_url = f"fake://signed/{stored.manifest_path}?expires_in={SIGNED_URL_EXPIRES_SECONDS}"`,
      matching `output_links`' existing fake-URL style exactly (see design.md Decision 3).
      **Independent of, and not gated on, the existing `output_keys`-empty short-circuit** —
      this adapter has the identical early-return branch `supabase_store.py` does (line
      ~332-336); it must not be allowed to skip manifest-url synthesis for a legacy run, or
      the fake/real parity test in 1.1 and the tool-layer legacy scenario in task 2 will both
      fail only on this adapter. Confirm 1.1 green.
      **Done:** all 50 tests in `test_store_parity.py` green.

## 2. MCP tool

- [x] 2.1 Write `test_get_download_links_tool.py` tests first (red):
      - The tool's JSON response includes a `manifest_url` key bound to the same run (assert
        it's present and non-empty on the happy path), alongside the unchanged existing keys
        (`experiment`, `tool_class`, `run_ref`, `version_dir`, `outputs`, `output_links` — the
        response has never included a raw `manifest_path` key; confirm this from the current
        code before writing the assertion, per proposal.md's correction).
      - **Legacy-run scenario**: a run with empty `output_keys` returns `output_links == {}`
        in the JSON but a populated `manifest_url` — this is a distinct scenario from the
        happy path, not implied by it, and must not be skipped.
      **Done:** confirmed red via `KeyError: 'manifest_url'` on both new/extended tests before
      implementing.
- [x] 2.2 Update `sections/core/get_download_links.py`'s response dict to add
      `"manifest_url": stored.manifest_url`, and update the function's docstring to mention
      `manifest_url` alongside its existing `output_links` description. Confirm 2.1 green.
      **Done:** 12 tests green in `test_get_download_links_tool.py`.

## 3. Docs

- [x] 3.1 Update `bloommcp/docs/storage-backends.md`'s `get_download_links` section in three
      specific places (not a single generic mention):
      (a) the section's intro sentence — state it re-signs `output_links` for the run's
      outputs *and* a `manifest_url` for the run's `manifest_path`;
      (b) the existing "a single output's lookup failure fails the whole call" bullet — add
      that a manifest-signing failure aborts the call identically;
      (c) the existing legacy-run bullet ("nothing to sign — `output_links` comes back empty
      for it, not an error") — add that `manifest_url` is still populated even when
      `output_links` is empty, since the manifest always exists for any committed run
      regardless of whether per-artifact output keys were ever recorded for it.
      **Done:** all three edits applied; also added a fourth bullet (not originally
      enumerated, found needed during the doc pass) noting `manifest_url` has no
      `sha256`/`size_bytes` counterpart, since the review's doc pass flagged that
      `storage-backends.md` had no existing tie-back point for it.

## 4. Validation

- [x] 4.1 `openspec validate add-bloommcp-manifest-download-link --strict`. **Done — valid.**
- [x] 4.2 Full `bloommcp` unit suite green, plus lint (`black`/`ruff` pinned per
      `.pre-commit-config.yaml`, via `uvx` if not on PATH). Re-run
      `test_store_parity.py`/`test_get_download_links_tool.py` individually first to confirm
      the new/modified tests specifically pass before the full-suite pass.
      **Done:** `test_store_parity.py` (50 passed), `test_get_download_links_tool.py`
      (12 passed) individually, then full suite: 1046 passed, 29 skipped (up from #599's
      1040 passed baseline). `black@26.3.1`/`ruff@0.9.9` (pinned via `uvx`, matching
      `.pre-commit-config.yaml`) both clean on every file this change touches.
- [x] 4.3 Immediately before finishing, re-check `gh pr view 611` and
      `git log egao28/bloommcp-get-download-links-599..origin/staging` — if #599 has since
      merged, note whether a rebase onto `staging` is needed before this change's own PR opens
      (do not rebase preemptively while #611 is still open/unmerged — see design.md and this
      change's git-workflow review notes). If a rebase does happen later, re-diff
      `test_store_parity.py` and `test_get_download_links_tool.py` against the pre-rebase tip
      before trusting "tests still pass" — this repo has twice had a staging-merge silently
      drop an appended test section in exactly these kinds of overlapping-tail-of-file edits.
      **Done:** `gh pr view 611` shows `state: OPEN, mergeable: CONFLICTING,
      mergeStateStatus: DIRTY`, `origin/staging` is 28 commits ahead of
      `egao28/bloommcp-get-download-links-599` — #599/PR #611 is **not** merged as of this
      writing. This change's own PR opens against `egao28/bloommcp-get-download-links-599`,
      not `staging` (see design.md's sequencing note); no rebase performed.

## 5. Post-PR review fixes (PR #612, 5-lens review)

- [x] 5.1 **Blocking (inherited from #611):** the tool's catch-all returned raw `str(exc)`
      instead of `safe_error_text(exc)`. Resolved automatically — this branch was rebased onto
      `egao28/bloommcp-get-download-links-599`'s post-#611-review tip (`fix(#599): address PR
      #611 5-lens review`, which already fixed this at the shared call site), so no separate
      change was needed here. Re-ran the full suite post-rebase to confirm: 1108 passed, 29
      skipped (up from this change's own pre-rebase 1046, since #599's fix commit added tests
      of its own too).
- [x] 5.2 **Important:** `manifest_url` makes `ExperimentBlock.source_path` MCP-reachable for
      the first time (previously required direct Supabase Storage/admin access) — not called
      out anywhere in this change's original scope docs. Added design.md Decision 4 (ship
      as-is, disclosed, no redaction — a path string, not a credential, already classified
      non-secret per `openspec/project.md`; redacting would mean rewriting manifest content,
      contradicting this change's own "no manifest content change" Non-Goal) plus a
      corresponding Risk bullet, a new `storage-backends.md` bullet, and a docstring sentence
      on the tool itself.
- [x] 5.3 **Important:** a legacy (v2) manifest's `manifest_url` resolves to a schema-thin
      document (missing `seed`/`agent`/`environment`/per-artifact `output_sha256`/
      `output_keys` — all v3-only fields), but this change's own framing ("verify a run's
      provenance... everything recorded in the manifest") didn't disclose that gap. Added a
      clause to `storage-backends.md`'s existing legacy-run bullet and a design.md Risk entry.
- [x] 5.4 **Important:** the "no `sha256`/`size_bytes` counterpart" caveat lived only in
      `storage-backends.md`, not in the MCP tool's own docstring where a calling agent would
      actually see it. Added to `get_download_links.py`'s docstring.
- [x] 5.5 **Informational (not actioned):** CI never ran on PR #612 because it targets a
      feature branch, not `main`/`staging` — already disclosed in the PR body's own stacking
      note. Confirmed the suite/lint claims independently instead (5.1's re-run); CI will run
      for real once this is retargeted to `staging` per task 4.3's plan.
