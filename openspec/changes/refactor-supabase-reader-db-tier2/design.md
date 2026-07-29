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

- **bloom#413** ("upload input files to the bloommcp-data bucket") — the PR that would have
  given ad-hoc, non-DB-backed uploads a home — is **closed** (2026-07-23, not pursued). The
  original roadmap draft assumed a two-ID-shape dispatch (`experiment_id`-shaped names go to
  the DB, upload-filename-shaped names fall through to whatever #413 built); the roadmap
  itself now flags that assumption as stale and defers the decision to this change (see D1).
- **bloom#476** ("retire bloommcp's remaining `BLOOM_TRAITS_DIR` read bypasses") is open and
  explicitly blocked on this change landing — its own proposal
  (`retire-bloommcp-traits-dir-bypass`) states the local-disk raw-tier fallback "is genuinely
  load-bearing on the default (Supabase) backend today" and can't be retired until Tier 2
  replaces it. This change is what makes that bypass dead code.

## Goals / Non-Goals

- Goals:
  - `SupabaseReader.load_experiment` resolves raw reads from Postgres, not local disk.
  - `SupabaseReader.list_experiments()` enumerates DB experiments, not CSV files.
  - Source/run selection has an explicit, discoverable seam (not a silent "always latest").
  - Provenance survives losing its file-hash content-address: a DB-backed raw read still
    records *something* identifying which row served it.
  - Zero new migrations — Tier 1 already shipped every DB-side primitive this tier calls.
- Non-Goals:
  - Reintroducing an upload/blob-backed raw input path (that's whatever supersedes #413, if
    anything ever does — not this change).
  - Wiring `source_id`/`run_id` pinning into any LLM-facing tool parameter (Tier 3's
    LLM-facing-text scope; no consumer tool has a use case yet).
  - Touching `LocalReader`, `FakeReader`'s internals, or `_resolve_versioned_cleaned`.
  - A dedicated experiment-summary RPC (see D4 — deferred until proven necessary).

## Decisions

### D1: Two-ID-shape dispatch vs. DB-only resolution

**Status: resolved — DB-only.**

The roadmap's original design called for `load_experiment` to dispatch on the shape of
`name`: an experiment-id-shaped string (`str(experiment_id)`) resolves via the DB; a
filename-shaped string falls through to bloom#413's (never-built) upload resolution. Since
#413 closed with nothing to fall through to, keeping the dispatch would mean a non-numeric
`name` either (a) silently falls back to reading local disk — reintroducing the exact
`BLOOM_TRAITS_DIR` bypass bloom#476 is waiting on this change to remove — or (b) dispatches
to a branch that immediately no-ops, which is dead code the day it ships.

**Decision:** `load_experiment`'s raw tier is DB-only. `name` is parsed as
`int(name)`; a `ValueError` (non-numeric `name`) is treated identically to an unresolvable
`experiment_id` — a structured `ExperimentNotFoundError`, with a remedy message naming the
expected `str(experiment_id)` shape (Tier 3 will update the LLM-facing tool text to match;
until then the error message itself is the only place this is documented to a caller).

**Rejected alternative:** keep the dispatch shape with a local-disk fallback for
non-numeric names. Rejected because it directly reintroduces bloom#476's bypass and keeps a
path alive that would 404 in most deployments today (an empty `bloommcp_input/` bucket, per
the roadmap's Live-state facts) for no caller that currently exists.

### D2: Source/run selection — `SourceSelectable` capability

**Status: resolved.**

`get_experiment_traits`/`list_experiment_trait_sources` support pinning a `source_id_` or
`run_id_` (mutually exclusive, default latest by `is_latest`), but the `ExperimentReader`
port's `load_experiment(name, *, version, require_clean)` has no slot for either — widening
the base Protocol would force `LocalReader`/`FakeReader` to grow a parameter for a capability
neither has. Mirroring the existing `RawSourced` precedent (an `isinstance`-gated optional
capability, not a Protocol-wide change):

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
        """Resolve which source backs `name`'s raw read, honoring an explicit pin."""
        ...
```

`SupabaseReader.load_experiment` gains matching optional keyword-only `source_id`/`run_id`
params (a concrete extension beyond the base Protocol's signature — Python's structural
typing doesn't require every implementer to share it). `list_sources` wraps
`list_experiment_trait_sources`; `resolve_source` without a pin returns whatever
`get_experiment_traits` treated as latest for that call, so it's usable purely for
provenance-recording even when nothing pins anything.

`_ports.py` gains a `source_for(filename)` helper mirroring `raw_source_for`:

```python
def source_for(filename: str) -> Optional[SourceInfo]:
    return (
        _reader.resolve_source(filename)
        if isinstance(_reader, SourceSelectable)
        else None
    )
```

`start_run` calls it alongside `raw_source_for` and threads the result into
`Provenance.stamp(..., source_id=..., source_name=...)` — see D3.

**Rejected alternative:** add `source_id`/`run_id` params directly to the
`ExperimentReader.load_experiment` Protocol signature. Rejected as scope creep on a port
three other adapters (`LocalReader`, `FakeReader`, and any future adapter) would have to
grow a meaningless parameter for.

### D3: Provenance/manifest schema v3→v4 (additive)

**Status: resolved — mirrors the shipped v2→v3 bump exactly.**

Neither `Provenance` (`contract/provenance.py`) nor `VersionEntry`
(`manifest/schema.py`) has a `source_id`/`source_name` field today, and both are
`extra="forbid"`. Recording the DB source identity is therefore an additive schema bump,
not a rename — the same shape as the v2→v3 `+seed/agent/output_sha256` change:

- `manifest/schema.py`: `CURRENT_SCHEMA_VERSION = 4`; `VersionEntry` gains a v4-additive
  block (`source_id: Optional[int] = None`, `source_name: Optional[str] = None`), same
  pattern as the existing `# --- v3 additive ---` block. `Manifest.manifest_schema_version`
  needs no code change (it already defaults to `CURRENT_SCHEMA_VERSION`); the "newer version
  rejected" example in the spec text bumps from "e.g. 4" to "e.g. 5" (see the spec delta).
- `contract/provenance.py`: `Provenance` gains `source_id`/`source_name` fields;
  `Provenance.stamp()` gains matching optional kwargs; `to_version_entry()` passes them
  through.
- **`ExperimentBlock` is unchanged.** Its `source_path`/`input_sha256` fields are plain
  `str`, not `Optional[str]` — but `supabase_store.py:196-218` already treats a `None`
  source path as `source_path=""`/`input_sha256=""` (this is how `FakeReader`-style
  path-less adapters already work today, since `raw_source_for` returns `None` for any
  adapter that isn't `RawSourced`). A DB-backed `SupabaseReader` that drops `RawSourced`
  hits this exact, already-handled branch — no type change needed. `source_id`/`source_name`
  live on `VersionEntry` instead, matching how `seed` (also per-run, not per-experiment)
  lives there rather than on `ExperimentBlock`.
- Deploy ordering follows the same v3 precedent: upgrade readers before any writer emits
  v4.

**Rejected alternative:** loosen `ExperimentBlock.source_path`/`input_sha256` to `Optional`
and add `source_id`/`source_name` there instead. Rejected — unnecessary (the empty-string
path already round-trips cleanly through the existing str-typed fields) and would conflate
"the experiment's raw-input identity" (already the wrong home for something that can vary
per pinned run) with "which source this run resolved."

### D4: `list_experiments()` DB enumeration and summary counts

**Status: resolved.**

Tier 1 gave no "list all experiments" RPC — only per-experiment
`get_experiment_traits`/`list_experiment_trait_sources`. `list_experiments()` queries
`cyl_experiments` directly via a PostgREST table read (`get_postgrest_client().table(...)`,
not a new RPC/migration), reusing the schema-wide `bloom_agent` grant Tier 1's own review
already confirmed covers this table.

The harder question: `ExperimentSummary.rows`/`.trait_columns`/`.total_columns` are
displayed verbatim to the calling LLM by `list_available_experiments.py`
(`f"Samples: {exp.rows}, Traits: {exp.trait_columns}, Total columns: {exp.total_columns}"`)
— a `0` placeholder would be actively misleading, not a harmless stub, and computing exact
counts via a full `get_experiment_traits` bulk fetch per experiment just to build a listing
would repeat the exact per-trait-round-trip cost problem Tier 1 was built to avoid, at
listing scale (`N` experiments × full pivot each).

**Decision:** compute `rows` (distinct `plant_id` count) and `trait_columns`
(distinct `trait_name` count) per experiment via lightweight PostgREST `count`-aggregate
queries against the same tables `get_experiment_traits` joins — not a full bulk fetch. This
is `O(N)` cheap COUNT queries for a listing of `N` experiments, not `O(N × traits)` row
transfers. `total_columns` is `trait_columns` plus the fixed metadata-column count declared
by the canonical role table (§ below).

**Rejected alternative:** a dedicated summary RPC (e.g. `list_experiment_summaries()`
returning counts directly). Deferred, not rejected outright — no performance data yet shows
the per-experiment COUNT-query approach is too slow (per the project's own complexity-budget
convention: add complexity only with performance data showing the current approach doesn't
scale). If a future measurement shows this is too slow at real experiment-count scale, a
summary RPC is the natural follow-up.

**Open question, not resolved here:** the exact `cyl_experiments` column set (id/name/
whatever else) needs confirming against the live schema during implementation — this
design assumes at minimum an `id` and a human-readable name column exist, consistent with
every other `cyl_*` table referenced in this program, but wasn't independently re-verified
against a live schema dump while writing this proposal.

## Column-role mapping (unchanged from the roadmap, restated for implementers)

| canonical role | ← DB source | notes |
| --- | --- | --- |
| `genotype` | `accessions.name` | required |
| `sample_id` | `cyl_plants.qr_code` | the replicate unit for cylinder data |
| `replicate` | *(none)* | optional; never use `wave_number` (wave is a planting cohort, not the replicate unit — resolved upstream, never load-bearing) |
| `image_path` | cylinder image path | optional |
| *(metadata)* | `cyl_waves.number`→`wave`, `plant_age_days`, `date_scanned` | plain columns, not roles |

The long→wide pivot keys on `(scan_id, plant_id)` (or the finest grain `get_experiment_traits`
returns per plant/scan), with one output column per distinct `trait_name`.

## Risks / Trade-offs

- **`float4` precision** — `trait_value` is Postgres `real` (~7 significant figures); a
  value like `4.2` round-trips as `4.19999980926514`. Pre-existing in Tier 1's RPC, not
  introduced here, but this is the first Python consumer to actually hit it — worth a
  regression test pinning the expected float behavior rather than being surprised by it in
  a downstream QC/stats tool.
- **`is_latest` partitions per-scan, not per-(scan, trait)`** — a bulk read can under-report
  a partially-reprocessed scan's trait set. Tier 1's own design.md already flags this;
  Tier 2 callers (this pivot) should treat a missing trait as "not present in the latest
  delivery," not "never measured" — worth a docstring note on the pivot function, not a
  behavior change.
- **Double source resolution** — `start_run`'s new `source_for()` call and
  `load_experiment`'s own internal source resolution are separate call sites (mirroring how
  `raw_source_for`/`load_experiment` already duplicate path resolution today), so a single
  tool invocation resolves "which source is latest" twice. Accepted as consistent with
  existing precedent; not worth a shared-cache mechanism without evidence it matters.
- **Deploy-ordering gate carried forward from v3** — a v4-writing deployment must have
  every reader upgraded first, same as v3. Not new risk, just restating an existing
  constraint that now applies one version later.

## Testing (TDD)

- Unit tests against a fake DB row-fetcher injected into `SupabaseReader` (mirrors the
  existing `FakeReader` precedent) — no live DB required for this tier.
- `load_experiment(str(experiment_id))` returns the expected wide frame with correct
  canonical roles, built against the `bloommcp/tests/fixtures/cylinder_*` golden fixture
  (bloom#483, merged).
- Multi-source test: a fixture with two `source_id`s never mixes rows across them in one
  frame.
- `list_experiments()` returns DB-sourced summaries with non-placeholder counts.
- `require_clean`/`version` resolution for the cleaned-output tiers is unchanged — existing
  `test_resolves_versioned_cleaned_then_raw` stays green with no modification.
- Old (pre-v4) manifests still read under v4 code — extend `test_v2_backcompat.py`'s
  pattern with a `test_v3_backcompat.py`-equivalent case, or add v3-fixture coverage
  alongside the existing v2 fixture.
- `test_provenance_roundtrip.py` / `test_provenance_to_version_entry.py` extended with
  `source_id`/`source_name` cases (populated and `None`).
- **Deletions, not updates:** `tests/data_access/test_local_reader.py::
  test_same_raw_bytes_yield_same_roles_as_supabase` and `tests/data_access/
  test_supabase_reader.py::test_raw_source_path_rejects_path_traversal`.

## Migration Plan

No Supabase migration — Tier 1 already shipped every DB primitive this tier calls. The only
"migration" is the manifest schema bump, which follows the existing additive-bump deploy
gate (upgrade readers before any writer emits v4) already established for v2→v3.

## Open Questions

- Exact `cyl_experiments` column set for `list_experiments()` (see D4) — confirm against
  the live schema during implementation.
- Whether a future caller ever needs to pin `source_id`/`run_id` through a tool parameter
  (currently no consumer does) — left for whoever files that need, per D2's non-goal.
