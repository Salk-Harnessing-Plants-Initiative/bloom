## 1. `supabase_reader.py` documentation/warning accuracy

- [ ] 1.1a Write a failing test asserting `supabase_reader.py`'s **module docstring**
      names `data-access-roadmap.md`'s Tier 2 DB-direct rewrite as the retirement path and
      no longer claims removal is pending "the follow-up that migrates inputs into
      `bloommcp_input/`" (bloom PR #368, closed). Assert what the corrected text should
      say, not just the absence of the old phrase — a test that only checks
      `"bloommcp_input" not in docstring` would pass trivially without the Tier 2 mention
      actually being added.
- [ ] 1.1b Implement: rewrite the module docstring (lines 1-9) per 1.1a.
- [ ] 1.2a Write a failing test asserting the runtime **`_LOCAL_RAW_DEPRECATION`** message
      (lines 31-35) — the string actually passed to `warnings.warn` — names Tier 2 as the
      tracked retirement path and no longer describes the fallback as "promoted, not
      slated for removal" (that framing predates this proposal and contradicts having a
      tracked retirement path at all; it never mentioned the bucket, so this is a distinct
      fix from 1.1a, not the same edit applied twice). As with 1.1a, assert what the
      corrected text should say (names Tier 2), not just the absence of the old phrase —
      a test that only checks `"not slated for removal" not in message` would pass
      trivially without the Tier 2 mention actually being added.
- [ ] 1.2b Implement: rewrite `_LOCAL_RAW_DEPRECATION` per 1.2a. Keep the
      `DeprecationWarning` category and trigger condition (`source_label == "raw"`)
      unchanged.
- [ ] 1.3 No change to `raw_source_path`'s docstring (lines 76-92) — confirmed unrelated
      (path-traversal-security documentation, not a migration-plan citation). Leave as-is;
      do not touch it under this task.
- [ ] 1.4 Confirm the existing deprecation test
      (`bloommcp/tests/data_access/test_supabase_reader.py:~25`,
      `pytest.warns(DeprecationWarning)`) still passes unmodified — a named regression
      checkpoint proving the trigger condition didn't change, only the message.

## 2. Spec reconciliation (`bloommcp-experiment-read`)

- [ ] 2.1 Write the MODIFIED `ExperimentReader Port` requirement: correct the
      "Single-experiment read consumers go through the port" and "No consumer imports the
      storage writer or Supabase directly" scenarios' stale module names
      (`qc_tools.py`/`storage_tools.py`/`correlation_tools`/`tools/workflows/*`, all
      retired by `devendor-bloommcp-analysis`) to the current
      `sections/sleap_roots/analysis/*` locations, and state that `qc_inspect.py`'s
      provenance already routes through `_ports.raw_source_for` — shipped by #479/PR
      #526, not this change (no new scenario/test needed here; it's already covered by
      `test_source_csv_honors_local_root_only_mode`).
- [ ] 2.2 Write the MODIFIED `SupabaseReader Adapter` requirement: update its intro and
      deprecation-signal scenario to match the corrected message/plan reference, and add
      two distinct scenarios matching Task 1's two separate fixes — one for the module
      docstring no longer citing the closed bucket-migration plan, one for
      `_LOCAL_RAW_DEPRECATION` no longer using the "promoted, not slated for removal"
      framing.

## 3. Verification

- [ ] 3.1 `cd bloommcp && uv run --frozen --extra test pytest tests/ -m "not integration and not live_smoke"`
      — matches `.github/workflows/pr-checks.yml`'s CI invocation exactly (the
      `live_smoke`-marked tests under `tests/smoke/` need a live Docker Compose +
      Supabase + MinIO stack and must not be run as a stand-in for the CI gate). Full
      suite green.
- [ ] 3.2 `ruff check` (pinned per `.pre-commit-config.yaml`), `ruff format --check`, and
      `black --check` clean on all changed files.
- [ ] 3.3 `openspec validate retire-bloommcp-traits-dir-bypass --strict` passes.

## 4. Follow-up (process — no code commit)

- [x] 4.1 Post a comment on #476 (from this PR) linking `data-access-roadmap.md` Tier 2
      and this change, explicitly recommending #476 stay **open**, re-scoped to depend on
      that tier, rather than being closed when this PR merges — a concrete, checkable
      artifact instead of a free-floating "confirm at review time" reminder. See
      `design.md` Open Questions for the reasoning. Done:
      https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/476#issuecomment-5075068236
