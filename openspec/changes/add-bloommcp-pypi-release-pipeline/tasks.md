## 1. Package version introspection

- [x] 1.1 Add `bloom_mcp.__version__` to `bloommcp/src/bloom_mcp/__init__.py` via
      `importlib.metadata.version("bloommcp")`, falling back to `"0.0.0+unknown"` on
      `PackageNotFoundError` (same pattern as `bloomcli/src/bloomctl/__init__.py`).
- [x] 1.2 Add `--version`/`-V` handling to `main()` in `bloommcp/src/bloom_mcp/server.py`,
      via a plain `sys.argv[1:]` check (no argparse/click) — print `bloom-mcp <version>` and
      return before any `validate_*_env()` call.
- [x] 1.3 Add `bloommcp/tests/test_version.py`:
  - `__version__` equals the version parsed from `bloommcp/pyproject.toml` (not merely
    "non-empty") — mirror `bloomcli/tests/test_changelog_version_sync.py`'s
    `_current_version()` helper for parsing.
  - The `PackageNotFoundError` fallback path: `monkeypatch` `importlib.metadata.version` to
    raise, `importlib.reload(bloom_mcp)`, assert `__version__ == "0.0.0+unknown"`, then
    restore and reload so later tests see the real version.
  - `bloom-mcp --version` (and `-V`) exits/returns without invoking env validation, verified
    with `monkeypatch.setattr(sys, "argv", ["bloom-mcp", "--version"])` and no `BLOOM_*` /
    `SUPABASE_*` env set.
- [x] 1.4 Commit: `feat(#663): add bloom_mcp.__version__ and --version/-V flag`.

## 2. Changelog and release runbook

- [x] 2.1 Create `bloommcp/CHANGELOG.md` (Keep a Changelog format + the same PEP 440
      pre-release note, matching `bloomcli/CHANGELOG.md`'s exact header wording, substituting
      `bloommcp` for `bloomctl`) with an `[Unreleased]` section.
- [x] 2.2 Create `bloommcp/RELEASE_PROCESS.md`, mirroring `bloomcli/RELEASE_PROCESS.md`'s
      structure (Overview, Version management, Cutting a release, Setup requirements incl. the
      PyPI trusted-publisher field table, Troubleshooting), substituting
      `bloommcp`/`bloom-mcp`/`bloommcp-vX.Y.Z`.
- [x] 2.3 Commit: `docs(#663): add bloommcp CHANGELOG.md and RELEASE_PROCESS.md`.

## 3. Package metadata

- [x] 3.1 Add `[project.urls]` (Homepage, Repository, Changelog) to
      `bloommcp/pyproject.toml`, matching `bloomcli`'s convention.
- [x] 3.2 Commit: `chore(#663): add [project.urls] to bloommcp/pyproject.toml`.
- [x] 3.3 Bump `bloommcp/pyproject.toml`'s `version` from `0.1.0` to `0.1.0a1`.
- [x] 3.4 Run `cd bloommcp && uv lock` to regenerate `bloommcp/uv.lock` (it records the
      package's own version at line ~109 — `scripts/check-uv-locks.py` / the `uv-lock-check`
      pre-commit hook will fail otherwise) and commit the updated lockfile alongside the bump.
- [x] 3.5 Commit: `chore(#663): bump bloommcp version to 0.1.0a1`.

## 4. Version-bump workflow

- [x] 4.1 Add `.github/workflows/version-bloommcp.yml`, mirroring `version-bloomcli.yml`:
      `workflow_dispatch` with `bump_type` choice + optional `custom_version`,
      `working-directory: bloommcp`. After bumping via `uv version`, run `uv lock` so the
      lockfile stays in sync (do **not** copy `version-bloomcli.yml`'s stale
      "bloomctl commits no uv.lock" assumption — `bloommcp/uv.lock`, like `bloomcli/uv.lock`,
      is a checked service lockfile). Opens a PR whose `add-paths` covers both
      `bloommcp/pyproject.toml` and `bloommcp/uv.lock`, branch `bloommcp-version-bump-<version>`.
- [x] 4.2 Commit: `feat(#663): add version-bloommcp.yml`.

## 5. Release workflow

- [x] 5.1 Add `.github/workflows/release-bloommcp.yml`, **three** jobs (revised after review:
      the original draft mirrored `release-bloomcli.yml`'s actual-on-`main` two-job shape to
      minimize surprise; a review pointed out that reasoning only justifies _retrofitting_ a
      live, production-critical file — `release-bloommcp.yml` is a brand-new file, so there is
      no cost to giving it the safer, credential-isolated shape directly instead of
      deliberately reintroducing the #629 flaw into new infrastructure):
  - `validate-release`: read version; job-level guard
    `if: github.event_name != 'release' || startsWith(github.event.release.tag_name, 'bloommcp-')`;
    (release-only) validate tag matches version and a `## [version]` `CHANGELOG.md` entry
    exists; lint (`uvx ruff@0.9.9 check .`); run
    `uv run --extra test pytest -m "not integration and not live_smoke" -q`.
  - `build-and-verify` (needs `validate-release`; no `id-token`/`pypi` environment — this is
    where third-party code runs): pin `[project.urls]` in the build checkout to the release tag
    (release-only, no-op on dispatch), `uv build`, record the artifact's `sha256sum`, run
    `uvx twine@7.0.0 check dist/*` (pinned — new code, unlike `release-bloomcli.yml`'s inherited
    unpinned `uvx twine check`), verify the wheel imports (`bloom_mcp`, `bloom_mcp.tools`,
    `bloom_mcp.manifest`, `bloom_mcp.server`, `bloom_mcp.server.build_app()`, **plus**
    `bloom_mcp.data_access.SupabaseReader`, `bloom_mcp.result_store.SupabaseResultStore`,
    `postgrest.APIError`, `supabase.create_client` — the concrete adapters `build_app()` alone
    doesn't reach, per `design.md`'s gap analysis — with `SUPABASE_URL`/`BLOOM_AGENT_KEY`
    pinned empty), verify `bloom-mcp --version` runs from an isolated `--no-project`
    environment, re-check the sha256sum, then upload `dist/` + the checksum as an artifact.
  - `build-and-publish` (needs `build-and-verify`; holds `environment: pypi` +
    `id-token: write`, runs no third-party code): download the verified artifact, re-check its
    sha256sum, then (release-only) `uv publish --trusted-publishing always`.
- [x] 5.2 Commit: `feat(#663): add release-bloommcp.yml with its own tag-prefix guard`
      (depends on tasks 1–4 landing first, since this workflow's verify step imports/runs them).

## 6. Tag-prefix scoping retrofit (existing bloomcli workflow — isolated commit)

- [x] 6.1 Add the symmetric job-level guard to `.github/workflows/release-bloomcli.yml`'s
      `validate-release` job:
      `if: github.event_name != 'release' || startsWith(github.event.release.tag_name, 'bloomctl-')`.
      No new step; `build-and-publish`'s existing steps are untouched.
- [x] 6.2 Update `bloomcli/RELEASE_PROCESS.md` to document only the `bloomctl-vX.Y.Z` tag form
      (drop the bare `vX.Y.Z`/`X.Y.Z` mentions, since those are now skipped rather than
      validated — see the `bloomcli-packaging` spec delta).
- [x] 6.3 Extend `tests/unit/test_release_bloomcli_workflow_shape.py` with an assertion that
      `validate-release`'s job-level `if:` contains the `bloomctl-` `startsWith` guard
      alongside `github.event_name != 'release'`.
- [x] 6.4 Commit: `fix(#663): scope release-bloomcli.yml to bloomctl- tags` (isolated,
      single-file, easy independent revert — this touches a currently-live, production-critical
      workflow that real `bloomctl` releases depend on).
- [x] 6.5 Found during review: `version-bloomcli.yml`'s `bump-version` job never ran `uv lock`,
      so `bloomcli/uv.lock` (a checked service lockfile per `scripts/check-uv-locks.py`) drifted
      out of sync with every version bump — the exact bug `version-bloommcp.yml` (task 4.1) was
      written to avoid. Add a `Sync uv.lock` step (`uv lock`) before opening the PR, and add
      `bloomcli/uv.lock` to `add-paths`.
- [x] 6.6 Extend `tests/unit/test_release_bloomcli_workflow_shape.py`'s version-workflow
      assertion to require `uv lock` in `bump-version`'s steps text, mirroring the existing
      `test_release_bloommcp_workflow_shape.py` assertion.
- [x] 6.7 Commit: `fix(#663): sync bloomcli/uv.lock in version-bloomcli.yml's bump PR` (isolated,
      single-file plus its regression test — independent of everything else in this PR).

## 7. New regression-guard test (bloommcp)

- [x] 7.1 Add `tests/unit/test_release_bloommcp_workflow_shape.py`, mirroring
      `tests/unit/test_release_bloomcli_workflow_shape.py`'s structure and asserting the
      bloommcp-specific equivalents: trigger shape, `validate-release` → `build-and-publish`
      gating, the `bloommcp-` tag-prefix guard (and that `workflow_dispatch` bypasses it),
      OIDC/`pypi`-environment/no-stored-token on the publish job, and that the wheel-import
      step covers the Supabase adapter classes (not just `build_app()`).
- [x] 7.2 Commit: `test(#663): add release-bloommcp.yml workflow-shape regression guard`.

## 8. Validation

- [x] 8.1 `openspec validate add-bloommcp-pypi-release-pipeline --strict`.
- [x] 8.2 `cd bloommcp && uv run --extra test pytest tests/test_version.py -v`.
- [x] 8.3 `cd bloommcp && uv run --extra test pytest tests/ -m "not integration and not live_smoke" -v`
      (confirm the `__init__.py`/`server.py` edits don't regress `test_package_baseline.py` or
      other fresh-import-sensitive tests).
- [x] 8.4 `uv run --extra test pytest tests/unit/test_release_bloomcli_workflow_shape.py tests/unit/test_release_bloommcp_workflow_shape.py -v`.
- [x] 8.5 `python scripts/check-uv-locks.py` (confirms `bloommcp/uv.lock` is back in sync after
      task 3.4's regeneration).
- [x] 8.6 `uvx ruff@0.9.9 check bloommcp/`.
- [x] 8.7a Manually reproduce `build-and-verify`'s steps locally (`uv build`, `sha256sum`,
      `uvx twine@7.0.0 check dist/*`, the wheel-import smoke test including the Supabase
      adapters, `bloom-mcp --version` from the built wheel) — confirmed passing. This is not a
      substitute for a real dry run (job-level `if:` guards, `needs:` gating, and the
      artifact upload/download handoff are untested by it), but it does verify the underlying
      commands actually work.
- [ ] 8.7b Dry-run `release-bloommcp.yml` via `workflow_dispatch`, confirming it builds/verifies
      without attempting a publish. **Cannot happen before merge**: GitHub only allows
      dispatching a workflow that already exists on the default branch (confirmed —
      `gh workflow run release-bloommcp.yml --ref <this-branch>` 404s with "workflow ... not
      found on the default branch"). This PR now targets `staging` (task 9.5), so "the default
      branch" means: not merge to `staging`, but the next `staging → main` promotion after that
      — do the dry run once this has reached `main`, before cutting the first real Release.

## 9. Review round 2 (2026-08-26): stale premise, weak guard tests, staging targeting

Found by a second human review, two days after PR #667 ("promote staging to main",
2026-08-22) rolled up `release-bloomcli.yml`'s hardened three-job shape to `main` — eight days
after this branch opened (2026-08-14) and after the round-1 review fix (task 5.1's note). The
branch was never rebased/re-verified against `main` in between.

- [x] 9.1 `release-bloommcp.yml`'s `build-and-verify` job now matches `release-bloomcli.yml`'s
      current `main` shape on every axis it was missing: an exhaustive `pkgutil.walk_packages`
      wheel-import walk, run twice (default resolution, then `--prerelease=allow`), and an
      entry-point check that proves a real invocation with no env fails fast rather than hanging
      (bloom_mcp has no `bloomctl.errors:main`-style wrapper, so the check is adapted to its own
      `--version`-returns-before-validation contract instead of the wrapper-vs-bare-CLI check —
      see design.md). The workflow's header comment no longer claims bloomcli's hardened shape
      is "not yet on `main`".
- [x] 9.2 Running the new `--prerelease=allow` pass for the first time failed immediately:
      `httpx 1.0.dev5` resolved and broke `postgrest`'s import, exactly the #629 failure mode.
      Added the same load-bearing `httpx<1.0`/`supabase<3` upper bounds
      `bloomcli/pyproject.toml` already carries to `bloommcp/pyproject.toml`, then regenerated
      `bloommcp/uv.lock`. Reran both wheel-import passes, `twine check`, and the full
      `bloommcp` test suite (1405 passed) to confirm the fix and no regression.
- [x] 9.3 Fixed both `test_validate_release_skips_tags_that_are_not_bloom{ctls,mcps}` tests
      (they only asserted each `if:` clause's substring independently, never the joined `||`
      expression — a flipped `||`/`&&` would pass both while disabling every real release).
      Added `_guard_permits`, a small evaluator exercising the guard's actual truth table, to
      both `tests/unit/test_release_bloomcli_workflow_shape.py` and
      `tests/unit/test_release_bloommcp_workflow_shape.py`.
- [x] 9.4 Added `.github/workflows/release-tag-guard.yml` (no job-level skip; fails loudly when
      a Release tag matches neither `bloomctl-` nor `bloommcp-`, closing the gap where a typo'd
      tag made both release workflows skip cleanly with no visible signal anywhere) and
      `tests/unit/test_release_tag_guard_workflow_shape.py`.
- [x] 9.5 Retargeted this PR from `main` to `staging` (`gh pr edit --base staging`) and rebased
      the branch's 5 non-merge commits onto `origin/staging`, per `openspec/project.md`'s
      staging-first branching convention — the original `main` targeting was a misreading of
      that doc, not a deliberate exception (see design.md's "Base branch").
- [x] 9.6 Documented, in both `bloomcli/RELEASE_PROCESS.md` and `bloommcp/RELEASE_PROCESS.md`:
      the shared `pypi` environment's lack of protection rules (now doubled by this PR),
      `release-tag-guard.yml` under "the workflow run is skipped entirely", and a new
      partial-publish-failure entry (PyPI rejects re-uploading an existing file, so a
      wheel-succeeds/sdist-fails run needs a version bump, not a rerun).
- [x] 9.7 Updated `proposal.md`, `design.md`, and both `specs/*/spec.md` deltas to drop the
      stale "hardened shape lives on staging, not main" framing throughout and describe the
      current state accurately.
- [x] 9.8 Corrected the PR description's Test plan: the workflow-shape test count was stale at
      "19 tests" (already actually 29 before this round; 45 after task 9.3/9.4's additions —
      15 `test_release_bloomcli_workflow_shape.py` + 19 `test_release_bloommcp_workflow_shape.py` + 11 `test_release_tag_guard_workflow_shape.py`).

## 10. Review round 3 (2026-08-28 review, fixed same day): a 5-subagent parallel review of the round-2 commits found no blocking issues, but four real Important-tier gaps and several Suggestions

- [x] 10.1 `release-tag-guard.yml`'s bash `case` match was case-sensitive while the real
      per-package guards' GitHub Actions `startsWith()` is not — a tag like
      `BLOOMMCP-v1.0.0` would correctly pass the real workflow's guard (and fail loudly at its
      own tag/version-mismatch check) while this workflow misreported "matches no known
      prefix," even though it did match one. Nothing shipped silently either way, but the
      diagnostic was wrong. Fixed with `shopt -s nocasematch` before the `case`; added
      mixed-case regression cases (`BLOOMMCP-v1.0.0`, `Bloomctl-V1.0.0`) to
      `test_known_package_tags_pass`.
- [x] 10.2 `test_release_tag_guard_workflow_shape.py`'s `_run_guard_script` hardcoded `"bash"`
      (instead of the repo's own `_bash_executable()` helper, already used by
      `test_deploy_kong_reload_on_config_change.py` in the same directory, which resolves Git
      Bash explicitly since plain `bash` can resolve to the WSL launcher shim on Windows) and
      passed `env={"TAG": tag}`, replacing the process environment instead of merging with
      `os.environ` and dropping `PATH`. Fixed both, mirroring the sibling test file's pattern
      exactly (added a `timeout=` too, matching that file's convention).
- [x] 10.3 Nothing enforced `release-tag-guard.yml`'s `KNOWN_PREFIXES` staying in sync with
      each package's own tag-prefix guard — a comment said to keep them in sync, but a third
      package added later with only its own `release-*.yml` guard updated would fail loudly
      only once its release tag was actually cut, not in CI. Added
      `test_guard_prefixes_match_every_release_workflows_own_guard`, cross-checking the
      guard's prefix list against `release-bloomcli.yml`'s and `release-bloommcp.yml`'s own
      `startsWith(...)` guards.
- [x] 10.4 SHA-pinned `actions/checkout`, `actions/upload-artifact`, and
      `actions/download-artifact` in `release-bloommcp.yml` (previously `@v4`, unlike
      `astral-sh/setup-uv`, already SHA-pinned) — consistent with this design's established
      precedent of giving the brand-new file hardening `release-bloomcli.yml` doesn't have yet
      (see design.md's "twine is pinned" decision) rather than SHA-pinning `release-bloomcli.yml`
      itself, which stays out of scope per the diff-minimization principle for that live file.
- [x] 10.5 `release-bloommcp.yml`'s "Pin repo links to the release tag" step passed the
      release tag directly as a `re.sub` replacement _string_; a tag containing a
      backslash-digit sequence (e.g. `\1`) would be misread as a backreference and raise
      `re.error`. Not exploitable (only a write-access user controls the tag), but a
      write-access typo could still break the build. Fixed by wrapping the replacement in a
      lambda (`re.sub(pat, lambda m, r=repl: r, s)`) so it's inserted literally.
- [x] 10.6 Documented, in both `bloomcli/RELEASE_PROCESS.md` and `bloommcp/RELEASE_PROCESS.md`:
      the version-bump workflows' concurrency gap (flagged as a Suggestion in round 2 and still
      undocumented) — a `concurrency:` group only serializes overlapping dispatches, it does
      not stop two dispatches with different inputs from each computing a new version off the
      same not-yet-merged base and opening independent, conflicting bump PRs.
      `version-bloomcli.yml` has no `concurrency:` guard at all, so its doc note is worded
      accordingly (no serialization whatsoever, not just the different-bump_type gap).
- [ ] 10.7 Not addressed this round (deferred, see PR discussion): the `pypi` GitHub
      Environment's missing protection rules (repo Settings change, requires org admin rights
      neither this branch's author nor this fix has); centralizing the tag-prefix strings
      across the three workflow files behind a single source of truth (a Suggestion, not
      blocking — 10.3's cross-check test closes the "no automated signal" gap without a
      structural refactor); a fail-fast "already on PyPI" pre-check; and a scheduled canary
      dry-run workflow.

## 11. Review round 4 (2026-09-02): a 5-subagent parallel review of the round-3 fix

commits found one blocking gap that survived two prior self-review rounds, plus several
real Important-tier gaps and Suggestions

- [x] 11.1 **Blocking**: `docker-build-bloomcli.yml` (untouched by any earlier round of
      this PR) had no tag-prefix guard, so every bloommcp release produced a permanent,
      misleading red X on this unrelated GHCR-publishing workflow — `validate-tag`'s
      `if: github.event_name == 'release'` ran unconditionally on every published Release,
      stripped `bloomctl-v`/`v` from a `bloommcp-vX.Y.Z` tag (a no-op, since it doesn't
      match), and failed the version-mismatch check. No wrong image shipped
      (`build-and-push` already required `validate-tag` to succeed), but exactly the
      cross-firing-failure class this PR otherwise closes. Fixed with
      `if: github.event_name == 'release' && startsWith(github.event.release.tag_name, 'bloomctl-')`
      (an `&&` join, not the `||` release-bloomcli.yml/release-bloommcp.yml use — this job
      is release-only auxiliary validation, not the main gate every trigger type passes
      through). Added a job-level MODIFIED requirement + scenario to this change's
      `bloomcli-packaging` spec delta, and extended
      `tests/unit/test_docker_build_bloomcli_workflow_shape.py` with a truth-table test
      (mirroring `_guard_permits` from the release-workflow test files, adapted for the
      `&&` shape).
- [x] 11.2 Corrected the PR description's stale test-count claim (said 45, actually 48 —
      `def test_` count differs from pytest's collected-test count once
      `@pytest.mark.parametrize` expansion is counted; the PR description now says so
      explicitly to avoid this exact mismatch recurring).
- [x] 11.3 The `--prerelease=allow` httpx/supabase upper-bound fix (round 2, task 9.2) had
      no PR-time regression test — only a real `workflow_dispatch`/release run exercises
      that check. Added `test_the_dependency_caps_that_keep_pre_releases_installable_are_still_there`
      to `bloommcp/tests/test_package_baseline.py`, mirroring
      `bloomcli/tests/test_errors.py`'s pre-existing, identical guard for bloomcli's own
      caps (missed when the bounds were first added — bloomcli already had exactly this
      pattern to copy).
- [x] 11.4 The tag↔version bash matching logic in `release-bloommcp.yml`/
      `release-bloomcli.yml`'s "Validate tag matches version" step was only checked via
      string-containment in the shape tests, never actually executed — unlike
      `release-tag-guard.yml`'s bash, which round 3 already executes via
      `subprocess.run`. Added `_run_validate_tag_script` + matching/mismatching-tag tests
      to both `tests/unit/test_release_bloommcp_workflow_shape.py` and
      `tests/unit/test_release_bloomcli_workflow_shape.py`, executing the real `run:`
      script (TAG/VERSION via `env:`, matching how the real step receives them).
- [x] 11.5 `openspec/changes/add-bloommcp-pypi-release-pipeline/specs/bloommcp-pypi-release/spec.md`
      still said `version-bloommcp.yml` opens a PR "that changes only" `pyproject.toml`,
      but the shipped workflow also commits the regenerated `bloommcp/uv.lock`
      (load-bearing — task 4.1/§9.7). Fixed the requirement text and its scenario.
- [x] 11.6 `version-bloommcp.yml`'s `actions/checkout@v4` was left unpinned — inconsistent
      with this pipeline's own SHA-pinning rationale (round 3, task 10.4) — and nothing
      asserted any file in this pipeline stayed fully SHA-pinned, so this drift (and any
      future one) went uncaught. SHA-pinned it, and added
      `test_every_action_is_sha_pinned` (scans every `uses:` in both `release-bloommcp.yml`
      and `version-bloommcp.yml`) to `tests/unit/test_release_bloommcp_workflow_shape.py`.
- [x] 11.7 Documented (not code-fixed — low-probability, per the review) that
      `build-and-verify` only smoke-tests the wheel, never the sdist, in
      `bloommcp/RELEASE_PROCESS.md`'s "Cutting a release" step 4.
- [x] 11.8 Suggestions: noted in `proposal.md` why `bloom-mcp --version`'s output format
      (`bloom-mcp <version>`) intentionally doesn't mirror `bloomctl --version`'s
      click-generated format (`bloomctl, version <version>`) — `bloom-mcp` has no click
      dependency to mirror that format from. Updated `bloommcp/CHANGELOG.md`'s
      `[0.1.0a1]` date (was the branch-open date, 2026-08-14) and added a step to both
      packages' `RELEASE_PROCESS.md` reminding the release-cutter to move `[Unreleased]`
      entries under the new dated heading — dated the actual cut day, not whenever the
      entries were written.

## 12. Manual follow-up (outside this PR's reach)

- [ ] 12.1 Register the PyPI trusted publisher for `bloommcp` (PyPI Project Name `bloommcp`,
      Owner `Salk-Harnessing-Plants-Initiative`, Repository `bloom`, Workflow
      `release-bloommcp.yml`, Environment `pypi`) — requires PyPI admin rights; the `pypi`
      GitHub environment already exists and is reused. **Recommended now, in parallel with
      review/merge, not deferred to after**: every value this needs (repo, workflow filename,
      environment name) is already fixed by this PR and won't change during review. Registering
      it late just leaves a "Release published but PyPI never got it" window open longer than
      necessary.
- [ ] 12.2 After merge: bump to a real first version via `version-bloommcp.yml`, add its
      `CHANGELOG.md` entry, and cut a GitHub Release tagged `bloommcp-v0.1.0a1` to trigger the
      first real publish.
- [ ] 12.3 Add a required reviewer on the shared `pypi` GitHub Environment (Settings →
      Environments → `pypi` → Protection rules) — it has none today and now gates two
      packages' publish credentials instead of one (round 3 review, task 10.7). Requires repo
      admin rights this fix does not have.

## PR

- https://github.com/Salk-Harnessing-Plants-Initiative/bloom/pull/681
