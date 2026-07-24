## 1. `qc_inspect.py` provenance fix

- [ ] 1.1a Write a failing regression test asserting `qc_inspect`'s persisted run
      `source_csv` resolves via the active reader (`_ports.raw_source_for`) rather than a
      hard-coded `TRAITS_DIR` path — e.g. inject a `FakeReader`/monkeypatched reader with a
      distinct input root (or set `BLOOM_EXPERIMENT_LOCAL_ROOT`) and assert the recorded
      `source_csv` follows it instead of `TRAITS_DIR`.
- [ ] 1.1b Implement: in `qc_inspect.py`, replace
      `local_src = TRAITS_DIR / params.experiment` /
      `source_csv=local_src if local_src.exists() else None` with
      `source_csv=_ports.raw_source_for(params.experiment)`; drop the now-unused
      `from bloom_mcp.experiment_utils import TRAITS_DIR` import (line 64); update the
      stale `# ... flows into TRAITS_DIR / experiment` comment (line 421) to describe the
      reader-routed guard instead.

## 2. `supabase_reader.py` documentation/warning accuracy

- [ ] 2.1a Write a failing test asserting `supabase_reader.py`'s **module docstring**
      names `data-access-roadmap.md`'s Tier 2 DB-direct rewrite as the retirement path and
      no longer claims removal is pending "the follow-up that migrates inputs into
      `bloommcp_input/`" (bloom PR #368, closed). Assert what the corrected text should
      say, not just the absence of the old phrase — a test that only checks
      `"bloommcp_input" not in docstring` would pass trivially without the Tier 2 mention
      actually being added.
- [ ] 2.1b Implement: rewrite the module docstring (lines 1-9) per 2.1a.
- [ ] 2.2a Write a failing test asserting the runtime **`_LOCAL_RAW_DEPRECATION`** message
      (lines 31-35) — the string actually passed to `warnings.warn` — names Tier 2 as the
      tracked retirement path and no longer describes the fallback as "promoted, not
      slated for removal" (that framing predates this proposal and contradicts having a
      tracked retirement path at all; it never mentioned the bucket, so this is a distinct
      fix from 2.1a, not the same edit applied twice).
- [ ] 2.2b Implement: rewrite `_LOCAL_RAW_DEPRECATION` per 2.2a. Keep the
      `DeprecationWarning` category and trigger condition (`source_label == "raw"`)
      unchanged.
- [ ] 2.3 No change to `raw_source_path`'s docstring (lines 76-92) — confirmed unrelated
      (path-traversal-security documentation, not a migration-plan citation). Leave as-is;
      do not touch it under this task.
- [ ] 2.4 Confirm the existing deprecation test
      (`bloommcp/tests/data_access/test_supabase_reader.py:~25`,
      `pytest.warns(DeprecationWarning)`) still passes unmodified — a named regression
      checkpoint proving the trigger condition didn't change, only the message.

## 3. Spec reconciliation (`bloommcp-experiment-read`)

- [ ] 3.1 Write the MODIFIED `ExperimentReader Port` requirement: correct the
      "Single-experiment read consumers go through the port" and "No consumer imports the
      storage writer or Supabase directly" scenarios' stale module names
      (`qc_tools.py`/`storage_tools.py`/`correlation_tools`/`tools/workflows/*`, all
      retired by `devendor-bloommcp-analysis`) to the current
      `sections/sleap_roots/analysis/*` locations, and add a scenario for the
      `qc_inspect.py` provenance fix (Task 1).
- [ ] 3.2 Write the MODIFIED `SupabaseReader Adapter` requirement: update its intro and
      deprecation-signal scenario to match the corrected message/plan reference, and add
      two distinct scenarios matching Task 2's two separate fixes — one for the module
      docstring no longer citing the closed bucket-migration plan, one for
      `_LOCAL_RAW_DEPRECATION` no longer using the "promoted, not slated for removal"
      framing.

## 4. Verification

- [ ] 4.1 `cd bloommcp && uv run --frozen --extra test pytest tests/ -m "not integration"`
      — full suite green.
- [ ] 4.2 `ruff check` (pinned per `.pre-commit-config.yaml`), `ruff format --check`, and
      `black --check` clean on all changed files.
- [ ] 4.3 `openspec validate retire-bloommcp-traits-dir-bypass --strict` passes.

## 5. Follow-up (process — no code commit)

- [ ] 5.1 At review, confirm with Evelyn/Elizabeth whether #476 should stay open
      (re-scoped to depend on `data-access-roadmap.md` Tier 2) or be split into a new
      tracking issue for the `SupabaseReader` DB-direct rewrite — see `design.md` Open
      Questions. Do not close #476 outright when this change merges without that
      confirmation.
