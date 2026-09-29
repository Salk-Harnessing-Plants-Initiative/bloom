## Why

`bloommcp_output/` is one flat root shared by every user. It holds about 16 `<tool_class>_<stem>/`
children per experiment. Both outlier audit scripts (#585, #593) list all of it on every run. As
a result, checking one experiment costs O(all analyses), and anything that breaks root
enumeration breaks every audit at once. That includes #854's backstop at about 50,000 children.

[#919](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/919) sketches four fixes.
This change takes the smallest, **scope the sweeps**. The other three (partitioned key layout,
catalog index, `list_v2` cursor) need storage migrations and stay tracked in #919.

## What Changes

- **`--experiment IDENTIFIER` filter (repeatable)** on `audit_stale_outlier_trims.py` and
  `audit_untrustworthy_outlier_fits.py`, plus an `experiments=` argument on their scan functions.
  A scoped run resolves each `<tool_class>_<stem>/` prefix directly and never lists the shared
  root. Without the flag, behavior is unchanged.
- **New pure helper `bloom_mcp/audit_scope.py`.** It validates and de-duplicates the requested
  identifiers and derives stems the way `AnalysisDir` does. It also builds the report's
  `experiment_scope` record (design Decisions 1, 2 and 4).
- **Scoped reports disclose their scope.** The persisted payload gains `experiment_scope`. Scoped
  results also gain `unevaluated` (no manifest, or no `latest` pointer), and the summary line
  starts with `scoped to N experiment(s): `. A scoped run in which every requested experiment
  errors exits `1` and persists nothing (design Decision 3).

## Non-Goals

- **None of #919's structural fixes.** No partitioned key layout, no catalog index, no `list_v2`
  cursor. The "audit everything" case is unchanged, and #919 stays open for it.
- **No change to hit or error semantics**, and no change to MCP tools, `AnalysisDir` or the storage
  backends.
- **No access-control change.** The RLS edge #919 mentions depends on per-user identity, which is
  not yet built (`bloommcp/docs/roadmap.md`).

## Impact

- **Affected specs:** `bloommcp-outliers-staleness-audit` and `bloommcp-outliers-fit-audit`. Each
  gets two ADDED requirements and one MODIFIED requirement. The MODIFIED requirement qualifies
  "enumerates every `<tool_class>_<stem>` manifest" and the enumeration-failure and exit-code
  scenarios as full-sweep behavior.
- **Affected code (`bloommcp/`):**
  - `scripts/audit_stale_outlier_trims.py` and `scripts/audit_untrustworthy_outlier_fits.py`:
    module and scan-function docstrings are updated too.
  - New `src/bloom_mcp/audit_scope.py`: ships in the wheel and must import with no environment
    set, so it passes the release `walk_packages` smoke.
  - `CHANGELOG.md`: one `[Unreleased]` line.
- **Reports:** additive only. Persisted payloads gain `experiment_scope`, and scoped results gain
  `unevaluated`. A full sweep's scan result is byte-identical to today, so the existing tests stay
  unmodified.
