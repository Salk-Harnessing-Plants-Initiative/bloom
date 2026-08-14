## Context

Follow-up to #33 (parent) via #663. `bloomcli` already ships `bloomctl` to PyPI through
`release-bloomcli.yml` / `version-bloomcli.yml`. As verified directly against
`origin/main` (`git show origin/main:.github/workflows/release-bloomcli.yml`), main's actual
current pipeline is a straightforward two-job shape: `validate-release` (tag/version/changelog/
lint/test) → `build-and-publish` (build, `twine check`, wheel-import + `bloomctl --version`
checks, then `uv publish` — all in the same job, which also holds the OIDC publish credential).
A more hardened rework — splitting build/verify from publish into a third job so the publish
credential never shares a job with freshly-built or third-party code, plus a deeper
`pkgutil.walk_packages` dependency-chain import check — has merged to `staging` (tracked by
issue #629, a real incident where a broken pre-release shipped because a shallow
`import bloomctl` check passed while every real command failed on a lazily-imported
dependency) but has **not** reached `main` as of this writing. This proposal mirrors main's
actual current two-job pattern, since that is what a reviewer will actually diff this PR
against and what `bloomcli`'s own pipeline looks like today — not staging's not-yet-rolled-up
rework, which this PR has no reason to get ahead of.

(This design went through an earlier draft that mistakenly mirrored the staging-only
three-job shape, discovered via a 5-subagent OpenSpec review — a review that was itself
partly working from the same stale/mixed branch state. The mismatch was caught before
implementation was finalized by directly diffing `origin/main` against `origin/staging` for
every touched file. The corrected design below reflects `main`'s real content.)

## Goals / Non-Goals

- Goals:
  - A `pip install bloommcp` path gated the same way `bloomctl`'s currently is: tag/version/
    changelog checks, lint + tests, then a build/verify/publish job that stops before
    publishing on any failure.
  - Match `bloomcli`'s actual current pattern closely enough that the two release pipelines in
    this repo read as variations of one design, not two ad hoc ones.
  - Close the cross-firing gap named in the issue (item 6) for both workflows, with an
    automated regression guard (not just a manual dry run) proving both.
- Non-Goals:
  - Adopting the not-yet-merged three-job split (`build-and-verify` separated from
    `build-and-publish`) or the `pkgutil.walk_packages` dependency-chain walk from `staging`'s
    `#629` rework. That rework belongs to a future `staging` → `main` rollup, not this PR;
    bringing it in here would make bloommcp's pipeline diverge from bloomcli's _actual_ current
    pipeline instead of matching it.
  - A PyPI-specific `README.pypi.md` / install-command rewriting. `bloomcli`'s current
    `build-and-publish` job rewrites both `pyproject.toml` and `README.pypi.md` (and, on
    staging's rework, versioned install-command strings); bloommcp has no `README.pypi.md`, so
    its link-pinning step only rewrites `pyproject.toml`.
  - Registering the actual PyPI trusted publisher (external, manual, no repo access).
  - Pinning `twine`'s version in the `uvx twine check` step. `release-bloomcli.yml`'s own
    equivalent step is already unpinned today, inconsistent with this repo's stated convention
    of pinning CI security/build tools (`openspec/project.md`). Copying the existing
    (inherited) inconsistency rather than fixing it unilaterally here, out of scope for this
    change.

## Decisions

- **Mirror `bloomcli`'s actual current two-job shape**: `validate-release` (tag/version/
  changelog/lint/test, no build) → `build-and-publish` (build, `twine check`, wheel-import +
  entry-point checks, then `uv publish` — same job, same credential). No separate verify job,
  no artifact checksum handoff — that is staging's not-yet-merged pattern, not main's.
  `build-and-publish` requests `id-token: write` and the `pypi` environment exactly like
  `bloomcli`'s does.
  - Note: `bloomcli`'s current `build-and-publish` job explicitly `checkout`s twice (once
    implicitly per job) and its own `Verify wheel imports` step is a plain
    `import bloomctl; print(...)` one-liner — the same shallow shape #629 later showed to be
    insufficient. It is not this PR's job to backport that fix onto `release-bloomcli.yml` (out
    of scope for #663; tracked separately by whatever rolls `staging` up to `main`).
- **Wheel-import check for bloommcp goes slightly beyond `bloomcli`'s literal one-liner**,
  independent of anything from `bloomcli`/`staging`: it imports `bloom_mcp`, its `tools`/
  `manifest`/`server` submodules, calls `bloom_mcp.server.build_app()`, and additionally
  imports the concrete Supabase-backed adapters —
  `bloom_mcp.data_access.SupabaseReader`, `bloom_mcp.result_store.SupabaseResultStore` — plus
  their `postgrest`/`supabase` transitive imports. This is a deliberate, targeted addition
  specific to bloommcp's own structure: those two adapter classes are wired only inside
  `main()`'s composition root, which sits _after_ the `--version` early return this PR adds —
  so neither `build_app()` nor `bloom-mcp --version` alone would catch a broken adapter or a
  lazily-imported dependency going stale. This closes the same _class_ of gap #629 named for
  bloomcli, without needing bloomcli's own fix to have landed on main first.
- **Entry-point verification is `bloom-mcp --version`**, matching `bloomcli`'s own single
  `bloomctl --version` check (no deeper entry-point-wiring verification exists on main's
  current `bloomcli` pipeline either, so there is nothing further to mirror here).
- **Tag-prefix guard mechanics**: a **job-level** `if:` condition on `validate-release` —
  `if: github.event_name != 'release' || startsWith(github.event.release.tag_name, 'bloommcp-')`
  (and the `bloomctl-` symmetric on `release-bloomcli.yml`) — not a step. Two reasons:
  1. `github.event.release.tag_name` is unset (empty string) on `workflow_dispatch`, so a bare
     `startsWith(...)` with no `github.event_name` check would incorrectly skip every dispatch
     run.
  2. GitHub Actions skip-propagates through `needs:`, so gating only `validate-release` is
     sufficient to also skip `build-and-publish` — no need to duplicate the guard on every job,
     and no need to touch `build-and-publish`'s own steps at all.
- **Tag convention**: `bloommcp-vX.Y.Z`, matching the existing `bloomctl-vX.Y.Z` tags actually
  used for every `bloomcli` release to date (`git tag -l` shows `bloomctl-v0.1.0a1..a4`; no
  bare `vX` or `X` tag has ever been used in practice). Retrofitting the `bloomctl-` guard onto
  `release-bloomcli.yml` narrows its previously-_documented_ (but never actually used) support
  for a bare `vX.Y.Z`/`X.Y.Z` tag — accepted deliberately (see the `bloomcli-packaging` delta),
  with `bloomcli/RELEASE_PROCESS.md` updated to document only the prefixed form going forward.
- **Capability placement**: the tag-prefix requirement is split across two capability deltas —
  `bloommcp-pypi-release` (bloommcp's own guard) and `bloomcli-packaging` (bloomcli's side of
  the retrofit). `bloomcli-packaging` does not yet exist under `openspec/specs/` on `main`
  (verified directly — it exists only on other, not-yet-merged branches), so this ADDED delta
  will seed that capability's first requirement when this change is archived; it is not
  overwriting an existing spec.
- **Pre-release version (`0.1.0a1`)**: matches `bloomctl`'s progression
  (`aN → bN → rcN → stable`) and signals accurately that #33's public tool-surface question is
  still open.
- **Test selection for the release gate**: `uv run --extra test pytest -m "not integration and
not live_smoke" -q`, matching the exact marker set `pr-checks.yml`'s `python-audit` job
  already excludes for bloommcp (the `integration` marker needs the full statsmodels/umap
  oracle fixtures and is "known to intermittently stall in CI containers"; `live_smoke` needs
  the live Docker Compose + Supabase + MinIO stack, unavailable to an isolated release runner).
  This is bloommcp-specific — `bloomcli` only has an `integration` marker to exclude.
- **`[project.urls]` added but link-pinning scoped to `pyproject.toml` only** (no
  `README.pypi.md` to rewrite, and no `bloommcp==X`/`bloommcp@X` install-command strings exist
  in-repo yet to rewrite either).
- **`bloommcp/uv.lock` is regenerated as part of the version bump**, both in this proposal's
  own `0.1.0` → `0.1.0a1` change and in every future `version-bloommcp.yml` run. Confirmed by
  reading `origin/main:bloommcp/uv.lock` directly: it records `version = "0.1.0"` for the
  `bloommcp` package itself (line 109), and `bloommcp` is one of the services
  `scripts/check-uv-locks.py` checks (invoked by both the `uv-lock-check` pre-commit hook and
  `pr-checks.yml`'s `python-audit` job) — so a version bump with no matching `uv lock` run
  fails CI. `version-bloommcli.yml`'s own comment ("bloomctl commits no uv.lock, not in
  scripts/check-uv-locks.py") is stale on main too (`bloomcli/uv.lock` exists and `bloomcli`
  **is** in `SERVICES`) — `version-bloommcp.yml` does not copy that stale assumption; it runs
  `uv lock` and its `add-paths` includes `bloommcp/uv.lock` alongside `bloommcp/pyproject.toml`.
- **A `bloommcp/RELEASE_PROCESS.md` runbook is added**, mirroring `bloomcli/RELEASE_PROCESS.md`
  (Overview, Version management, Cutting a release, Setup requirements incl. the PyPI trusted
  publisher field table, Troubleshooting), substituting `bloommcp`/`bloom-mcp`/
  `bloommcp-vX.Y.Z` and describing the extended wheel-import check in place of a walk-packages
  mention (since bloomcli's own doc doesn't describe one either, on main). Without this, the
  release-cutting procedure in `tasks.md` would be lost once this OpenSpec change is archived —
  `bloomcli` solved this with a durable doc; bloommcp's proposal should not regress on that.

## Risks / Trade-offs

- The first real Release will fail at the publish step until the PyPI trusted publisher is
  registered (external, manual — see proposal's Out of Scope). Mitigated by `workflow_dispatch`
  exercising every other gate (lint, test, build, twine-check, wheel-import, entry-point) as a
  dry run with zero PyPI risk.
- `bloommcp/pyproject.toml`'s version bump (`0.1.0` → `0.1.0a1`) is a judgment call beyond the
  issue's literal checklist. Flagged explicitly in the proposal so it can be rejected in review
  without blocking the rest of the pipeline.
- Retrofitting `release-bloomcli.yml` touches a currently-live, production-critical file that
  real `bloomctl` releases depend on, and — like all release/version workflow changes — cannot
  be exercised by normal PR CI. Mitigated by: keeping the change to a single job-level `if:`
  line (no step changes), the existing + extended `tests/unit/test_release_bloomcli_workflow_shape.py`
  regression suite, and committing this change in its own isolated commit so it is trivially
  revertable independent of everything else in this PR.
- Once `staging`'s `#629` hardening eventually rolls up to `main`, `release-bloomcli.yml`'s
  shape will change again (three jobs) and `release-bloommcp.yml` will read as inconsistent
  with it until someone applies the same hardening to bloommcp's pipeline. Accepted: matching
  what is _actually on main today_ is more important than pre-emptively matching a shape that
  hasn't landed there yet, and the eventual rollup PR is the natural place to harden both
  pipelines together.

## Migration Plan

Purely additive (two new workflow files, a new changelog, a version bump + lockfile
regeneration, a version-metadata addition, a `[project.urls]` addition, a new release
runbook), plus one narrow retrofit to an existing workflow (a single job-level guard
condition). No runtime behavior of the shipped MCP server changes. Rollback is deleting the
two new workflow files, reverting the `release-bloomcli.yml` guard, and reverting the version
bump; nothing depends on them existing.

**Ordering**: version introspection (`__version__`/`--version`) → CHANGELOG.md → pyproject
version bump + lockfile → `version-bloommcp.yml` → `release-bloommcp.yml` (depends on
everything above existing, since its verify step imports and runs them) →
`release-bloomcli.yml` retrofit (independent of everything else; isolated commit). See
`tasks.md` for the exact commit sequence.

**Base branch**: `main`, not `staging` (`openspec/project.md`'s documented default for feature
branches). This is the correct choice for this specific class of change regardless of which
branch currently has the more-hardened `release-bloomcli.yml`: GitHub only evaluates
`on: release`-triggered workflows from the repo's default branch (`main`), so
`release-bloommcp.yml` cannot fire at all unless it lives there; and the immediately-preceding
bloommcp-infrastructure change (#590) also branched from and merged to `main` directly.

## Open Questions

- None blocking. The PyPI trusted-publisher registration is a known, tracked follow-up outside
  this PR's reach.
