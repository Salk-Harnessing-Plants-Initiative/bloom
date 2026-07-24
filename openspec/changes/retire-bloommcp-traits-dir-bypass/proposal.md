## Why

[#476](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/476) — `bloommcp/docs/roadmap.md` Tier 2 called for consolidating the read path onto
Supabase Storage (`bloommcp_input/`) via the `ExperimentReader` port and retiring the
legacy local `BLOOM_TRAITS_DIR` disk-read path. Two call sites still bypass the port:

- `qc_inspect.py:503` — `local_src = TRAITS_DIR / params.experiment`, building the
  provenance `source_csv` from a hard-coded `TRAITS_DIR` global instead of the active
  reader, exactly the bug #479's PR review independently found and fixed on its own
  (unmerged) branch for a different reason (fully-local-mode provenance). This proposal
  re-does that one-line fix on `staging` directly so it isn't hostage to #479's merge
  order.
- `bloommcp/src/bloom_mcp/data_access/supabase_reader.py` — `SupabaseReader`'s raw-tier
  read still comes from local `BLOOM_TRAITS_DIR` and says so in its own docstring
  (**"deprecated"**, `_LOCAL_RAW_DEPRECATION`, `raw_source_path`, lines 1-9/31-35/76-92).

Investigating this second site surfaced that it's not simply "unfinished cleanup":
`bloommcp/docs/data-access-roadmap.md` (DRAFT, Live-state facts) already traced the full
write path and confirmed **`bloommcp_input/` has no producer anywhere in the repo** —
nothing in `bloomcli/`, `web/`, or `supabase_client.py`'s public surface writes to that
bucket prefix. Two PRs attempting to fix this raw tier by wiring it to the bucket were
opened and then closed for exactly this reason: bloom PR #368 (read `bloommcp_input/`
directly) and PR #413 (add an upload path that writes there first). The roadmap's actual
plan (Tier 2, not yet filed/started, gated on a Postgres migration + Benfica's RPC-shape
review) is to rewrite `SupabaseReader`'s raw tier to query Bloom's Postgres directly by
`experiment_id` instead — at which point this local-disk fallback becomes moot on its own.
That same roadmap doc already cross-references #476 and recommends sequencing it
"alongside or after Tier 2," not as fully independent cleanup.

So `SupabaseReader`'s local-disk raw-tier read is genuinely load-bearing on the default
(Supabase) backend today — it is the only thing serving raw reads, not dead code —
and its own documentation is now stale: it still describes the closed bucket-migration
plan as the reason it'll go away. Left as-is, that stale docstring risks a third attempt
at the same rejected fix.

## What Changes

- **`qc_inspect.py`** — replace the hard-coded `local_src = TRAITS_DIR / params.experiment`
  / `source_csv=local_src if local_src.exists() else None` with
  `source_csv=_ports.raw_source_for(params.experiment)` (mirrors `_ports.start_run`,
  already the pattern `qc_clean.py` uses). Drop the now-unused
  `from bloom_mcp.experiment_utils import TRAITS_DIR` import and update the stale
  `# ... flows into TRAITS_DIR / experiment` comment at line 421.
- **`supabase_reader.py`** — correct the module docstring, `_LOCAL_RAW_DEPRECATION`
  message, and `raw_source_path`'s docstring so they name the actual tracked retirement
  path (`data-access-roadmap.md`'s Tier 2 DB-direct rewrite) instead of the closed
  bucket-upload plan. **No behavior change**: the `DeprecationWarning` still fires under
  the same condition (`source_label == "raw"`), and the raw-tier read still resolves from
  `BLOOM_TRAITS_DIR` exactly as today — wiring it to `read_input_csv`/`bloommcp_input/`
  instead would make raw reads 404 in every environment today (bucket confirmed empty),
  repeating #368/#413's mistake.
- **`bloommcp-experiment-read` spec** — MODIFIED, two requirements:
  - `ExperimentReader Port`: its "consumers go through the port" scenarios still name
    `qc_tools.py`/`storage_tools.py`/`correlation_tools`/`tools/workflows/*` — all retired
    by `devendor-bloommcp-analysis` (confirmed gone from `bloommcp/src/`). Reconciled to
    the current `sections/sleap_roots/analysis/*` locations, and added a scenario for the
    `qc_inspect.py` provenance fix above.
  - `SupabaseReader Adapter`: updated to describe the raw-tier fallback as an intentional
    interim adapter (not a bug awaiting an imminent fix) and to match the corrected
    deprecation-signal wording.

## Impact

- **Affected specs:** `bloommcp-experiment-read` (MODIFIED only — no ADDED/REMOVED).
- **Affected code:**
  - `bloommcp/src/bloom_mcp/sections/sleap_roots/analysis/qc_inspect.py`
  - `bloommcp/src/bloom_mcp/data_access/supabase_reader.py`
- **Affected tests:**
  - `bloommcp/tests/tools/test_qc_inspect_tool.py` — new regression test for the
    provenance source-path fix.
  - `bloommcp/tests/data_access/test_supabase_reader.py` — new test on the corrected
    message text; the existing `pytest.warns(DeprecationWarning)` test
    (line ~25) is a named regression checkpoint and must stay green unmodified.

## Scope / Non-Goals

- **Does not implement the DB-direct rewrite** (`data-access-roadmap.md` Tiers 1-3) — that
  program is separate, larger, DRAFT (pending Elizabeth approval), and gated on a Postgres
  migration + Benfica's RPC-shape review that hasn't started.
- **Does not touch `LocalReader` / `BLOOM_STORAGE_BACKEND=local`** — explicitly out of
  scope per the issue; that path stays exactly as-is.
- **Does not change `SupabaseReader`'s read behavior, return values, or resolution
  order** — only its documentation/warning text changes. The raw-tier local-disk read
  itself is unchanged.
- **Does not fully close #476's architectural ask.** This proposal resolves the one
  bypass that's safely, independently fixable today (`qc_inspect.py`) and stops the other
  (`supabase_reader.py`) from misdocumenting its own future — it does not retire
  `BLOOM_TRAITS_DIR` from the default read path, which is `data-access-roadmap.md` Tier
  2's job. Recommend #476 stay open, re-scoped to track that roadmap's Tier 2, rather than
  closed by this change — see `design.md` Open Questions.
