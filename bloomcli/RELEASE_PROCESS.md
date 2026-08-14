# bloomctl Release Process

How to cut a `bloomctl` release to PyPI. The pipeline is a single workflow,
`.github/workflows/release-bloomcli.yml`, that publishes to **real PyPI** via
trusted publishing (OIDC) — there is no TestPyPI lane and no stored token.

## Overview

- **Publish** — `uv publish` with [PyPI trusted publishing](https://docs.pypi.org/trusted-publishers/)
  (OIDC). No API token in CI.
- **Trigger** — publishing runs only when a **GitHub Release is published**. A
  manual `workflow_dispatch` run validates + builds + smoke-tests but does NOT
  publish (a safe dry run).
- **Pre-releases** — publish to real PyPI as PEP 440 `aN`/`bN`/`rcN`, marked
  "pre-release" on the GitHub Release. `uvx bloomctl` ignores them unless the
  caller passes `--prerelease=allow`.
- **Package-scoped** — the workflow only acts on a Release tagged `bloomctl-vX.Y.Z`; a
  Release for a different monorepo package (e.g. `bloommcp-vX.Y.Z`) is skipped cleanly,
  not failed (see "Tag scoping" below).

## Version management

The version lives in `bloomcli/pyproject.toml` (single source of truth;
`bloomcli/src/bloomctl/__init__.py` reads it from installed metadata).

- **In CI:** run the **version-bloomcli** workflow (Actions tab → Run workflow),
  pick a bump type, and it opens a PR with the bump.
- **Locally:**

  ```bash
  cd bloomcli
  uv version --bump patch    # 0.1.0  -> 0.1.1
  uv version --bump minor    # 0.1.0  -> 0.2.0
  uv version --bump alpha    # 0.2.0  -> 0.2.0a1
  uv version --bump stable   # 0.2.0a1 -> 0.2.0
  ```

### Pre-release progression

`0.2.0a1 → 0.2.0a2 → 0.2.0b1 → 0.2.0rc1 → 0.2.0`. Pre-releases go to real PyPI
and are marked as a pre-release on GitHub.

## Cutting a release

1. Bump the version (workflow or `uv version`), merge the bump PR.
2. Add a `## [X.Y.Z] - YYYY-MM-DD` entry to `bloomcli/CHANGELOG.md`.
3. Create a **GitHub Release** whose tag is `bloomctl-vX.Y.Z` (this is now the only
   accepted tag form — see "Tag scoping" below; a bare `vX.Y.Z` or `X.Y.Z` tag is skipped,
   not validated). Tick **"Set as a pre-release"** for `aN`/`bN`/`rcN`.
4. Publishing the Release runs two workflows:
   - `release-bloomcli.yml`, in three jobs:
     - `validate-release`: tag ↔ version match, changelog entry exists, lint + tests.
     - `build-and-verify`: `uv build`, then import every `bloomctl` module and the
       supabase/postgrest chain from the built wheel — once with stable dependencies
       and once with `--prerelease=allow`. `import bloomctl` on its own stays green
       on a build where every real command dies, which is how `0.1.0a4` shipped
       broken (#629).
     - `build-and-publish`: checks the artifact's checksum and runs `uv publish`.
       Nothing else runs in this job — it holds the PyPI credential, so no package
       code executes beside it.
   - `docker-build-bloomcli.yml`: validates the same tag ↔ version match
     independently, then builds and pushes a matching GHCR version tag (see
     `bloomcli/README.md`'s "Container image" section for the image and tag
     scheme).
5. Verify on PyPI. `--version` is not enough — it passed on `0.1.0a4`, where every
   real command died on import. Pull the dependency chain the way the gate does:

   ```bash
   uvx --from "bloomctl==X.Y.Z" python -c "
   import importlib, pkgutil, bloomctl
   [importlib.import_module(m.name) for m in pkgutil.walk_packages(bloomctl.__path__, 'bloomctl.')]
   from supabase import create_client
   print('ok')"
   ```

   Naming the exact version resolves stable dependencies, which is also what the
   README tells users to install — `--pre` / `--prerelease=allow` lets every
   transitive dependency install an unfinished version too, and that is what
   broke `0.1.0a4`.

### Tag scoping

`release-bloomcli.yml`'s `validate-release` job carries a job-level guard —
`github.event_name != 'release' || startsWith(github.event.release.tag_name, 'bloomctl-')` —
so a Release cut for a different monorepo package (`bloommcp-vX.Y.Z`) is skipped, not failed.
The reciprocal guard on `release-bloommcp.yml` (`bloommcp-` prefix) means a `bloomctl` release
likewise never fails that workflow. `workflow_dispatch` always passes the guard (it has no
release tag). This narrows the tag-parsing step's previous support for a bare `vX.Y.Z` or
`X.Y.Z` tag — never actually used for a real release — to `bloomctl-vX.Y.Z` only.

### Project URLs

The committed repo links point at `main` (the source of truth): the
`[project.urls]` in `bloomcli/pyproject.toml` and the "project repository" link
in `README.pypi.md`. At publish time `release-bloomcli.yml` rewrites both in the
build checkout to the release tag (`.../tree/bloomctl-vX.Y.Z/bloomcli`) so the
PyPI sidebar links and the rendered long-description link resolve to the exact
commit that version shipped, never a moving branch. This is automatic — no
per-release edit — and the change is never committed back.

## Setup requirements (one-time, before the first release)

### PyPI trusted publishing

Register a **pending trusted publisher** on PyPI (the package need not exist yet)
bound to exactly these values — they must match the workflow or publishing fails:

| Field             | Value                               |
| ----------------- | ----------------------------------- |
| PyPI Project Name | `bloomctl`                          |
| Owner             | `Salk-Harnessing-Plants-Initiative` |
| Repository name   | `bloom`                             |
| Workflow name     | `release-bloomcli.yml`              |
| Environment name  | `pypi`                              |

Salk-HPI has no PyPI organization, so the project is registered under **Talmo's
PyPI org**. The GitHub owner (Salk-HPI) and the PyPI org (Talmo) are independent;
trusted publishing binds the GitHub repo to the PyPI project.

### GitHub Environment

Create a repo Environment named **`pypi`** (Settings → Environments). Add
protections (required reviewers, wait timer) here if a human gate on publishing
is wanted.

## Troubleshooting

- **`uv publish` fails with a trusted-publishing error** — the pending publisher
  or the `pypi` environment is missing or the names don't match exactly. PyPI
  uploads nothing, so it's safe to fix and re-run the job.
- **`validate-release` fails on tag/version** — the Release tag doesn't equal the
  `pyproject.toml` version. Retag the Release or bump the version.
- **`validate-release` fails on changelog** — add the `## [X.Y.Z]` entry to
  `bloomcli/CHANGELOG.md`.
- **The workflow run is skipped entirely** — the Release tag doesn't start with `bloomctl-`
  (it was probably meant for `bloommcp`'s release instead).

## References

- [PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/)
- [PEP 440 versioning](https://peps.python.org/pep-0440/)
- [Keep a Changelog](https://keepachangelog.com/)
