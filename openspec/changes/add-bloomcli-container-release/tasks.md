Commit-granularity note (applies throughout): squash each task-group's failing-test +
implementation pair into a single commit before pushing (e.g. 1.1+1.2 together,
3.1+3.2 together) rather than pushing the red state separately — `pr-checks.yml`'s
`python-audit` job runs the full `tests/unit/` suite unconditionally on every push, so a
red-only intermediate commit produces needless failing CI runs.

## 0. Verify the changelog scope before bumping anything

- [x] 0.1 Cross-check `bloomcli/CHANGELOG.md`'s `[Unreleased]` section against the
      canonical set of merged PRs/issues it should cover: **#397/#408** (`cyl
      ingest-result`), **#411/#458** (`cyl download-for-predict` — issue + implementing
      PR, same feature), **#407/#508** (`--predictions-dir` blob upload), **#433** (CLI
      `cyl` command-group reorg). Confirm nothing merged is missing, nothing listed is
      unmerged/inaccurate, and the `### Changed` bullet for the CLI reorg cites `(#433)`
      (it currently doesn't — add the citation while this section is already being
      touched). Fix the changelog text first if any gap is found — do this before
      touching the version number, since §5 below assumes the `[Unreleased]` content is
      correct.

## 1. Dockerfile (shape tests first)

- [x] 1.0 Add `bloomcli/.dockerignore`, mirroring `bloommcp/.dockerignore` /
      `langchain/.dockerignore`, excluding at least `tests/`, `__pycache__/`, `.venv/`,
      `dist/`.
- [x] 1.1 Write failing tests in `tests/unit/test_bloomcli_dockerfile_shape.py`
      (pytest, parses `bloomcli/Dockerfile` as text/lines — mirroring the parsing style
      of `tests/unit/test_release_bloomcli_workflow_shape.py` but for a Dockerfile, not
      YAML): base image is a pinned `python:3.11-slim@sha256:...`; a digest-pinned
      `uv` binary is copied in (`COPY --from=ghcr.io/astral-sh/uv:...@sha256:...`); no
      line matches `apt-get install`; a non-root `USER` instruction appears (not
      `USER root` and not absent) before the final `ENTRYPOINT`; the final instruction
      is `ENTRYPOINT ["bloomctl"]` (exec form — no shell form, no `CMD`); no `EXPOSE`
      or `HEALTHCHECK` instruction; `bloomcli/.dockerignore` exists and contains a
      `tests` (or `tests/`) entry.
- [x] 1.2 Write `bloomcli/Dockerfile` until 1.0-1.1 pass — base it on
      `bloommcp/Dockerfile`'s shape (digest-pinned base + digest-pinned `uv` + non-root
      `bloom` user + two-layer `uv sync --frozen --no-dev --no-cache`) minus the
      `apt-get` block (design.md Decision 2).
- [x] 1.3 Manually build the image locally (`docker build -f bloomcli/Dockerfile -t
      bloomctl:local bloomcli/`) and run `docker run --rm bloomctl:local --version`.
      Confirm it prints a version string and exits 0. Report the result — this is a
      one-time manual smoke check, in addition to (not instead of) §2's ongoing
      pre-merge gate.

## 2. Pre-merge validation + CVE scanning — extend the existing job (design.md Decision 6)

**Read design.md Decision 6 in full before starting** — `pr-checks.yml`'s `docker-build`
job is NOT a single reusable Trivy step; each existing image gets three separate step
blocks, and it's easy to add only some of them.

**Rebase-first note:** open PR #429 (`fix/trivy-report-clean-scan-count`) rewrites the
exact `for img in bloom-web langchain bloommcp caddy workflows` loop this section also
touches. Rebase onto `staging` immediately before starting this section and check
whether #429 has merged first — the conflict is mechanical but not free.

- [x] 2.1 Write failing tests (extend `tests/unit/test_pr_checks_workflow_shape.py`, or
      add a sibling file if that one is already large) asserting `pr-checks.yml`'s
      `docker-build` job has, for `bloomcli`, all three of: (a) a build step
      (`context: ./bloomcli`, `file: ./bloomcli/Dockerfile`, `tags: bloomcli:ci`,
      `push: false, load: true`); (b) a report-only Trivy step (`severity:
      'CRITICAL,HIGH'`, `exit-code: '0'`); and (c) a **separate** blocking Trivy step
      (`severity: 'CRITICAL'`, `exit-code: '1'`) — assert this third step exists
      distinctly from (b), since omitting it would silently make bloomctl's scan
      non-enforcing. Also assert `bloomcli` appears in **both** `for img in ...` loops
      inside the "Generate Trivy report" step. Add a parametrized assertion (mirroring
      this same test file's existing `test_overlay_build_context_matches_prod`
      pattern) that the `bloomcli` build step's `context`/`file` values are identical
      between `pr-checks.yml` and (once §3 exists) `docker-build-bloomcli.yml`, so the
      two build paths can't silently diverge later.
- [x] 2.2 Wire `bloomcli` into `pr-checks.yml`'s `docker-build` job until 2.1 passes —
      diff the new block against `bloommcp`'s three corresponding steps and confirm
      identical shape apart from image name/context/tag. Locally reproduce before
      pushing: `docker build -f bloomcli/Dockerfile -t bloomcli:ci bloomcli/ && trivy
      image bloomcli:ci --severity CRITICAL,HIGH`.

## 3. GHCR publishing workflow — push-only (shape tests first)

- [x] 3.1 Write failing tests in `tests/unit/test_docker_build_bloomcli_workflow_shape.py`
      (pytest + PyYAML, mirroring `tests/unit/test_release_bloomcli_workflow_shape.py`'s
      style exactly):
      - `on` triggers are exactly `push` (branches: `[staging]`, `paths: [bloomcli/**]`
        — assert **exact equality** on the paths list, not a loose "contains" check),
        `release` (`types: [published]`), and `workflow_dispatch` — **no `pull_request`
        trigger at all** (PR validation lives in `pr-checks.yml` per §2), and no
        `push.branches` entry other than `staging` (explicitly assert `main` is absent).
      - The job requests `packages: write` permission; no PAT/secret other than
        `secrets.GITHUB_TOKEN` is referenced anywhere in the file.
      - Tag derivation does **not** use `docker/metadata-action`'s `type=semver` tag
        type anywhere in the file (grep for the literal string `type=semver` and assert
        it's absent). Assert the workflow's raw text contains the literal
        prefix-stripping substrings `TAG#bloomctl-v` and `TAG_VERSION#v}` (proving the
        real file embeds this exact shell logic, not just a same-shaped duplicate) —
        **and separately**, write a small parametrized pytest that re-implements the
        identical stripping logic in Python and feeds it sample tags
        `bloomctl-v0.1.0a2`, `v0.1.0a2`, `0.1.0a2`, asserting each resolves to
        `0.1.0a2` (defense-in-depth for edge cases the raw-text grep can't catch).
      - A `validate-tag` job exists, gated `if: github.event_name == 'release'`, that
        derives the release tag's stripped version and compares it against
        `bloomcli/pyproject.toml`'s version, failing the job on a mismatch; the
        `build-and-push` job (or equivalent) has `needs: [validate-tag]` with an `if:`
        condition that only requires `validate-tag` to have succeeded when the trigger
        is `release` (the `staging`/`workflow_dispatch` paths must not be blocked by a
        job that only runs on `release`).
      - The image name resolves to `ghcr.io/salk-harnessing-plants-initiative/bloomctl`.
      - The `staging`/`sha-` tags are pushed on the `push` trigger; `workflow_dispatch`
        pushes only `sha-<short>` (no `staging` tag mutation from a manual run).
- [x] 3.2 Write `.github/workflows/docker-build-bloomcli.yml` until 3.1 passes —
      structurally mirror `sleap-roots-predict`'s `docker-build.yml` (Buildx setup,
      `docker/login-action`, `docker/metadata-action`, `docker/build-push-action`) but
      with the trigger/tag substitutions and the `validate-tag` job from design.md
      Decision 3 — no `pull_request` trigger, `staging` not `main`, explicit `type=raw`
      version-tag derivation instead of `type=semver`, release-tag/version cross-check
      before any push on the `release` trigger. **PR-review correction:** SHA-pin the
      four `docker/*` actions in this file (not `pr-checks.yml`'s existing `@vN`
      tag-pin style for the same action family) — this job runs `docker/login-action`
      with live `packages: write` GHCR credentials, a higher-value target than
      `pr-checks.yml`'s build-only, push-`false` usage of the same actions.
- [x] 3.3 Confirm `openspec validate add-bloomcli-container-release --strict` still
      passes after adding this file, and re-run the pytest suite from 3.1 (not just the
      validator).

## 4. Docs

- [x] 4.1 Add a short "Container image" section to `bloomcli/README.md` documenting the
      new `ghcr.io/salk-harnessing-plants-initiative/bloomctl` image, its tag scheme
      (`sha-<short>` / `staging` / a version tag per release), and that it's built from
      monorepo source (not `pip install`d) and validated pre-merge via `pr-checks.yml`
      (not the push-only publishing workflow) — matching the level of detail the two
      prior `cyl` CLI changes' README updates used.
- [x] 4.2 Add a bullet under `[Unreleased]`'s existing `### Added` heading in
      `bloomcli/CHANGELOG.md` for the new Dockerfile + GHCR publishing (do this *before*
      §5 moves `[Unreleased]` to `[0.1.0a2]`, so the entry ships as part of that
      version, not left behind in an empty `[Unreleased]`).
- [x] 4.3 Add a short note to `bloomcli/RELEASE_PROCESS.md`'s "Cutting a release"
      section (step 4) that publishing a Release also fires `docker-build-bloomcli.yml`
      alongside `release-bloomcli.yml` (including the new `validate-tag` check),
      pushing a matching GHCR version tag — cross-reference `bloomcli/README.md`'s new
      "Container image" section for the tag scheme rather than re-describing it.

## 5. Version bump (directly on this branch — see design.md Decision 4)

**TDD ordering matters here** — write and observe the changelog-sync test fail *before*
fixing the changelog, not after (the version bump alone is enough to make it fail, since
`0.1.0a1`'s heading already exists today).

- [x] 5.1 Bump `bloomcli/pyproject.toml`'s `version` field from `0.1.0a1` to `0.1.0a2`.
- [x] 5.2 Write `bloomcli/tests/test_changelog_version_sync.py` asserting
      `bloomcli/pyproject.toml`'s `version` field has a matching `## [<version>]`
      heading in `bloomcli/CHANGELOG.md` — the same check `release-bloomcli.yml`'s
      `validate-release` job performs at release time, now exercised as a **standing
      regression guard**. Run it now and confirm it **fails** (per 5.1, `pyproject.toml`
      says `0.1.0a2` but the changelog still has `[Unreleased]`, not `[0.1.0a2]`).
- [x] 5.3 Rename `bloomcli/CHANGELOG.md`'s `## [Unreleased]` heading to
      `## [0.1.0a2] - 2026-07-23`, leaving all its content (including §4.2's new
      bullet) intact underneath, and add a fresh empty `## [Unreleased]` heading above
      it so future changes have somewhere to land. Re-run 5.2's test and confirm it now
      **passes**. Sanity-check both directions: temporarily revert 5.1 or 5.3
      individually and confirm the test goes red each time, then restore both.
- [x] 5.4 Regenerate `bloomcli/uv.lock` (`cd bloomcli && uv lock`) so its
      self-referencing package-version entry matches `0.1.0a2`. Run
      `cd bloomcli && uv lock --check` to confirm it's now in sync.
- [x] 5.5 **`bloomcli/uv.lock` has never been committed to this repo** —
      `.gitignore`'s blanket `uv.lock` rule catches it (confirmed via `git log
      --all -- bloomcli/uv.lock`, empty, and by cloning HEAD into a scratch
      dir and observing the file is absent after checkout). Left unfixed, the
      Dockerfile's `uv sync --frozen` (§1) fails on the first real CI run,
      since `actions/checkout` restores only tracked files. Update
      `.gitignore`'s comment to list `bloomcli/` among the known, tracked
      service dirs, then `git add -f bloomcli/uv.lock` once — it stays
      tracked going forward via the same mechanism the other 4 services rely
      on (gitignore doesn't apply to already-tracked files).

## 6. Close the bloomcli CI-audit-tooling gap (design.md Decision 7)

- [x] 6.1 Add `bloomcli` to `.pre-commit-config.yaml`'s `uv-lock-check` hook `files:`
      regex (currently `^(langchain|bloommcp|services/video-worker|services/workflows)/...`).
- [x] 6.2 Add `bloomcli` to `scripts/check-uv-locks.py`'s `SERVICES` tuple.
- [x] 6.3 Add a "Audit bloomcli dependencies" `uvx pip-audit@2.10.0` step to
      `pr-checks.yml`'s `python-audit` job, mirroring the existing four services' steps
      exactly (same action version, same invocation shape).
- [x] 6.4 Add `bloomcli` to `.claude/commands/pre-merge.md`'s per-service audit loop
      (Step 2) and Docker-build list (Step 3) — for Step 3's smoke-test line, note that
      `bloomcli`'s image has `ENTRYPOINT ["bloomctl"]`, not a Python entrypoint, so the
      correct smoke line is `docker run --rm bloomcli:test --version`, **without** the
      `--entrypoint python` override the existing Python-service entries use.
- [x] 6.5 Write `tests/unit/test_service_audit_tooling_sync.py`: import the real
      `SERVICES` tuple from `scripts/check-uv-locks.py`; parse
      `.pre-commit-config.yaml`'s `uv-lock-check` hook's `files:` regex into a service
      set; grep `pr-checks.yml`'s `python-audit` job for `Audit <service> dependencies`
      step names; grep `.claude/commands/pre-merge.md`'s Step 2 loop — assert all four
      derived sets are equal (name `services/workflows`'s pre-existing absence from
      `pre-merge.md`'s loop as a documented, explicit exception if it's still missing
      there, rather than silently excluding it from the equality check). This is a
      **standing guard for the next service added**, not just a one-time confirmation
      that `bloomcli` landed correctly — `tests/unit/test_check_uv_locks.py` does NOT
      cover this (it only exercises `check_uv_locks.py`'s control flow against its own
      unrelated hardcoded fixture tuple).
- [x] 6.6 Run the new test from 6.5 and the existing `tests/unit/test_check_uv_locks.py`
      to confirm both pass and nothing broke.

## 7. Pre-merge

- [x] 7.1 Run `/pre-merge`. Confirm it now covers `bloomcli` automatically (per §6's
      fix) as well as: all new pytest files (§1.1, §2.1, §3.1, §5.2, §6.5) plus the
      full existing `tests/unit/` suite (regression-guard the other workflow-shape
      tests aren't broken by incidental changes); `openspec validate
      add-bloomcli-container-release --strict`; `cd bloomcli && uv lock --check` and
      `uvx pip-audit@2.10.0` (now automated per §6, but worth confirming manually once
      here too). Fix anything flagged.

## 8. Manual follow-up (NOT part of this PR — do not automate)

- [ ] 8.1 After this PR merges to `staging` and CI is green: create a GitHub Release
      tagged `bloomctl-v0.1.0a2` (or `v0.1.0a2`/`0.1.0a2`), marked **pre-release**
      (matches the `aN` PEP 440 convention). Publishing it fires `release-bloomcli.yml`
      (→ real PyPI) and `docker-build-bloomcli.yml` (→ the `0.1.0a2` GHCR tag, gated by
      §3's `validate-tag` job, which will correctly fail if the chosen tag doesn't match
      `pyproject.toml`'s version). This is an irreversible, externally-visible action
      performed by the user, not by an agent — flagged explicitly per project
      convention.
- [ ] 8.2 If the first push in §8.1 (or an earlier `workflow_dispatch` test run) fails
      GHCR auth with `GITHUB_TOKEN` + `packages: write`, fall back to creating a new PAT
      with `write:packages` scope and storing it as a repo secret (per design.md
      Decision 3's documented fallback — **note:** no existing `GHCR_READ_TOKEN` secret
      exists in this repo to mirror; a new one must be created from scratch) — this is a
      plausible first-time-push failure mode since nothing has ever pushed to this GHCR
      namespace before this change.
