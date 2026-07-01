---
name: Prepare bloomctl Release
description: Drive a bloomctl PyPI release (version bump, changelog, GitHub Release) per RELEASE_PROCESS.md
category: Release
tags: [bloomctl, release, pypi, changelog]
---

# Prepare bloomctl Release

Guide a `bloomctl` release end to end, following
[`bloomcli/RELEASE_PROCESS.md`](../../bloomcli/RELEASE_PROCESS.md). The pipeline
publishes to real PyPI via trusted publishing on a **published GitHub Release**
only.

## Steps

1. **Decide the version.** Ask which bump is intended (`patch`/`minor`/`major`,
   or a pre-release `alpha`/`beta`/`rc`/`stable`). Confirm the resulting PEP 440
   version.

2. **Bump it.** Prefer the CI path (Actions → **version-bloomcli** → Run
   workflow), or locally:

   ```bash
   cd bloomcli && uv version --bump <type>
   ```

   Merge the resulting bump PR before continuing.

3. **Update the changelog.** Add a `## [X.Y.Z] - YYYY-MM-DD` section to
   `bloomcli/CHANGELOG.md` under `[Unreleased]`, summarizing Added/Changed/Fixed.
   (`validate-release` blocks publishing if this entry is missing.)

4. **Dry-run (optional but recommended).** Trigger `release-bloomcli.yml` via
   `workflow_dispatch` — it validates, builds, and smoke-tests the wheel
   (`import bloomctl` + `bloomctl --version`) without publishing.

5. **Cut the Release.** Create a GitHub Release whose tag matches the version
   (`bloomctl-vX.Y.Z`). Tick **"Set as a pre-release"** for `aN`/`bN`/`rcN`.
   Publishing it runs `release-bloomcli.yml` → validate → build → publish to PyPI.

6. **Verify.** `uvx bloomctl --version` (stable) or
   `uvx --prerelease=allow bloomctl --version` (pre-release).

## Guardrails

- Never publish by pushing to a branch or a raw tag — only a published Release
  (or a non-publishing dispatch dry run) is valid.
- The tag MUST equal the `pyproject.toml` version, and the changelog MUST have a
  matching entry, or `validate-release` fails.
- If publishing fails on trusted publishing, the pending publisher / `pypi`
  environment setup is incomplete — see RELEASE_PROCESS.md "Setup requirements".
  Nothing is uploaded on failure, so it's safe to fix and re-run.
