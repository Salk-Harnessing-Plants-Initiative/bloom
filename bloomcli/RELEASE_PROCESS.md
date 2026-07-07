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
3. Create a **GitHub Release** whose tag matches the version (`bloomctl-vX.Y.Z`,
   `vX.Y.Z`, or `X.Y.Z`). Tick **"Set as a pre-release"** for `aN`/`bN`/`rcN`.
4. Publishing the Release runs `release-bloomcli.yml`:
   - `validate-release`: tag ↔ version match, changelog entry exists, lint + tests.
   - `build-and-publish`: `uv build`, import the wheel + run `bloomctl --version`,
     then `uv publish`.
5. Verify on PyPI: `uvx bloomctl --version` (stable) or
   `uvx --prerelease=allow bloomctl --version` (pre-release).

## Setup requirements (one-time, before the first release)

### PyPI trusted publishing

Register a **pending trusted publisher** on PyPI (the package need not exist yet)
bound to exactly these values — they must match the workflow or publishing fails:

| Field | Value |
|---|---|
| PyPI Project Name | `bloomctl` |
| Owner | `Salk-Harnessing-Plants-Initiative` |
| Repository name | `bloom` |
| Workflow name | `release-bloomcli.yml` |
| Environment name | `pypi` |

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

## References

- [PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/)
- [PEP 440 versioning](https://peps.python.org/pep-0440/)
- [Keep a Changelog](https://keepachangelog.com/)
