This proposal was first implemented and pushed to PR #643 (open, not yet merged against
`staging`), then revised in place — on the same branch, before merge — after the issue author's
follow-up comment redirected the output-artifact half of the design. See design.md's "Revision
history." Section A below is the original work already pushed (kept for history — do not redo
it). Section B is the follow-up revision's own task list, landed as additional commits on the
same branch/PR.

Commit plan: commit each numbered task group as ONE commit (not sub-step-by-sub-step) — within a
group, the "test first" sub-step is designed to be red against pre-implementation code, so
splitting a group across commits leaves an intermediate commit with intentionally-failing tests.
Suggested commit messages use this repo's `type(#642): description` convention.

## A. Original implementation (PR #643, merged — history, not re-done)

Added `self_serve_base_url()`/`local_output_root()` helpers; defaulted `BLOOM_STORAGE_URL` in
`create_signed_url` instead of raising; defaulted `BLOOM_PLOTS_URL` under the `BLOOM_LOCAL_ROOT`
tier and added it to `validate_env()`'s optional set; mounted `/output` and `/plots` `StaticFiles`
in `build_app()`. Full test suite green, lint clean, pushed to PR #643. See the original commits
(`efb7443`, `b8da2c8`, `4eeab05`, `7cb6ff6`, `6ddb4ed`, `479a7db`) for the historical diff — not
reproduced here since section B partially reverts and partially builds on top of it.

## B. Follow-up revision: outputs get a direct path, not a URL

### B1. Revert the `/output` self-serve mechanism (outputs only — `/plots` is untouched)

- [x] Revert `LocalStorageBackend.create_signed_url` (`storage_backend.py`) to its original
      raising behavior when `BLOOM_STORAGE_URL` is unset — undoes A's defaulting change. Kept
      `self_serve_base_url()`/`local_output_root()` themselves (still used elsewhere — see B2/B3).
- [x] Remove the `/output` `Mount` from `server.py`'s `build_app()`; keep the `/plots` `Mount`
      and its `is_local_backend()` gate.
- [x] Restore `test_local_create_signed_url_raises_when_unset_no_path_leak` in
      `test_storage_backend.py` (removing the two self-serve-default tests A added).
- [x] Strip all `/output`-specific cases from `test_local_static_mounts.py` (absent-on-default,
      serving tests for both tiers, garbage-identity, missing-file-404) — keep the `/plots`
      cases. Remove the `/output/k` parametrize case from `test_identity_middleware.py`'s
      `test_action_from_path` — keep `/plots/k`.
- [x] Update `test_local_store_roundtrip_matches_contract` (`test_storage_backend.py`) and
      `test_fully_local_qc_clean_to_pca_via_local_root_only`/
      `test_fully_local_qc_clean_to_pca_no_supabase` (`test_local_mode.py`) to stop setting
      `BLOOM_STORAGE_URL` and instead assert the new path-based `output_links` shape (see B2/B3).

### B2. `OutputLink` gains `path`; `url` becomes optional

- [x] **Test first** (`tests/contract/test_run_links.py`): updated `test_output_link_field_set`
      to expect `path` in the field set; added
      `test_output_link_url_and_path_both_default_to_none`. Confirmed the field-set test failed
      before the model change.
- [x] Implemented in `contract/models.py`: `OutputLink.url: Optional[str] = None`,
      `OutputLink.path: Optional[str] = None`. Updated the class docstring.

### B3. `build_output_links` takes `path_for` as an alternative to `url_for`

- [x] **Test first** (`tests/result_store/test_artifacts.py`): added
      `test_path_for_populates_path_and_leaves_url_none`, `test_url_for_leaves_path_none`,
      `test_neither_url_for_nor_path_for_raises`, `test_both_url_for_and_path_for_raises`,
      `test_path_for_key_scope_guard_still_applies`. Confirmed all four new
      (path_for/neither/both/guard) tests failed with `TypeError`/wrong-behavior before the
      signature change.
- [x] Implemented in `result_store/_artifacts.py`: `build_output_links` signature becomes
      `(output_keys, output_sha256, output_size_bytes, *, expected_prefix, url_for=None,
path_for=None)` — both keyword-only, exactly one required
      (`(url_for is None) == (path_for is None)` raises `ValueError`). Added an explicit
      `if url_for and not url: raise ValueError(...)` guard, since `OutputLink.url` being
      `Optional` (B2) removed Pydantic's own type-level rejection of a `None`/empty signed URL —
      see design.md Decision 6.
- [x] Fixed `tests/result_store/test_store_parity.py`'s `_wrong_prefix` helper, which called the
      real `build_output_links` with `url_for` positionally — now keyword (`url_for=url_for`),
      matching the new keyword-only signature. Confirmed `test_key_outside_run_prefix_fails_...`
      (both `kind` parametrizations) still passes.

### B4. `SupabaseResultStore.commit()` branches on `is_local_backend()`

- [x] Implemented in `result_store/supabase_store.py`: imported `is_local_backend`,
      `local_output_root` from `storage_backend`. `commit()` now calls
      `build_output_links(..., path_for=lambda key: str(local_output_root() / key), ...)` when
      `is_local_backend()`, else the original `url_for=lambda key: _sc.create_signed_url(...)`
      path, unchanged. `FakeResultStore.commit()` is untouched — it never simulates the local
      backend and always synthesizes a `url` (confirmed: it imports nothing from
      `storage_backend`).
- [x] Updated `test_local_store_roundtrip_matches_contract` (`test_storage_backend.py`) to drop
      `BLOOM_STORAGE_URL` entirely and assert
      `stored.output_links["cleaned"].path == str(tmp_path / stored.output_keys["cleaned"])` and
      `.url is None`.
- [x] Updated `test_fully_local_qc_clean_to_pca_via_local_root_only` (`test_local_mode.py`) to
      drop its `BLOOM_STORAGE_URL` setenv and assert every `qc_res.output_links.values()` entry
      has `url is None`, `path is not None`, and `Path(path).is_file()`.
- [x] Fixed the pre-existing regression this change surfaced:
      `test_signing_call_returning_no_url_fails_commit_not_silently_none`
      (`test_supabase_result_store.py`) relied on `OutputLink.url: str` (non-`Optional`) to reject
      a `None` signed URL at construction — B2 made that no longer type-invalid. Fixed by B3's
      explicit `url_for`-truthiness guard in `build_output_links` rather than by changing this
      test; confirmed the test passes unmodified against the new guard.

### B5. Docs

- [x] Rewrote `bloommcp/docs/storage-backends.md`: retitled "Downloading outputs" to "Reaching
      outputs: signed URLs and direct paths", rewrote the backend-specific bullets (outputs now
      described as resolving via a direct filesystem path for the local backend — no URL, no
      self-serving — distinct from plots' self-served `/plots` default), and fixed the
      now-stale "Inline-vs-link" bullet, the "one difference" bullet, and the "Two ways to use
      it" follow-up sentence to stop claiming outputs are self-served over HTTP.
- [x] Fixed the same now-stale `/output` reference in
      `specs/bloommcp-packaging/spec.md`'s "Local-Mode Self-Served Plots URL" requirement (it
      cited `/output`'s precedent for `/plots`'s auth stance — `/output` no longer exists).
- [x] Updated `bloom_mcp/auth.py`'s `BLOOMMCP_PUBLIC_URL` comment and `docker-compose.dev.yml`'s
      matching comment to say "self-serve base for local-mode `/plots` URLs" (dropped the
      "/output" half of the cross-reference A added).
- [x] Reworded the `BLOOM_STORAGE_URL` comment in `docker-compose.dev.yml` — A's rewording
      ("defaults to bloommcp's own address") was no longer accurate; now states plainly that
      bloommcp's own `output_links` don't need this var at all, and it's only relevant for a
      caller of `create_signed_url` against a separately-stood-up server.
- [x] `openspec validate update-bloommcp-local-url-defaults --strict` — valid.

### B6. Full verification

- [x] `cd bloommcp && uv run --frozen --extra test pytest tests/ -m "not integration and not
      live_smoke" -v --tb=short` — green (1136 passed before this docs pass; docs-only changes
      don't affect test outcomes).
- [x] `uvx pre-commit run --files <all changed files>` — all hooks pass (Black/Ruff/Ruff-format/
      Prettier/gitleaks/etc.), matching how A's own lint pass was actually run (bloommcp's own
      `uv run black`/`ruff` aren't invokable directly — not project dependencies). One stable,
      harmless Black/Ruff-format disagreement on a single assert-message line-wrap in
      `test_run_links.py` — confirmed convergent (identical file hash before/after a repeat run),
      not an infinite oscillation.
- [x] Updated PR #643's description to reflect the revised design, noting that this supersedes
      the `/output`-self-serve mechanism from the branch's earlier commits per the issue's
      follow-up comment.
