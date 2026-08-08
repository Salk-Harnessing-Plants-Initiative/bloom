## Context

Issue #626 identifies two independent, already-half-built selection axes that no tool exposes:
which raw DB source/pipeline-run backs a raw read, and which cleaned version a `require_clean`
tool consumes. The issue's own "Decisions needed" section flags four open questions; three are
effectively pre-resolved by its own "Proposed changes" list (source-selection tool is
isinstance-gated, not backend-excluded; #625 is sequenced independently). Only the Protocol-shape
question and the exact field wording needed real analysis — both resolved below.

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

## Risks / Trade-offs

- **`remove_outliers`'s and `cross_experiment_correlations`'s non-uniform defaults are the easiest
  place to introduce a silent regression** (see proposal.md) — mitigated by a spy-based test per
  tool asserting the exact `load_experiment` call args when the new field is omitted, not just
  output-shape equivalence.
- **`FakeReader` needs new multi-source fixture support** before any tool-layer multi-source test
  can be written — sequenced as its own early task so later tasks are not blocked mid-stream.
- **Scope is wide (9 call sites + 1 new tool) but each site's change is small and uniform** — the
  risk is inconsistency across sites, not individual complexity; tasks.md sequences one task per
  site with its own test rather than one large multi-file task.

## Migration Plan

Purely additive — every new parameter defaults to `None`/absent and every default path is
unchanged. No data migration. No feature flag needed: the new parameters simply do not exist in
any current caller's request, so nothing changes until a caller opts in.

## Open Questions

None outstanding — the issue's four "Decisions needed" are resolved above (Decision 1) or in
proposal.md's Impact section (spec-capability placement for the two unarchived-baseline tools).
