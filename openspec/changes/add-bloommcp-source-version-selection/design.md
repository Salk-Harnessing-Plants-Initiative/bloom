## Context

Issue #626 identifies two independent, already-half-built selection axes that no tool exposes:
which raw DB source/pipeline-run backs a raw read, and which cleaned version a `require_clean`
tool consumes. The issue's own "Decisions needed" section flags four open questions:

1. Protocol extension shape vs. capability-only route — genuinely open; resolved in Decision 1.
2. Exact LLM-facing field wording — genuinely open; resolved in Decision 4.
3. Should `list_experiment_sources` exist for non-Supabase backends at all — pre-resolved by the
   issue's own "Proposed changes" item 1 (isinstance-gated, not backend-excluded); reaffirmed
   explicitly in Decision 6 below rather than left implicit.
4. Relationship to #625 — pre-resolved by the issue itself ("no shared migration or code
   dependency... sequence independently"); reaffirmed in Decision 7 below. #625 is merged (PR
   #628); this change does not touch `supabase_client.py`'s timeout work or
   `get_experiment_summary_counts`.

## Goals / Non-Goals

- Goals: wire the already-shipped `SourceSelectable`/versioned-manifest mechanisms into tool
  surfaces; keep every new parameter optional and default-preserving; give `LocalReader`/
  `FakeReader` a clear, typed rejection instead of a bare `TypeError`.
- Non-Goals: redesigning how `SupabaseReader` resolves or stores sources/versions (PR #557,
  already shipped and out of scope); adding a DB migration; changing `list_experiments()`'s
  return shape; retroactively archiving the unrelated backlog of already-merged-but-unarchived
  OpenSpec changes noticed during this investigation (`add-bloommcp-clustering-tool`,
  `add-bloommcp-qc-inspect-tool`, etc. — a pre-existing repo-wide gap, not this change's job).

## Decisions

### Decision 1: Extend the `ExperimentReader` Protocol directly, not a capability-only route

Issue #626 poses this as open: declare `source_id`/`run_id` on the `ExperimentReader` Protocol
itself (every adapter must implement-or-reject), or keep them isolated inside the existing
isinstance-gated `SourceSelectable` capability (`list_sources`/`resolve_source`), leaving
`load_experiment`'s Protocol signature untouched.

The codebase's established convention for *optional* capabilities is exactly the
isinstance-gated route (`SourceSelectable`, `RawSourced` — both in `ports.py`), so the capability
route was the more consistent-looking option at first read. It does not actually work here,
though: the mechanism that carries a pin *into a read* already shipped in PR #557 as extra kwargs
on `SupabaseReader.load_experiment`'s own concrete signature — not as a separate method that
`SourceSelectable` could own instead. A caller holding a value typed as the generic
`ExperimentReader` Protocol has no lever to pass `source_id`/`run_id` through to a read except via
`load_experiment` itself; `SourceSelectable.resolve_source()` only *discovers* what a pin would
resolve to, it does not *apply* one to a subsequent read. Keeping the Protocol signature untouched
would mean every one of `qc_clean`/`qc_inspect`/`load_experiment_data` has to reach past its
declared `ExperimentReader` type (an unchecked attribute call or a runtime `cast`) to use a
feature the issue explicitly wants exposed as ordinary tool parameters. That is worse for
discoverability than the Protocol-extension route, not better — the codebase's isinstance-gating
convention is for capabilities that are genuinely absent on some adapters (a fake has no on-disk
path; a local reader has no DB source), not for a parameter every adapter must at least be able to
receive and answer definitively (accept and honor it, or reject it clearly).

**Decision**: add `source_id: Optional[int] = None, run_id: Optional[str] = None` to
`ExperimentReader.load_experiment`'s Protocol declaration. Every adapter must accept both kwargs;
`SupabaseReader` already does (no change to its logic). `LocalReader`/`FakeReader` gain the kwargs
and immediately raise the new `SourcePinningUnsupportedError` when either is non-`None` — this is
the "required-but-defaults-to-a-clear-rejection" shape, not silent ignoring. The existing
`SourceSelectable` capability is unchanged and still owns *discovery* (`list_sources`,
`resolve_source`); the Protocol addition only owns *applying* a pin to a read, and only
`SupabaseReader` can do anything with it.

### Decision 2: A new, distinct error class for the reject-not-ignore contract

`AmbiguousSourceSelectionError` (both given) and `SourcePinNotFoundError` (given pin, no match) are
both about a source pin that *could* apply but doesn't resolve cleanly. `LocalReader`/`FakeReader`
rejecting *any* non-`None` pin is a different condition — "this adapter has no source concept at
all" — so it gets its own class, `SourcePinningUnsupportedError(ExperimentReadError)`, following
the file's existing one-class-per-condition convention. Being an `ExperimentReadError` subclass
means it flows through every existing `except ExperimentReadError` / `errors=(ExperimentReadError,)`
site with no new catch/mapping code.

### Decision 3: `core_list_experiment_sources` returns formatted text, not JSON

The two closest precedents disagree: `list_available_experiments` returns a human-readable text
block with a trailing "next step" hint; `list_existing_analyses` returns `json.dumps(...)`. This
tool is picked as a **discovery** tool in the same family as `list_available_experiments` (list
things, then tell the caller what to do next), so it follows that precedent: formatted text with
each source's fields on its own line and a closing hint naming the exact follow-up call
(`qc_clean(experiment=..., source_id=...)`). The pre-existing text/JSON inconsistency between the
two precedents is not resolved by this change.

### Decision 4: LLM-facing field wording

`source_id`/`run_id` `Field(description=...)` text (on `qc_clean`, `qc_inspect`,
`load_experiment_data`) states plainly that omitting both preserves today's default and that
`core_list_experiment_sources` is how to discover valid values — so an agent that has not already
called the discovery tool still gets a hint rather than guessing a `source_id`. The `version`
field on the 6 `require_clean=True` tools states its own tool's specific default explicitly (most
say "latest"; `remove_outliers` says "latest_qc") rather than a generic "the default version",
so an agent reading only that one tool's schema is not misled about which tier it will get.

### Decision 5: Multi-source tool-layer tests use a monkeypatched `SupabaseReader`, not `FakeReader`

An earlier draft of this proposal planned to make `FakeReader` implement `SourceSelectable` so
`core_list_experiment_sources` and `qc_clean`'s advisory note could be tested against multiple
in-memory sources. That is wrong and was caught in review: `FakeReader` deliberately does **not**
implement `SourceSelectable` today, and `test_supabase_reader.py::test_fake_reader_is_not_source_selectable`
locks that in as intentional (`FakeReader` has no source-versioned substrate to fake). Making it
`SourceSelectable` would flip that test red and directly contradict this change's own new
scenarios — "A non-Supabase backend gets a clear 'not applicable' message" (`bloommcp-source-selection`)
and "A source pin given to an adapter with no source concept is rejected clearly"
(`bloommcp-experiment-read`) both name `FakeReader` as an adapter that lacks the capability.

**Decision**: `FakeReader` stays non-`SourceSelectable`, unchanged. Tests that need multi-source
*data* (the "multiple sources are listed" and "multi-source experiment gets an advisory note"
scenarios) extend the existing monkeypatched-`SupabaseReader`-boundary pattern
`test_supabase_reader.py` already uses for Tier-2 multi-source coverage (`fake_supabase_db`
fixtures, no network) — a real `SupabaseReader` against a fake Postgres/PostgREST boundary,
injected via `_ports.configure(reader=...)`, rather than a second capability bolted onto
`FakeReader`. Tests that need a `SourceSelectable`-less adapter (`LocalReader`, `FakeReader`) keep
using either as-is.

### Decision 6: `load_experiment_data` forces `version="raw"` when a source pin is given

`load_experiment_data`/`_ports.load_frame` call `load_experiment(filename)` with the Protocol
default `version="latest"` today. `SupabaseReader.load_experiment` raises
`AmbiguousSourceSelectionError` whenever a source pin is given but a cleaned tier would otherwise
resolve first (a pin cannot apply to a cleaned read — only `version="raw"` is source-versioned).
Without a fix, passing `source_id`/`run_id` to `load_experiment_data` would raise on every
experiment that already has *any* cleaned output — the common case, not an edge case, since most
registered experiments get QC'd at least once.

**Decision**: when either `source_id` or `run_id` is non-`None`, `load_frame` forces
`version="raw"`, mirroring `qc_clean`/`qc_inspect`'s existing producer-side forcing. This is
consistent with the semantics of a source pin (it only ever meant anything against the raw tier)
and makes the "explicit source pin is honored" scenario actually reachable, not merely
schema-valid.

### Decision 7: Recording the pinned source in committed provenance needs no new plumbing, just a test

`qc_clean.py` and `qc_inspect.py` already pass `source=frame.resolved_source` into
`store.create_run(...)`, and `SupabaseReader.load_experiment`'s raw tier already sets
`ExperimentFrame.resolved_source` from whatever `resolve_source(name, source_id=, run_id=)`
returns. So once tasks 3.2/4.2 thread the new params into the existing `load_experiment(...)` call
and leave that `source=frame.resolved_source` line untouched, a pinned run's committed
`source_id`/`source_name` is automatically traceable — no new provenance code is needed. This is
easy to break silently (e.g. a future edit re-deriving `source` independently instead of reusing
the frame's), so this proposal adds an explicit spec scenario and regression test locking the
guarantee in rather than leaving it as an unstated side effect.

### Decision 8 (reaffirming issue decision 3): `core_list_experiment_sources` is always registered

Per the issue's own "Proposed changes" item 1, the tool is isinstance-gated (returns a message on
`LocalReader`/`FakeReader`), not excluded from the tool set when `BLOOM_STORAGE_BACKEND=local`.
It is also **not** added to `ALWAYS_INCLUDE_MCP_TOOLS` — it is an occasional discovery aid a caller
reaches for after `list_available_experiments`, not a foundational read path every session needs
pinned. `test_tool_name_lists_match_live_registry` (`test_devendor_invariants.py`) only verifies
names already in the hand-list resolve to live tools, so it gives no signal either way on this
choice — it is a judgment call, not something that test can confirm or refute.

### Decision 9 (reaffirming issue decision 4): no dependency on #625

#625's `list_experiments()` aggregate-RPC perf fix (PR #628) is merged into `staging`. This change
touches neither `supabase_client.py`'s timeout/RPC work nor `get_experiment_summary_counts`; the
two changes share only a parameter-naming convention (`source_id_`/`run_id_`), not code. Sequenced
independently, as the issue specifies.

## Risks / Trade-offs

- **`remove_outliers`'s and `cross_experiment_correlations`'s non-uniform defaults are the easiest
  place to introduce a silent regression** (see proposal.md) — mitigated by a spy-based test per
  tool asserting the exact `load_experiment` call args when the new field is omitted, not just
  output-shape equivalence. `cross_experiment_correlations`'s spy must target `reader.load_experiment`
  itself, not the `_load_cleaned` helper wholesale — mocking `_load_cleaned` out entirely cannot
  catch a bug where its new `version` param is accepted but never forwarded to the inner
  `load_experiment` call.
- **Multi-source production scale is tiny today** — the issue's own live check found only 2 of 224
  experiments with more than one source. The multi-source discovery/advisory-note path is
  therefore likely to go unexercised by real users for a long time; a subtle wording or
  off-by-one bug there could ship unnoticed. This proposal's test coverage (Decision 5) is
  synthetic (fake Postgres boundary), which is the practical ceiling for an automated PR — a
  manual check against a real multi-source experiment (`experiment_id=1` or `7206207`) on staging
  is recommended before/after merge as a follow-up, not a merge gate.
- **`FakeReader` needs new multi-source fixture support, but not via `SourceSelectable`** (Decision
  5) — sequenced as its own early task so later tasks are not blocked mid-stream, using the
  monkeypatched-`SupabaseReader` pattern instead.
- **Scope is wide (9 call sites + 1 new tool) but each site's change is small and uniform** — the
  risk is inconsistency across sites, not individual complexity; tasks.md sequences one task per
  site with its own test rather than one large multi-file task.

## Migration Plan

Purely additive — every new parameter defaults to `None`/absent and every default path is
unchanged. No data migration. No feature flag needed: the new parameters simply do not exist in
any current caller's request, so nothing changes until a caller opts in.

## Open Questions

None outstanding — the issue's four "Decisions needed" are resolved above (Decisions 1, 4, 8, 9).
