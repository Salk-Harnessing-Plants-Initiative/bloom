# bloommcp DB-direct trait reads — roadmap

**Status: DRAFT — pending adversarial roadmap-level review + Elizabeth approval.**

**Origin.** `bloommcp/docs/roadmap.md`'s Deferred line names this item ("DB-direct read
adapter (sub-project `#2`)") with no further tiering — this doc is that tiering.
`sleap-roots-pipeline/docs/bloom-integration/roadmap.md` §"Track B" states the naming
bridge: *"that spec's 'integration sub-project `#2`' = tier **A2** above — A2 gates the
bloom-mcp data-access layer."* **Tier 1 here is gated on A2** (see Live-state facts).

**Design (source of truth, corrected below):** vault
`docs/superpowers/specs/2026-06-04-bloom-mcp-data-access-design.md` — the original
read-only data-access design, written before the Phase 2 vertical slice shipped its own
`ExperimentReader` port. Related: the master spec
`2026-05-11-metcalf-2026-evelyn-bloom-mcp-design.md` and
`2026-06-15-bloom-mcp-phase2-persistence-design.md` (defines the **real**
`ExperimentReader` port this program targets — see Tier 2). The original design's own
framing — *"the refactored bloom_mcp will query Bloom (Supabase/Postgres) directly by
`experiment_id` **instead of** reading exported CSVs"* — is exactly this program's
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
     `AnalysisWriter`." **`AnalysisWriter` is now fully dead code**
     ([bloom#487](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/487),
     open, filed this session) — grepped: no live tool imports it, a dedicated import-guard
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
  the producer doesn't. This matches bloom PR #368's own admission — *"Since prod has no
  input data, switching the raw source is non-breaking"* — confirming the bucket is
  genuinely empty in production, not just under-exercised. **This sharpens the case for
  this program's redirection:** the DB-direct raw tier reads data that unambiguously
  exists and is actively written by a real pipeline (Bloom's `cyl_*` tables, via the A2
  write-back RPC); the bucket-CSV path is read-ready infrastructure for an input
  mechanism nobody has built yet.
- **⚠️ bloom PR #368 is in tension with this program, not merely adjacent — flag to
  Benfica.** [PR #368](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/pull/368)
  ("read raw input from the `bloommcp_input/` bucket"), authored by **Benfica
  (`blm3886`)** herself, open since 2026-06-30. It moves `SupabaseReader`'s raw tier
  *further* into CSV-from-bucket territory — the opposite direction from where Tier 2
  below puts it. Because #368 targets `SupabaseReader` directly (`supabase_reader.py` +
  `experiment_utils.py::load_experiment_data`), landing it first means Tier 2 then has to
  rip out work she just added, rather than the two being independent. Given the bucket
  has no producer yet (above), **holding #368 costs nothing** — no production data or
  in-flight consumer depends on it landing now. **Recommend raising this with Benfica
  directly before either #368 or Tier 2 proceeds**, rather than silently superseding her
  open PR in a roadmap doc she hasn't seen.
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
    anticipates a DB-backed adapter: *"A future DB-direct adapter can satisfy the same
    port by **declaring** column roles for whatever shape it sources... so role detection
    never leaks into callers."*
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

  | canonical role | ← DB source | notes |
  |---|---|---|
  | `genotype` | `accessions.name` | required |
  | `sample_id` | `cyl_plants.qr_code` | the replicate unit for cylinder data |
  | `replicate` | *(none)* | optional; **do not use `wave_number`** — wave is a planting cohort, not the replicate unit ([sleap-roots-analyze#142](https://github.com/talmolab/sleap-roots-analyze/issues/142), **closed 2026-06-10** — already resolved upstream: a 4-agent sweep confirmed `replicate`'s values are never load-bearing (heritability groups by genotype, not replicate), so analyze's config accepts it as optional today; no outstanding work here) |
  | `image_path` | cylinder image path | optional |
  | *(metadata)* | `cyl_waves.number`→`wave`, `plant_age_days`, `date_scanned` | plain columns, not roles |

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
  - [sleap-roots-analyze#144](https://github.com/talmolab/sleap-roots-analyze/issues/144)
    (open) — Track B's B2 ("analyze consumes the contract"); same cross-repo Track B area,
    distinct task.
  - **⚠️ bloom PR #339** (open, created 2026-06-23, last pushed 2026-07-08, base `staging`)
    edits `bloommcp/docs/roadmap.md` directly. **Checked via `gh pr diff`: it does NOT
    touch the "Deferred" line this roadmap edits** — that line appears as unchanged
    context in both of #339's hunks, so there is no line-level collision with this PR's
    two-line edit. The real issue is broader: GitHub already reports
    `mergeable: false` / `mergeable_state: dirty` for #339 against current `staging`, and
    the *live* `bloommcp/docs/roadmap.md` already contains content nearly identical to
    what #339 proposes (the same Tier-3/3b/3c reshape, the same tier statuses) —
    apparently landed through a different path (folded into the #438-era commits). #339
    now looks stale/redundant across the whole file, not narrowly conflicting on one
    line. **Recommend flagging to Elizabeth for likely closure** (not a rebase) — this
    roadmap's own edit is safe regardless, but #339 is dead weight independent of it.

## Hard constraints / decisions carried from the design

- **Auth:** query as the shared `bloom_agent` role, no per-user token/RLS (Benfica,
  2026-06-09 — reads are not per-user sensitive; re-verify this hasn't been revisited
  before Tier 1's PR).
- **One source per frame, never mixed** — every `ExperimentFrame` load ties to exactly one
  resolved `cyl_trait_sources` row; ambiguity is a caller error, not a silent merge.
- **Provenance — real gap, not "unchanged from the design":** checked
  `bloommcp/src/bloom_mcp/contract/provenance.py`'s `Provenance` model and
  `bloommcp/src/bloom_mcp/storage/schema.py`'s `VersionEntry`/`ExperimentBlock` — **neither
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

| Tier | Goal | Oracle / validation | Depends on | Tracking | Status |
|---|---|---|---|---|---|
| **1 — bulk-read DB migration** | Additive Supabase migration adding a bulk trait-fetch surface for one experiment + `list_experiment_trait_sources(experiment_id_)`, built as a sibling to the shipped `cyl_scan_traits_source`/`_latest` views (reuse the existing `is_latest` logic — don't re-derive latest-selection). Shape (RPC returning long-format all-traits vs. PostgREST embedded-join query) is a Benfica-reviewed decision, same review gate as the shipped migration. Confirm `bloom_agent` grants cover the full join chain, not just the read-surface objects (spot-check only — already broadly granted via `20260414002000_security_groups.sql`'s schema-wide `GRANT SELECT ... TO bloom_agent` + matching RLS `SELECT` policies on all six join tables, confirmed this session; not expected to need a grant change). | One call fetches all 649–880 traits for the bloom#483 cylinder fixture experiment in a single round trip; matches `get_scan_traits`'s existing latest/source_id/run_id semantics byte-for-byte on overlapping rows; migration is **forward-only** with a manual rollback script under `supabase/rollbacks/` (this repo's convention — no auto-generated down-migrations), tested up+down on local Supabase | A2 (nearly done — see Live-state facts) | *(not yet filed — file at kickoff, per the skill's just-in-time issue policy)* | ⬜ |
| **2 — rewrite `SupabaseReader`'s raw tier to query the DB directly** | Modify `SupabaseReader` (`supabase_reader.py`) **in place** — no new class: (a) `load_experiment`'s raw-tier fallback treats `name` as `str(experiment_id)` and calls Tier 1's bulk fetch + long→wide pivot + canonical-role rename (table above) instead of reading a CSV from `bloommcp_input/`/local disk; `_resolve_versioned_cleaned`'s cleaned-output tiers are untouched. (b) `list_experiments()` — currently scans CSV files/the bucket; rewrite to enumerate Bloom experiments from the DB instead, so the two always-included discovery tools (`list_available_experiments.py`, `list_existing_analyses.py`) keep working. Decide + implement the source/run-selection gap explicitly — either a `SourceSelectable` capability protocol (isinstance-gated, mirroring `RawSourced`) or an equivalent seam — don't leave it silently defaulted. **Extend `Provenance`/`VersionEntry` additively** (v3→v4, mirroring the existing seed/agent/output_sha256 precedent) to carry `source_id`/`source_name`, replacing the file-hash-based `RawSourced` content-address this raw tier no longer has — see Hard constraints. A fake DB row-fetcher injected for the raw-fetch seam; no live DB required for this tier's tests. **Coordinate with Benfica on PR #368 first** (see Live-state facts) — landing it before this tier means reverting her work. | Unit tests against fakes: `load_experiment(str(experiment_id))` returns the expected wide frame + correct roles; `list_experiments()` returns sensible DB-sourced summaries; golden fixture off bloom#483's cylinder data (raw 129×880 or post-QC 123×649) — **bloom#483 is still open and no cylinder fixture files exist in the repo yet, so this tier's TDD plan explicitly depends on either #483 landing first or a hand-built cylinder-shaped fixture as a fallback (don't block on #483 silently)**; multi-source test — never mixes across `source_id`; `require_clean`/`version` resolution unchanged for the cleaned-output tiers; old manifests (pre-v4) still read after the Provenance bump; **two existing tests need outright deletion, not an update** — `tests/data_access/test_local_reader.py`'s `test_same_raw_bytes_yield_same_roles_as_supabase` asserts `SupabaseReader` and `LocalReader` read identical on-disk CSV bytes, a premise a DB-backed raw tier voids; `tests/data_access/test_supabase_reader.py`'s `test_raw_source_path_rejects_path_traversal` guards a local-disk traversal case that no longer applies once the raw tier drops `RawSourced` | Tier 1 (soft — buildable against fakes once the target RPC shape is settled, even pre-merge); **bloom#483** (fixture — see oracle) | *(not yet filed)* | ⬜ |
| **3 — LLM-facing surface + cleanup** | No new selector/env var needed — `BLOOM_STORAGE_BACKEND`'s existing binary `local`/`supabase` switch is untouched, since Tier 2 changed what `supabase` mode does internally rather than adding a third option. **Update the LLM-facing surface**: the tool schemas' `Field(description=...)` text (`qc_clean.py`, `qc_inspect.py`, `remove_outliers.py`, `clustering.py`, `pca_analysis.py` all currently say "CSV filename"), `list_existing_analyses.py`'s `experiment_filename` param, and `list_available_experiments.py`'s hardcoded "use its filename" response text all actively tell the calling LLM to pass a CSV filename — now wrong under the default `supabase` backend, which expects `str(experiment_id)`; reword to the backend-agnostic `name`/experiment identifier. Retire the now-dead CSV-from-bucket/local-disk raw-tier code Tier 2 replaced (coordinate with bloom#476, which targets the same file). Update `storage-backends.md` to describe `supabase` mode as DB-direct trait reads, not bucket CSVs. | Integration test round-trips a fixture experiment through `SupabaseReader` end-to-end against a **local Supabase instance** (first tier requiring a live DB, not fakes); `LocalReader` tests stay green (untouched); updated tool-schema/docstring text reviewed for accuracy; `storage-backends.md` updated; dead code removed, not left as an unreachable branch | Tier 2 | *(not yet filed)* | ⬜ |

## Tracking issues

Per the `roadmap-driven-pipeline` skill's just-in-time policy, no tier issues are filed
yet — file **one tracking issue per tier** at the point each tier is actually reached
(not all three upfront), and link it from this table.

## Reconciliation log (adversarial roadmap review, 2026-07-21)

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
  + `20260506000001_bloom_role_rls_policies.sql`). → Tier 1's phrasing softened from an
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
  question — "why do we need `DBReader` *and* `SupabaseReader`?" — surfaced that
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
