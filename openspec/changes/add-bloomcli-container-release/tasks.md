## 0. Verify the changelog scope before bumping anything

- [ ] 0.1 Cross-check `bloomcli/CHANGELOG.md`'s `[Unreleased]` section against the
      actual merged PRs it should cover: #397/#408 (`cyl ingest-result`), #411 (`cyl
      download-for-predict`), #407/#508 (`--predictions-dir` blob upload), #433 (CLI
      reorg), #458. Confirm nothing merged is missing and nothing listed is
      unmerged/inaccurate. Fix the changelog text first if any gap is found — do this
      before touching the version number, since Decision 4/§4 below assumes the
      `[Unreleased]` content is correct.

## 1. Dockerfile (shape tests first)

- [ ] 1.1 Write failing tests in `tests/unit/test_bloomcli_dockerfile_shape.py`
      (pytest, parses `bloomcli/Dockerfile` as text/lines — mirroring the parsing style
      of `tests/unit/test_release_bloomcli_workflow_shape.py` but for a Dockerfile, not
      YAML): base image is a pinned `python:3.11-slim@sha256:...`; a digest-pinned
      `uv` binary is copied in (`COPY --from=ghcr.io/astral-sh/uv:...@sha256:...`); no
      line matches `apt-get install`; a non-root `USER` instruction appears (not
      `USER root` and not absent) before the final `ENTRYPOINT`; the final instruction
      is `ENTRYPOINT ["bloomctl"]` (exec form — no shell form, no `CMD`); no `EXPOSE`
      or `HEALTHCHECK` instruction (this is a CLI image, not a service).
- [ ] 1.2 Write `bloomcli/Dockerfile` until 1.1 passes — base it on
      `bloommcp/Dockerfile`'s shape (digest-pinned base + digest-pinned `uv` + non-root
      `bloom` user + two-layer `uv sync --frozen --no-dev --no-cache`) minus the
      `apt-get` block (design.md Decision 2).
- [ ] 1.3 Manually build the image locally (`docker build -f bloomcli/Dockerfile -t
      bloomctl:local bloomcli/`) and run `docker run --rm bloomctl:local --version`.
      Confirm it prints a version string and exits 0. Report the result — this is a
      manual smoke check, not a CI gate (design.md Decision 5).

## 2. GHCR workflow (shape tests first)

- [ ] 2.1 Write failing tests in `tests/unit/test_docker_build_bloomcli_workflow_shape.py`
      (pytest + PyYAML, mirroring `tests/unit/test_release_bloomcli_workflow_shape.py`'s
      style exactly): `on` triggers are exactly `push` (branches: `[staging]`, paths
      filtered to `bloomcli/**`), `release` (`types: [published]`), `pull_request`
      (paths filtered to `bloomcli/**`), and `workflow_dispatch` — no `push` trigger for
      any branch other than `staging` (explicitly assert `main` is absent from the
      `push.branches` list); the job requests `packages: write` permission and
      no separate PAT/secret is referenced for GHCR auth (only
      `secrets.GITHUB_TOKEN`); the image tags include `sha-` (long or short — pin down
      whichever the implementation uses) and a `staging` literal; a step or `if:`
      condition makes the push conditional on `github.event_name != 'pull_request'`
      (build-only on PRs); the image name resolves to
      `ghcr.io/salk-harnessing-plants-initiative/bloomctl`.
- [ ] 2.2 Write `.github/workflows/docker-build-bloomcli.yml` until 2.1 passes —
      structurally mirror `sleap-roots-predict`'s `docker-build.yml` (Buildx setup,
      `docker/login-action` gated on non-PR events, `docker/metadata-action` for tag
      derivation, `docker/build-push-action` with `push: ${{ github.event_name !=
      'pull_request' }}`) but with the trigger/tag substitutions from design.md
      Decision 3 (`staging` not `main`; no `latest`/`type=raw` tag; add the semver tag
      only via the `release` trigger).
- [ ] 2.3 Confirm `openspec validate add-bloomcli-container-release --strict` still
      passes after adding this file (path-filter syntax and job shape are easy to get
      subtly wrong; re-run the pytest suite from 2.1, not just the validator).

## 3. Docs

- [ ] 3.1 Add a short "Container image" section to `bloomcli/README.md` documenting the
      new `ghcr.io/salk-harnessing-plants-initiative/bloomctl` image, its tag scheme
      (`sha-<short>` / `staging` / a semver tag per release), and that it's built from
      monorepo source (not `pip install`d), matching the level of detail the two prior
      `cyl` CLI changes' README updates used.
- [ ] 3.2 Add a bullet under `[Unreleased]`'s existing `### Added` heading in
      `bloomcli/CHANGELOG.md` for the new Dockerfile + GHCR publishing (do this *before*
      §4 moves `[Unreleased]` to `[0.1.0a2]`, so the entry ships as part of that
      version, not left behind in an empty `[Unreleased]`).

## 4. Version bump (directly on this branch — see design.md Decision 4)

- [ ] 4.1 Bump `bloomcli/pyproject.toml`'s `version` field from `0.1.0a1` to `0.1.0a2`.
- [ ] 4.2 Rename `bloomcli/CHANGELOG.md`'s `## [Unreleased]` heading to
      `## [0.1.0a2] - 2026-07-23`, leaving all its content (including §3.2's new
      bullet) intact underneath, and add a fresh empty `## [Unreleased]` heading above
      it so future changes have somewhere to land.
- [ ] 4.3 Confirm `release-bloomcli.yml`'s changelog-entry check would pass: grep
      `bloomcli/CHANGELOG.md` for `^## \[0.1.0a2\]` and confirm a match (this is exactly
      what that workflow's `validate-release` job checks — verifying it here catches a
      typo before it would otherwise only surface when someone actually cuts the
      Release).

## 5. Pre-merge

- [ ] 5.1 Run `/pre-merge`. Confirm it covers: the two new pytest files (§1.1, §2.1)
      plus the full existing `tests/unit/` suite (regression-guard the other
      workflow-shape tests aren't broken by incidental changes); `openspec validate
      add-bloomcli-container-release --strict`; and — since `bloomcli` is not in any of
      CI's automated per-service audit lists (same gap `add-cyl-blob-upload`'s §7.1
      already noted) — manually run `cd bloomcli && uv lock --check` and `uvx
      pip-audit@2.10.0` if `pyproject.toml`/`uv.lock` changed at all in this branch.
      Fix anything flagged.

## 6. Manual follow-up (NOT part of this PR — do not automate)

- [ ] 6.1 After this PR merges to `staging` and CI is green: create a GitHub Release
      tagged `bloomctl-v0.1.0a2` (or `v0.1.0a2`/`0.1.0a2`), marked **pre-release**
      (matches the `aN` PEP 440 convention). Publishing it fires `release-bloomcli.yml`
      (→ real PyPI) and `docker-build-bloomcli.yml` (→ the `0.1.0a2` GHCR tag). This is
      an irreversible, externally-visible action performed by the user, not by an
      agent — flagged explicitly per project convention.
