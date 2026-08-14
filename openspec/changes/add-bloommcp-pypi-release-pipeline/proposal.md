## Why

bloommcp's packaging _mechanics_ are already done (`pyproject.toml` metadata, `[build-system]`,
the `bloom-mcp` entry point, Docker mode, and the CI wheel-build/import smoke test closed via
#316), but there is still no path from a merged change to `pip install bloommcp` — no
version-bump workflow, no release workflow, no changelog, and no registered PyPI trusted
publisher. `bloomcli` already solved exactly this for `bloomctl` (`release-bloomcli.yml` /
`version-bloomcli.yml`); this proposal builds the bloommcp-specific instance of that same
pipeline rather than inventing a new one, and closes a tag-scoping gap so the two release
workflows don't cross-fire.

## What Changes

- Add `version-bloommcp.yml` (manual `workflow_dispatch`), mirroring `version-bloomcli.yml`:
  bump `bloommcp/pyproject.toml`'s version and open a PR touching only that file, branch
  `bloommcp-version-bump-<version>`.
- Add `release-bloommcp.yml`, mirroring `release-bloomcli.yml`'s actual current two-job shape
  (`validate-release` → `build-and-publish`): tag-equals-version, changelog-entry, lint, and
  test gates; then build + `twine check` + wheel-import + `bloom-mcp --version` entry-point
  verification; then — only on a published Release, never on `workflow_dispatch` —
  `uv publish --trusted-publishing always`. The wheel-import check goes slightly beyond
  bloomcli's literal one-liner, additionally importing the concrete Supabase-backed adapters
  (`SupabaseReader`/`SupabaseResultStore`) that `build_app()` alone doesn't reach — see
  `design.md`.
- Add `bloommcp/CHANGELOG.md` (Keep a Changelog format, matching `bloomcli/CHANGELOG.md`) with
  an `[Unreleased]` section — the release gate has nothing to validate against without it.
- Add `bloom_mcp.__version__` (same `importlib.metadata` pattern as `bloomctl.__version__`) and
  a `--version`/`-V` flag on the `bloom-mcp` entry point (`main()`) that prints the version and
  returns before any environment validation or server startup. Today there is nothing for a
  release gate to assert against, and `bloom-mcp` has no way to report its version at all.
- Add a release-tag-prefix guard to **both** `release-bloommcp.yml` (new) and the existing
  `release-bloomcli.yml`, so publishing a Release for one package no longer triggers a failing
  run of the other's workflow (today `release-bloomcli.yml` fires on _any_ published Release
  and only self-aborts via a failed tag/version check). The guard is a job-level `if:` on
  `validate-release` (not a new step in the credential-holding `build-and-publish` job — see
  `design.md`), and adds a matching regression test to `tests/unit/`.
- Add `bloommcp/RELEASE_PROCESS.md`, a release runbook mirroring `bloomcli/RELEASE_PROCESS.md`,
  so the release-cutting procedure has a durable home instead of living only in this change's
  `tasks.md` (which disappears once archived).
- Add a minimal `[project.urls]` block (Homepage/Repository/Changelog) to
  `bloommcp/pyproject.toml`, matching `bloomcli`'s convention, plus a build-time step (in
  `build-and-publish`) that points those links at the release tag rather than a moving branch —
  mirroring `bloomcli`'s existing link-pinning step, scoped down to `pyproject.toml` only since
  bloommcp has no PyPI-specific `README.pypi.md`.
- Bump `bloommcp/pyproject.toml`'s version from `0.1.0` to `0.1.0a1` so the first PyPI release
  is explicitly a pre-release, matching `bloomctl`'s convention — parent issue #33 leaves the
  stable public tool-surface question open, so shipping an un-prefixed `0.1.0` would overstate
  stability.

## Out of Scope

- **Registering the PyPI trusted publisher for `bloommcp` on pypi.org** — a one-time, manual
  action on the PyPI web console by a maintainer with admin rights on the `bloommcp` PyPI
  project (bind repo `Salk-Harnessing-Plants-Initiative/bloom` + workflow
  `release-bloommcp.yml` + environment `pypi`). Cannot be done from a code PR; tracked as a
  manual follow-up in `tasks.md`.
- **Confirming the `bloommcp` name is available on PyPI** — already confirmed in the issue
  thread (comment from @blm3886, 2026-08-13): `bloommcp` is available, `bloom-mcp` is taken.
- **Rewriting `README.md` for a PyPI/end-user audience** — suggested in the issue thread but
  explicitly marked "fine to skip" by the commenter.
- **The `BLOOMMCP_ALLOW_NO_AUTH=1` local-install requirement** — tracked on #265.
- **Defining the stable public MCP tool surface** — tracked on parent #33.

## Impact

- Affected specs:
  - new capability `bloommcp-pypi-release` (ADDED)
  - `bloomcli-packaging` (ADDED — the reciprocal tag-prefix-guard requirement on
    `release-bloomcli.yml`, which this proposal also modifies)
- Affected code:
  - `.github/workflows/version-bloommcp.yml` (new)
  - `.github/workflows/release-bloommcp.yml` (new)
  - `.github/workflows/release-bloomcli.yml` (add tag-prefix guard — isolated commit)
  - `bloomcli/RELEASE_PROCESS.md` (document the narrowed `bloomctl-vX.Y.Z`-only tag form)
  - `bloommcp/CHANGELOG.md` (new)
  - `bloommcp/RELEASE_PROCESS.md` (new)
  - `bloommcp/pyproject.toml` (version bump to `0.1.0a1`, add `[project.urls]`)
  - `bloommcp/uv.lock` (regenerated for the version bump — `bloommcp` is a checked service in
    `scripts/check-uv-locks.py`)
  - `bloommcp/src/bloom_mcp/__init__.py` (add `__version__`)
  - `bloommcp/src/bloom_mcp/server.py` (add `--version`/`-V` handling in `main()`)
  - `bloommcp/tests/test_version.py` (new)
  - `tests/unit/test_release_bloommcp_workflow_shape.py` (new — regression guard mirroring
    `tests/unit/test_release_bloomcli_workflow_shape.py`)
  - `tests/unit/test_release_bloomcli_workflow_shape.py` (extended with the new tag-prefix
    guard assertion)
