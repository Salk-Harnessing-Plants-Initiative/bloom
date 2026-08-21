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
dependency) but has **not** reached `main` as of this writing.

(This design went through an earlier draft that mistakenly mirrored the staging-only
three-job shape, discovered via a 5-subagent OpenSpec review — a review that was itself
partly working from the same stale/mixed branch state. The mismatch was caught before
implementation was finalized by directly diffing `origin/main` against `origin/staging` for
every touched file. A subsequent, human-solicited review then pointed out that the
"match main's actual two-job shape" reasoning only justifies _retrofitting_ an already-live,
production-critical file (`release-bloomcli.yml`) — `release-bloommcp.yml` is a brand-new
file, so there is no cost to giving it the safer three-job shape directly instead of
deliberately shipping the exact flaw #629 already proved unsafe into new infrastructure. The
design below reflects that revision: `release-bloommcp.yml` adopts the three-job,
credential-isolated shape (checksum-verified artifact handoff, no `pkgutil.walk_packages`
walk); `release-bloomcli.yml` itself stays two-job, unchanged beyond the tag-prefix guard —
retrofitting the rest of #629's hardening onto that live file is still out of scope for #663.)

## Goals / Non-Goals

- Goals:
  - A `pip install bloommcp` path gated at least as strictly as `bloomctl`'s currently is:
    tag/version/changelog checks, lint + tests, then a build/verify/publish sequence that stops
    before publishing on any failure and never runs third-party code alongside the publish
    credential.
  - Read as a variation of `bloomcli`'s design, not an ad hoc one — same validate/build/publish
    stages, same tag-prefix guard mechanics, same wheel-import-goes-beyond-the-entry-point
    reasoning — even where the two pipelines' job counts now differ (see Decisions).
  - Close the cross-firing gap named in the issue (item 6) for both workflows, with an
    automated regression guard (not just a manual dry run) proving both.
- Non-Goals:
  - The `pkgutil.walk_packages` exhaustive dependency-chain walk, or the `--prerelease=allow`
    double-resolution import check, from `staging`'s `#629` rework. Those are mechanically
    exhaustive hardening on top of the credential/job-isolation fix (which this PR _does_
    adopt, see Decisions); bloommcp's own explicit adapter-class imports already close the
    specific gap #629 exploited for this package's structure, so the additional exhaustive walk
    is deferred to whatever eventually rolls the rest of `staging`'s `#629` rework to `main`.
  - Retrofitting `release-bloomcli.yml` itself with the three-job split. That file is live and
    production-critical; this PR's only change to it is the single job-level tag-prefix guard
    (plus the unrelated `version-bloomcli.yml` uv.lock fix found during review — see Decisions).
    Splitting it into three jobs is a larger, independently-revertable change that belongs with
    whatever eventually rolls the rest of `staging`'s `#629` hardening to `main`.
  - A PyPI-specific `README.pypi.md` / install-command rewriting. `bloomcli`'s current
    `build-and-publish` job rewrites both `pyproject.toml` and `README.pypi.md` (and, on
    staging's rework, versioned install-command strings); bloommcp has no `README.pypi.md`, so
    its link-pinning step only rewrites `pyproject.toml`.
  - Registering the actual PyPI trusted publisher (external, manual, no repo access) — though
    see tasks.md §9.1: recommended to happen now, in parallel with review, not deferred.

## Decisions

- **`release-bloommcp.yml` adopts the three-job, credential-isolated shape**:
  `validate-release` (tag/version/changelog/lint/test, no build) → `build-and-verify` (build,
  record the artifact's checksum, `twine check`, wheel-import + entry-point checks, re-verify
  the checksum, upload the artifact — no publish credential) → `build-and-publish` (download
  the verified artifact, re-verify its checksum, `uv publish` — holds `id-token: write` and the
  `pypi` environment, runs no other code). This mirrors staging's `#629` rework's job/credential
  split (minus the `pkgutil.walk_packages` walk — see Non-Goals) rather than `release-bloomcli.yml`'s
  current on-`main` two-job shape, because `release-bloommcp.yml` is a brand-new file: unlike
  retrofitting a live production workflow, there is no diff-minimization argument for shipping
  the exact credential/third-party-code coupling #629 already proved unsafe into it.
  - **`release-bloomcli.yml` itself is not retrofitted with this split** — that file's only
    change in this PR is the tag-prefix guard (a single job-level `if:`, no step changes),
    consistent with the original diff-minimization reasoning for touching a currently-live,
    production-critical file real `bloomctl` releases depend on. `bloomcli`'s `build-and-publish`
    job still explicitly `checkout`s and still has the plain `import bloomctl; print(...)`
    one-liner #629 showed to be insufficient — backporting that fix is still out of scope for
    #663, tracked separately by whatever rolls the rest of `staging`'s `#629` rework to `main`.
  - **Found during review**: `version-bloomcli.yml` (a different file, same "existing bloomcli
    workflow" family) had its own real, unrelated bug — its `bump-version` job never ran
    `uv lock`, so `bloomcli/uv.lock` drifted out of sync with every version bump despite being a
    checked service lockfile. Fixed as its own isolated commit (tasks.md §6.5–6.7); this is not
    part of the `#629` job-split question, just a bug this review surfaced while reading the
    same file family.
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
  2. GitHub Actions skip-propagates through `needs:` (transitively, across both hops of
     `validate-release` → `build-and-verify` → `build-and-publish`), so gating only
     `validate-release` is sufficient to also skip the other two jobs — no need to duplicate
     the guard, and no need to touch `build-and-verify`'s or `build-and-publish`'s own steps.
- **`twine` is pinned (`uvx twine@7.0.0 check dist/*`)**, unlike `release-bloomcli.yml`'s own
  equivalent step, which is still unpinned today — inconsistent with this repo's stated
  convention of pinning CI security/build tools (`openspec/project.md`). Copying that
  inherited inconsistency into brand-new code would have had no excuse; fixing it unilaterally
  on `release-bloomcli.yml` itself remains out of scope for #663 (that file's diff is
  minimized to the tag-prefix guard — see above).
- **Tag convention**: `bloommcp-vX.Y.Z`, matching the existing `bloomctl-vX.Y.Z` tags actually
  used for every `bloomcli` release to date (`git tag -l` shows `bloomctl-v0.1.0a1..a4`; no
  bare `vX` or `X` tag has ever been used in practice). Retrofitting the `bloomctl-` guard onto
  `release-bloomcli.yml` narrows its previously-_documented_ (but never actually used) support
  for a bare `vX.Y.Z`/`X.Y.Z` tag — accepted deliberately (see the `bloomcli-packaging` delta),
  with `bloomcli/RELEASE_PROCESS.md` updated to document only the prefixed form going forward.
- **Capability placement**: the tag-prefix requirement is split across two capability deltas —
  `bloommcp-pypi-release` (bloommcp's own guard) and `bloomcli-packaging` (bloomcli's side of
  the retrofit). `bloomcli-packaging` does not yet exist under `openspec/specs/` on `main` —
  confirmed directly (`git show main:openspec/specs/bloomcli-packaging/spec.md` fails). But an
  earlier check here stopped at `openspec/specs/` and missed `openspec/changes/`: an unarchived
  change, `openspec/changes/add-bloomcli-container-release/`, already sits on `main` today with
  its own `specs/bloomcli-packaging/spec.md` ADDED delta (a different requirement — "Container
  Image Buildable From Monorepo Source" — from a Dockerfile+GHCR PR, #515, that merged; the
  _archiving_ PR for it, #519, was closed/superseded by #636 without ever landing, so the
  change itself was never folded into `openspec/specs/`). So this PR's own ADDED delta for
  `bloomcli-packaging` is not seeding that capability's first requirement in isolation — it is
  one of two independent, currently-unarchived ADDED deltas for the same capability name, sitting
  side by side on `main`. Each adds a distinct requirement (container-image-buildable vs.
  release-workflow-tag-scoping), so they are not in _content_ conflict and should combine
  cleanly into one `bloomcli-packaging` spec with two Requirement sections — **but only if**
  whichever change archives second treats the base spec `openspec archive` produces from
  whichever archives first as the thing being extended, rather than re-running a greenfield
  ADDED that clobbers it. Flagged here explicitly so neither archiving pass does that silently;
  no action is needed from this PR itself beyond this note, since it cannot control the order
  the two changes get archived in.
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
- The two pipelines are now inconsistent in the other direction: `release-bloommcp.yml` ships
  three-job credential isolation from day one, while `release-bloomcli.yml` stays two-job (the
  publish credential still shares a job with `twine check` and the wheel-import smoke test)
  until `staging`'s `#629` rework eventually rolls up to `main`. Accepted: retrofitting that
  hardening onto a currently-live, production-critical file real `bloomctl` releases depend on
  is a larger, separately-reviewable change than this PR's scope, whereas giving a brand-new
  file the safer shape costs nothing. The eventual rollup PR is the natural place to bring
  `release-bloomcli.yml` up to the same isolation `release-bloommcp.yml` now has.
- The `bloomcli-packaging` capability now has two independent, unarchived ADDED deltas on `main`
  at once (this PR's, and `openspec/changes/add-bloomcli-container-release`'s — see "Capability
  placement" above). Neither change can control the other's archival order; flagged explicitly
  so whichever archives second merges into the existing base spec rather than overwriting it.

## Migration Plan

Purely additive (two new workflow files, a new changelog, a version bump + lockfile
regeneration, a version-metadata addition, a `[project.urls]` addition, a new release
runbook), plus two narrow, independent retrofits to existing workflows: a single job-level
guard condition on `release-bloomcli.yml`, and a `uv lock` step + `add-paths` entry on
`version-bloomcli.yml` (the unrelated bug found during review — see Decisions). No runtime
behavior of the shipped MCP server changes. Rollback is deleting the two new workflow files,
reverting the `release-bloomcli.yml` guard and the `version-bloomcli.yml` fix, and reverting
the version bump; nothing depends on them existing.

**Ordering**: version introspection (`__version__`/`--version`) → CHANGELOG.md → pyproject
version bump + lockfile → `version-bloommcp.yml` → `release-bloommcp.yml` (depends on
everything above existing, since its verify step imports and runs them) →
`release-bloomcli.yml` tag-prefix retrofit → `version-bloomcli.yml` uv.lock fix (both
independent of everything else and of each other; isolated commits). See `tasks.md` for the
exact commit sequence.

**Base branch**: `main`, not `staging` (`openspec/project.md`'s documented default for feature
branches). This is the correct choice for this specific class of change regardless of which
branch currently has the more-hardened `release-bloomcli.yml`: GitHub only evaluates
`on: release`-triggered workflows from the repo's default branch (`main`), so
`release-bloommcp.yml` cannot fire at all unless it lives there; and the immediately-preceding
bloommcp-infrastructure change (#590) also branched from and merged to `main` directly.

## Open Questions

- None blocking. The PyPI trusted-publisher registration is a known, tracked follow-up outside
  this PR's reach.
