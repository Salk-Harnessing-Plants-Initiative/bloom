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

## 6. Post-PR review rework (PR #622, two independent lenses — scientific rigor + security)

- [x] 6.1 **Blocking:** `manifest_url` is not scoped to the requested run at all — `manifest.json`
      is keyed only by `(experiment, tool_class)`, so any known `run_ref` unlocks every run's
      `params`/`source_id`/`source_name`/`based_on_version` for that pair, not just the resolved
      one. Directly contradicts this tool's own documented "not a browsing/discovery feature"
      invariant. Decision: drop `manifest_url` entirely; return the resolved run's own `params`/
      `based_on_version` inline instead (see design.md Decision 5 for the full finding and the
      two-option decision this required).
      **Done:**
      - `result_store/ports.py`: removed `StoredRun.manifest_url`; added `params: dict` /
        `based_on_version: str` fields, both defaulting empty, documented as populated only by
        `get_run`/`get_download_links` — never `from_version_entry`/`commit`/`list_runs`.
      - `result_store/supabase_store.py`: `get_run` now attaches `params`/`based_on_version` from
        the same `entry` it already resolves; `get_download_links` no longer signs
        `manifest_path` at all (the `if not stored.output_keys: return stored` short-circuit
        needed no `manifest_url=` kwarg to drop, since `get_run` already carries the two new
        fields through).
      - `result_store/fake_store.py`: added a private `self._provenance` side table (mirrors the
        existing `_output_sizes` pattern), populated at `commit()` time, consulted only by
        `get_run` — keeps `params`/`based_on_version` off the shared `StoredRun` objects
        `list_runs` returns, since this adapter has no per-call manifest re-read the way
        `SupabaseResultStore` does. Confirmed via grep that `get_run` has exactly one caller in
        the codebase (`get_download_links`, both adapters) before scoping the attachment there.
      - `sections/core/get_download_links.py`: response carries `params`/`based_on_version` in
        place of `manifest_url`; docstring rewritten to explain the drop and why.
      - `docs/storage-backends.md`: rewrote the `get_download_links` section's manifest-specific
        bullets to describe `params`/`based_on_version` instead of `manifest_url`.
      - `design.md`/`proposal.md`: added Decision 5 / a rework note documenting the finding, the
        two options the review posed, and why "scope down" was chosen over "re-open risk
        acceptance" (external OAuth-authenticated MCP clients, #613, merged in the same commit
        range the review flagged as changing who this now reaches).
      - Both spec deltas (`bloommcp-result-store`, `bloommcp-get-download-links-tool`) rewritten
        to describe the new fields; see task 6.4.
- [x] 6.2 **Important (compounding the above, now moot):** because `manifest.json` is rewritten
      in place on every future commit, a `manifest_url` signed for one run could return a
      different document by the time it's fetched. Moot — no manifest is signed or fetched
      anymore.
- [x] 6.3 **Important (now moot):** no server-side log line existed for a manifest-signing
      failure, and the tool layer's generic `{"error": ...}` gave no indication which resource
      failed. Moot — there is no manifest-signing call left to fail.
- [x] 6.4 Updated `test_store_parity.py` and `test_get_download_links_tool.py`: replaced every
      `manifest_url`-shaped assertion with the `params`/`based_on_version` equivalent (including
      a new parity check that `get_run`/`get_download_links` return the *resolved* run's own
      `params`, not some other run's — the regression this rework exists to prevent — and that
      `list_runs`/`commit()`'s own return value still leave `params == {}`/`based_on_version ==
      ""`, mirroring the existing `output_links`-empty regression guard). Full suite re-run
      green after the rework; `black`/`ruff` clean on every touched file.
- [x] 6.5 **Informational:** design.md/proposal.md still asserted "#611 not yet merged into
      staging" / "get_download_links does not exist on staging yet" — both false since #611
      merged (2026-08) and this PR's own base auto-retargeted to `staging` as a result. Fixed:
      updated the Context/Risks (design.md) and Sequencing (proposal.md) sections to record the
      merge and mark the old sequencing constraint superseded, rather than silently deleting the
      historical narrative.

## 7. Post-PR review round 2 (PR #622, 5-lens review of the rework itself)

- [x] 7.0 **Note:** PR description was stale relative to the actual diff (still described
      shipping `manifest_url`, though the diff had already replaced it with scoped
      `params`/`based_on_version` mid-review). Fixed by editing the GitHub PR description
      directly (not an OpenSpec doc) to describe the shipped design; see the PR itself for the
      current text.
- [x] 7.0b Branch was reported behind `staging` (`#611` — already merged per task 6.5 — is a
      dependency of a *different*, unrelated `#646` fix that landed on `staging` since). Rebased
      onto `origin/staging`; no conflicts (the intervening commits touched an unrelated OpenSpec
      change and web/deploy files, none overlapping this change's files).
- [x] 7.1 **Blocking:** `list_existing_analyses` (unauthenticated relative to any per-experiment
      permission — bloommcp has none — and always-included) already enumerates every historical
      `run_ref` for an experiment. Looping `get_download_links` once per enumerated ref
      reconstructs the same "every run's raw `params`" corpus the `manifest_url` design was
      rejected (Decision 5) for exposing in a single call — just spread across N calls. `params`
      is genuinely new MCP-reachable surface (individually and in aggregate) introduced by this
      change. Decision: **accept, do not narrow or rate-limit** — added design.md Decision 6 with
      the full reasoning (this composition already applies to `output_links`, the actual
      analysis data, since `#599`'s own design.md frames enumerate-then-fetch as the intended
      usage; `params` metadata doesn't cross a new trust boundary riding the same, already-wider
      composition; bloommcp has no per-experiment authorization to narrow against, and no
      rate-limiting infrastructure to attach a mitigation to). Added a corresponding
      `storage-backends.md` bullet and a `tasks.md`-cited test (7.4) making the boundary
      explicit. Revisit alongside `output_links`'s identical acceptance if bloommcp ever adds
      per-experiment/per-caller authorization.
- [x] 7.2 **Important:** `ResultStore.get_run`'s Protocol docstring (`ports.py`) still read
      "Resolve a run by reference..." with no mention that it now returns populated
      `params`/`based_on_version` — both concrete adapters were correct, but a reader of the
      Protocol alone wouldn't know. Fixed: docstring now states this explicitly, including that
      `commit`/`list_runs` (via `from_version_entry`) leave the two fields at their defaults.
- [x] 7.3 **Important:** `FakeResultStore`'s seed helpers (`seed_run_with_keys`, `seed_v2_run`,
      `seed_collision`) didn't populate `params`/`based_on_version`, so the parity test for "a
      legacy run still gets its own provenance" only asserted `isinstance(dict)`/`isinstance(str)`
      — it would have passed even if the fake adapter's provenance lookup silently broke (e.g.
      always returning `{}`/`""`). Fixed: all three helpers (via a shared `_stub_stored_run`)
      now accept optional `params`/`based_on_version` and register them into the same
      `_provenance` side table `commit()` populates — a seeded run's `get_run` resolution now
      exercises the identical lookup path a genuinely committed run takes, not a shortcut.
      Updated `test_get_download_links_legacy_run_with_no_keys_yields_no_links_parity`
      (`test_store_parity.py`) and `test_legacy_run_response_still_carries_its_own_params`
      (`test_get_download_links_tool.py`) to pass explicit values and assert real equality
      (`{"a": 1}`/`"raw"` matching `_prov()`'s real values for the parity test; `{"legacy":
      True}`/`"raw"` for the tool test) instead of type-only checks.
- [x] 7.4 **Suggestion:** added
      `test_enumerate_via_list_existing_analyses_then_loop_reconstructs_every_runs_params`
      (`test_get_download_links_tool.py`) — commits 3 runs with distinct `params`, enumerates
      their `run_ref`s via `list_existing_analyses`, loops `get_download_links` per ref, and
      asserts every run's `params` is recoverable in aggregate. Makes the "scoped per call, not
      per experiment" boundary explicit and regression-visible rather than requiring cross-file
      verification against design.md prose alone.
- [x] 7.5 **Suggestion:** "Decision 5" (this change) and `#599`'s own design.md also has a
      "Decision 5" (`get_download_links is not a foundational tool`) — bare `design.md Decision
      5`/`Decision 6` cross-references in code comments, docstrings, and `storage-backends.md`
      could be confused with the wrong change's decision. Fixed: every such reference in touched
      files now names the change explicitly (`add-bloommcp-manifest-download-link's design.md
      Decision 5`, `add-bloommcp-get-download-links's design.md Decision 6`). References from
      *within* this change's own `proposal.md`/`tasks.md` to their sibling `design.md` are left
      unqualified — unambiguous by directory context, unlike a shared code file or doc a reader
      might reach from anywhere.
- [x] 7.6 Full `bloommcp` suite re-run green after all of the above (65 tests in the two directly
      affected files, up from 64 — the new 7.4 test); `black@26.3.1`/`ruff@0.9.9` clean on every
      touched file; `openspec validate add-bloommcp-manifest-download-link --strict` passes.
