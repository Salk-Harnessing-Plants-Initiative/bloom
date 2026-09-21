## 1. Test scaffolding + paging tests

**Tasks 1.1–1.11 land in one atomic commit** (the implementation in 1.6 is what turns the RED
tests green). Placement note: put the new tests in the existing section-5 `list_prefix` family
as `# ─── 5e. Supabase list_prefix pagination (#396) ───`, **not** at EOF — PR #782 (open)
appends its own section there, and the file already has two sections numbered `9`.

- [x] 1.1 Teach the existing `_FakeSbStorageClient` (`bloommcp/tests/test_storage_backend.py:641`)
      the real listing contract instead of adding a second, disagreeing stand-in: widen to
      `list(self, prefix, options=None)`, record each `(prefix, options)` call on
      `self.calls`, and slice the sorted immediate-child names by `options["offset"]` /
      `options["limit"]`, defaulting to storage3's own `offset=0, limit=100` when options are
      absent (so the fake reproduces the truncation under test rather than hiding it). Add a
      self-bound inside `list`: `assert len(self.calls) <= sb._SUPABASE_LIST_MAX_PAGES + 2`,
      so a regressed cap fails fast instead of hanging — `pytest-timeout` is not in the
      `test` extra, so an in-fake bound is the only available mechanism.
- [x] 1.2 `test_supabase_list_prefix_pages_past_the_default_limit`: seed 250 children, assert
      all 250 are returned in order and that offsets advanced `0, 100, 200`. **RED today**
      (returns only the first 100).
- [x] 1.3 `test_supabase_list_prefix_single_page_costs_one_request` (7 children → one call,
      all 7) and `test_supabase_list_prefix_empty_listing_returns_empty_list` (0 children →
      one call, `[]`). **GREEN today — regression guards, not RED**: today's single
      unpaginated call already satisfies both. They exist so a future
      terminate-on-empty-page rewrite cannot quietly add a round-trip to the hot
      `read_manifest` existence check. (Note the name: this is an empty *listing*; the empty
      *prefix* case is 1.5's passthrough parametrization.)
- [x] 1.4 `test_supabase_list_prefix_exact_page_multiple_is_not_truncated`: exactly 100
      children → two calls (offsets `0, 100`, the second empty), all 100 returned. Pins the
      boundary where a full final page is indistinguishable from a continuing one. **RED today.**
- [x] 1.5 `test_supabase_list_prefix_request_pins_page_size_and_sort_order`: assert the first
      call's options are `{"limit": sb._SUPABASE_LIST_PAGE_SIZE, "offset": 0, "sortBy":
      {"column": "name", "order": "asc"}}`, and parametrize over prefixes `["",
      "bloommcp_output", "bloommcp_output/"]` asserting the recorded prefix is byte-identical
      to what was passed (the adapter forwards it verbatim while the local backend strips
      slashes, so normalization is where a >1-page divergence would hide). Plus
      `test_supabase_list_page_size_matches_client_default`: import
      `storage3.constants.DEFAULT_SEARCH_OPTIONS` and assert
      `sb._SUPABASE_LIST_PAGE_SIZE == DEFAULT_SEARCH_OPTIONS["limit"]` and
      the `sortBy` literal equals `DEFAULT_SEARCH_OPTIONS["sortBy"]` — without this the page
      size is asserted only against itself, and a lockfile bump could void the
      end-of-listing assumption while the suite stayed green. (Measured: the options-shape
      test is RED; the default-limit assertion passes as soon as the constant exists, so it
      is a constant guard rather than a RED test. No `_SUPABASE_LIST_SORT_BY` constant was
      added — the dict is built inline per call, so the test asserts the literal instead.)
- [x] 1.6 Implementation, in `bloommcp/src/bloom_mcp/storage_backend.py`. Add constants beside
      `_TMP_PREFIX` (`:46-48`) to stay clear of PR #782's hunks: `_SUPABASE_LIST_PAGE_SIZE =
      100` (comment: mirrors storage3's `DEFAULT_SEARCH_OPTIONS["limit"]`, so the request is
      one the server already accepts and a short page can be read as end-of-listing) and
      `_SUPABASE_LIST_MAX_PAGES = 50` (comment: a backstop, not the primary guard, and state
      the exact ceiling — a listing ends on a short page, so up to 4,999 children enumerate
      and 5,000 raises). Rewrite `list_prefix` to: build the client **once**; loop
      `client.list(prefix, {"limit": …, "offset": …, "sortBy": {"column": "name", "order":
      "asc"}})` with the `sortBy` dict built **inline per call**, with no module-level constant
      (storage3 shallow-merges the options into the request body, so a shared dict would travel
      by reference and be mutable across calls); de-duplicate order-preservingly; return on a
      page shorter than the requested limit; advance `offset` by `len(page)`; raise
      `StorageBackendError` with the prefix **early in the message** (it reaches persisted
      reports through `safe_error_text`, which truncates at 300 chars) when a page contributes
      no new names, and again if the request cap is exhausted, each preceded by a
      `logger.error` naming the prefix. Update the method docstring to state the paging
      contract — this also discharges the docstring note PR #389's review asked for and never
      got. Widen the `StorageBackendError` class docstring (`:117-125`), which still says
      "local-backend filesystem failure" though the Supabase adapter already raises it.
- [x] 1.7 `test_supabase_list_prefix_raises_when_server_ignores_offset`: a fake that always
      returns the same full page → `pytest.raises(sb.StorageBackendError,
      match=re.escape(prefix))` (`match` is `re.search`, so escape it), asserting it stops on
      the **second** request via the no-progress check rather than exhausting the cap, and
      that the message does not embed the accumulated names. **RED today.**
- [x] 1.8 `test_supabase_list_prefix_over_long_page_is_not_skipped_past`: a fake returning
      more entries than the requested `limit` → assert nothing between the limit and the
      page's true end is missing, pinning `offset += len(page)`. **RED today.**
- [x] 1.9 `test_list_prefix_parity_paginated_supabase_vs_local`: seed 150 siblings into both a
      `tmp_path` local root and the fake, and compare `sorted(...)` name lists across the same
      prefix table the existing `test_list_prefix_parity_fake_vs_local` (`:846`) sweeps — `""`,
      no-slash, trailing-slash, missing. Compare sorted **sets/lists**, never exact order:
      Python `sorted()` is codepoint order while the server orders by the `name` column under
      the database collation, so an exact-order assertion would be false and eventually flaky.
      This is the case the existing parity test structurally cannot catch. **RED today.**
- [x] 1.10 `test_supabase_list_prefix_deduplicates_across_page_boundary`: a fake that re-emits
      the previous page's last name at the head of the next page → assert no duplicate
      survives. Names are unique within a prefix, so a repeat can only come from a concurrent
      write racing a page seam, and `experiments_scanned` in a persisted audit report is
      exactly the integrity counter that would otherwise absorb the double-count. (Measured
      GREEN pre-fix, and vacuously so: unpaginated code makes one request, so the fake never
      reaches the offset>0 branch that injects the repeat. It becomes meaningful only once
      paging lands.)
- [x] 1.11 `test_supabase_list_prefix_sort_by_is_not_shared_mutable_state`: assert each
      request's `sortBy` is an equal-but-distinct object, so the shallow merge storage3 does
      into the request body cannot let one mutation corrupt every later listing in the
      process. **RED today** (no options are sent at all). This is what makes the
      build-it-inline decision in 1.6 enforceable rather than a comment.

## 2. Caller-level coverage + existing-test fallout

- [x] 2.1 `test_resolve_versioned_cleaned_resolves_version_dir_beyond_first_page`: the Supabase
      analogue of the local `test_resolve_versioned_cleaned_via_local_list_prefix_fallback`
      (`:884`) — 150 sibling version dirs, a manifest entry with `version_dir=""` whose id
      sorts onto page 2, assert the cleaned CSV resolves instead of "Manifest references
      version vN but its directory was not found". **RED today**, and the only test that
      proves the fix where a user would feel it.
- [x] 2.2 In `tests/unit/test_supabase_client.py`, update `test_list_prefix_returns_basenames`:
      its `supabase_mock.list.assert_called_once_with("bloommcp_output/qc_my_exp/")` must now
      expect the options argument. Assert it via the `storage_backend` constants rather than
      restating literals, so the two files cannot drift.
      `test_list_prefix_returns_empty_when_no_objects` needs no change (its MagicMock returns
      `[]` → short page → one call).
- [x] 2.3 Verify the fallout list is complete by searching for `.list` **definitions as well as
      call sites** (`grep -rn "\.list\b\|def list" bloommcp/tests tests/unit`) — the narrower
      call-site-only search is what let 1.1's fake slip past the first draft of this plan.
      Three currently-green tests reach `read_manifest` → `list_prefix` through real dispatch
      and fail with `TypeError` until 1.1 lands: `test_write_manifest_stamps_active_backend`
      (`:669`), `test_manifest_identical_across_backends_except_storage_backend` (`:712`),
      `test_repeated_backend_flip_logs_once_not_on_return` (`:784`).
- [x] 2.4 Confirm the unchanged-surface claim by naming its existing owners rather than a grep:
      `test_list_prefix_returns_bare_children` (`:164`), `test_list_prefix_root_and_missing`
      (`:176`), and `test_list_prefix_parity_fake_vs_local` (`:846`) must all still pass
      untouched, and the `fake_supabase_storage` fixture must need no edit (it patches the
      `supabase_client` free functions, above the seam).
- [x] 2.5 Add a `### Fixed` entry under `## [Unreleased]` in `bloommcp/CHANGELOG.md` (the
      package ships to PyPI as of `0.1.0a1`). Expect a trivial rebase against PR #782, which
      also adds one.

## 3. Verification

- [x] 3.1 RED before GREEN, without interactive stashing: write 1.1–1.5 and 1.7–1.9 and 2.1,
      run them **before** applying 1.6, and confirm the failures are exactly the tasks marked
      RED (1.2, 1.4, 1.5, 1.7, 1.8, 1.9, 2.1) while the two 1.3 guards pass. Then apply 1.6
      and re-run:
      `cd bloommcp && uv run --frozen --extra test pytest tests/test_storage_backend.py -q`
- [x] 3.2 Full suites, using the repo's actual invocations — bare `uv run pytest` resolves a
      non-project pytest and collects the Docker-dependent `integration`/`live_smoke` tests,
      and `tests/unit/test_ci_workflow_uv_conventions.py:230` enforces that every `uv run`
      pytest line carries `--extra test`:
      `cd bloommcp && uv run --frozen --extra test pytest tests/ -m "not integration and not live_smoke" -q`
      and, from the repo root, `uv run --extra test pytest tests/unit/ -q`.
- [x] 3.3 `pre-commit run --files <touched files>` (black/ruff are pinned there, not project
      dependencies), and `openspec validate fix-bloommcp-list-prefix-pagination --strict`.
