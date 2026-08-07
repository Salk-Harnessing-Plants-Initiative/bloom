## Why

`SupabaseReader.list_experiments()` (`bloommcp/src/bloom_mcp/data_access/supabase_reader.py:246-300`)
hangs for 4+ minutes on staging, confirmed via direct MCP calls and `pg_stat_activity`. Root cause: it
does one cheap `SELECT id, name FROM cyl_experiments` (224 rows today), then for **each** row calls the
`get_experiment_traits` RPC (Tier 1, [bloom#546](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/546))
with no source/run pin, fetching **every** raw trait row for that experiment just to compute
`len(set(plant_id))`/`len(set(trait_name))` client-side, then discards the rows. `cyl_scan_traits` has
28.8M rows; only 9 of the 224 experiments have any reachable data, but `experiment_id=1` alone
contributes 13.8M of them — that single call, fetched and discarded over the wire, is plausibly the
dominant cost, not the 224x fan-out by itself. This is one level up from Tier 1's own fix (which solved a
different N+1: per-trait round trips *within* one experiment); Tier 2's `list_experiments()` rewrite
([bloom#551](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/551), PR #557) introduced
this per-experiment fan-out and it wasn't flagged as a perf risk at that PR's review time.

Separately, `bloom_mcp/supabase_client.py`'s `get_postgrest_client()`/`call_rpc()` set no *explicit,
deliberately chosen* timeout — `supabase-py` 2.31.0 defaults `ClientOptions.postgrest_client_timeout` to
120s even with no override (confirmed by reading the installed package, not assumed), which already
bounds any *single* RPC call, but nobody chose that number for this use case and it's generous enough
that 224 sequential calls can still add up to a multi-minute wall-clock hang with no single call ever
timing out. This program tracks bloom#625 and bundles both the query fix and a deliberately-chosen,
overridable timeout, per the issue's own bundling decision.

## What Changes

- **New `get_experiment_summary_counts(experiment_id_ bigint DEFAULT NULL, source_id_ bigint DEFAULT
NULL, run_id_ text DEFAULT NULL) RETURNS TABLE(experiment_id bigint, n_plants int, n_traits int)`** — a
  SQL function (not a view, so it can take the same `source_id_`/`run_id_` pin parameters
  `get_experiment_traits` does) that aggregates `COUNT(DISTINCT plant_id)`/`COUNT(DISTINCT trait_name)`
  server-side, reusing `get_experiment_traits`'s exact join chain and latest/pin-source/pin-run
  disjunction against `cyl_scan_traits_source` — **not** a literal join to the `cyl_scan_traits_latest`
  view (which has no `plant_id`/`experiment_id` column reachable), so the unpinned case's semantics match
  `load_experiment`'s latest-selection byte-for-byte. `bloommcp`'s only caller
  (`list_experiments()`) always invokes it with all three arguments `NULL`; the `experiment_id_`/
  `source_id_`/`run_id_` parameters exist so a separate, already-flagged-as-out-of-scope
  source-selection effort can reuse this same function for one-experiment, pinned-source counts instead
  of needing a second one (see bloom#625's "Related" section).
- **Rewrite `list_experiments()`** to call this RPC once (`{}`/all-`NULL`) instead of 224 per-experiment
  `get_experiment_traits` calls, then merge the result by `experiment_id` onto the existing cheap
  `cyl_experiments` listing — **defaulting to zero counts for any experiment the RPC returns no row for**
  (the aggregate's `GROUP BY` only emits a row for experiments with at least one matching trait row;
  today 215 of 224 have none). This is a real behavior change from today's per-row catch-and-skip: a
  bulk-call failure now fails `list_experiments()` outright (see design.md Decision D4) rather than
  silently excluding whichever experiment happened to be mid-loop.
- **Add a deliberately-chosen, overridable timeout** to `get_postgrest_client()`, mirroring
  `get_storage_client()`'s existing `timeout_seconds` keyword-only parameter (same `ClientOptions`
  pattern, different field — `postgrest_client_timeout` vs. `storage_client_timeout`), so a slow/blocked
  query fails with a clear, structured error at a bound this program chose, not merely inherits
  `supabase-py`'s 120s package default. `call_rpc()` is otherwise unchanged (still calls
  `get_postgrest_client()` with no override, i.e. adopts the new bounded default).
- **Forward-only migration** (`supabase/migrations/20260807000000_get_experiment_summary_counts.sql`) +
  companion manual rollback under `supabase/rollbacks/`; explicit `REVOKE EXECUTE ... FROM PUBLIC` +
  `GRANT EXECUTE ... TO bloom_agent, bloom_user, bloom_admin, authenticated` (matching Tier 1's
  round-1-review-tightened posture, not `get_scan_traits`'s older implicit-PUBLIC default); hand-edit the
  five tracked `database.types.ts` copies.

**Decisions needing @blm3886 (Benfica)'s review before this ships** (per bloom#625's own "Decisions
needed" section, same gate as Tier 1's RPC-shape review):

1. **Function signature type: `bigint`, not the issue's literal `int`.** Every sibling function and the
   underlying columns (`cyl_experiments.id`, `get_experiment_traits`'s params) are `bigint`; using `int`
   would be a new, inconsistent type on the same tables. See design.md Decision D1.
2. **Timeout value.** Needs benchmarking against a realistic `load_experiment` call on the largest
   current experiment, per bloom#625's own proposal — not picked arbitrarily here. See design.md Decision
   D5 (left open, with a placeholder default and an explicit task to benchmark before merge).

## Impact

- Affected specs:
  - `cyl-trait-read` (existing capability, additive) — new `get_experiment_summary_counts` requirement,
    sibling to `add-bulk-trait-read-rpc`'s `get_experiment_traits`/`list_experiment_trait_sources`.
  - `bloommcp-experiment-read` (existing capability, modified) — `list_experiments()`'s "List experiments
    enumerates available inputs" scenario gets a stronger, single-round-trip + latest-semantics-matching
    guarantee; new requirement for the bounded RPC timeout on `SupabaseReader`'s underlying client.
- Affected code:
  - `supabase/migrations/` (new migration), `supabase/rollbacks/` (companion rollback), five tracked
    `database.types.ts` copies (same five as `add-bulk-trait-read-rpc`).
  - `bloommcp/src/bloom_mcp/data_access/supabase_reader.py` (`list_experiments()` rewritten).
  - `bloommcp/src/bloom_mcp/supabase_client.py` (`get_postgrest_client()` gains a `timeout_seconds`
    keyword-only override with a bounded, chosen default; `test_supabase_client.py`'s
    `test_client_accessors_accept_no_caller_credential_parameter` updated to match, since it asserts on
    the accessor's full parameter set, not just caller-credential params specifically).
  - `tests/integration/test_cyl_experiment_summary_counts.py` (new); `bloommcp/tests/data_access/
test_supabase_reader.py` (`list_experiments()` tests rewritten — the per-experiment-failure test no
    longer has meaning once there's one bulk call, not 224); `bloommcp/tests/conftest.py`'s
    `FakeSupabaseDB.call_rpc` dispatcher (new branch for `get_experiment_summary_counts`).
- Backward compatible: purely additive at the SQL layer (new function only; `get_experiment_traits`,
  `get_scan_traits`, and every existing view/function are untouched). `list_experiments()`'s Python-level
  behavior changes (see above) but its `ExperimentSummary` return shape does not.
- **Non-goals (explicitly out of scope, per bloom#625's "Related" section):** the source-discovery/
  source-pinning MCP-facing capability (`SourceSelectable.list_sources()`/`resolve_source()` already
  exist but no tool exposes either) is a separate, larger effort with its own future OpenSpec proposal —
  this change only ensures `get_experiment_summary_counts`'s signature can be reused by it later.
