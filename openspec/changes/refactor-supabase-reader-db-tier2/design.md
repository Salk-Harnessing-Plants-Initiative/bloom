## Context

`SupabaseReader` (`bloommcp/src/bloom_mcp/data_access/supabase_reader.py`) implements the
`ExperimentReader` port (`data_access/ports.py`). Today its cleaned-output tiers
(`_resolve_versioned_cleaned`, versioned `qc_<stem>` manifests then the legacy un-versioned
cleaned CSV) correctly read from Supabase Storage; its **raw** tier — the fallback used when
no cleaned output exists — reads a CSV from the local `BLOOM_TRAITS_DIR` disk path, which the
module docstring already calls deprecated. Tier 1 of the data-access roadmap
(`add-bulk-trait-read-rpc`, merged via PR #548) added `get_experiment_traits` and
`list_experiment_trait_sources` specifically so this tier could stop reading a CSV and query
Bloom's Postgres tables directly. This change is that rewrite.

Two upstream facts shape the scope below:

- **bloom#413** (PR, "upload input files to the bloommcp-data bucket") — the PR that would
  have given ad-hoc, non-DB-backed uploads a home — is **closed** (2026-07-23, not pursued).
  **Correction from this design's first draft:** closing #413 does not mean the idea is
  dead — **bloom#388** ("Let users upload input files and download output files for
  bloommcp") is open, assigned, and listed under a freshly-filed epic (bloom#554,
  2026-07-29) as a live prerequisite. See D1 — the DB-only decision below still holds, but
  not because "nothing exists to fall through to."
- **bloom#476** ("retire bloommcp's remaining `BLOOM_TRAITS_DIR` read bypasses") is open and
  explicitly blocked on this change landing.
- **bloom#552** (filed 2026-07-29, same day as #551) already tracks the LLM-facing
  "CSV filename" tool-text fix this proposal defers — the roadmap's "Tier 3" is only
  partially unfiled (the `BLOOM_TRAITS_DIR` boot/compose-mount half still has no tracking
  issue).

## Goals / Non-Goals

- Goals:
  - `SupabaseReader.load_experiment` resolves raw reads from Postgres, not local disk.
  - `SupabaseReader.list_experiments()` enumerates DB experiments, not CSV files.
  - Source/run selection has an explicit, discoverable seam (not a silent "always latest"),
    with **one source per frame structurally guaranteed**, not just asserted in prose.
  - Provenance survives losing its file-hash content-address, **and the wiring actually
    reaches a shipped tool's committed manifest** (not just an unused code path).
  - Zero new Postgres migrations — Tier 1 already shipped every DB-side primitive this
    tier calls (a documented, bounded exception: see D4 for the one place this goal is in
    tension with a cheap `list_experiments()`).
- Non-Goals:
  - Reintroducing an upload/blob-backed raw input path — that's bloom#388/whatever
    supersedes it, not this change.
  - Wiring `source_id`/`run_id` pinning into any LLM-facing tool parameter (bloom#552's
    scope; no consumer tool has a use case yet).
  - Touching `LocalReader`, `FakeReader`'s internals, or `_resolve_versioned_cleaned`.
  - Redesigning the roadmap's `sample_id ← cyl_plants.qr_code` column mapping (that
    decision was made and re-verified at the roadmap level) — this change instead adds a
    load-time uniqueness check so a violation fails loudly rather than silently mislabeling
    two plants as one (see D5).

## Decisions

### D1: Two-ID-shape dispatch vs. DB-only resolution

**Status: resolved — DB-only, corrected justification.**

The roadmap's original design called for `load_experiment` to dispatch on the shape of
`name`: an experiment-id-shaped string (`str(experiment_id)`) resolves via the DB; a
filename-shaped string falls through to an upload-backed resolution. This design's first
draft justified dropping that dispatch on "#413 closed, nothing to fall through to" — that
premise is incomplete: **bloom#388** is a live, assigned, currently-roadmapped issue for
exactly this upload feature (referenced from the newly-filed epic bloom#554). The DB-only
decision still holds, but on a narrower, more defensible basis:

**Decision:** `load_experiment`'s raw tier is DB-only for this change. `name` is parsed as
`int(name)`; a `ValueError` (non-numeric `name`) is treated identically to an unresolvable
`experiment_id` — a structured `ExperimentNotFoundError`. This is correct **regardless** of
#388's eventual shape, because: (a) #388 has no implementation today — there is nothing to
dispatch *to* yet, live issue or not; (b) whatever #388 ships is not guaranteed to reuse the
old #413 `input_ref`-filename convention this design's first draft assumed; (c) wiring a
dispatch branch to a target that doesn't exist yet is exactly the kind of speculative
seam this project's complexity-budget convention (openspec/AGENTS.md: "add complexity only
with... concrete scale requirements") argues against. If #388 ships a filename-shaped
convention later, adding the second dispatch arm to `SupabaseReader.load_experiment` is a
small, additive follow-up — not a reason to block this one on it now.

**Rejected alternative:** keep the dispatch shape with a local-disk fallback for
non-numeric names. Rejected because it directly reintroduces bloom#476's bypass.

### D2: Source/run selection — `SourceSelectable` capability, with structural mixing prevention

**Status: resolved.**

`get_experiment_traits`/`list_experiment_trait_sources` support pinning a `source_id_` or
`run_id_` (mutually exclusive — the RPC itself `RAISE EXCEPTION`s if both are given, per
`add-bulk-trait-read-rpc`'s shipped migration), but the `ExperimentReader` port's
`load_experiment(name, *, version, require_clean)` has no slot for either. Mirroring the
existing `RawSourced` precedent (an `isinstance`-gated optional capability):

```python
@dataclass(frozen=True)
class SourceInfo:
    source_id: int
    source_name: Optional[str]
    pipeline_run_id: Optional[str]

@runtime_checkable
class SourceSelectable(Protocol):
    def list_sources(self, name: str) -> list[SourceInfo]:
        """Enumerate available (source_id, source_name, pipeline_run_id) tuples for `name`."""
        ...

    def resolve_source(
        self, name: str, *, source_id: Optional[int] = None, run_id: Optional[str] = None
    ) -> Optional[SourceInfo]:
        """Resolve which source backs `name`'s raw read, honoring an explicit pin.

        Returns `None` when `name` has no tracked source at all (only legacy,
        pre-source-tracking rows with `source_id IS NULL`) — this is a normal state,
        not an error; `load_experiment` still succeeds using that legacy data, with
        no source identity to record.
        """
        ...
```

**Concurrent pin rejection, before the RPC is ever called:** `SupabaseReader` validates
`source_id`/`run_id` mutual exclusivity itself and raises a caller-safe
`ExperimentReadError` subclass (`AmbiguousSourceSelectionError`) when both are given —
per the "Structured errors, not tuples/strings" hard constraint, a caller must never see a
raw Postgres `RAISE EXCEPTION` message.

**Structural single-source enforcement (fixes this design's first-draft gap — "one source
per frame" was previously asserted in prose with no mechanism):** `load_experiment` never
calls `get_experiment_traits` unpinned. It always resolves a concrete source **first** via
`resolve_source(name, source_id=..., run_id=...)`, then passes that resolved `SourceInfo`'s
own `source_id` as an **explicit** `source_id_` pin to `get_experiment_traits` — even when
the caller gave no pin at all (in which case `resolve_source`'s own "latest" logic supplies
the concrete id). This makes single-source-per-frame true by construction rather than by
trusting `get_experiment_traits`'s unpinned per-scan `is_latest` disjunction to happen to
agree across every scan in the experiment — see the Risks section for why that disjunction
alone cannot be trusted to avoid cross-scan mixing. It also collapses what the first draft
called "double source resolution" (a real reproducibility problem — see Risks) into a
single resolution whose result is used for both the read and the provenance record: no
second round trip, no window for "latest" to advance between the two.

When `resolve_source` returns `None` (legacy-only data, no tracked source), `load_experiment`
falls back to an unpinned `get_experiment_traits` call (there is nothing to pin) and records
`source_id=None`/`source_name=None` in provenance — a correctly-represented "no source
identity available," not a fabricated one.

`SupabaseReader.load_experiment` gains matching optional keyword-only `source_id`/`run_id`
params (a concrete extension beyond the base Protocol's signature — Python's structural
typing doesn't require every implementer to share it).

**Rejected alternative:** add `source_id`/`run_id` params directly to the
`ExperimentReader.load_experiment` Protocol signature. Rejected as scope creep on a port
three other adapters would have to grow a meaningless parameter for.

### D3: Provenance identity — wired through `ResultStore.create_run`, not the unused `start_run`

**Status: resolved — corrected from this design's first draft, which routed the new
`source_id`/`source_name` recording exclusively through `tools/_ports.py`'s `start_run`.
`start_run` has zero real callers today** (confirmed via `grep -rn "start_run"
bloommcp/src bloommcp/tests`) **— every shipped producer tool
(`qc_clean.py`, `qc_inspect.py`, `remove_outliers.py`, `pca_analysis.py`, `clustering.py`,
`descriptive_stats.py`, `umap_analysis.py`) calls `_ports.store().create_run(...,
source_csv=_ports.raw_source_for(params.experiment))` directly**, with `Provenance`
already stamped generically (with no experiment context) by `contract/wrap.py`'s
`as_mcp_tool` decorator at line ~115. Wiring only through `start_run` would ship a code
path no production call exercises.

**Corrected decision:** add a `source: Optional[SourceInfo] = None` parameter to
`ResultStore.create_run` (`result_store/ports.py`'s Protocol, plus both
`SupabaseResultStore.create_run` and `FakeResultStore.create_run`), mirroring the existing
`source_csv: Optional[Path] = None` parameter exactly. Inside `create_run` (both adapters),
before storing `provenance` in the per-run state dataclass, merge it in:

```python
if source is not None:
    provenance = provenance.model_copy(
        update={"source_id": source.source_id, "source_name": source.source_name}
    )
```

This is a **single change point** shared by every caller (mirrors how `source_csv`'s
hashing is centralized once in `SupabaseResultStore.commit`, not duplicated per tool) —
`commit()`'s existing `state.provenance.model_copy(update={...})` call for
`outputs`/`output_keys`/`output_sha256`/`version_dir`/`user_label` already flows through to
`to_version_entry()` unchanged, so `source_id`/`source_name` ride along automatically once
set at `create_run` time.

Each of the 7 producer-tool call sites gains one line, mirroring their existing
`source_csv=_ports.raw_source_for(params.experiment)`:

```python
source=_ports.source_for(params.experiment),
```

`_ports.py` gains `source_for(filename)`, mirroring `raw_source_for` exactly:

```python
def source_for(filename: str) -> Optional[SourceInfo]:
    return (
        _reader.resolve_source(filename)
        if isinstance(_reader, SourceSelectable)
        else None
    )
```

`start_run` (still unused by any shipped tool) is updated for consistency with the same
one-line addition, so it doesn't silently drift further out of date for whatever future
tool eventually adopts it — but it is not the load-bearing wiring path.

**Manifest schema v3→v4 (additive), mirrors the shipped v2→v3 bump exactly:**

- `manifest/schema.py`: `CURRENT_SCHEMA_VERSION = 4`; `VersionEntry` gains a v4-additive
  block (`source_id: Optional[int] = None`, `source_name: Optional[str] = None`), same
  pattern as the existing `# --- v3 additive ---` block.
- `contract/provenance.py`: `Provenance` gains `source_id`/`source_name` fields (defaulting
  to `None`) so `model_copy(update={...})` has somewhere to write them;
  `to_version_entry()` passes them through. **`Provenance.stamp()` itself gains no new
  kwargs** — nothing calls it with source context (the decorator that calls `stamp()` has
  no experiment/reader context at all); the merge happens later, at `create_run()` time,
  matching the existing `input_validation` precedent (`qc_clean.py` also merges that field
  into `provenance` via `model_copy` before `create_run`, not via `stamp()`).
- `result_store/ports.py`'s `StoredRun` dataclass gains `source_id: Optional[int] = None`,
  `source_name: Optional[str] = None`; `StoredRun.from_version_entry` passes them through
  from the `VersionEntry`, mirroring how it already projects `seed`/`agent`/`environment`.
- **`ExperimentBlock` is unchanged.** Its `source_path`/`input_sha256` fields are plain
  `str`, not `Optional[str]` — but `supabase_store.py:195-218` already treats a `None`
  source path as `source_path=""`/`input_sha256=""` (this is how `FakeReader`-style
  path-less adapters already work today, since `raw_source_for` returns `None` for any
  adapter that isn't `RawSourced`). No type change needed.
- Deploy ordering follows the same v3 precedent: upgrade readers before any writer emits
  v4.

**Rejected alternative (this design's own first draft):** route recording exclusively
through `start_run`/`Provenance.stamp()` kwargs. Rejected — verified dead code, would ship
untested-in-production.

**Rejected alternative:** loosen `ExperimentBlock.source_path`/`input_sha256` to `Optional`
and add `source_id`/`source_name` there instead. Rejected — unnecessary, and would conflate
"the experiment's raw-input identity" with "which source this run resolved" (a per-run
fact, like `seed`, correctly living on `VersionEntry`).

### D4: `list_experiments()` DB enumeration — corrected cost model

**Status: resolved — corrected from this design's first draft, which claimed
`trait_columns`/`total_columns` could be computed via a cheap PostgREST `count`-aggregate
query. That's wrong: PostgREST's `count=exact` counts matched rows of a relation; it
cannot compute `COUNT(DISTINCT trait_name)`, and no existing view exposes a per-experiment
distinct-trait-name count (`cyl_scan_trait_names` is global, not experiment-scoped).**

Tier 1 gave no "list all experiments" or "summarize one experiment" RPC — only per-experiment
`get_experiment_traits`/`list_experiment_trait_sources`. `list_experiments()` queries
`cyl_experiments` directly via a PostgREST table read for the experiment roster (id, name —
exact column set to confirm against the live schema during implementation), reusing the
schema-wide `bloom_agent` grant Tier 1's review already confirmed covers this table, and the
soft-delete filter the `bloom_agent` RLS `SELECT` policy already applies
(`deleted_at IS NULL`).

**Corrected decision:** `rows` (distinct plant count) is genuinely cheap — a plain
`count=exact` HEAD query against `cyl_plants` filtered through the experiment's waves.
`trait_columns`/`total_columns`, however, have no cheap path: computing them accurately
requires the same `get_experiment_traits` bulk fetch `load_experiment` itself performs,
deduplicating `trait_name` client-side. **This is accepted as a genuine, explicit
tradeoff of moving off a local directory scan** — a directory `ls` was free; a Postgres
read is not. `list_experiments()` therefore costs one bulk fetch per experiment being
listed, same order of magnitude as loading each one. If this proves too slow at real
experiment-count scale, a dedicated summary RPC is the natural follow-up (deferred, not
built now, per this project's complexity-budget convention: no performance data yet shows
the direct approach doesn't scale) — this is the one place the "zero new migrations" goal
is knowingly in tension with a cheap listing, resolved in favor of zero new migrations for
this change.

**Partial-failure semantics:** a `get_experiment_traits` failure for one experiment during
listing excludes that experiment from the returned list (logged server-side), rather than
failing the whole `list_experiments()` call — fails open per-item, consistent with "an
empty list, not an error, when none are available" in the baseline `ExperimentReader Port`
requirement's "List experiments enumerates available inputs" scenario.

**`ExperimentSummary.filename` for a DB-sourced entry is `str(experiment_id)`** — no
fabricated extension. This is load-bearing: `list_available_experiments.py` prints
`exp.filename` verbatim as the value to pass back into `load_experiment(name)`
("`To analyze an experiment, use its filename (e.g., '{experiments[0].filename}')`"); under
D1's DB-only resolution, `load_experiment` requires an `int`-parseable `name`, so
`ExperimentSummary.filename` must already be in that shape or the discovery→read round trip
silently breaks. `.stem` is the same value (there is no extension to strip).

**Rejected alternative:** a dedicated summary RPC now. Deferred, not built, absent
performance data showing the direct approach is too slow.

### D5: `sample_id` uniqueness is validated at load time, not assumed

**Status: resolved — new decision, added in response to review; not in this design's
first draft.**

The roadmap's column-role mapping assigns `sample_id ← cyl_plants.qr_code`. But
`cyl_plants`' own uniqueness constraint is `UNIQUE (wave_id, qr_code)` — a QR code is unique
only *within* one planting cohort (`cyl_wave`), not experiment-wide, and QR-tag reuse
across waves of the same experiment is an ordinary operational occurrence, not a contrived
edge case. Every downstream tool treats `sample_id` as uniquely naming one physical plant.
This change does not relitigate the roadmap's column mapping (that decision was made and
re-verified at the roadmap level, independent of this proposal), but it does not ship
silently trusting it either.

**Decision:** after the long→wide pivot, `SupabaseReader` validates that `sample_id` values
are unique across the returned frame's rows. A collision raises a structured
`ExperimentReadError` subclass (`AmbiguousSampleIdentityError`) naming the colliding
`qr_code` (not the underlying `plant_id`s, to avoid leaking internal ids in an
agent-facing message) rather than silently returning a frame where two physically distinct
plants share one `sample_id`. The pivot also retains `cyl_plants.id` (`plant_id` from the
RPC) as a `metadata_cols` entry — not LLM-facing by default, but present in the frame so a
collision (or any downstream question about a specific row) is traceable back to the exact
DB row, not just the ambiguous `qr_code`.

**Rejected alternative:** silently compose a wave-qualified `sample_id` (e.g.
`f"{wave}:{qr_code}"`) to guarantee uniqueness unilaterally. Rejected — that's a column-role
mapping change, which is the roadmap's decision to make (it's already been reviewed once at
that level), not something to quietly redefine inside this adapter-rewrite change. Failing
loudly on an actual collision surfaces the question to a human rather than presupposing an
answer.

## Column-role mapping (unchanged from the roadmap, restated for implementers)

| canonical role | ← DB source | notes |
| --- | --- | --- |
| `genotype` | `accessions.name` | required |
| `sample_id` | `cyl_plants.qr_code` | the replicate unit for cylinder data — see D5 for the uniqueness check this change adds around it |
| `replicate` | *(none)* | optional; never use `wave_number` (wave is a planting cohort, not the replicate unit — resolved upstream, never load-bearing) |
| `image_path` | cylinder image path | optional |
| *(metadata)* | `cyl_waves.number`→`wave`, `plant_age_days`, `date_scanned`, `cyl_plants.id`→`plant_id` (D5, traceability) | plain columns, not roles |

The long→wide pivot keys on `(scan_id, plant_id)`, with one output column per distinct
`trait_name`. An experiment with zero trait rows (a valid, Tier-1-documented state — "an
experiment with no trait rows returns cleanly") produces a frame with zero `trait_cols`,
not an error; `int(name)` resolving to a real `cyl_experiments` row is what determines
found-vs-not-found, independent of whether that experiment has any measurements yet.

## Risks / Trade-offs

- **`float4` precision** — `trait_value` is Postgres `real` (~7 significant figures,
  confirmed at `cyl_scan_traits.value REAL`); a value like `4.2` round-trips as
  `4.19999980926514`. Pre-existing in Tier 1's RPC, not introduced here — worth a
  regression test pinning the expected float behavior (see Testing) rather than being
  surprised by it in a downstream QC/stats tool.
- **`is_latest` partitions per-scan, not per-experiment** — `cyl_scan_traits_source`'s
  `is_latest` is `max(source_id) OVER (PARTITION BY scan_id)`. For a multi-wave experiment
  where reprocessing has only reached some scans, an *unpinned* bulk fetch can legitimately
  span different `source_id`s per scan — this is exactly why D2 never calls
  `get_experiment_traits` unpinned: `resolve_source` picks one concrete `source_id` first
  (the experiment-wide max, not scan-by-scan), and that single id is what gets pinned and
  recorded. A consequence worth flagging, not fixed by this change: a scan with even one
  trait touched by a newer run bumps that scan's `max(source_id)`, which can make *other*,
  unrelated legacy (`source_id IS NULL`) trait rows on the same scan fail `is_latest` —
  previously-good measurements can look "not latest" rather than "not yet recomputed."
  This is a pre-existing property of the shipped Tier 1 view, out of this change's scope to
  fix, but callers pinning an explicit `source_id` (via `SourceSelectable`) can route
  around it when it matters.
- **Application-level exposure surface widens, independent of the DB grant.** Today's raw
  tier is scoped to whatever CSV happens to be physically present in a given deployment's
  `BLOOM_TRAITS_DIR` — an incidental, per-deployment boundary. After this change,
  `load_experiment(str(experiment_id))`/`list_experiments()` can address any
  `cyl_experiments` row in the shared DB by integer id, from any bloommcp deployment; there
  is no allowlist mechanism. The DB-level `bloom_agent` grant/RLS is unchanged (correctly a
  non-goal), but the *practical* reachable set through this specific code path is now the
  whole DB rather than one deployment's local directory contents. Flagged here explicitly
  rather than left implicit — no mitigation is proposed in this change (the roadmap's own
  stated auth model is the shared `bloom_agent` role with no per-experiment scoping), but a
  reviewer relying on "only files placed here are reachable" as an informal per-lab
  boundary should know that assumption no longer holds.
- **Deploy-ordering gate carried forward from v3** — a v4-writing deployment must have
  every reader upgraded first, same as v3.

## Testing (TDD)

- Unit tests against a fake DB row-fetcher injected into `SupabaseReader`. **Not** a mirror
  of `fake_reader.py` (that's a full alternate `ExperimentReader` reimplementation that
  bypasses `SupabaseReader`'s own pivot/rename logic entirely) — the correct precedent is
  `tests/data_access/conftest.py`'s `fake_supabase_storage` fixture shape (a
  function-boundary monkeypatch of the `supabase_client` module, with a `_no_network`
  guard), applied to the new RPC-call helper instead of the storage helpers.
- `load_experiment(str(experiment_id))` returns the expected wide frame with correct
  canonical roles. The `bloommcp/tests/fixtures/cylinder_*` golden fixtures are
  wide-format CSVs with `accession_id` (not the long-format `trait_name`/`trait_value`/
  `accession_name` shape `get_experiment_traits` returns) — building the long-format
  golden fixture requires melting the existing wide fixture and fabricating
  `source_id`/`trait_name` fields it doesn't carry today; this is real fixture-construction
  work, not a wrap-and-reuse.
- Both `source_id` and `run_id` pinned simultaneously raises `AmbiguousSourceSelectionError`
  before any RPC call (D2).
- A `source_id`/`run_id` pin matching nothing raises `ExperimentNotFoundError` (not a
  silent empty frame — an explicit pin the caller expected to resolve should fail loudly
  when it doesn't).
- An experiment with zero trait rows returns a valid frame with zero `trait_cols`, not an
  error.
- Multi-source fixture: an unpinned fetch pins a single resolved `source_id_` internally —
  test that the RPC call the fake receives always carries an explicit `source_id_`, never
  unpinned, once `resolve_source` returns a concrete source.
- A colliding `sample_id` across two rows raises `AmbiguousSampleIdentityError` (D5).
- `list_experiments()` returns DB-sourced summaries with a `filename` equal to
  `str(experiment_id)` (round-trips through `load_experiment` unchanged) and real
  (non-placeholder) `rows`/`trait_columns`/`total_columns`; a failure fetching one
  experiment's traits excludes it from the list rather than failing the whole call.
- `require_clean`/`version` resolution for the cleaned-output tiers is unchanged.
  `test_resolves_versioned_cleaned_then_raw` (existing) does **not** stay green
  unmodified — it currently exercises the local-disk raw fallback this change removes
  (`load_experiment("exp.csv")`, non-numeric name) and must be rewritten against a numeric
  experiment id and the fake DB fixture.
- **Two existing schema-version tests break on the v3→v4 bump and need fixing, not just
  new tests added alongside them:** `tests/contract/test_v2_backcompat.py`'s
  `test_newer_schema_version_is_rejected` (hardcodes `manifest_schema_version: 4` as the
  rejected/too-new case — becomes valid post-bump, must be updated to `5`) and
  `tests/contract/test_schema_v3.py`'s `test_current_schema_version_is_3` (hardcodes
  `CURRENT_SCHEMA_VERSION == 3` / `manifest_schema_version == 3` — both now `4`).
- Old (pre-v4) manifests still read under v4 code — extend `test_v2_backcompat.py`'s
  pattern with v3-fixture coverage alongside the existing v2 fixture.
- `test_provenance_roundtrip.py` / `test_provenance_to_version_entry.py` extended with
  `source_id`/`source_name` cases (populated and `None`).
- A `create_run(..., source=SourceInfo(...))` call (`SupabaseResultStore` and
  `FakeResultStore`) results in a committed `VersionEntry`/`StoredRun` carrying
  `source_id`/`source_name` — this is the test that actually proves the wiring reaches a
  shipped tool's manifest, replacing the first draft's dead `start_run`-only coverage.
- **Deletions, not updates:** `tests/data_access/test_local_reader.py::
  test_same_raw_bytes_yield_same_roles_as_supabase` and `tests/data_access/
  test_supabase_reader.py::test_raw_source_path_rejects_path_traversal`.

## Migration Plan

No Supabase migration — Tier 1 already shipped every DB primitive this tier calls. The only
"migration" is the manifest schema bump, following the existing additive-bump deploy gate
already established for v2→v3.

## Open Questions

- Exact `cyl_experiments` column set for `list_experiments()` — confirm against the live
  schema during implementation.
- Whether `get_experiment_traits`'s per-experiment bulk-fetch cost for `list_experiments()`
  (D4) is acceptable at real experiment-count scale, or needs a follow-up summary RPC —
  no performance data exists yet either way.
- Whether bloom#388, once built, should extend `SupabaseReader`'s dispatch (D1) or take a
  different shape entirely — deferred to whoever implements #388.
