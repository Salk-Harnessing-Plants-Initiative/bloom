## Context

Follow-up to #33 (parent) via #663. `bloomcli` already ships `bloomctl` to PyPI through
`release-bloomcli.yml` / `version-bloomcli.yml`.

This design has gone through two rounds of "the branch's premise about `release-bloomcli.yml`
went stale while it sat in review" — worth recording both, since the second is the direct
cause of this revision.

**Round 1 (caught before implementation, 2026-08-14).** An earlier draft mistakenly mirrored
`staging`'s three-job hardened shape as if it were `main`'s, discovered via a 5-subagent
OpenSpec review that was itself partly working from the same stale/mixed branch state. Directly
diffing `origin/main` against `origin/staging` for every touched file showed `main`'s actual
pipeline at the time was a straightforward two-job shape (`validate-release` →
`build-and-publish`, credential and third-party code sharing one job), while the hardened
three-job rework — job/credential isolation plus a `pkgutil.walk_packages` exhaustive import
walk and a `--prerelease=allow` double-resolution check, all tracked by issue #629 (a real
incident where a broken pre-release shipped because a shallow `import bloomctl` check passed
while every real command failed on a lazily-imported dependency) — lived only on `staging`. A
subsequent, human-solicited review then pointed out that "match main's actual two-job shape"
only justifies _retrofitting_ an already-live, production-critical file — `release-bloommcp.yml`
is brand-new, so there was no cost to giving it the safer three-job shape directly. The design
at that point: `release-bloommcp.yml` three-job and credential-isolated, but without the
`pkgutil.walk_packages`/`--prerelease=allow` hardening (deferred, see the now-removed Non-Goal
below); `release-bloomcli.yml` itself untouched beyond the tag-prefix guard.

**Round 2 (this revision, found by a second human review, 2026-08-26).** PR #667 ("promote
staging to main") merged that same hardened three-job `release-bloomcli.yml` shape —
`pkgutil.walk_packages` walk, `--prerelease=allow` double-resolution, and an
entry-point-failure-mode check — into `main` on 2026-08-22, eight days after this branch was
opened. The branch was never rebased or re-verified against `main` afterward, so by review time
`release-bloommcp.yml` shipped with a comment claiming bloomcli's hardened shape was "not yet on
`main`" (false since 2026-08-22) and a `build-and-verify` job measurably weaker than what
`release-bloomcli.yml` actually runs today on exactly the axis #629 was about. This revision
closes that gap directly: `release-bloommcp.yml`'s `build-and-verify` now matches
`release-bloomcli.yml`'s current `main` shape on every axis (see Decisions), and the stale
"deferred to staging's eventual rollup" framing is gone throughout this document. Running the
new `--prerelease=allow` check for the first time also surfaced a real, independent bug — see
"Wheel-import check" in Decisions.

This revision also retargets the PR from `main` to `staging`, per `openspec/project.md`'s
staging-first branching convention (feature branches are created from and target `staging`;
`main` is the consolidation/release branch, updated only by periodic `staging → main`
promotions) — see "Base branch" under Migration Plan for why the original choice of `main` was
a misreading of that convention, not a deliberate exception to it.

## Goals / Non-Goals

- Goals:
  - A `pip install bloommcp` path gated at least as strictly as `bloomctl`'s currently is:
    tag/version/changelog checks, lint + tests, then a build/verify/publish sequence that stops
    before publishing on any failure and never runs third-party code alongside the publish
    credential.
  - Read as a variation of `bloomcli`'s design, not an ad hoc one — same validate/build/publish
    stages, same tag-prefix guard mechanics, same job/credential split, and now the same
    exhaustive wheel-import + entry-point-failure hardening, axis for axis (see Decisions).
  - Close the cross-firing gap named in the issue (item 6) for both workflows, with an
    automated regression guard (not just a manual dry run) proving both, plus a third guard
    (`release-tag-guard.yml`, added in this revision) closing the adjacent gap the review round
    found: a typo'd tag makes _both_ workflows skip silently, with no failing run to say so.
- Non-Goals:
  - Retrofitting `release-bloomcli.yml` with anything beyond the tag-prefix guard (a single
    job-level `if:`, no step changes) and the unrelated `version-bloomcli.yml` `uv.lock` fix
    found during review (see Decisions). It needs no further retrofit: `main` already carries
    the full hardened three-job shape as of PR #667 (2026-08-22) — `release-bloommcp.yml`
    matches that shape directly now rather than deferring to it, see Decisions.
  - A PyPI-specific `README.pypi.md` / install-command rewriting. `bloomcli`'s current
    `build-and-publish` job rewrites both `pyproject.toml` and `README.pypi.md` (and, on
    staging's rework, versioned install-command strings); bloommcp has no `README.pypi.md`, so
    its link-pinning step only rewrites `pyproject.toml`.
  - Registering the actual PyPI trusted publisher (external, manual, no repo access) — though
    see tasks.md §11.1: recommended to happen now, in parallel with review, not deferred.

## Decisions

- **`release-bloommcp.yml` matches `release-bloomcli.yml`'s current `main` shape, axis for
  axis**: `validate-release` (tag/version/changelog/lint/test, no build) → `build-and-verify`
  (build, record the artifact's checksum, `twine check`, an exhaustive `pkgutil.walk_packages`
  wheel-import walk run twice — default resolution and `--prerelease=allow` — plus the
  entry-point checks below, re-verify the checksum, upload the artifact — no publish credential)
  → `build-and-publish` (download the verified artifact, re-verify its checksum, `uv publish` —
  holds `id-token: write` and the `pypi` environment, runs no other code). `release-bloommcp.yml`
  is a brand-new file, so there was never a diff-minimization argument for giving it anything
  less than what `bloomcli` already proved out for exactly this class of bug (#629); this
  revision closes the gap where an earlier draft fell short of that (see Context).
  - **`release-bloomcli.yml` itself needs no further retrofit here** — `main` already carries
    this full shape as of PR #667 (2026-08-22), independent of this PR. This PR's only change to
    that file remains the tag-prefix guard (a single job-level `if:`, no step changes).
  - **Found during review**: `version-bloomcli.yml` (a different file, same "existing bloomcli
    workflow" family) had its own real, unrelated bug — its `bump-version` job never ran
    `uv lock`, so `bloomcli/uv.lock` drifted out of sync with every version bump despite being a
    checked service lockfile. Fixed as its own isolated commit (tasks.md §6.5–6.7); this is not
    part of the `#629` job-split question, just a bug this review surfaced while reading the
    same file family.
- **Wheel-import check now matches `bloomcli`'s current exhaustive shape, plus a targeted
  addition**: it runs `pkgutil.walk_packages` over every `bloom_mcp` submodule — twice, once at
  default resolution and once with `--prerelease=allow`, exactly like `bloomcli`'s current
  `main` check — and additionally imports the concrete Supabase-backed adapters
  (`bloom_mcp.data_access.SupabaseReader`, `bloom_mcp.result_store.SupabaseResultStore`) and
  calls `bloom_mcp.server.build_app()` explicitly. The walk alone would still import those
  adapters' modules (and so exercise their `postgrest`/`supabase` transitive imports), but the
  explicit class-level imports pin the exact names a refactor could otherwise let silently drop;
  `build_app()` is asserted directly since it, not just an importable module, is bloommcp's own
  composition entry point. Running the `--prerelease=allow` pass for the first time (this
  revision) failed immediately: `httpx 1.0.dev5` resolved and broke `postgrest`'s import
  (`ImportError: cannot import name 'Timeout' from 'httpx'`) — the exact failure mode #629
  described, this time caught before merge instead of after. `bloomcli/pyproject.toml` already
  carries load-bearing `httpx<1.0`/`supabase<3` upper bounds for this reason;
  `bloommcp/pyproject.toml` had neither. Fixed by adding the matching bounds (see tasks.md).
- **Entry-point verification now matches `bloomcli`'s current fail-fast-check shape, adapted to
  bloommcp's different entry-point architecture**: `bloomcli`'s check proves an unhandled
  exception becomes a one-line message via `bloomctl.errors:main` (distinguishing the wrapped
  console script from the bare CLI, which `bloomctl --version` alone cannot do). `bloom_mcp`'s
  console script is the bare `bloom_mcp.server:main` — there is no wrapper to distinguish, so
  that exact check does not port over. What server.py's `main()` does document, and what
  `--version` alone cannot prove, is its own contract: `--version`/`-V` return before any env
  validation, and a real invocation with no env configured fails fast (raises `RuntimeError`)
  rather than silently starting the server or hanging in `uvicorn.run()`. The new check drives
  both paths explicitly (`bloom-mcp --version`, then a real `bloom-mcp` run under a 10s timeout
  with no env), and pins the registered console-script target
  (`entry_points(group="console_scripts")` must resolve `bloom-mcp` to `bloom_mcp.server:main`)
  the way `bloomcli`'s check pins `bloomctl.errors:main`.
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
  - **The regression tests for both guards were themselves weak** (found in this revision's
    review round): they asserted each clause's substring in the `if:` condition independently,
    never the joined `||` expression — a future edit that silently flipped `||` to `&&`, or
    otherwise broke the join, would still contain both substrings and pass, while permanently
    disabling every real release (a release event's `github.event_name != 'release'` clause is
    false, so `&&` would always evaluate to false regardless of the tag). Fixed by adding a
    small evaluator (`_guard_permits` in both test files) that parses the condition into exactly
    two `||`-joined clauses and exercises its actual truth table — `workflow_dispatch` always
    passes, a real release with the right prefix passes, one with the other package's or a
    typo'd prefix does not.
- **`release-tag-guard.yml` closes the double-silent-skip gap** (also found in this revision's
  review round): `release-bloomcli.yml` and `release-bloommcp.yml` are each designed to skip
  cleanly — not fail — when a Release tag belongs to the other package. That is correct for the
  two-package case, but it has no third state: a Release tagged with an unknown or typo'd prefix
  (e.g. `bloomcp-v1.0.0`) makes _both_ workflows skip cleanly at once, and nothing in the
  Actions UI says a Release was published that shipped nowhere. `release-tag-guard.yml` is a
  third, minimal workflow — no job-level `if:`, `permissions: {}` (it never checks out the
  repo or calls the GitHub API, so even `contents: read` would be more than it needs) — that
  runs on every published Release and fails loudly when the tag matches neither known prefix.
  It intentionally does not attempt to also validate tag/version/changelog for the matching
  package; that stays each package's own `validate-release` job's job. Its bash `case` match
  runs under `shopt -s nocasematch`, matching GitHub Actions' `startsWith()` case-insensitive
  semantics exactly (round 3 review: without this, a mixed-case tag like `BLOOMMCP-v1.0.0`
  would correctly pass the real per-package guard while this workflow misreported "matches no
  known prefix" — never silent, since the real guard still fails loudly at its own
  tag/version-mismatch check, but a wrong diagnostic). A regression test
  (`test_guard_prefixes_match_every_release_workflows_own_guard`) cross-checks this workflow's
  `KNOWN_PREFIXES` against each real workflow's own guard, so a third package added later with
  only its own guard updated fails in CI instead of only in production the first time its tag
  is cut.
- **`twine` is pinned (`uvx twine@7.0.0 check dist/*`)**, unlike `release-bloomcli.yml`'s own
  equivalent step, which is still unpinned today — inconsistent with this repo's stated
  convention of pinning CI security/build tools (`openspec/project.md`). Copying that
  inherited inconsistency into brand-new code would have had no excuse; fixing it unilaterally
  on `release-bloomcli.yml` itself remains out of scope for #663 (that file's diff is
  minimized to the tag-prefix guard — see above). The same brand-new-file reasoning extended
  in round 3 to SHA-pinning `actions/checkout`/`actions/upload-artifact`/
  `actions/download-artifact` in `release-bloommcp.yml` (previously `@v4`, unlike
  `astral-sh/setup-uv`, already SHA-pinned) — `release-bloomcli.yml` itself stays tag-pinned,
  matching the repo's dominant convention (only `promote-security-to-main.yml` SHA-pins
  `actions/checkout` today) and its own diff-minimization principle.
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
  in-repo yet to rewrite either). The link-pinning step's `re.sub` calls pass each replacement
  through a lambda (`re.sub(pat, lambda m, r=repl: r, s)`) rather than the string directly
  (round 3 review) — the release tag flows into that replacement string, and `re.sub` treats a
  literal `\1`-style sequence in a plain-string replacement as a backreference, so a tag
  containing one would raise `re.error` instead of substituting it literally. Only a
  write-access user controls the tag (not attacker-exploitable), but a typo could still break
  an otherwise-valid build.
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
  publisher field table, Troubleshooting), substituting `bloommcp`/`bloom-mcp`/`bloommcp-vX.Y.Z`
  and describing the exhaustive-walk/double-resolution/entry-point-failure-mode checks alongside
  the Supabase-adapter imports. This revision also adds, to both packages' `RELEASE_PROCESS.md`:
  a note that the shared `pypi` environment has no protection rules today and now gates two
  packages' publish credentials instead of one, a mention of `release-tag-guard.yml` under "the
  workflow run is skipped entirely", and a partial-publish-failure entry (PyPI rejects
  re-uploading a file that already exists, so a wheel-succeeds/sdist-fails run needs a new
  version, not a rerun — neither doc mentioned this before). Without the runbook itself, the
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
- The `pypi` GitHub Environment has no protection rules today (no required reviewers, no
  branch restriction) — pre-existing, shared with `bloomcli`, and outside this PR's reach to
  fix directly (it's a repo setting, not a code change). This PR doubles what depends on that
  being safe. Mitigated by documenting the gap explicitly in both packages'
  `RELEASE_PROCESS.md` and recommending a required reviewer be added now (see Decisions/Setup
  requirements) rather than treating it as someone else's problem because it predates this PR.
- This PR's base branch was `main` at authoring time (2026-08-14) — a misreading of
  `openspec/project.md`'s staging-first branching convention, not a deliberate exception to it
  (see "Base branch" under Migration Plan). Retargeting to `staging` in this revision means
  `release-bloommcp.yml` and `release-tag-guard.yml` won't be reachable by a real Release, and
  `release-bloommcp.yml`/`version-bloommcp.yml` won't be dispatchable via `workflow_dispatch`,
  until the next `staging → main` promotion — GitHub only evaluates `on: release`-triggered and
  dispatches `workflow_dispatch`-triggered workflows from files that exist on the default
  branch. This is not a new risk introduced by retargeting; it is the same path
  `release-bloomcli.yml`'s own hardening took via `staging` before #667 promoted it, and it does
  not block review or merge to `staging`.
- The `bloomcli-packaging` capability now has two independent, unarchived ADDED deltas on `main`
  at once (this PR's, and `openspec/changes/add-bloomcli-container-release`'s — see "Capability
  placement" above). Neither change can control the other's archival order; flagged explicitly
  so whichever archives second merges into the existing base spec rather than overwriting it.

## Migration Plan

Purely additive (three new workflow files, a new changelog, a version bump + lockfile
regeneration, two new load-bearing dependency upper bounds, a version-metadata addition, a
`[project.urls]` addition, a new release runbook), plus two narrow, independent retrofits to
existing workflows: a single job-level guard condition on `release-bloomcli.yml`, and a
`uv lock` step + `add-paths` entry on `version-bloomcli.yml` (the unrelated bug found during
review — see Decisions). No runtime behavior of the shipped MCP server changes. Rollback is
deleting the three new workflow files, reverting the `release-bloomcli.yml` guard and the
`version-bloomcli.yml` fix, and reverting the version bump and dependency-bound changes;
nothing depends on them existing.

**Ordering**: version introspection (`__version__`/`--version`) → CHANGELOG.md → pyproject
version bump + dependency upper bounds + lockfile → `version-bloommcp.yml` →
`release-bloommcp.yml` (depends on everything above existing, since its verify step imports and
runs them) → `release-tag-guard.yml` (independent of everything else) →
`release-bloomcli.yml` tag-prefix retrofit → `version-bloomcli.yml` uv.lock fix (both
independent of everything else and of each other; isolated commits). See `tasks.md` for the
exact commit sequence.

**Base branch**: `staging`, not `main`. The original choice of `main` (recorded when this
branch was opened, 2026-08-14) cited `openspec/project.md` as documenting `main` as the default
for feature branches — that citation was backwards. `openspec/project.md`'s Git Workflow
section is explicit: "Every feature/fix/docs PR targets `staging` by default... Feature
branches: Create from `origin/staging`," with `main` reserved for periodic `staging → main`
promotion PRs. This revision retargets to `staging` accordingly. The one true technical
constraint — GitHub only evaluates `on: release`-triggered workflows, and only accepts
`workflow_dispatch` runs, for files that exist on the repo's default branch (`main`) — does not
argue for `main` as the PR's _base_; it only means `release-bloommcp.yml` and
`release-tag-guard.yml` stay untriggerable until the next `staging → main` promotion carries
them over, exactly as `release-bloomcli.yml`'s own hardening did before #667 (see Risks). The
immediately-preceding bloommcp-infrastructure change (#590) branching from and merging to
`main` directly was not itself evidence of the documented convention — it may simply predate
consistent adherence to it, and does not override what `openspec/project.md` states today.

## Open Questions

- None blocking. The PyPI trusted-publisher registration is a known, tracked follow-up outside
  this PR's reach.
