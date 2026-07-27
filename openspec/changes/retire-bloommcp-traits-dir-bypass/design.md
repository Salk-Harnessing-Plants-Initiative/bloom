## Context

Issue #476 asks to audit two `BLOOM_TRAITS_DIR` read bypasses and either (a) route them
through `ExperimentReader`, or (b) delete them if provably unreachable.

- **Site (a), `qc_inspect.py:503`**, is already resolved — not this change's scope. #479's
  PR review independently found and fixed this same line (for a different reason —
  fully-local-mode provenance), and that work merged into `staging` as
  [PR #526](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/pull/526). See the
  Reconciliation note below for how this was caught.
- **Site (b), `supabase_reader.py`'s raw-tier fallback**, is not a simple bypass to route
  or delete — it is the *only* thing serving raw experiment reads on the default
  (Supabase) backend today. `bloommcp/docs/data-access-roadmap.md`'s Live-state facts
  traced the full write path for the `bloommcp_input/` Storage bucket and confirmed it has
  **no producer anywhere in the repo**. Two PRs already tried to fix this exact site the
  "obvious" way and were closed for that reason:
  - PR #368 — read `bloommcp_input/<name>` directly. Closed 2026-07-23: "since prod has no
    input data, switching the raw source is non-breaking" — i.e. it would just always 404.
  - PR #413 — add an upload endpoint that writes to `bloommcp_input/` first, so #368's read
    would have something to find. Closed the same day — the upload path isn't being
    pursued.
  - The roadmap's actual plan (Tier 2) is to rewrite the raw tier to query Bloom's
    Postgres directly by `experiment_id`, which needs a new bulk-read RPC (Tier 1, not yet
    filed, gated on Benfica's review) — a materially bigger, separate piece of work.

## Goals / Non-Goals

- Goals:
  - Fix `supabase_reader.py`'s two distinct doc problems: the module docstring citing the
    closed bucket-migration plan as the removal trigger (the same belief that already
    produced two wrong-direction PRs), and `_LOCAL_RAW_DEPRECATION`'s "promoted, not
    slated for removal" framing, which contradicts this proposal's own decision that the
    fallback has a tracked retirement path. These are two separate sentences with two
    separate problems, not one blanket "stop citing the bucket" edit.
- Non-Goals:
  - Re-implementing `qc_inspect.py`'s provenance-source fix — already shipped by
    #479/PR #526; nothing to do here.
  - Implementing `data-access-roadmap.md`'s Tier 1/2 (DB-direct rewrite).
  - Changing `SupabaseReader`'s raw-tier read behavior, return values, or `RawSourced`
    contract.
  - Touching `LocalReader` / `BLOOM_STORAGE_BACKEND=local`.

## Decisions

- **Decision:** Treat `SupabaseReader`'s local-disk raw-tier read as an intentional,
  load-bearing interim adapter — document it as such (pointing at
  `data-access-roadmap.md` Tier 2 as the tracked retirement path) rather than attempting a
  partial fix.
  - Alternative considered — wire the raw tier to `supabase_client.read_input_csv`
    (`bloommcp_input/`), per the issue's literal option (a). Rejected: this is PR #368's
    exact approach, already attempted and closed because the bucket has no producer; it
    would silently break raw reads (a real regression) in every environment today.
  - Alternative considered — delete the fallback outright, per the issue's option (b)
    ("if provably unreachable"). Rejected: it is not unreachable. It is the only path
    serving raw reads on the default backend today; deleting it without a replacement
    breaks `qc_clean`/`qc_inspect`/every tool that reads an uncleaned experiment.
  - Alternative considered — implement `data-access-roadmap.md` Tier 1/2 as part of this
    change. Rejected as out of scope: that program is DRAFT, gated on a Postgres migration
    Benfica hasn't reviewed yet, and is a materially larger, separate piece of work than
    what issue #476 itself scopes.

## Risks / Trade-offs

- **This does not fully close #476.** The issue's underlying architectural ask —
  retiring `BLOOM_TRAITS_DIR` from the default read path — is genuinely `data-access-
  roadmap.md` Tier 2's job, not something this change can do safely on its own. Mitigation:
  say so explicitly in the proposal rather than claim full closure; recommend #476 stay
  open and re-scoped to track that roadmap dependency.
- **Documentation-only changes to `supabase_reader.py` could look like "not really fixing
  anything."** Mitigation: the value is specifically preventing a third wrong-direction
  attempt at the bucket-wiring fix (two already closed) — that's a real, if narrow,
  outcome, not busywork.
- **Keeping the fallback alive indefinitely assumes `BLOOM_TRAITS_DIR` is complete.**
  Treating the local-disk raw-tier read as a stable "load-bearing interim adapter" (rather
  than wiring it to the empty bucket) is the right call given #368/#413's closures, but it
  leaves an unstated assumption: a Supabase-backend deployment's `BLOOM_TRAITS_DIR`
  actually contains every experiment a user might request a raw read for. If it doesn't,
  those reads 404 silently, indefinitely — `data-access-roadmap.md` Tier 2 isn't filed yet
  and has no timeline. Mitigation: state this explicitly in `proposal.md`'s Scope section
  rather than leave it implicit; this change does not resolve the risk, only stops
  documenting past it.

## Migration Plan

None — no data or schema migration, and no runtime behavior change at all in this
revision (doc/warning-text only, `qc_inspect.py` having dropped out of scope per the
Reconciliation note below).

## Open Questions

- Should #476 be closed by this change, kept open and re-scoped to depend on
  `data-access-roadmap.md` Tier 2, or split into a new tracking issue for the
  `SupabaseReader` DB-direct rewrite specifically? This proposal assumes "kept open,
  re-scoped" as the default — recommend Evelyn/Elizabeth confirm at review time.

## Follow-ups (not this change)

- `bloommcp/tests/test_persistence_import_guard.py`'s AST-based import guard currently
  forbids only `{"supabase", "AnalysisDir"}`, not `TRAITS_DIR` — it would not catch a
  future PR silently reintroducing a `TRAITS_DIR`-based bypass elsewhere (the same class
  of bug #476 itself is about). Worth extending in a follow-up; out of scope here since
  this change makes no code changes to guard.

## Reconciliation note (5-agent review, 2026-07-24)

A 5-subagent review (Code Quality / Testing / Scientific Rigor / Security / Behavioural
Correctness) independently converged on the same blocking finding: the first two
revisions of this proposal targeted `qc_inspect.py:503` as work still to do, but by the
time PR #530 was opened, [PR #526](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/pull/526)
(#479's implementation) had already merged into `staging` — fixing that exact line for an
unrelated reason (fully-local-mode provenance) — 34 seconds before this PR opened, per the
review's own timestamp check. The proposal's citations (line 64's import, line 421's
comment, a "failing test to write") no longer matched the file at all: the import was
already gone, the comment already documented the fix, and the regression test
(`test_source_csv_honors_local_root_only_mode`) already existed.

**Resolved:** rebased this branch onto the current `staging` tip (which now includes
#526), dropped the `qc_inspect.py` site from scope entirely rather than rescoping it to a
confirmation step (per the review's own Suggestion — there is nothing left to confirm
that the merged test doesn't already cover), and reworded `proposal.md`/`design.md`'s
"Why"/Context to state the site is resolved via #526, citing the PR number the way #368/
#413 already were. This is exactly the kind of drift the roadmap docs' own reconciliation
logs exist to catch — recorded here for the same reason.
