## Context

Changes D+E (merged #371) made `insert_cyl_result_envelope` the sole writer of `cyl_scan_traits` and
mint one `cyl_trait_sources` row per pipeline run (`idempotency_key` UNIQUE; provenance in `metadata`
jsonb). Schema relevant here:

- `cyl_scan_traits(id, scan_id NOT NULL, value real, source_id BIGINT NULL → cyl_trait_sources(id),
trait_id INT → cyl_traits(id))`, `UNIQUE (scan_id, source_id, trait_id)`, index on `scan_id`.
- `cyl_trait_sources(id, name NOT NULL, metadata jsonb, idempotency_key text UNIQUE)`. **There is no
  `created_at`/timestamp column** (confirmed) — so "latest" must be `max(id)`, which is monotonic
  because identity ids increase as reprocessing mints new sources.
- `cyl_traits(id, name UNIQUE)` — the trait-name registry.

The contract `Provenance` (pinned `v0.1.0a2`, `contracts/schema/result_envelope.schema.json`) carries
an **optional, nullable** `pipeline_run_id` that the schema documents as a batch key: _"One per-scan
result: 1 envelope : 1 source row : 1 scan"_, and one pipeline run emits many such envelopes that share
`pipeline_run_id`. The write-back RPC stores the whole provenance in `metadata` and sets
`cyl_trait_sources.name = coalesce(metadata->>'pipeline_run_id', 'sleap-roots:' || idempotency_key)`.

Current read surface (all source-blind):

- `get_scan_traits(experiment_id_ BIGINT, trait_name_ TEXT)` — plain SQL, SECURITY INVOKER, joins
  `cyl_scan_traits → cyl_traits`; sole caller `web/app/app/traits/[speciesId]/[experimentId]/TraitExplorer.tsx:194`.
- `cyl_scan_trait_names` = `SELECT name FROM cyl_traits` — no runtime consumer (only generated types).
- `cyl_trait_by_experiment_wave` (20260521140000) aggregates `cyl_scan_traits` and is the heavily-used
  read path (langchain `cyl_tools.py`, web traits page) — **out of scope here** (deferred follow-up).

## Goals / Non-Goals

- **Goals:** latest-source-by-default reads with no duplicate rows and no cross-source mixing; a single
  shared substrate so the "latest = max(id)" rule is defined once; optional pin-by-source and
  group-by-run; full backward compatibility for the 2-arg caller; forward-only migration + rollback.
- **Non-Goals (one broadened sibling issue, "repoint source-blind cyl trait readers"):** rebuilding
  `cyl_trait_by_experiment_wave` (double-counts across sources; consumed by langchain + web traits
  page); repointing `langchain/tools/cyl_tools.py` `get_scan_traits_tool` (source-blind direct read of
  `cyl_scan_traits`, and **already broken** — it selects `trait_name`, dropped from `cyl_scan_traits`
  in 20241119) at the new scan-grain surface, and refreshing `context_tools.py` `CONTEXT_CYL` (stale
  schema: lists the dropped `trait_name`, omits `source_id`); image-grain reads (change B); a UI for
  source selection. The substrate view is built so these repoints are trivial follow-ups.

## Decisions

- **D1 — "latest" = `max(source_id)` per scan, computed once in `cyl_scan_traits_source.is_latest`
  with a window function.** `is_latest := source_id IS NOT DISTINCT FROM max(source_id) OVER (PARTITION
BY scan_id)`. A window aggregate is a single pass (vs. a correlated subquery per row), which matters
  for the predicate-less `cyl_scan_trait_names` consumer; the semantics are identical. `IS NOT DISTINCT
FROM` makes the NULL≡NULL case true, so a **legacy scan whose rows all have `source_id IS NULL`**
  (window max = NULL) keeps its rows (backward compat). When a scan has any non-null source, that
  scan's window max is that non-null value for every row in the partition, so the NULL-source legacy
  rows are correctly superseded. The view also exposes `trait_name` (LEFT JOIN `cyl_traits`) so the
  function and the names view read it from the substrate and the view is self-sufficient for a future
  scan-grain repoint of source-blind readers.
- **D2 — latest is chosen at SCAN grain, then we take exactly that source's traits — no per-trait
  "latest".** If the latest run produced fewer traits than an older run, the missing traits are simply
  absent (we do **not** backfill from older sources). This is the explicit "no cross-source mixing"
  guarantee; a per-`(scan, trait)` "latest" would Frankenstein values across runs.
- **D3 — `get_scan_traits` is a single 4-arg function, old 2-arg dropped.** `CREATE OR REPLACE` cannot
  add a parameter, and keeping two overloads makes a 2-named-arg PostgREST call ambiguous (PGRST203).
  Dropping the 2-arg and creating one function with `source_id_ BIGINT DEFAULT NULL, run_id_ TEXT
DEFAULT NULL` means exactly one candidate; a 2-arg call binds the defaults (verified over PostgREST
  HTTP, not only direct SQL — the ambiguity only manifests in PostgREST's named-arg resolution).
  Implemented in PL/pgSQL (needs `RAISE` for the both-args guard), `STABLE` (an intentional improvement
  over the legacy function's default `VOLATILE`; it is read-only), SECURITY INVOKER (unchanged
  posture). It reads `trait_name`/`value` from `cyl_scan_traits_source src` and joins
  `cyl_scans → … → accessions` for the other output columns. The three-mode disjunction MUST be wrapped
  as a **single parenthesized AND-operand** so the `source_id_`/`run_id_` branches stay conjoined with
  the experiment+trait filter (`AND` binds tighter than `OR`; dropping the parens leaks rows from other
  experiments):
  `WHERE cyl_experiments.id = experiment_id_ AND src.trait_name = trait_name_ AND (
   (source_id_ IS NULL AND run_id_ IS NULL AND src.is_latest)
   OR (source_id_ IS NOT NULL AND src.source_id = source_id_)
   OR (run_id_ IS NOT NULL AND src.source_id = (SELECT max(s2.source_id)
         FROM cyl_scan_traits_source s2
         WHERE s2.scan_id = src.scan_id AND s2.trait_id = src.trait_id
           AND s2.pipeline_run_id = run_id_)) )`,
  with a leading `IF source_id_ IS NOT NULL AND run_id_ IS NOT NULL THEN RAISE EXCEPTION`. The `run_id_`
  branch selects each `(scan, trait)`'s **latest delivery within that run** (`max(source_id)` among that
  run's rows) so a run with duplicate deliveries yields at most one row per `(scan, trait)` (no
  `is_latest` filter — "as of run X" may return a scan's superseded run values by design). Result
  columns keep their exact names/types/order, including `trait_value FLOAT` (the implicit `real→float8`
  widening of `value`, which stays nullable) and `date_scanned` (the raw `cyl_scans.date_scanned`
  `date` coerced to `text` — not reformatted via `to_char`, which would change the string the TS caller
  parses). The `ORDER BY` MUST be **table-qualified** — `ORDER BY accessions.name, cyl_plants.id` — not
  the legacy `ORDER BY accession_name, plant_id`: in a `LANGUAGE plpgsql` `RETURNS TABLE` function the
  OUT columns are variables, and `cyl_scans.plant_id` is in scope, so bare `plant_id` is ambiguous and
  (under the default `variable_conflict = error`) raises on **every** call. Qualify all sort/select
  identifiers to their source tables to avoid OUT-variable/column collisions.
- **D4 — `cyl_scan_traits_latest` and `cyl_scan_trait_names` are thin views on the substrate.**
  `cyl_scan_traits_latest := SELECT scan_id, trait_id, trait_name, source_id, value FROM
cyl_scan_traits_source WHERE is_latest`. `cyl_scan_trait_names := SELECT DISTINCT trait_name FROM
cyl_scan_traits_latest WHERE trait_name IS NOT NULL ORDER BY trait_name`, emitted `AS name` so the
  view's output column stays `name` (its stable contract, unchanged from the prior definition). The
  `IS NOT NULL` guard stops an unresolved legacy row with `NULL` `trait_id` from surfacing a `NULL`
  name. No duplicated max() logic anywhere.
- **D5 — Grants are issued explicitly per object; `bloom_*` roles are load-bearing.** Each view uses
  `WITH (security_invoker = on)` and an explicit `GRANT SELECT … TO bloom_agent, bloom_user,
bloom_admin, authenticated`, mirroring the `cyl_trait_by_experiment_wave` precedent — but the grants
  that actually gate the PostgREST read path are the **`bloom_*`** ones (the session role is
  `bloom_user`/`bloom_agent` via `custom_access_token_hook`, not `authenticated`), so role tests use
  `SET LOCAL ROLE bloom_user`/`bloom_agent` and assert reads of the **joined** `source_name` column
  (a `security_invoker` view over a second table fails for a role lacking `SELECT` on
  `cyl_trait_sources` even if the view grant succeeded). Critically, recreating `cyl_scan_trait_names`
  via `DROP VIEW … CREATE VIEW …` **loses** its prior object-level grant (it relied on the one-time
  `GRANT SELECT ON ALL TABLES` snapshot), so the migration MUST re-issue `GRANT SELECT ON
public.cyl_scan_trait_names …` or the view becomes unreadable. `get_scan_traits` keeps the prior
  default `PUBLIC` `EXECUTE` (the legacy 2-arg had no explicit grant); the migration MUST NOT copy the
  adjacent write-back RPC's `REVOKE EXECUTE … FROM PUBLIC` block, which would break the read path. No
  RLS or write policy changes.
- **D6 — `pipeline_run_id` is exposed but treated as graceful-degrade.** It is optional/nullable in the
  contract; when a producer omits it the source's `name` is a per-scan `sleap-roots:<key>` fallback, so
  `run_id_`/`pipeline_run_id` grouping only returns rows for data whose producer set it. Latest and
  pin-by-source paths are unaffected. The read path reads the one provenance attribute it needs
  (`pipeline_run_id`); it does not otherwise depend on the opaque `metadata` shape.

## Migration / Rollback

Single forward-only migration `supabase/migrations/<ts>_cyl_trait_read_source_aware.sql`, in
dependency order inside one transaction: (1) `CREATE OR REPLACE VIEW cyl_scan_traits_source` (+ grants);
(2) `CREATE OR REPLACE VIEW cyl_scan_traits_latest` (+ grants); (3) `DROP VIEW IF EXISTS
cyl_scan_trait_names; CREATE VIEW cyl_scan_trait_names …` (+ grants — the DROP+CREATE is needed here
because the column set changes from the prior definition); (4) `DROP FUNCTION IF EXISTS
public.get_scan_traits(bigint, text); CREATE FUNCTION public.get_scan_traits(bigint, text, bigint, text)`.
Additive/non-destructive — no table or data is dropped or rewritten, so a single `supabase db push`
applies it. Each created/recreated view re-issues its explicit `GRANT SELECT`. Companion
`supabase/rollbacks/<ts>_cyl_trait_read_source_aware_rollback.sql` drops the two new views (dependents
before the substrate), `DROP`s the four-argument `get_scan_traits` **before** restoring the prior
`get_scan_traits(bigint, text)` body verbatim (`LANGUAGE SQL`) — otherwise both overloads coexist and
reintroduce the PGRST203 ambiguity this change removes — and restores `cyl_scan_trait_names = SELECT
name FROM cyl_traits`. The timestamp must be strictly greater than `20260630180000` (e.g.
`20260701000000`). All five tracked `database.types.ts` copies are regenerated (hand-edited mirroring
#371, since the local dev DB has a known migration gap; CI's `compose-health-check` is the authoritative
signal that the migration applies), keeping the two new function args optional so the sole 2-arg TS
caller still typechecks under `tsc --noEmit`.

## Risks / Trade-offs

- **`is_latest` is a `max(source_id) OVER (PARTITION BY scan_id)` window aggregate** in
  `cyl_scan_traits_source` — a single partition/sort pass, not a per-row correlated subquery; the
  predicate-less `cyl_scan_trait_names` consumer scans the whole table, mitigated by the existing
  `cyl_scan_traits(scan_id)` index and modest scan-grain volume. `is_latest` is recomputed by readers
  (a view column, not materialized) — acceptable at this scale; the substrate view is the single place
  to optimize (e.g. materialize) if it ever isn't.
- **The `run_id_` branch uses one correlated `max(source_id)` subquery** (evaluated only when `run_id_`
  is set) to pick the latest delivery within a run — bounded by the `(scan, trait)` rows sharing that
  run; acceptable.
- **Dropping then recreating `get_scan_traits`** is atomic within the migration transaction, so no
  window where the function is missing for live callers. The two new views use `CREATE OR REPLACE VIEW`
  so the migration body is re-runnable (idempotent).
- **Known coupling — `cyl_scan_traits_source` reads `cyl_trait_sources.metadata`** (for
  `pipeline_run_id`), so the change-A break-glass rollback
  (`supabase/rollbacks/20260609000000_add_cyl_trait_source_provenance_rollback.sql`), which
  `DROP COLUMN metadata`, must use `CASCADE` — otherwise it errors with `DependentObjectsStillExist`
  while these read-path views exist. That rollback therefore cascade-drops the three read-path views;
  restore them by re-running this migration. A regression test
  (`test_change_a_rollback_cascades_read_views`) pins this invariant. To roll back _this_ change alone,
  use its own rollback, which drops the views before touching the base tables.

## Testing (TDD)

Integration tests in `tests/integration/test_cyl_read_path.py` using the `pg_conn` fixture
(`supabase_admin`, BYPASSRLS, per-test rollback). Seed two sources for one scan by calling
`insert_cyl_result_envelope` twice with the same `image_ids` but different `idempotency_key`,
`pipeline_run_id`, and trait values; for `get_scan_traits` build the full
species→experiment→wave→plant→accession→scan→images chain. Oracle: default returns the latest source's
values (no duplicate rows, no mixing); pinning the older `source_id` returns the older values; `run_id_`
returns a run's values across scans; both args error; a 2-arg call still works; a legacy NULL-source
scan is still returned; `cyl_scan_trait_names` lists distinct latest names. Tests are written RED first.

## Open Questions

- None blocking. Producer population of `pipeline_run_id` (D6) is a sleap-roots-pipeline concern tracked
  on the roadmap, not by this change.
