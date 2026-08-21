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
      found on the default branch"). Do this immediately after merge, before cutting the first
      real Release — not "before merge" as originally written here, which was never achievable
      once this became a new top-level workflow file.

## 9. Manual follow-up (outside this PR's reach)

- [ ] 9.1 Register the PyPI trusted publisher for `bloommcp` (PyPI Project Name `bloommcp`,
      Owner `Salk-Harnessing-Plants-Initiative`, Repository `bloom`, Workflow
      `release-bloommcp.yml`, Environment `pypi`) — requires PyPI admin rights; the `pypi`
      GitHub environment already exists and is reused. **Recommended now, in parallel with
      review/merge, not deferred to after**: every value this needs (repo, workflow filename,
      environment name) is already fixed by this PR and won't change during review. Registering
      it late just leaves a "Release published but PyPI never got it" window open longer than
      necessary.
- [ ] 9.2 After merge: bump to a real first version via `version-bloommcp.yml`, add its
      `CHANGELOG.md` entry, and cut a GitHub Release tagged `bloommcp-v0.1.0a1` to trigger the
      first real publish.

## PR

- https://github.com/Salk-Harnessing-Plants-Initiative/bloom/pull/681
