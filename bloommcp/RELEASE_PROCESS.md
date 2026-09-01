# bloommcp Release Process

How to cut a `bloommcp` release to PyPI. The pipeline is a single workflow,
`.github/workflows/release-bloommcp.yml`, that publishes to **real PyPI** via
trusted publishing (OIDC) — there is no TestPyPI lane and no stored token.

## Overview

- **Publish** — `uv publish` with [PyPI trusted publishing](https://docs.pypi.org/trusted-publishers/)
  (OIDC). No API token in CI.
- **Trigger** — publishing runs only when a **GitHub Release is published**. A
  manual `workflow_dispatch` run validates + builds + smoke-tests but does NOT
  publish (a safe dry run).
- **Pre-releases** — publish to real PyPI as PEP 440 `aN`/`bN`/`rcN`, marked
  "pre-release" on the GitHub Release. A plain `pip install bloommcp` / `uvx bloommcp`
  ignores them unless the caller pins the exact version (`pip install bloommcp==0.1.0a1`)
  or passes `--pre` (pip) / `--prerelease=allow` (uv). The first release (`0.1.0a1`) is a
  pre-release — parent issue #33 leaves the stable public tool-surface question open.
- **Package-scoped** — the workflow only acts on a Release tagged `bloommcp-vX.Y.Z`; a
  Release for a different monorepo package (e.g. `bloomctl-vX.Y.Z`) is skipped cleanly,
  not failed (see "Tag scoping" below).

## Version management

The version lives in `bloommcp/pyproject.toml` (single source of truth;
`bloommcp/src/bloom_mcp/__init__.py` reads it from installed metadata).

- **In CI:** run the **version-bloommcp** workflow (Actions tab → Run workflow),
  pick a bump type, and it opens a PR with the bump (including a regenerated
  `bloommcp/uv.lock`). Its `concurrency:` group only serializes overlapping
  runs — it does not stop two dispatches with **different** `bump_type`s from
  each computing a new version off the same not-yet-merged base and opening
  two independent bump PRs. Don't dispatch a second bump before the first
  one's PR has merged.
- **Locally:**

  ```bash
  cd bloommcp
  uv version --bump patch    # 0.1.0  -> 0.1.1
  uv version --bump minor    # 0.1.0  -> 0.2.0
  uv version --bump alpha    # 0.2.0  -> 0.2.0a1
  uv version --bump stable   # 0.2.0a1 -> 0.2.0
  uv lock                    # keep bloommcp/uv.lock in sync with the bump
  ```

### Pre-release progression

`0.2.0a1 → 0.2.0a2 → 0.2.0b1 → 0.2.0rc1 → 0.2.0`. Pre-releases go to real PyPI
and are marked as a pre-release on GitHub.

## Cutting a release

1. Bump the version (workflow or `uv version`), merge the bump PR.
2. Add a `## [X.Y.Z] - YYYY-MM-DD` entry to `bloommcp/CHANGELOG.md`.
3. Create a **GitHub Release** whose tag is `bloommcp-vX.Y.Z` (this is the only accepted
   tag form — see "Tag scoping" below). Tick **"Set as a pre-release"** for `aN`/`bN`/`rcN`.
4. Publishing the Release runs `release-bloommcp.yml`, three jobs:
   - `validate-release`: skipped entirely unless the tag starts with `bloommcp-`;
     otherwise validates tag ↔ version match, changelog entry exists, lint + tests.
   - `build-and-verify` (no publish credential — third-party code runs here, not
     alongside the OIDC token): `uv build`, `twine check`, imports `bloom_mcp` and its
     `tools`/`manifest`/`server` submodules plus the concrete Supabase-backed adapters
     (`bloom_mcp.data_access.SupabaseReader`, `bloom_mcp.result_store.SupabaseResultStore`)
     and their `postgrest`/`supabase` transitive imports — not just
     `bloom_mcp.server.build_app()`, which alone doesn't reach those adapters (see the
     `add-bloommcp-pypi-release-pipeline` OpenSpec change's `design.md` for why) — runs
     `bloom-mcp --version`, records the artifact's checksum, and uploads it.
   - `build-and-publish` (holds `id-token: write` + the `pypi` environment, no other
     code): downloads the verified artifact, re-checks its hash, then `uv publish`.
5. Verify on PyPI:

   ```bash
   uv run --isolated --no-project --with "bloommcp==X.Y.Z" python -c "
   import bloom_mcp, bloom_mcp.tools, bloom_mcp.manifest, bloom_mcp.server
   bloom_mcp.server.build_app()
   from bloom_mcp.data_access import SupabaseReader
   from bloom_mcp.result_store import SupabaseResultStore
   print('ok', bloom_mcp.__version__)"
   ```

   Naming the exact version resolves the same dependency set a real installer gets —
   bloommcp's only pre-release-floored direct dependencies (`sleap-roots-analyze`,
   `sleap-roots-contracts`) already resolve to their pre-release versions under a normal,
   exact-version install (no `--prerelease=allow` needed, since the rest of bloommcp's
   dependencies are stable-versioned).

### Tag scoping

`release-bloommcp.yml`'s `validate-release` job carries a job-level guard —
`github.event_name != 'release' || startsWith(github.event.release.tag_name, 'bloommcp-')` —
so a Release cut for a different monorepo package (`bloomctl-vX.Y.Z`) is skipped, not failed.
The reciprocal guard on `release-bloomcli.yml` (`bloomctl-` prefix) means a `bloommcp` release
likewise never fails that workflow. `workflow_dispatch` always passes the guard (it has no
release tag).

### Project URLs

The committed repo links point at `main` (the source of truth): the `[project.urls]` in
`bloommcp/pyproject.toml`. At publish time `release-bloommcp.yml` rewrites them in the build
checkout to the release tag (`.../tree/bloommcp-vX.Y.Z/bloommcp`) so the PyPI sidebar links
resolve to the exact commit that version shipped, never a moving branch. This is automatic —
no per-release edit — and the change is never committed back.

## Setup requirements (one-time, before the first release)

### PyPI trusted publishing

Register a **pending trusted publisher** on PyPI (the package need not exist yet)
bound to exactly these values — they must match the workflow or publishing fails:

| Field             | Value                               |
| ----------------- | ----------------------------------- |
| PyPI Project Name | `bloommcp`                          |
| Owner             | `Salk-Harnessing-Plants-Initiative` |
| Repository name   | `bloom`                             |
| Workflow name     | `release-bloommcp.yml`              |
| Environment name  | `pypi`                              |

The `pypi` GitHub Environment already exists (shared with `bloomctl`'s release pipeline) —
no new Environment is needed.

**That shared environment currently has no protection rules** (no required reviewers, no
branch restriction) — this pipeline is the second publish credential to gate on it, doubling
what depends on that being safe. Adding a required reviewer (Settings → Environments →
`pypi` → Protection rules) is recommended now rather than after the first `bloommcp` release.

## Troubleshooting

- **`uv publish` fails with a trusted-publishing error** — the pending publisher
  or the `pypi` environment is missing or the names don't match exactly. PyPI
  uploads nothing, so it's safe to fix and re-run the job.
- **`validate-release` fails on tag/version** — the Release tag doesn't equal the
  `pyproject.toml` version. Retag the Release or bump the version.
- **`validate-release` fails on changelog** — add the `## [X.Y.Z]` entry to
  `bloommcp/CHANGELOG.md`.
- **The workflow run is skipped entirely** — the Release tag doesn't start with `bloommcp-`
  (it was probably meant for `bloomctl`'s release instead). If it doesn't start with either
  package's prefix, `release-tag-guard.yml` fails loudly on the same Release so this doesn't
  go unnoticed.
- **`uv lock --check` / the `uv-lock-check` pre-commit hook fails after a version bump** —
  run `uv lock` inside `bloommcp/` and commit the updated `uv.lock`.
- **`uv publish` partially succeeds** (e.g. the wheel uploads, then the sdist fails) — PyPI
  rejects re-uploading a filename that already exists, so re-running the job does not resume
  where it left off: the file that already uploaded is now rejected as a duplicate too.
  Recovery is a new version (bump, changelog entry, re-tag, new Release), not a rerun of the
  same one.

## References

- [PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/)
- [PEP 440 versioning](https://peps.python.org/pep-0440/)
- [Keep a Changelog](https://keepachangelog.com/)
- `bloomcli/RELEASE_PROCESS.md` — the pattern this document mirrors
