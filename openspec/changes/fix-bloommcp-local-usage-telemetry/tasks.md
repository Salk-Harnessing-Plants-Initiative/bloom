## 0. Proposal

- [ ] 0.1 Commit the OpenSpec proposal itself (`proposal.md`, `design.md`, `tasks.md`, both
      `specs/` delta files) as the first commit on this branch, before any code change —
      matching this repo's own precedent (e.g. the sibling #626 branch's
      `docs(#626): add OpenSpec proposal for source/version selection` opening commit).

## 1. Skip usage telemetry entirely for the local backend

- [ ] 1.1 Write a failing test in `bloommcp/tests/test_identity_middleware.py`:
      `test_local_backend_skips_usage_recording` — with `monkeypatch.setenv("BLOOM_STORAGE_BACKEND",
      "local")`, a qualifying (non-`/health`, non-`401`) request through `IdentityMiddleware`
      results in `record_usage_async` never being called (assert `recorded_usage == []`,
      mirroring `test_health_path_is_not_recorded`'s style).
- [ ] 1.2 Write a second failing test, `test_supabase_backend_still_records_usage` — same
      request shape, but with `monkeypatch.setenv("BLOOM_STORAGE_BACKEND", "supabase")`
      **explicitly set** (not merely unset), asserting `recorded_usage` DOES contain the call.
      Setting it explicitly (rather than leaving it unset) is what actually distinguishes a
      correct `is_local_backend()` gate from an incorrect implementation like
      `if not os.environ.get("BLOOM_STORAGE_BACKEND"):` — the many existing tests in this file
      that already pass with the var unset would not catch that mistake.
- [ ] 1.3 In `identity.py`'s `IdentityMiddleware.__call__`, gate the existing
      `record_usage_async(...)` call on `not storage_backend.is_local_backend()`, importing
      `is_local_backend` the same way `record_usage_async` is already imported (lazily,
      inside the `if` block, as a stylistic match — not a circular-import necessity;
      `storage_backend.py` has no import of `identity`/`usage`, so a top-level import would
      also be safe, but keep it lazy for consistency with the existing convention in this
      exact function).
- [ ] 1.4 Run both new tests; confirm they pass. Run the full `test_identity_middleware.py`
      file to confirm no existing test broke (in particular, every test that runs with
      `BLOOM_STORAGE_BACKEND` unset must still see recording happen, since 1.3's gate must
      only trigger on the literal `"local"` value).

## 2. Downgrade usage-recording failure logging from traceback to warning

- [ ] 2.1 Extend `_RecordingLogger` in `bloommcp/tests/test_usage.py` to record which method
      was called, without changing its existing `.messages` shape (a flat list of formatted
      strings) — two existing tests
      (`test_dropped_recording_log_never_contains_the_raw_identity`,
      `test_call_rpc_failure_log_never_contains_the_raw_identity`) assert against `.messages`
      directly and must keep passing unchanged. Add a second list, e.g. `self.calls`, storing
      `(level, formatted_message)` tuples, populated by both `.exception()` and `.warning()`
      alongside the existing `.messages.append(...)`.
- [ ] 2.2 Write a failing test, `test_call_rpc_failure_is_logged_as_warning_not_exception` —
      using the extended `_RecordingLogger`, assert the RPC-failure path
      (`monkeypatch.setattr(sc, "call_rpc", _boom)`, mirroring
      `test_call_rpc_failure_log_never_contains_the_raw_identity`'s setup) results in a
      `"warning"` entry in `.calls` and no `"exception"` entry.
- [ ] 2.3 Write a matching failing test,
      `test_submission_failure_is_logged_as_warning_not_exception` — for the submission-failure
      path (`record_usage_async`'s own except clause), reusing the `_DeadExecutor` pattern from
      `test_record_usage_async_swallows_a_submission_failure`, with the same `_RecordingLogger`
      assertion: `"warning"` logged, `"exception"` not.
- [ ] 2.4 In `usage.py`, change `_do_record`'s except clause from `logger.exception(...)` to
      `logger.warning(...)`, capturing the exception (`except Exception as exc:`) and
      interpolating its message explicitly (e.g. `%s`, `exc`) — `logger.warning` does not
      include a traceback by default the way `logger.exception` does, so the message itself
      must now name the failure.
- [ ] 2.5 Make the same change to `record_usage_async`'s own except clause (the
      submission-failure path a few lines below `_do_record`) for consistency — same rationale,
      not explicitly named in the issue but the same "best-effort recording shouldn't look like
      a crash" principle applies to both sites in this module (design.md Decision 2).
- [ ] 2.6 Run all new/changed tests plus the full `test_usage.py` file; confirm
      `test_call_rpc_failure_log_never_contains_the_raw_identity` and
      `test_dropped_recording_log_never_contains_the_raw_identity` still pass unchanged (they
      assert on `.messages` content, untouched by 2.1's addition of `.calls`).

## 3. Documentation wording

- [ ] 3.1 Write a failing test, `tests/unit/test_bloommcp_local_mode_docs.py` (new file,
      mirroring `tests/unit/test_bloommcp_data_mount_rename.py`'s
      `test_no_stale_sleap_out_csv_references` pattern — a fixed file list, read from
      `REPO_ROOT`, grepped for banned strings): for `bloommcp/docs/connecting-claude-code.md`,
      `bloommcp/docs/storage-backends.md`, and `_WIKI/BLOOMMCP/README.md`, assert none of
      `"no connection to the shared server at all"`, `"nothing shared with anyone else"`,
      `"fully-local (offline)"` appear, and that each file contains
      `"no experiment data leaves your machine"` (or the file-appropriate equivalent wording).
- [ ] 3.2 In `bloommcp/docs/connecting-claude-code.md`, reword both the main claim (currently,
      lines 78-81: "bloommcp supports a fully-local mode with no connection to the shared
      server at all: your own input files in, your own output files out, nothing shared with
      anyone else") and the earlier same-file signpost sentence (lines 35-37, "for a fully
      offline workflow with no access to Bloom's live data") so both consistently state the
      guarantee as "no experiment data leaves your machine" rather than an absolute
      no-connection claim.
- [ ] 3.3 In `bloommcp/docs/storage-backends.md`'s "Opt-in: the `local` backend" section
      (currently, lines 130-131: "Set `BLOOM_STORAGE_BACKEND=local` to run fully offline —
      local input, local output, no Supabase boot gate"), reword to state the guarantee as
      data-locality, keeping the opt-in mechanics description (no Supabase boot gate, local
      input/output) intact. Optionally (light touch, not required for correctness): reword the
      unrelated "runs fully offline" phrase at lines 244-247 (about why cross-backend mixing
      can't be detected) to avoid leaving it as the sole remaining unqualified "fully offline"
      claim in this file once lines 130-131 are corrected.
- [ ] 3.4 In `_WIKI/BLOOMMCP/README.md:82` (currently: "`BLOOM_STORAGE_BACKEND=local` opts
      into a fully-local (offline) mode instead"), drop the "(offline)" parenthetical (or
      replace it with the same data-locality phrasing) — this file already defers detail to
      `storage-backends.md` "to avoid the two docs drifting out of sync," so keep the edit
      minimal and consistent with that existing practice.
- [ ] 3.5 Run the new `test_bloommcp_local_mode_docs.py` test; confirm it passes.

## 4. Validate

- [ ] 4.1 Run `openspec validate fix-bloommcp-local-usage-telemetry --strict` and resolve any
      issues.
- [ ] 4.2 Run the bloommcp test suite the way CI actually runs it:
      `cd bloommcp && uv run --frozen --extra test pytest tests/ -m "not integration and not
      live_smoke" -v --tb=short`, and separately `uv run --extra test pytest tests/unit/test_bloommcp_local_mode_docs.py`
      from the repo root, to confirm no regressions beyond the files touched above.
- [ ] 4.3 Run this repo's formatting/lint checks on every changed file type: `ruff`/`black` for
      the two changed Python source files and the three changed/added test files, and
      `prettier` for the three changed Markdown doc files (`.pre-commit-config.yaml` runs
      prettier on `.md` files too) — e.g. `uv run pre-commit run --files <changed files>`.
- [ ] 4.4 Mark every task above `[x]` once done, as its own final commit
      (`docs(#641): mark tasks.md complete`), matching this repo's own precedent (e.g. the
      sibling #626 branch's closing commit of the same shape).
