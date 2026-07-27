## Why

[#476](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/476) — `bloommcp/docs/roadmap.md` Tier 2 called for consolidating the read path onto
Supabase Storage (`bloommcp_input/`) via the `ExperimentReader` port and retiring the
legacy local `BLOOM_TRAITS_DIR` disk-read path. The issue named two call sites still
bypassing the port:

- `qc_inspect.py:503` (`local_src = TRAITS_DIR / params.experiment`) — **already resolved,
  not part of this change.** #479's PR review independently found and fixed this exact
  line (for a different reason — fully-local-mode provenance), and that work merged into
  `staging` as [PR #526](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/pull/526)
  before this proposal was opened. Verified directly against current `staging`
  (`29edec9`, #526's merge commit): `qc_inspect.py` already reads
  `source_csv=_ports.raw_source_for(params.experiment)`, the stale `TRAITS_DIR` import is
  already gone, and a regression test
  (`test_source_csv_honors_local_root_only_mode`,
  `bloommcp/tests/tools/test_qc_inspect_tool.py`) already covers it. An earlier draft of
  this proposal targeted this site as new work — a review round (5 independent agents)
  caught that the draft had gone stale after #526 merged out from under it; this revision
  drops that scope rather than re-doing already-merged work. See `design.md`'s
  Reconciliation note.
- `bloommcp/src/bloom_mcp/data_access/supabase_reader.py` — `SupabaseReader`'s raw-tier
  read still comes from local `BLOOM_TRAITS_DIR`. Two of its three doc sites are stale in
  different ways, not the same way: the **module docstring** (lines 1-9) explicitly says
  the `DeprecationWarning` exists "so the follow-up that migrates inputs into
  `bloommcp_input/` can remove it" — citing the specific bucket-migration plan that's since
  been closed (see below). `_LOCAL_RAW_DEPRECATION` (lines 31-35), the message actually
  raised at runtime, doesn't mention the bucket at all — it says "the path is promoted,
  not slated for removal," which is a *different* problem: that framing contradicts this
  proposal's own decision to treat the fallback as having a tracked Tier 2 retirement path.
  `raw_source_path`'s docstring (lines 76-92) is pure path-traversal-security documentation
  (guards `../secrets.csv`-style escapes) — unrelated to either issue, no change needed.

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
(Supabase) backend today — it is the only thing serving raw reads, not dead code — and
its documentation has two distinct problems: the module docstring still describes the
closed bucket-migration plan as the reason it'll go away (risking a third attempt at the
same rejected fix), and the runtime deprecation message's "not slated for removal"
framing simply doesn't match this proposal's decision that the fallback does have a
tracked retirement path (Tier 2).

## What Changes

- **`qc_inspect.py`** — no change; already fixed by #479/PR #526 (see Why).
- **`supabase_reader.py`** — two distinct, per-site doc fixes (not one blanket edit):
  - The **module docstring** (lines 1-9): stop citing the closed bucket-upload plan as
    the removal trigger; name `data-access-roadmap.md`'s Tier 2 DB-direct rewrite instead.
  - **`_LOCAL_RAW_DEPRECATION`** (lines 31-35): drop the "promoted, not slated for
    removal" framing — it contradicts the fallback having a tracked retirement path — and
    name Tier 2 there too.
  - `raw_source_path`'s docstring (lines 76-92) is unrelated (path-traversal security) and
    is **not** touched by this change.
  **No behavior change**: the `DeprecationWarning` still fires under the same condition
  (`source_label == "raw"`), and the raw-tier read still resolves from `BLOOM_TRAITS_DIR`
  exactly as today — wiring it to `read_input_csv`/`bloommcp_input/` instead would make
  raw reads 404 in every environment today (bucket confirmed empty), repeating #368/#413's
  mistake.
- **`bloommcp-experiment-read` spec** — MODIFIED, two requirements:
  - `ExperimentReader Port`: its "consumers go through the port" scenarios still name
    `qc_tools.py`/`storage_tools.py`/`correlation_tools`/`tools/workflows/*` — all retired
    by `devendor-bloommcp-analysis` (confirmed gone from `bloommcp/src/`). Reconciled to
    the current `sections/sleap_roots/analysis/*` locations, and to state that
    `qc_inspect.py`'s provenance already routes through `_ports.raw_source_for` (shipped
    by #479/PR #526, not this change).
  - `SupabaseReader Adapter`: updated to describe the raw-tier fallback as an intentional
    interim adapter (not a bug awaiting an imminent fix) and to match the corrected
    deprecation-signal wording.

## Impact

- **Affected specs:** `bloommcp-experiment-read` (MODIFIED only — no ADDED/REMOVED).
- **Affected code:**
  - `bloommcp/src/bloom_mcp/data_access/supabase_reader.py` (doc/warning text only)
- **Affected tests:**
  - `bloommcp/tests/data_access/test_supabase_reader.py` — new tests on the corrected
    doc/message text (one per site — see `tasks.md`); the existing
    `pytest.warns(DeprecationWarning)` test (line ~25) is a named regression checkpoint
    and must stay green unmodified.
  - `bloommcp/tests/tools/test_qc_inspect_tool.py` — **no new test needed**; the
    provenance-routing coverage this proposal would have asked for
    (`test_source_csv_honors_local_root_only_mode`) already exists, shipped by #479/PR
    #526.

## Scope / Non-Goals

- **Does not implement the DB-direct rewrite** (`data-access-roadmap.md` Tiers 1-3) — that
  program is separate, larger, DRAFT (pending Elizabeth approval), and gated on a Postgres
  migration + Benfica's RPC-shape review that hasn't started.
- **Does not touch `LocalReader` / `BLOOM_STORAGE_BACKEND=local`** — explicitly out of
  scope per the issue; that path stays exactly as-is.
- **Does not change `SupabaseReader`'s read behavior, return values, or resolution
  order** — only its documentation/warning text changes. The raw-tier local-disk read
  itself is unchanged.
- **Does not fully close #476's architectural ask.** Of the issue's two named bypasses,
  `qc_inspect.py`'s is already resolved (via #479/PR #526, not this change); this proposal
  only stops `supabase_reader.py` from misdocumenting its own future. It does not retire
  `BLOOM_TRAITS_DIR` from the default read path, which is `data-access-roadmap.md` Tier
  2's job. Recommend #476 stay open, re-scoped to track that roadmap's Tier 2, rather than
  closed by this change — see `design.md` Open Questions.
- **Residual risk, stated explicitly (not left implicit in "load-bearing interim
  adapter"):** keeping the local-disk raw-tier fallback alive indefinitely assumes a
  Supabase-backend deployment's `BLOOM_TRAITS_DIR` actually contains every experiment a
  user might request a raw read for. If it doesn't, those reads 404 silently, and will
  keep doing so indefinitely — `data-access-roadmap.md` Tier 2 (the real fix) isn't filed
  yet and has no timeline. This proposal does not resolve that risk; it only stops the
  docs from claiming a fix is imminent when it isn't.
