## Context

Issue #476 asks to audit two `BLOOM_TRAITS_DIR` read bypasses and either (a) route them
through `ExperimentReader`, or (b) delete them if provably unreachable.

- **Site (a), `qc_inspect.py:503`**, is cheap and safe: `_ports.raw_source_for` already
  exists and is already the pattern `qc_clean.py`/`_ports.start_run` use for the identical
  provenance-sourcing problem. #479's PR review independently found and fixed this same
  line (for a different reason — fully-local-mode provenance), but that branch is
  unmerged, so `staging` still has the bare `TRAITS_DIR` read today.
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
  - Fix site (a) now — it's genuinely independent, low-risk, and already validated by
    #479's implementation.
  - Fix `supabase_reader.py`'s two distinct doc problems: the module docstring citing the
    closed bucket-migration plan as the removal trigger (the same belief that already
    produced two wrong-direction PRs), and `_LOCAL_RAW_DEPRECATION`'s "promoted, not
    slated for removal" framing, which contradicts this proposal's own decision that the
    fallback has a tracked retirement path. These are two separate sentences with two
    separate problems, not one blanket "stop citing the bucket" edit.
- Non-Goals:
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

## Migration Plan

None — no data or schema migration. The `qc_inspect.py` change only alters the resolved
`source_csv` path when a non-default input root is configured (e.g. a custom
`BLOOM_EXPERIMENT_LOCAL_ROOT`); on the default Supabase backend with no such override, the
resolved path is unchanged.

## Open Questions

- Should #476 be closed by this change, kept open and re-scoped to depend on
  `data-access-roadmap.md` Tier 2, or split into a new tracking issue for the
  `SupabaseReader` DB-direct rewrite specifically? This proposal assumes "kept open,
  re-scoped" as the default — recommend Evelyn/Elizabeth confirm at review time.
