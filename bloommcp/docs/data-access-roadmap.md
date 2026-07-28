# bloommcp DB-direct trait reads — roadmap

**Status: DRAFT — pending adversarial roadmap-level review + Elizabeth approval.**

**Origin.** `bloommcp/docs/roadmap.md`'s Deferred line names this item ("DB-direct read
adapter (sub-project `#2`)") with no further tiering — this doc is that tiering.
`sleap-roots-pipeline/docs/bloom-integration/roadmap.md` §"Track B" states the naming
bridge: _"that spec's 'integration sub-project `#2`' = tier **A2** above — A2 gates the
bloom-mcp data-access layer."_ **Tier 1 here is gated on A2** (see Live-state facts).

**Design (source of truth, corrected below):** vault
`docs/superpowers/specs/2026-06-04-bloom-mcp-data-access-design.md` — the original
read-only data-access design, written before the Phase 2 vertical slice shipped its own
`ExperimentReader` port. Related: the master spec
`2026-05-11-metcalf-2026-evelyn-bloom-mcp-design.md` and
`2026-06-15-bloom-mcp-phase2-persistence-design.md` (defines the **real**
`ExperimentReader` port this program targets — see Tier 2). The original design's own
framing — _"the refactored bloom_mcp will query Bloom (Supabase/Postgres) directly by
`experiment_id` **instead of** reading exported CSVs"_ — is exactly this program's
reframing below; the deployed Phase 2 slice shipped an interim CSV-based `SupabaseReader`
because the DB-side read surface (A2) wasn't ready yet, not because the plan changed.

**Program: fix `SupabaseReader` in place, don't add a third reader.** `SupabaseReader`'s
name already claims it reads from "our Supabase" — today it actually reads a CSV out of
a Storage **bucket** (`bloommcp_input/`), not Bloom's relational database. That's the
defect this program corrects: **`SupabaseReader`'s raw-tier fetch is rewritten to query
Bloom's Postgres directly by `experiment_id`**, so "supabase" mode finally means what its
name says. `LocalReader` (offline/dev, local CSV files) is untouched. **There is no new
adapter class and no new backend-selection mechanism** — `BLOOM_STORAGE_BACKEND`'s
existing binary `local`/`supabase` switch stays exactly as it is; only what `supabase`
mode does internally changes. Built tier-by-tier per the `roadmap-driven-pipeline`
workflow: each tier is one OpenSpec PR, TDD, oracle-first.

## Questions for Benfica (@blm3886)

The asks below are pulled out of the Live-state facts and Tiers sections so they're easy to
act on without reading the whole doc. Everything else here is optional context.

1. ~~**Sequencing on `supabase_reader.py`/`experiment_utils.py::load_experiment_data`.**~~
   **Resolved 2026-07-23** — Benfica closed both
   [#368](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/pull/368) and
   [#413](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/pull/413) when
   approving this roadmap: reading `cyl_*` directly makes #368's CSV path redundant, and
   the upload path isn't being pursued, so Tier 2 now owns `supabase_reader.py`'s raw tier
   cleanly with no coordination needed.
2. **Tier 1's RPC shape** (a bulk long-format RPC vs. a PostgREST embedded-join query for
   fetching an experiment's full trait set in one round trip) needs her review before it
   ships (confirmed 2026-07-23), same gate as the shipped
   `20260701000000_cyl_trait_read_source_aware.sql` migration.
3. ~~**Re-confirm the 2026-06-09 auth decision still holds**~~ **Confirmed 2026-07-23**
   (Benfica, on this roadmap's approval): shared `bloom_agent` role, no per-user
   token/RLS for trait reads still holds.

Separately, not blocking this roadmap: issue #406 (verified per-user identity + a
`bloommcp_usage` table) is still awaiting your reply to the two design questions from my
2026-07-07 review comment there — flagging since it's in the same neighborhood, not
re-asking here.

## Live-state facts (verified 2026-07-21)

- **Gate status: A2 is nearly, not fully, done.** Per the pipeline roadmap: read-path
  ✅ (bloom PR #373), `bloom cyl` ingest CLI ✅ (bloom PR #408), D re-pin a2→a3 ✅ (bloom
  PR #399). **Remaining, both off the critical path for this gate:** Box backfill
  (⬜, [sleap-roots-pipeline#19](https://github.com/talmolab/sleap-roots-pipeline/issues/19))
  and change B — `source_id` FK on `cyl_image_traits`
  ([salk-bloom#295](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/295),
  deferred, no image traits yet). Neither blocks a trait-read adapter (both are write-side/image-trait
  concerns). **Tier 1 below can start now.**
- **The vault design doc's two "corrections" are stale again — corrected here:**
  1. It says the MCP "is not read-only today — it writes versioned outputs via
     `AnalysisWriter`." **`AnalysisWriter` is now fully dead code, and has been deleted**
     ([bloom#487](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/487)) —
     grepped: no live tool imports it, a dedicated import-guard
     test (`tests/test_persistence_import_guard.py`) forbids it, and
     `result_store/supabase_store.py` explicitly avoids calling `AnalysisWriter.commit()`
     because it "hand-rolls a provenance-lossy entry." `SupabaseResultStore` (the real
     `ResultStore` port adapter) fully superseded it. **Don't design around
     `AnalysisWriter` existing.**
  2. §4 proposes two new RPCs — `get_experiment_traits(experiment_id_, source_id_)` (bulk,
     long-format) and `list_experiment_trait_sources(experiment_id_)`. **Neither was
     built.** The read-path migration that actually shipped
     (`supabase/migrations/20260701000000_cyl_trait_read_source_aware.sql`, confirmed by
     direct read this session) delivered `cyl_scan_traits_source` / `cyl_scan_traits_latest`
     views + a **4-arg, per-trait** `get_scan_traits(experiment_id_, trait_name_, source_id_,
run_id_)` — not a bulk fetch. **This is the concrete missing piece** (Tier 1).
     `bloom_agent`/`bloom_user`/`bloom_admin`/`authenticated` have confirmed `SELECT` grants
     on all three read objects (verified in the migration text); `get_scan_traits` is
     `SECURITY INVOKER`, not definer — Tier 1's PR should confirm `bloom_agent`'s grants on
     the underlying join tables (`cyl_scans`, `cyl_waves`, `cyl_plants`, `accessions`,
     `species`, `cyl_experiments`) cover this path too, since invoker-security functions
     don't inherit a definer's privileges.
- **Scale motivating Tier 1:** [bloom#483](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/483)'s
  cylinder fixture is **129 samples × 880 raw trait columns / 123 × 649 post-QC**. One
  `get_scan_traits` call per trait would mean 649–880 round trips to load one experiment —
  not viable for a wide-pivot read.
- **`bloommcp_input/` has no producer today — confirmed by tracing the write path, not
  assumed.** The bucket migration
  (`supabase/migrations/20260605000000_create_bloommcp_data_bucket.sql`) grants
  `bloom_agent` INSERT/UPDATE on `bloommcp-data` (both prefixes), and the wiki
  (`_WIKI/BLOOMMCP/storage-workflow.md`) describes `bloommcp_input/` as "flat,
  **LLM-supplied** raw CSVs." But `supabase_client.py`'s public surface exposes only
  `read_input_csv` — **no write/upload function for that prefix exists anywhere in the
  repo** (grepped `bloomcli/` and `web/` too — nothing writes there). The grant exists;
  the producer doesn't. This matches bloom PR #368's own admission — _"Since prod has no
  input data, switching the raw source is non-breaking"_ — confirming the bucket is
  genuinely empty in production, not just under-exercised. **This sharpens the case for
  this program's redirection:** the DB-direct raw tier reads data that unambiguously
  exists and is actively written by a real pipeline (Bloom's `cyl_*` tables, via the A2
  write-back RPC); the bucket-CSV path is read-ready infrastructure for an input
  mechanism nobody has built yet.
- **✅ Resolved 2026-07-23 — the two Benfica PRs that touched this same raw-tier code are
  closed; Tier 2 owns `supabase_reader.py`'s raw tier outright.**
  - [PR #368](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/pull/368) ("read
    raw input from the `bloommcp_input/` bucket"), open 2026-06-30 → **closed 2026-07-23**.
    Would have moved `SupabaseReader`'s raw tier to read `bloommcp_input/<name>` — written
    against a bucket that, at the time, had **no producer** (see above); redundant now that
    the raw tier reads `cyl_*` directly.
  - [PR #413](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/pull/413) ("upload
    input files to the bloommcp-data bucket"), open 2026-07-07 → **closed 2026-07-23**.
    Would have added `POST /uploads`/`POST /uploads/sign` writing to `bloommcp_input/` and
    extended `SupabaseReader`/`FakeReader` to resolve an uploaded input by name; the upload
    path isn't being pursued, so this resolution no longer applies.
  - Benfica closed both when approving this roadmap, per her review comment: reading
    `cyl_*` directly makes #368's CSV path redundant, and since the upload path isn't
    being pursued, Tier 2 owns this file cleanly. No sequencing/ownership coordination
    remains — Tier 2 no longer needs a two-ID-shapes dispatch (experiment-id vs.
    upload-filename); see the design note below, kept for history.
- **The real `ExperimentReader` port already exists and differs from the vault design's
  proposed shape** (`bloommcp/src/bloom_mcp/data_access/ports.py`, shipped by Phase 2 Tier
  2/#307, confirmed by direct read this session):
  - `ExperimentReader.load_experiment(name: str, *, version: str = "latest", require_clean:
bool = False) -> ExperimentFrame`, where `ExperimentFrame` has flat fields
    (`df`, `trait_cols`, `metadata_cols`, `genotype_col`, `replicate_col`, `sample_id_col`,
    `source`) — **not** the vault design's separate `TraitRepository` class /
    `BloomTraitSource` Protocol / `ExperimentTraits` dataclass (§5–6 there). Those types
    are superseded; `SupabaseReader` keeps implementing the **real** port, just with a
    different raw-tier implementation inside it. The port's own docstring already
    anticipates a DB-backed adapter: _"A future DB-direct adapter can satisfy the same
    port by **declaring** column roles for whatever shape it sources... so role detection
    never leaks into callers."_
  - **`version`/`require_clean` are bloommcp's own written-output versioning axis**
    (`qc_clean`'s versioned-cleaned CSVs in `bloommcp_output/qc_<stem>/manifest.json`,
    resolved by the backend-agnostic `_resolve_versioned_cleaned(o_dir, stem, version)` in
    `experiment_utils.py` — `o_dir` is accepted for signature compatibility but ignored, per
    its own docstring), **not** Bloom's `cyl_trait_sources`/pipeline-run provenance axis.
    These are orthogonal: `_resolve_versioned_cleaned` only cares about a string `stem`
    key and needs no change — only the **raw**-tier fallback (today: local CSV or
    `bloommcp_input/` bucket) gets rewritten to a DB fetch.
  - **Addressing resolved, not just flagged:** since `_resolve_versioned_cleaned`'s `stem`
    is just `Path(name).stem`, passing `name=str(experiment_id)` (e.g. `"42"`) reuses the
    existing versioned-cleaned resolution unchanged (`Path("42").stem == "42"`). Adopt
    this convention explicitly (Tier 2) rather than leaving the int-vs-string question
    open. This is a real behavior change for `SupabaseReader`'s callers — see Tier 3's
    LLM-facing-text task.
  - **Why `SupabaseReader` legitimately needs two raw-tier resolution paths, not a
    naming fix.** Elizabeth asked whether reading Storage-bucket CSVs even belongs under
    a class named `SupabaseReader` if "supabase" is supposed to mean "our database."
    Resolved: the load-bearing distinction isn't Storage-vs-DB (Supabase Storage is as
    much "Supabase" as its Postgres) — it's **whether the experiment's source of truth
    lives in Bloom's relational schema at all**. Cylinder-pipeline experiments do (this
    program's target — reading a CSV for these is pure redundancy). **Ad-hoc uploads via
    bloom PR #413 (below) never will** — a user-uploaded CSV is a blob, full stop, with
    no DB row to redirect to. So `load_experiment(name)` needs an explicit two-tier
    resolution inside the **same** class (not a new one, not a naming change): a
    numeric-shaped `name` (`str(experiment_id)`, this program's convention) resolves via
    Tier 1's DB fetch; a filename-shaped `name` (matching #413's `input_ref` convention)
    falls back to the Storage-bucket upload resolution #413 builds. One class, two ID
    shapes, unambiguous dispatch — not a conflict to resolve by picking one.
  - **Source/run selection has no slot in the `ExperimentReader` Protocol.** `get_scan_traits`
    supports `source_id_`/`run_id_` (mutually exclusive, default latest); the port's
    `load_experiment` doesn't expose either. Tier 2 must decide this explicitly rather
    than silently always resolving latest.
  - Precedent for adapter-only optional capabilities already exists: `RawSourced`
    (`isinstance`-gated), used so readers can expose a raw path for content-addressing
    without widening the core Protocol. A `SourceSelectable`-shaped capability is the
    natural analogue for source/run pinning; `SupabaseReader`'s DB-backed raw tier likely
    stops satisfying `RawSourced` (there's no on-disk path anymore) and needs a
    `source_id`/`source_name`-based content-address instead (see Provenance gap below).
- **Column-role mapping (design §6, re-verified against the live schema this session,
  not re-derived from scratch):**

  | canonical role | ← DB source                                                 | notes                                                                                                                                                                                                                                                                                                                                                                                                                                     |
  | -------------- | ----------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
  | `genotype`     | `accessions.name`                                           | required                                                                                                                                                                                                                                                                                                                                                                                                                                  |
  | `sample_id`    | `cyl_plants.qr_code`                                        | the replicate unit for cylinder data                                                                                                                                                                                                                                                                                                                                                                                                      |
  | `replicate`    | _(none)_                                                    | optional; **do not use `wave_number`** — wave is a planting cohort, not the replicate unit ([sleap-roots-analyze#142](https://github.com/talmolab/sleap-roots-analyze/issues/142), **closed 2026-06-10** — already resolved upstream: a 4-agent sweep confirmed `replicate`'s values are never load-bearing (heritability groups by genotype, not replicate), so analyze's config accepts it as optional today; no outstanding work here) |
  | `image_path`   | cylinder image path                                         | optional                                                                                                                                                                                                                                                                                                                                                                                                                                  |
  | _(metadata)_   | `cyl_waves.number`→`wave`, `plant_age_days`, `date_scanned` | plain columns, not roles                                                                                                                                                                                                                                                                                                                                                                                                                  |

- **Adjacent, not-to-duplicate work (cross-referenced, not folded in):**
  - [bloom#374](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/374)
    (open) — existing source-blind consumers (`cyl_trait_by_experiment_wave` view,
    `get_scan_traits_tool`) need repointing at the latest-source surface; independently
    broken (one selects a dropped column). Same substrate, distinct scope — don't duplicate.
  - [bloom#476](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/476)
    (open, filed 2026-07-20) — retiring bloommcp's remaining `BLOOM_TRAITS_DIR` read
    bypasses (`qc_inspect_tool.py`, `supabase_reader.py`'s deprecated local fallback).
    Once Tier 2 replaces `SupabaseReader`'s raw tier with a DB fetch, the local-disk
    fallback #476 targets inside `supabase_reader.py` becomes moot on its own — worth
    sequencing #476 alongside or after Tier 2 rather than as fully independent cleanup.
  - [bloom#474](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/474)
    (open) — asks whether `docker-compose.prod.yml`'s bind-mount-permission bug (fixed in
    dev via #472/#473) also hits prod/staging for `SLEAP_OUT_CSV`/`PLOTS_DIR`/
    `ANALYSIS_OUTPUT`. **Folded into Tier 3 above, not just cross-referenced:** once Tier
    2/3 land, `SLEAP_OUT_CSV` drops out of #474's prod risk entirely (nothing reads it in
    the default backend) — `PLOTS_DIR`/`ANALYSIS_OUTPUT` are untouched by this roadmap and
    stay #474's problem.
  - [bloom#477](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/477)
    (open) — asks to confirm `SLEAP_OUT_CSV`/`ANALYSIS_OUTPUT` are dead weight in
    staging/prod and stop mounting them (or rename for clarity). **This is Tier 3's compose
    cleanup, verbatim** — see the tier row. Its own findings: `ANALYSIS_OUTPUT` is already
    "bridge-only, deprecated" per `storage_backend.py`'s own comment (touched only by
    unrelated `phenotyping_segmentation` demo tools + one legacy plot, not the core
    pipeline) — entirely orthogonal to this roadmap. `SLEAP_OUT_CSV` is touched by those
    same demo tools _in addition to_ the legacy read-fallback this roadmap retires — so
    landing Tier 2/3 answers #477's question for the primary pipeline but leaves the
    demo-tool dependency as a smaller, separate loose end before the mount can be fully
    dropped.
  - [sleap-roots-analyze#144](https://github.com/talmolab/sleap-roots-analyze/issues/144)
    (open) — Track B's B2 ("analyze consumes the contract"); same cross-repo Track B area,
    distinct task.
  - **bloom PR #339** (edits `bloommcp/docs/roadmap.md` directly). **UPDATE 2026-07-21: no
    longer a risk** — the Tiers-table restructuring it proposes turned out to be genuinely
    missing from `staging` (not redundant as first assessed), rebased onto current
    `staging` and reconciled against live issue state; now reports `mergeable: MERGEABLE`,
    awaiting a non-author reviewer. Original stale assessment kept below for context: #339
    now looks stale/redundant across the whole file, not narrowly conflicting on one
    line. **Recommend flagging to Elizabeth for likely closure** (not a rebase) — this
    roadmap's own edit is safe regardless, but #339 is dead weight independent of it.

## Hard constraints / decisions carried from the design

- **Auth:** query as the shared `bloom_agent` role, no per-user token/RLS (Benfica,
  2026-06-09 — reads are not per-user sensitive; re-confirmed still holds, Benfica,
  2026-07-23).
- **One source per frame, never mixed** — every `ExperimentFrame` load ties to exactly one
  resolved `cyl_trait_sources` row; ambiguity is a caller error, not a silent merge.
- **Provenance — real gap, not "unchanged from the design":** checked
  `bloommcp/src/bloom_mcp/contract/provenance.py`'s `Provenance` model and
  `bloommcp/src/bloom_mcp/manifest/schema.py`'s `VersionEntry`/`ExperimentBlock` — **neither
  has a `source_id`/`source_name` field today**, and both are `_StrictModel`
  (`extra="forbid"`), so recording it isn't a rename, it's an additive schema bump (the
  same shape as Tier 1 (Phase 2)'s v2→v3 `+seed/agent/output_sha256` change) plus a
  `Provenance.stamp()` signature change and a `to_version_entry()` update. **This is
  explicit Tier 2 scope** (see the tier table), not a pre-existing mechanism.
- **Structured errors**, not tuples/strings — reuse `ExperimentReadError` /
  `ExperimentNotFoundError` from `ports.py`; add DB-specific subclasses only if a case
  doesn't fit the existing ones.
- **Testing:** fakes first, no live DB, per the design's §10 (a fake DB row-fetcher for
  the raw-fetch seam, injected into `SupabaseReader`) — mirrors the existing `FakeReader`
  precedent in `data_access/`.
- **Migration safety (repo-wide constraint, not specific to this program):** per the
  pipeline roadmap's "Bloom DB safety" hard constraint and every real migration in this
  repo, schema changes are **forward-only**, with a **manual rollback script** under
  `supabase/rollbacks/` (this repo ships no auto-generated down-migrations). Tier 1's
  migration follows this convention exactly — see its oracle row.

## Tiers

Status: ✅ done · 🔵 in progress · ⬜ not started.

| Tier                                                                 | Goal                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           | Oracle / validation                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  | Depends on                                                                                                                         | Tracking                                                                                                                                                                                                                                                  | Status |
| -------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------ |
| **1 — bulk-read DB migration**                                       | Additive Supabase migration adding a bulk trait-fetch surface for one experiment + `list_experiment_trait_sources(experiment_id_)`, built as a sibling to the shipped `cyl_scan_traits_source`/`_latest` views (reuse the existing `is_latest` logic — don't re-derive latest-selection). Shape (RPC returning long-format all-traits vs. PostgREST embedded-join query) is a Benfica-reviewed decision, same review gate as the shipped migration. Confirm `bloom_agent` grants cover the full join chain, not just the read-surface objects (spot-check only — already broadly granted via `20260414002000_security_groups.sql`'s schema-wide `GRANT SELECT ... TO bloom_agent` + matching RLS `SELECT` policies on all six join tables, confirmed this session; not expected to need a grant change).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | One call fetches all 649–880 traits for the bloom#483 cylinder fixture experiment in a single round trip; matches `get_scan_traits`'s existing latest/source_id/run_id semantics byte-for-byte on overlapping rows; migration is **forward-only** with a manual rollback script under `supabase/rollbacks/` (this repo's convention — no auto-generated down-migrations), tested up+down on local Supabase                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           | A2 (nearly done — see Live-state facts)                                                                                            | [bloom#546](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/546); OpenSpec `add-bulk-trait-read-rpc` implemented on branch `egao28/bulk-trait-read-migration-546`, not yet a PR — RPC shape (D1) awaiting Benfica's review before merge | 🔵     |
| **2 — rewrite `SupabaseReader`'s raw tier to query the DB directly** | Modify `SupabaseReader` (`supabase_reader.py`) **in place** — no new class: (a) `load_experiment`'s raw-tier fallback dispatches on the shape of `name` — experiment-id-shaped (`str(experiment_id)`) calls Tier 1's bulk fetch + long→wide pivot + canonical-role rename (table above); upload-filename-shaped (matching PR #413's `input_ref` convention) falls through to whatever read-side resolution #413 lands, since ad-hoc uploads have no DB representation to redirect to (see the two-ID-shapes design note above) — this is **additive dispatch, not a replacement** of #413's contribution. `_resolve_versioned_cleaned`'s cleaned-output tiers are untouched either way. (b) `list_experiments()` — currently scans CSV files/the bucket; rewrite to enumerate Bloom experiments from the DB, **merged with** whatever uploaded-input listing #413 adds, so the two always-included discovery tools (`list_available_experiments.py`, `list_existing_analyses.py`) surface both kinds. Decide + implement the source/run-selection gap explicitly — either a `SourceSelectable` capability protocol (isinstance-gated, mirroring `RawSourced`) or an equivalent seam — don't leave it silently defaulted. **Extend `Provenance`/`VersionEntry` additively** (v3→v4, mirroring the existing seed/agent/output_sha256 precedent) to carry `source_id`/`source_name`, replacing the file-hash-based `RawSourced` content-address this raw tier no longer has — see Hard constraints. A fake DB row-fetcher injected for the raw-fetch seam; no live DB required for this tier's tests. ~~Coordinate with Benfica on PRs #368/#413 first~~ **No longer needed — both closed 2026-07-23** (see Live-state facts); Tier 2 owns this file outright.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    | Unit tests against fakes: `load_experiment(str(experiment_id))` returns the expected wide frame + correct roles; `list_experiments()` returns sensible DB-sourced summaries; golden fixture off bloom#483's cylinder data (raw 129×880 or post-QC 123×649) — **bloom#483 is still open and no cylinder fixture files exist in the repo yet, so this tier's TDD plan explicitly depends on either #483 landing first or a hand-built cylinder-shaped fixture as a fallback (don't block on #483 silently)**; multi-source test — never mixes across `source_id`; `require_clean`/`version` resolution unchanged for the cleaned-output tiers; old manifests (pre-v4) still read after the Provenance bump; **two existing tests need outright deletion, not an update** — `tests/data_access/test_local_reader.py`'s `test_same_raw_bytes_yield_same_roles_as_supabase` asserts `SupabaseReader` and `LocalReader` read identical on-disk CSV bytes, a premise a DB-backed raw tier voids; `tests/data_access/test_supabase_reader.py`'s `test_raw_source_path_rejects_path_traversal` guards a local-disk traversal case that no longer applies once the raw tier drops `RawSourced` | Tier 1 (soft — buildable against fakes once the target RPC shape is settled, even pre-merge); **bloom#483** (fixture — see oracle) | _(not yet filed)_                                                                                                                                                                                                                                         | ⬜     |
| **3 — LLM-facing surface + cleanup**                                 | No new selector/env var needed — `BLOOM_STORAGE_BACKEND`'s existing binary `local`/`supabase` switch is untouched, since Tier 2 changed what `supabase` mode does internally rather than adding a third option. **Update the LLM-facing surface**: the tool schemas' `Field(description=...)` text (`qc_clean.py`, `qc_inspect.py`, `remove_outliers.py`, `clustering.py`, `pca_analysis.py` all currently say "CSV filename"), `list_existing_analyses.py`'s `experiment_filename` param, and `list_available_experiments.py`'s hardcoded "use its filename" response text all actively tell the calling LLM to pass a CSV filename — now wrong under the default `supabase` backend, which expects `str(experiment_id)`; reword to the backend-agnostic `name`/experiment identifier. Retire the now-dead CSV-from-bucket/local-disk raw-tier code Tier 2 replaced (coordinate with bloom#476, which targets the same file). Update `storage-backends.md` to describe `supabase` mode as DB-direct trait reads, not bucket CSVs. **Also retire `BLOOM_TRAITS_DIR` from `_REQUIRED_DIRS`/boot validation** (`experiment_utils.py:24-28`) and drop the `SLEAP_OUT_CSV` bind-mount + `BLOOM_TRAITS_DIR` env var from `docker-compose.prod.yml` — once Tier 2 lands, nothing in the default `supabase`-backend path reads that directory, but the container still hard-requires it to exist/be writable at boot regardless of use; leaving that requirement in place keeps [bloom#474](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/474)'s bind-mount-permission risk alive for a directory nothing needs. **This is exactly [bloom#477](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/477)'s ask** (confirm `SLEAP_OUT_CSV` is dead weight in staging/prod, then stop mounting it) — coordinate/close together rather than duplicating. Caveat from #477: `SLEAP_OUT_CSV` is _also_ touched by the unrelated `phenotyping_segmentation` demo tools (`compute_median`/`compute_min`/`compute_mode`), so Tier 2/3 landing removes the primary qc_clean→pca_analysis pipeline's use of it but doesn't make it **fully** dead on its own — #477's demo-tool question is a separate, smaller loose end. (`ANALYSIS_OUTPUT`/`PLOTS_DIR`, #477's other two directories, are unaffected by this roadmap — they're `ResultStore`/plot-serving concerns, out of this program's read-only scope.) | Integration test round-trips a fixture experiment through `SupabaseReader` end-to-end against a **local Supabase instance** (first tier requiring a live DB, not fakes); `LocalReader` tests stay green (untouched); updated tool-schema/docstring text reviewed for accuracy; `storage-backends.md` updated; dead code removed, not left as an unreachable branch; `_REQUIRED_DIRS`/compose mount for `SLEAP_OUT_CSV` removed for the `supabase` backend, not just the Python read path                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             | Tier 2                                                                                                                             | _(not yet filed; cross-ref [bloom#474](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/474), [bloom#477](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/477))_                                                       | ⬜     |

## Tracking issues

Per the `roadmap-driven-pipeline` skill's just-in-time policy, no tier issues are filed
yet — file **one tracking issue per tier** at the point each tier is actually reached
(not all three upfront), and link it from this table.

## Reconciliation log — history/audit trail, not required reading

The entries below record how this doc's own claims were checked and corrected across
several rounds of review and revision. They matter if you want to see _why_ a given line
reads the way it does, or if you're auditing the process — not for understanding what's
currently being proposed. The sections above already reflect every correction made here.

### Adversarial roadmap review, 2026-07-21

4-lens review (factual accuracy / dependency & sequencing / completeness /
scope-consistency-safety), each lens run independently against live `gh` state and the
actual code/schema/migrations — not against this document's own citations. Resolutions:

- **[Factual] sleap-roots-analyze#142 mis-cited as open/active-tracking** — it's closed
  (2026-06-10), already resolved (a 4-agent sweep confirmed `replicate` is never
  load-bearing). → **Reconciled:** column-role table note rewritten to state it's
  resolved, no outstanding upstream work.
- **[Factual] `_resolve_versioned_cleaned` signature mis-cited** — doc said
  `_resolve_versioned_cleaned(stem, version)`; the real signature is `(o_dir, stem,
version)` (`o_dir` accepted but ignored, per its own docstring). → **Reconciled:**
  citation corrected in Live-state facts.
- **[Factual, minor] bloom#476 "filed this session"** — its `createdAt` is 2026-07-20, a
  day before this doc's verification date. → **Reconciled:** reworded to "filed
  2026-07-20."
- **[Dependency, BLOCKING-shaped] PR #339's risk was mischaracterized** — the original
  draft claimed a narrow line-collision risk on the Deferred-line edit. `gh pr diff`
  shows #339 never touches that line (unchanged context in both hunks); the real
  finding is that GitHub already reports #339 as `mergeable: false` / `dirty` against
  current `staging`, and the live `bloommcp/docs/roadmap.md` already contains content
  nearly identical to what #339 proposes (apparently landed via the #438-era commits) —
  #339 looks stale/redundant across the whole file, not narrowly conflicting. →
  **Reconciled:** rewrote the PR #339 bullet; recommend flagging it to Elizabeth for
  likely closure, not a rebase.
- **[Completeness, real gap] Tier 2 never planned `ExperimentReader.list_experiments()`**
  — the Protocol requires it and two always-included discovery tools
  (`list_available_experiments.py`, `list_existing_analyses.py`) call it; a DB-direct
  raw tier without it would silently break both. → **Reconciled:** added as explicit
  Tier 2 scope + oracle.
- **[Completeness, real gap] LLM-facing tool text says "CSV filename"** — five tool
  `Field(description=...)` strings + two discovery-tool docstrings actively instruct the
  calling LLM to pass a CSV filename; wrong/misleading once the default backend expects
  an experiment id. → **Reconciled:** added as explicit Tier 3 scope + oracle.
- **[Completeness, real gap] Tier 2's oracle fixture doesn't exist yet** — bloom#483 (the
  cylinder fixture) is still open; no cylinder fixture files exist in
  `bloommcp/tests/fixtures/` today. → **Reconciled:** Tier 2's "Depends on" now lists
  bloom#483 explicitly, with a hand-built-fixture fallback named so Tier 2 isn't silently
  blocked if #483 stalls.
- **[Completeness, real gap] Provenance/VersionEntry has no `source_id`/`source_name`
  field** — the original draft said "unchanged from the design," but neither model has
  this field today (both are `extra="forbid"`), so recording it is an additive v3→v4
  schema bump, not a pre-existing mechanism. → **Reconciled:** Hard constraints section
  rewritten to state the real gap; added as explicit Tier 2 scope.
- **[Completeness, checked, no change needed] `bloom_agent` grants on the join tables**
  — already covered by a schema-wide `GRANT SELECT ... TO bloom_agent` +
  matching per-table RLS `SELECT` policies (confirmed in `20260414002000_security_groups.sql`
  - `20260506000001_bloom_role_rls_policies.sql`). → Tier 1's phrasing softened from an
    open question to a spot-check, since there's nothing outstanding to find.
- **[Scope/safety, real gap] Tier 1's migration-safety oracle was underspecified** — "up/down
  tested" risks reading as auto-generated down-migrations, which contradicts this repo's
  actual convention (forward-only + a manual rollback script under `supabase/rollbacks/`,
  confirmed against every recent migration). → **Reconciled:** Tier 1's oracle and the
  Hard constraints section now state the convention explicitly.
- **[Scope/safety, checked, no change needed] Auth/RLS, two-master risk, blob-upload
  entanglement, PR #339 diff surgical-ness, scope creep** — all checked against live
  migrations/PRs/`git status`/`git diff` and confirmed sound as drafted; no changes.

**Post-review architecture correction (2026-07-21, prompted by Elizabeth's questions
about consistency with bloommcp's existing data-access patterns):**

- **[Architecture, major re-scope] The program originally added a new `BloomDBReader`
  (later `DBReader`) sibling class alongside `SupabaseReader`/`LocalReader`.** Elizabeth's
  question — "why do we need `DBReader` _and_ `SupabaseReader`?" — surfaced that
  `SupabaseReader` reading a CSV out of a Storage bucket, rather than Bloom's actual
  database, is itself the defect, not a legitimate parallel path to preserve. **Reconciled:
  reframed the whole program around fixing `SupabaseReader` in place** (Tier 2 rewrites its
  raw tier to query the DB directly; no new class, no new backend-selection mechanism).
  This also resolves two problems the first draft had flagged as open: the
  `VALID_BACKENDS`/`is_local_backend()` extensibility mismatch (moot — no third value is
  being added) and the single-global-reader-singleton limit in `_ports.py` (moot — we're
  not trying to serve two kinds of experiments from one deployment).
- **[Factual, real gap] `bloommcp_input/`'s producer status was never checked in the
  first draft.** Traced the full write path: the bucket migration grants `bloom_agent`
  INSERT/UPDATE, but `supabase_client.py` exposes no write function for that prefix, and
  neither `bloomcli/` nor `web/` writes there — confirmed via grep. → **Reconciled:**
  added as an explicit Live-state facts bullet; strengthens the rationale for redirecting
  `SupabaseReader` to the DB rather than continuing down the bucket-CSV path.
- **[Scope/safety, real finding] bloom PR #368 is authored by Benfica and is in direct
  tension with Tier 2, not merely adjacent.** It moves the same raw tier further into
  CSV-from-bucket territory; landing it before Tier 2 means reverting her work. Since the
  bucket has no producer yet, holding it costs nothing. → **Reconciled:** elevated from
  an "adjacent, safe either order" bullet to an explicit flag-to-Benfica recommendation,
  cited by PR link, in both Live-state facts and Tier 2's goal column.

**Targeted re-review of this re-scope (2026-07-21):** ran two independent checks — one
re-verifying the new claims (`bloommcp_input` producer tracing, PR #368 details,
in-place-modification feasibility, no-new-selector) against live state, one checking the
whole document for internal consistency after the rewrite. Both confirmed sound, with one
real gap:

- **[Completeness, real gap] Tier 2's oracle said existing `SupabaseReader` tests get
  "updated in place," but two specific tests actually need outright deletion** — checked
  directly: `tests/data_access/test_local_reader.py`'s
  `test_same_raw_bytes_yield_same_roles_as_supabase` asserts `SupabaseReader` and
  `LocalReader` read identical on-disk CSV bytes (a premise a DB-backed raw tier voids),
  and `tests/data_access/test_supabase_reader.py`'s
  `test_raw_source_path_rejects_path_traversal` guards a local-disk traversal case that no
  longer applies once the raw tier drops `RawSourced`. → **Reconciled:** Tier 2's oracle
  now names both tests explicitly as deletions, not updates.
- Consistency check found no stale references to the abandoned `DBReader` design, no
  Tier 2/3 scope drift, and the Reconciliation log's claims all matched the live document
  — no further changes needed.

### Update (2026-07-22) — cross-referenced bloom#474 and bloom#477

Elizabeth asked how the bind-mount-permission follow-up (bloom#474) would change under
this roadmap. Investigation:

- Confirmed in `docker-compose.prod.yml` that `SLEAP_OUT_CSV`/`PLOTS_DIR`/`ANALYSIS_OUTPUT`
  are all actively bind-mounted + env-wired for the bloommcp service today (`BLOOM_TRAITS_DIR:
/app/data/SLEAP_OUT_CSV` etc.), and that `BLOOM_TRAITS_DIR` sits in `_REQUIRED_DIRS`
  (`experiment_utils.py:24-28`) — a boot-time requirement independent of whether the code
  path actually reads it.
- Found bloom#477, filed the same day as #474/#476, asking almost exactly this roadmap's
  Tier 3 question (confirm `SLEAP_OUT_CSV`/`ANALYSIS_OUTPUT` are dead weight in
  staging/prod, then stop mounting them) — with its own findings that `ANALYSIS_OUTPUT` is
  already "bridge-only, deprecated" per `storage_backend.py`, touched only by unrelated
  demo tools, and that `SLEAP_OUT_CSV` is touched by those same demo tools _in addition to_
  the legacy read-fallback this roadmap retires.
- **Folded into Tier 3** (not left as a cross-reference): retiring `BLOOM_TRAITS_DIR` from
  `_REQUIRED_DIRS` + the compose mount is now explicit Tier 3 scope, cross-referencing both
  issues directly in the tier row, with the demo-tool caveat stated so Tier 3 doesn't
  overclaim full resolution of #477.
- Updated the PR #339 bullet above from its original stale "mergeable: false / dirty,
  looks redundant" assessment to reflect that it was fixed this session (see the separate
  memory/PR history) — kept the original text below it for context rather than deleting
  the record of the mistaken initial assessment.

### Update (2026-07-22, continued) — resolved the "why SupabaseReader" question with PR #413

Following on the `bloommcp_input/`-producer finding: checked #406's current status (still
open, Benfica hasn't answered the two design questions from my 2026-07-07 review; its
prerequisite PR #265 is also still open) and found **PR #413** ("upload input files to the
bloommcp-data bucket," Benfica, open since 2026-07-07) — the actual, in-flight producer for
`bloommcp_input/`, which also extends `SupabaseReader`/`FakeReader` to resolve uploaded
inputs. This sharpened a question Elizabeth raised: if "supabase" is meant to mean "our
database," does it make sense for `SupabaseReader` to also resolve #413's uploaded CSVs?

**Resolved:** the load-bearing distinction isn't Storage-vs-DB (Supabase Storage is as much
"Supabase" as its Postgres) — it's whether an experiment's source of truth lives in Bloom's
relational schema at all. Cylinder-pipeline data does (this program's target); ad-hoc
uploads via #413 never will. So `SupabaseReader` legitimately needs **both** resolution
paths in one class, dispatching on the shape of `name` (experiment-id vs. upload-filename)
— not a naming fix, not a class split, and not "pick DB-direct over the upload path." Folded
into the Live-state facts (as a resolved design note, replacing the earlier "in tension, flag
to Benfica" framing with "complementary, coordinate on shared code") and into Tier 2's
scope (additive dispatch alongside whatever #413 lands, not a replacement of it). Also
flagged, not resolved: #413 (2026-07-07, full write+read round-trip) likely supersedes
#368's read-only change (2026-06-30, written against an empty bucket) — both still open,
not this roadmap's call to reconcile.

### Update (2026-07-23) — Benfica approved the roadmap; #368/#413 closed, auth re-confirmed

Benfica approved this PR, resolving all three open asks:

- **#368/#413 sequencing (Q1): resolved, not just answered.** Reading `cyl_*` directly
  makes #368's CSV path redundant, and the upload path isn't being pursued, so both PRs
  are now closed and Tier 2 owns `supabase_reader.py`'s raw tier with no coordination
  needed. Updated the Q1 entry, the Live-state facts warning bullet, and Tier 2's goal
  column to drop the now-moot coordination asks; kept the prior "two ID shapes" design
  note for history rather than deleting it outright, since it explains why the program
  was scoped that way before this resolution.
- **Tier 1's RPC shape (Q2): unchanged**, still gated on her review before the migration
  ships — matches what this doc already said.
- **Auth decision (Q3): re-confirmed.** Shared `bloom_agent` role, no per-user token/RLS
  for trait reads still holds as of 2026-07-23.

**Not done in this pass, flagged for a follow-up:** with the upload path (#413) off the
table, Tier 2's design note on "why `SupabaseReader` needs two raw-tier resolution paths"
(the experiment-id-shaped vs. upload-filename-shaped dispatch) may now be unnecessary
complexity — Tier 2 could simplify to DB-direct resolution only, no dispatch. That's a
scope change to Tier 2 itself, not just a bookkeeping update, so it's left for a deliberate
follow-up decision rather than folded in here silently.
