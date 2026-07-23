Commit-granularity note (applies throughout): squash each task-group's failing-test +
implementation pair into a single commit before pushing (e.g. 1.1+1.2 together,
2.1+2.2 together) rather than pushing the red state separately — `pr-checks.yml`'s
`python-audit` job runs the full `tests/unit/` suite unconditionally on every push, so a
red-only intermediate commit produces needless failing CI runs.

## 0. Verify the changelog scope before bumping anything

- [ ] 0.1 Cross-check `bloomcli/CHANGELOG.md`'s `[Unreleased]` section against the
      canonical set of merged PRs/issues it should cover: **#397/#408** (`cyl
      ingest-result`), **#411/#458** (`cyl download-for-predict` — issue + implementing
      PR, same feature), **#407/#508** (`--predictions-dir` blob upload), **#433** (CLI
      `cyl` command-group reorg). Confirm nothing merged is missing, nothing listed is
      unmerged/inaccurate, and the `### Changed` bullet for the CLI reorg cites `(#433)`
      (it currently doesn't — add the citation while this section is already being
      touched). Fix the changelog text first if any gap is found — do this before
      touching the version number, since §4 below assumes the `[Unreleased]` content is
      correct.

## 1. Dockerfile (shape tests first)

- [ ] 1.0 Add `bloomcli/.dockerignore`, mirroring `bloommcp/.dockerignore` /
      `langchain/.dockerignore`, excluding at least `tests/`, `__pycache__/`, `.venv/`,
      `dist/`.
- [ ] 1.1 Write failing tests in `tests/unit/test_bloomcli_dockerfile_shape.py`
      (pytest, parses `bloomcli/Dockerfile` as text/lines — mirroring the parsing style
      of `tests/unit/test_release_bloomcli_workflow_shape.py` but for a Dockerfile, not
      YAML): base image is a pinned `python:3.11-slim@sha256:...`; a digest-pinned
      `uv` binary is copied in (`COPY --from=ghcr.io/astral-sh/uv:...@sha256:...`); no
      line matches `apt-get install`; a non-root `USER` instruction appears (not
      `USER root` and not absent) before the final `ENTRYPOINT`; the final instruction
      is `ENTRYPOINT ["bloomctl"]` (exec form — no shell form, no `CMD`); no `EXPOSE`
      or `HEALTHCHECK` instruction; `bloomcli/.dockerignore` exists and contains a
      `tests` (or `tests/`) entry.
- [ ] 1.2 Write `bloomcli/Dockerfile` until 1.0-1.1 pass — base it on
      `bloommcp/Dockerfile`'s shape (digest-pinned base + digest-pinned `uv` + non-root
      `bloom` user + two-layer `uv sync --frozen --no-dev --no-cache`) minus the
      `apt-get` block (design.md Decision 2).
- [ ] 1.3 Manually build the image locally (`docker build -f bloomcli/Dockerfile -t
      bloomctl:local bloomcli/`) and run `docker run --rm bloomctl:local --version`.
      Confirm it prints a version string and exits 0. Report the result — this is a
      one-time manual smoke check, in addition to (not instead of) §1.4's ongoing
      pre-merge gate.

## 1.4 Pre-merge validation + CVE scanning (extend the existing job, per design.md Decision 6)

- [ ] 1.4.1 Add `bloomcli` as a sixth image to `pr-checks.yml`'s `docker-build` job
      (`context: ./bloomcli`, `file: ./bloomcli/Dockerfile`, `push: false, load: true`),
      reusing the job's existing Trivy-scan step for the new image — mirror the exact
      shape of the five existing image entries in that job.
- [ ] 1.4.2 Confirm (by reading the job, and — if feasible locally — a dry run) that the
      new entry is wired into the same matrix/step structure the other five use, not a
      bespoke one-off block.

## 2. GHCR publishing workflow — push-only (shape tests first)

- [ ] 2.1 Write failing tests in `tests/unit/test_docker_build_bloomcli_workflow_shape.py`
      (pytest + PyYAML, mirroring `tests/unit/test_release_bloomcli_workflow_shape.py`'s
      style exactly):
      - `on` triggers are exactly `push` (branches: `[staging]`, `paths: [bloomcli/**]`
        — assert **exact equality** on the paths list, not a loose "contains" check),
        `release` (`types: [published]`), and `workflow_dispatch` — **no `pull_request`
        trigger at all** (PR validation lives in `pr-checks.yml` per §1.4), and no
        `push.branches` entry other than `staging` (explicitly assert `main` is absent).
      - The job requests `packages: write` permission; no PAT/secret other than
        `secrets.GITHUB_TOKEN` is referenced anywhere in the file.
      - Tag derivation does **not** use `docker/metadata-action`'s `type=semver` tag
        type anywhere in the file (grep for the literal string `type=semver` and assert
        it's absent) — assert instead that a step computes a bare version by stripping
        a `bloomctl-v`/`v` prefix (mirror `release-bloomcli.yml`'s existing
        `TAG_VERSION="${TAG#bloomctl-v}"` / `TAG_VERSION="${TAG_VERSION#v}"` pattern —
        write a small pytest that feeds sample tags `bloomctl-v0.1.0a2`, `v0.1.0a2`,
        `0.1.0a2` through the identical shell-equivalent Python logic and asserts each
        resolves to `0.1.0a2`), and that this value is fed to `docker/metadata-action`
        as `type=raw,value=...` on the `release` trigger only.
      - The image name resolves to `ghcr.io/salk-harnessing-plants-initiative/bloomctl`.
      - The `staging`/`sha-` tags are pushed on the `push` trigger; `workflow_dispatch`
        pushes only `sha-<short>` (no `staging` tag mutation from a manual run).
- [ ] 2.2 Write `.github/workflows/docker-build-bloomcli.yml` until 2.1 passes —
      structurally mirror `sleap-roots-predict`'s `docker-build.yml` (Buildx setup,
      `docker/login-action`, `docker/metadata-action`, `docker/build-push-action`,
      pinned to this repo's existing `docker/*@vN` tag-pin convention already used in
      `pr-checks.yml` for the same action family) but with the trigger/tag
      substitutions from design.md Decision 3 — no `pull_request` trigger, `staging`
      not `main`, explicit `type=raw` version-tag derivation instead of `type=semver`.
- [ ] 2.3 Confirm `openspec validate add-bloomcli-container-release --strict` still
      passes after adding this file, and re-run the pytest suite from 2.1 (not just the
      validator).

## 3. Docs

- [ ] 3.1 Add a short "Container image" section to `bloomcli/README.md` documenting the
      new `ghcr.io/salk-harnessing-plants-initiative/bloomctl` image, its tag scheme
      (`sha-<short>` / `staging` / a version tag per release), and that it's built from
      monorepo source (not `pip install`d) and validated pre-merge via `pr-checks.yml`
      (not the push-only publishing workflow) — matching the level of detail the two
      prior `cyl` CLI changes' README updates used.
- [ ] 3.2 Add a bullet under `[Unreleased]`'s existing `### Added` heading in
      `bloomcli/CHANGELOG.md` for the new Dockerfile + GHCR publishing (do this *before*
      §4 moves `[Unreleased]` to `[0.1.0a2]`, so the entry ships as part of that
      version, not left behind in an empty `[Unreleased]`).
- [ ] 3.3 Add a short note to `bloomcli/RELEASE_PROCESS.md`'s "Cutting a release"
      section (step 4) that publishing a Release also fires `docker-build-bloomcli.yml`
      alongside `release-bloomcli.yml`, pushing a matching GHCR version tag —
      cross-reference `bloomcli/README.md`'s new "Container image" section for the tag
      scheme rather than re-describing it.

## 4. Version bump (directly on this branch — see design.md Decision 4)

- [ ] 4.1 Bump `bloomcli/pyproject.toml`'s `version` field from `0.1.0a1` to `0.1.0a2`.
- [ ] 4.2 Rename `bloomcli/CHANGELOG.md`'s `## [Unreleased]` heading to
      `## [0.1.0a2] - 2026-07-23`, leaving all its content (including §3.2's new
      bullet) intact underneath, and add a fresh empty `## [Unreleased]` heading above
      it so future changes have somewhere to land.
- [ ] 4.3 Write a failing test in `bloomcli/tests/test_changelog_version_sync.py`
      asserting `bloomcli/pyproject.toml`'s `version` field has a matching
      `## [<version>]` heading in `bloomcli/CHANGELOG.md` — the same check
      `release-bloomcli.yml`'s `validate-release` job performs at release time, now
      exercised as a **standing regression guard**, not a one-off manual check. Confirm
      it passes after 4.1/4.2, and confirm it would fail if either were reverted
      individually (sanity-check the test actually exercises both directions).
- [ ] 4.4 Regenerate `bloomcli/uv.lock` (`cd bloomcli && uv lock`) so its
      self-referencing package-version entry matches `0.1.0a2`. Run
      `cd bloomcli && uv lock --check` to confirm it's now in sync.

## 5. Close the bloomcli CI-audit-tooling gap (design.md Decision 7)

- [ ] 5.1 Add `bloomcli` to `.pre-commit-config.yaml`'s `uv-lock-check` hook `files:`
      regex (currently `^(langchain|bloommcp|services/video-worker|services/workflows)/...`).
- [ ] 5.2 Add `bloomcli` to `scripts/check-uv-locks.py`'s `SERVICES` tuple.
- [ ] 5.3 Add a "Audit bloomcli dependencies" `uvx pip-audit@2.10.0` step to
      `pr-checks.yml`'s `python-audit` job, mirroring the existing four services' steps
      exactly (same action version, same invocation shape).
- [ ] 5.4 Add `bloomcli` to `.claude/commands/pre-merge.md`'s per-service audit loop
      (Step 2) and Docker-build list (Step 3).
- [ ] 5.5 Run the existing `tests/unit/test_check_uv_locks.py` (and any other test that
      enumerates the audited-services list) to confirm nothing broke, and that
      `bloomcli` is now picked up.

## 6. Pre-merge

- [ ] 6.1 Run `/pre-merge`. Confirm it now covers `bloomcli` automatically (per §5's
      fix) as well as: the new pytest files (§1.1, §2.1, §4.3) plus the full existing
      `tests/unit/` suite (regression-guard the other workflow-shape tests aren't
      broken by incidental changes); `openspec validate
      add-bloomcli-container-release --strict`; `cd bloomcli && uv lock --check` and
      `uvx pip-audit@2.10.0` (now automated per §5, but worth confirming manually once
      here too). Fix anything flagged.

## 7. Manual follow-up (NOT part of this PR — do not automate)

- [ ] 7.1 After this PR merges to `staging` and CI is green: create a GitHub Release
      tagged `bloomctl-v0.1.0a2` (or `v0.1.0a2`/`0.1.0a2`), marked **pre-release**
      (matches the `aN` PEP 440 convention). Publishing it fires `release-bloomcli.yml`
      (→ real PyPI) and `docker-build-bloomcli.yml` (→ the `0.1.0a2` GHCR tag,
      correctly derived per §2's prefix-stripping logic regardless of which tag format
      is used). This is an irreversible, externally-visible action performed by the
      user, not by an agent — flagged explicitly per project convention.
- [ ] 7.2 If the first push in §7.1 (or an earlier `workflow_dispatch` test run) fails
      GHCR auth with `GITHUB_TOKEN` + `packages: write`, fall back to a PAT with
      `write:packages` scope, stored the same way `GHCR_READ_TOKEN` is stored elsewhere
      in this repo (per design.md Decision 3's documented fallback) — this is a
      plausible first-time-push failure mode since nothing has ever pushed to this GHCR
      namespace before this change.
