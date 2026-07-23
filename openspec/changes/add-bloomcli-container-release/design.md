## Context

### Where bloomctl is today

- `bloomcli/pyproject.toml:3` — `version = "0.1.0a1"`, matching the one and only
  published PyPI release (`[0.1.0a1] - 2026-06-30` in `bloomcli/CHANGELOG.md`).
- `bloomcli/CHANGELOG.md`'s `[Unreleased]` section lists real, already-merged features:
  the `cyl download`→`cyl.download` command-group reorg (#433), `cyl datasets
  list/get/create`, `cyl experiments list`, `cyl ingest-result` (#397/#408), `cyl
  ingest-result --predictions-dir` (blob upload, #407/#508), and `cyl
  download-for-predict` (#411/#458 — same feature, issue + implementing PR).
- No `Dockerfile` exists anywhere under `bloomcli/` (confirmed via glob at design time).
  `bloomctl` is PyPI-only.
- `.github/workflows/release-bloomcli.yml` already exists and is release-gated: it
  publishes to PyPI via OIDC trusted publishing only when a GitHub Release is
  *published* (or, without publishing, on `workflow_dispatch` as a dry run). It has
  never fired for a real release beyond `0.1.0a1`. Its `validate-release` job strips a
  `bloomctl-v`/`v` prefix from the release tag to compare against `pyproject.toml`'s
  (PEP 440) version, and checks a matching `## [<version>]` heading exists in
  `CHANGELOG.md`, before `build-and-publish` (gated via `needs:`) runs.
- `.github/workflows/version-bloomcli.yml` already exists: a `workflow_dispatch` that
  bumps `bloomcli/pyproject.toml`'s version and opens a PR for it.
- **Correction from the initial draft of this design (caught in adversarial review):**
  the `add-ghcr-image-publishing` OpenSpec change (its code merged via PR #268) is
  **not** a working GHCR-publishing precedent. PR #268 shipped only that change's
  **PR-1 ("Foundation")** — `docker-compose.ci.yml`, CI-compat prep in `pr-checks.yml`,
  and `bloom-web`'s runtime-config plumbing. **PR-3 ("Cutover" — the actual GHCR
  build/push job, and switching `docker-compose.prod.yml` from `build:` to `image:`)
  has never merged.** Verified directly: `.github/workflows/deploy.yml` has zero
  `ghcr.io` references and no `build-images` job; `docker-compose.prod.yml` still
  declares `build:` for every custom service; `openspec list` shows
  `add-ghcr-image-publishing` at 2/71 tasks (and those two are an unrelated
  env-cross-check test, not the GHCR job). **This means there is currently no working
  GHCR-push precedent anywhere in this repo, for any image.** This change is therefore
  the first one to actually build and push a container image to
  `ghcr.io/salk-harnessing-plants-initiative/*` from this repo. The design below builds
  in an explicit fix-forward posture for that reason (see Decision 3's rollback note and
  the Risks table), rather than claiming to follow a proven local pattern. Where this
  design still borrows `image-publishing`'s *specified* (not yet shipped) namespace/tag
  conventions, it says so explicitly as "the design that capability specifies," not as
  working prior art.
- `bloommcp/Dockerfile` is the closest in-repo precedent for "Python CLI/service + uv"
  shape: digest-pinned `python:3.11-slim` base + digest-pinned `uv` binary copy,
  non-root `bloom` user, `apt-get` only for matplotlib/scipy native deps.
- `services/workflows/` (a FastAPI service) is also not GHCR-published —
  `docker-compose.prod.yml` still declares it with `build:`. Its Dockerfile is a
  service-shape precedent only (and its `apt-get` needs — ffmpeg — don't transfer to
  bloomctl).
- `pr-checks.yml`'s `docker-build` job builds all five existing custom-service
  Dockerfiles (`bloom-web`, `langchain-agent`, `bloommcp`, `caddy`,
  `services/workflows`) on every PR — `push: false, load: true` (build-and-validate
  only, no registry push) — and Trivy-scans each resulting image for CVEs. This job is
  the actual, real, currently-working precedent for "validate a Bloom Dockerfile before
  merge," and this change extends it (see Decision 6) rather than inventing a parallel
  validation path.
- Cross-program precedent: `talmolab/sleap-roots-predict`'s `docker-build.yml` builds
  on push to `main` (its own integration/default branch — the *analog* of this repo's
  `staging`, not this repo's `main`, which plays a different, production role), on
  `release: published`, on PRs (build-only, path-filtered), and on
  `workflow_dispatch`; tags via `docker/metadata-action`
  (`type=ref,event=branch`/`type=semver`/`type=sha,format=long`/`type=raw,value=latest,
  enable={{is_default_branch}}`); auth via `secrets.GITHUB_TOKEN` + `packages: write`
  (no separate PAT). **Its `type=semver` tag derivation does not transfer directly** —
  see Decision 3.
- `scripts/check-uv-locks.py`'s `SERVICES` tuple and `.pre-commit-config.yaml`'s
  `uv-lock-check` hook `files:` regex both currently cover `langchain`, `bloommcp`,
  `services/video-worker`, `services/workflows` — **not** `bloomcli`. `bloomcli/uv.lock`
  self-references its own package (`name = "bloomctl"`, `version = "0.1.0a1"`), so a
  version bump with no corresponding `uv lock` regeneration would go stale, and nothing
  automated today would catch it. `pr-checks.yml`'s `python-audit` job's `pip-audit`
  matrix and `.claude/commands/pre-merge.md`'s per-service loops have the same gap.

### Stakeholders

- **Elizabeth (eberrigan)** — driving the A4 write-back tier; needs this change merged
  before Phase 2 (Argo DAG wiring, `sleap-roots-pipeline`) can meaningfully start.
- **Future bloomctl users on the cluster** — Phase 2's WorkflowTemplate will `docker run`
  whatever image tag this change makes available.
- **Future bloomctl users via `pip`/`pipx`** — get an accurate, current PyPI release.

## Goals / Non-Goals

### Goals

- A `bloomctl` container image can be built and run from any commit on `staging`,
  independent of PyPI release timing.
- The image is published to GHCR with the tag scheme `image-publishing` *specifies*
  (`sha-<short>` + mutable branch tag), so Phase 2 can pin an immutable reference the
  same way the predict/traits containers are pinned (adopting that capability's
  specified design, not proven local behavior — see Context).
- `bloomcli/CHANGELOG.md` and `bloomcli/pyproject.toml` accurately reflect everything
  that's shipped, ready for a real GitHub Release.
- CI shape (Dockerfile + workflow) is fenced by pytest tests, matching this repo's
  existing pattern for workflows that can't be exercised end-to-end in PR CI, and the
  Dockerfile itself is validated pre-merge the same way every other Bloom Dockerfile is
  (build + Trivy scan in `pr-checks.yml`).
- The pre-existing `bloomcli`-is-missing-from-dependency-audit-tooling gap is closed as
  part of this change, since this is the first change to give `bloomcli` any real
  CI-relevant packaging surface.

### Non-Goals

- Wiring the image into `sleap-roots-pipeline`'s Argo DAG (Phase 2 — separate change).
- Solving production image promotion generally (pre-existing `image-publishing`
  deferral; not reopened here).
- Building a non-interactive credential story for bloomctl-in-a-container (`bloom
  #398`/`#17` — separate, unstarted).
- Actually publishing the GitHub Release / triggering the real PyPI upload — prepared,
  not executed, by this change.
- Landing `add-ghcr-image-publishing`'s own deferred PR-3 (the compose-services GHCR
  cutover) — that remains that change's job; this change does not depend on it and does
  not do it.

## Decisions

### Decision 1: Container built from monorepo source, not PyPI or a pinned git ref

**Chosen:** `bloomcli/Dockerfile` runs `uv sync --frozen --no-dev --no-cache` against
`bloomcli/pyproject.toml` + `bloomcli/uv.lock` **at the commit being built**, then
`COPY`s the source and installs the project itself — exactly the pattern
`bloom-web`/`langchain-agent`/`bloommcp` already use (`context: ./bloomcli`,
`file: ./bloomcli/Dockerfile`).

**Why:** this decouples image freshness from release cadence. If the image installed
`bloomctl==<version>` from PyPI, the GHCR workflow could only build *after*
`release-bloomcli.yml` had already published that version — an ordering dependency that
would make it impossible to build/smoke-test the Dockerfile in the same PR that adds it
(no version would yet be published). Building from source means every `staging` push
gets a fresh, immediately-buildable image, matching how every other Bloom container
already works.

**Alternatives considered:**
- *Install from a pinned git commit* (`pip install git+...@<sha>#subdirectory=bloomcli`)
  — rejected: diverges from how every other Bloom image (and `sleap-roots-predict`)
  ships; adds no benefit over building from the checked-out source directly, since the
  workflow already has that source checked out.
- *Install from PyPI in the Dockerfile* (`uv pip install bloomctl==<version>`) —
  rejected: creates an ordering dependency where the image can only build after
  `release-bloomcli.yml` has already published that version, which defeats the goal of
  decoupling image freshness from release timing.

A real PyPI release (`0.1.0a2`) still ships as part of this change — for `pip`/`pipx`
users — it just doesn't gate the container.

### Decision 2: Base image — `python:3.11-slim` + `uv`, no apt packages

**Chosen:** mirror `bloommcp/Dockerfile`'s base-image and `uv`-install pattern
(digest-pinned `python:3.11-slim`, digest-pinned `uv` binary copy via
`COPY --from=ghcr.io/astral-sh/uv:...`, non-root `bloom` user, two-layer
`uv sync --frozen --no-dev --no-cache`), but **omit the `apt-get` block entirely**, and
add a `bloomcli/.dockerignore` (mirroring `bloommcp/.dockerignore` /
`langchain/.dockerignore`) excluding `tests/`, `__pycache__/`, `.venv/`, `dist/` from
the build context.

**Why:** `bloommcp`'s `apt-get` installs `libfreetype6-dev`/`libpng-dev`/`libjpeg-dev`/
`pkg-config`/`gcc` for matplotlib/scipy native builds. `bloomctl`'s dependency set
(`click`, `rich`, `httpx`, `supabase`, `python-dotenv`, `cryptography>=48.0.1`,
`sleap-roots-contracts`) has no such need — `cryptography` ships manylinux wheels for
the target platforms, and nothing else in the tree touches native code. `bloommcp` and
`langchain` both gitignore-style exclude their test/cache dirs from the build context;
`bloomcli` currently has no `.dockerignore` at all, which without one would ship
`tests/`, fixtures, and `__pycache__` into every image layer.

### Decision 3: GHCR workflow shape — dedicated `docker-build-bloomcli.yml`, staging/release/dispatch-triggered only; PR validation lives in `pr-checks.yml` instead

**Chosen:** a new, standalone workflow file that **only pushes** — it triggers on push
to `staging` (path-filtered to `bloomcli/**`), on `release: published`, and on
`workflow_dispatch`. **It has no `pull_request` trigger.** Instead, `bloomcli` is added
as a sixth image to `pr-checks.yml`'s existing `docker-build` job (Decision 6) — that
job already builds-and-Trivy-scans-without-pushing for every other custom service on
every PR, so adding `bloomcli` there gets Dockerfile validation, CVE scanning, *and* a
pre-merge gate for free from an already-proven pattern, instead of duplicating a
build-only path in the new workflow.

**Auth, and the rollback/fix-forward posture that goes with it:** `secrets.GITHUB_TOKEN`
+ `packages: write` — the standard GitHub Actions mechanism for first-party GHCR
pushes, independent of any Bloom-specific precedent (per the Context correction, this
repo has no proof this succeeds against this specific org's GHCR namespace yet, since
nothing has ever pushed there). Treated as a real but manageable risk, not a blocker: if
the first `workflow_dispatch` run 403s on push, the documented fallback is a PAT with
`write:packages` (the same shape as the existing `GHCR_READ_TOKEN` used for the *pull*
side elsewhere), stored the same way. This is safe to treat as fix-forward rather than
needing a rollback plan, because the workflow is a leaf — nothing currently consumes its
pushed tags (Phase 2 hasn't landed) — so a broken run's blast radius is "the image
doesn't exist or is mistagged," never a broken deploy or a broken `staging` branch; any
fix is a follow-up commit or `workflow_dispatch` re-run, never an emergency revert.

**Tag derivation — do NOT use `docker/metadata-action`'s `type=semver`:** bloomctl
versions are PEP 440 (`0.1.0a2`), not semver — strict-semver parsing would not reliably
match a PEP 440 pre-release identifier. Instead, the workflow derives its tags
explicitly, mirroring `release-bloomcli.yml`'s own tag-prefix-stripping logic rather
than relying on `docker/metadata-action` to parse an arbitrary ref:

- On push to `staging`: `sha-<short-git-sha>` (`git rev-parse --short HEAD`, matching
  `image-publishing`'s specified scheme) + the mutable literal tag `staging`.
- On `release: published`: same as above, **plus** a tag equal to the release's bare
  version — computed with the identical shell logic `release-bloomcli.yml` already uses
  (`TAG_VERSION="${TAG#bloomctl-v}"; TAG_VERSION="${TAG_VERSION#v}"`) — fed to
  `docker/metadata-action` as `type=raw,value=${{ steps.version.outputs.tag }}`, not
  `type=semver`. This guarantees the GHCR version tag and the PyPI-published version are
  always the same string, regardless of which of `bloomctl-vX.Y.Z`/`vX.Y.Z`/`X.Y.Z` was
  used to tag the Release.
- On `workflow_dispatch`: `sha-<short>` only (no `staging` mutation, so a manual
  dispatch can't accidentally move the `staging` pointer).

**A malformed release tag must not push a mistagged image (found in round-2 review):**
`release-bloomcli.yml`'s `validate-release` job strips the same prefix and compares the
result against `pyproject.toml`'s actual version, aborting before `build-and-publish` on
a mismatch. `docker-build-bloomcli.yml` fires independently off the same `release:
published` event with no cross-workflow dependency — without an equivalent check, a
Release cut with the wrong tag (e.g. `bloomctl-v0.1.0a3` while `pyproject.toml` still
says `0.1.0a2`) would correctly fail to publish to PyPI, but would still let
`docker-build-bloomcli.yml` build the actual current source and push a GHCR image
mistagged `0.1.0a3`. **Fix:** add a `validate-tag` job to `docker-build-bloomcli.yml`,
gating `build-and-push` via `needs:` — but *only* on the `release` trigger (the
`staging`/`workflow_dispatch` paths have no tag to validate against and must not be
gated by it):

```yaml
jobs:
  validate-tag:
    if: github.event_name == 'release'
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: bloomcli
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@37802adc94f370d6bfd71619e3f0bf239e1f3b78
      - name: Validate release tag matches pyproject.toml version
        env:
          TAG: ${{ github.event.release.tag_name }}
        run: |
          VERSION=$(uv version | awk '{print $NF}')
          TAG_VERSION="${TAG#bloomctl-v}"; TAG_VERSION="${TAG_VERSION#v}"
          if [ "$TAG_VERSION" != "$VERSION" ]; then
            echo "::error::Release tag ($TAG -> $TAG_VERSION) does not match bloomcli/pyproject.toml version ($VERSION) — refusing to push a mistagged GHCR image"
            exit 1
          fi
  build-and-push:
    needs: [validate-tag]
    if: always() && (github.event_name != 'release' || needs.validate-tag.result == 'success')
    ...
```

**Why not fold into `deploy.yml`'s `build-images` job:** that job's entire purpose (once
`add-ghcr-image-publishing`'s PR-3 eventually lands) is feeding
`docker-compose.prod.yml`'s `pull`/`up` steps for services actually deployed via
compose. `bloomctl` is not a compose-deployed service — its consumer is
`sleap-roots-pipeline`'s Argo DAG, a different system entirely. Folding it in would
conflate two unrelated consumers in one workflow and, worse, would couple this change's
landing to a job that doesn't exist in `deploy.yml` yet.

**Why not mirror `sleap-roots-predict`'s branch triggers literally:** its `main` plays
*this* repo's `staging` role (single integration branch), not this repo's `main`
(production/consolidation, 492+ commits behind `staging` at design time — see Context).
`image-publishing`'s specified-but-unshipped design already establishes staging-only
build triggers for custom images with no `main` trigger; this change follows that
specified intent for consistency with the rest of the program.

### Decision 4: Version bump lands in this PR directly; cutting the Release does not

**Chosen:** `bloomcli/pyproject.toml`'s version and `bloomcli/CHANGELOG.md`'s
`[Unreleased]`→`[0.1.0a2]` move are edited directly on this change's branch, rather than
via the separate `version-bloomcli.yml` dispatch-and-PR flow. `bloomcli/uv.lock` is
regenerated (`uv lock`) in the same commit, since it self-references the package's own
version and would otherwise silently drift (`bloomcli` is not in
`scripts/check-uv-locks.py`'s automated `SERVICES` tuple today — Decision 7 fixes that
gap, but the regeneration itself still needs to happen explicitly in this PR).

**Why:** this project's convention is proposal + implementation in the same PR; routing
the version bump through a second, independently-opened automated PR would split it out
unnecessarily when it's simple enough to include directly. `version-bloomcli.yml` remains
available for *future* bumps where dispatching it is preferable.

**Explicitly NOT part of this change's automation:** creating the actual GitHub Release
(tagged `bloomctl-v0.1.0a2`, marked pre-release) that fires `release-bloomcli.yml` and
publishes to real PyPI. That's a real, externally-visible, effectively-irreversible
publish action (PyPI releases can't be deleted, only yanked) — it's the user's call to
make, after this PR merges and CI is green on `staging`. `tasks.md` documents the exact
steps as a manual follow-up, not an automated task.

### Decision 5: Testing — pytest workflow/Dockerfile-shape tests (including a permanent changelog-sync guard), manual smoke test (not a CI gate)

**Chosen:** following `tests/unit/test_release_bloomcli_workflow_shape.py`'s existing
pattern, new pytest files assert the new Dockerfile's and workflow's static shape
(exact trigger set including exact-equality path-filter checks, tag derivation logic —
including that `type=semver` is NOT used, push-gating on event type, base image, absence
of apt packages, presence of `.dockerignore`, non-root user, `ENTRYPOINT` form, absence
of `EXPOSE`/`HEALTHCHECK`). A **new, permanent** test
(`bloomcli/tests/test_changelog_version_sync.py`) asserts `pyproject.toml`'s version has
a matching `## [<version>]` heading in `CHANGELOG.md` — the same check
`release-bloomcli.yml`'s `validate-release` job performs at release time, but exercised
as a standing regression guard on every PR from now on, not just verified once by hand
during this change (the original draft of this task was a one-off manual `grep`; review
correctly flagged that as insufficient for a requirement this proposal itself defines).

A manual `docker build` + `docker run <image> --version` is a `tasks.md` item for the
implementer to perform and report on once, in addition to (not instead of) the
now-automated `pr-checks.yml` build+Trivy-scan gate (Decision 6) that runs on every PR
going forward.

### Decision 6: CVE scanning + PR-time validation — extend `pr-checks.yml`'s existing `docker-build` job, don't build a parallel path

**Chosen:** add `bloomcli` as a sixth entry to `pr-checks.yml`'s `docker-build` job.
**This job is not a single reusable Trivy step — each of the 5 existing images gets
three separate step blocks, exactly mirrored for `bloomcli`:**
1. **Build** — `context: ./bloomcli`, `file: ./bloomcli/Dockerfile`,
   `tags: bloomcli:ci`, `push: false, load: true`.
2. **Report-only scan** — Trivy against `bloomcli:ci`, `severity: 'CRITICAL,HIGH'`,
   `exit-code: '0'`, `output: 'trivy-bloomcli.txt'` (does not fail the job).
3. **Blocking gate** — a *separate* Trivy step, `severity: 'CRITICAL'`,
   `exit-code: '1'` — **this is the step that actually blocks merge on a CRITICAL CVE.
   Omitting it (e.g. by only adding steps 1–2) would silently make bloomctl's scan
   non-enforcing while every sibling image's is.**

`bloomcli` also needs appending to the two `for img in bloom-web langchain bloommcp
caddy workflows` shell loops inside the job's "Generate Trivy report" step. (Today, only
CRITICAL severity blocks merge for any of the 5 images — HIGH is report-only; `bloomcli`
inherits the same policy, not a new one.)

**Verification, not just a manual read:** extend `tests/unit/test_pr_checks_workflow_shape.py`
(or add a sibling file) with an automated assertion that all three `bloomcli` step
blocks exist with the right shape, `bloomcli` appears in both `for img in ...` loops,
and — mirroring that same test file's existing `test_overlay_build_context_matches_prod`
pattern for `docker-compose.ci.yml`/`docker-compose.prod.yml` — a parametrized check
that the `bloomcli` build step's `context`/`file` values are byte-identical between
`pr-checks.yml` and `docker-build-bloomcli.yml`, so the two build paths can't silently
diverge over time (e.g. one gains a build-arg the other doesn't).

**Why:** this achieves two things at once, using an already-proven pattern instead of
two new ones: (1) Trivy CVE scanning for `bloomctl`'s dependencies
(`cryptography`/`supabase`/`sleap-roots-contracts`), matching the proposal's own stated
"parity with sibling images" goal, which the original draft missed entirely; and (2) a
real pre-merge validation gate for the Dockerfile, functionally equivalent to
`release-bloomcli.yml`'s `validate-release → build-and-publish` gating pattern, without
adding a redundant test/lint job inside `docker-build-bloomcli.yml` itself. Once a PR
merges to `staging`, `docker-build-bloomcli.yml`'s push-only job can trust that the
Dockerfile just built and scanned clean in `pr-checks.yml` on that same code — this is
the same trust model this repo already uses (and that `image-publishing`'s eventual
`build-images` job will also use) between PR-time validation and post-merge publish.

**Implementer note — real merge-conflict risk, not hypothetical:** as of round-2 review,
open PR #429 (`fix/trivy-report-clean-scan-count`) rewrites the exact `for img in
bloom-web langchain bloommcp caddy workflows` loop this decision also touches. Rebase
onto `staging` immediately before implementing this section, and check whether #429 has
merged first — the conflict is mechanical (adjacent lines in the same loop) but not
free.

### Decision 7: Close the bloomcli CI-audit-tooling gap in this PR

**Chosen:** since this is the first change to give `bloomcli` any Dockerfile/CI-relevant
packaging surface, add `bloomcli` to the four places that currently omit it:
`.pre-commit-config.yaml`'s `uv-lock-check` hook `files:` regex,
`scripts/check-uv-locks.py`'s `SERVICES` tuple, a new "Audit bloomcli dependencies" step
in `pr-checks.yml`'s `python-audit` job (mirroring the existing four services'
`pip-audit@2.10.0` steps), and `.claude/commands/pre-merge.md`'s per-service audit/build
loops.

**Why now, not deferred:** `add-cyl-blob-upload`'s own retro noted this exact gap and
punted it pending a check-in; this proposal is the natural point to close it, since (a)
it's a small, mechanical, four-file addition, and (b) it directly closes the loop on
Decision 4's `uv.lock`-drift concern — without this, a *future* bloomctl dependency bump
would silently go unaudited the same way this one almost did.

**A fix without a sync-guard test only fixes today (found in round-2 review):** adding
`bloomcli` to the four locations once doesn't stop a *future* new service from being
added to only some of them. `tests/unit/test_check_uv_locks.py` does **not** cover this
— it exercises `check_uv_locks.py`'s control flow against its own hardcoded local
fixture tuple, never the real `SERVICES` tuple or the other three files. **Fix:** add a
new `tests/unit/test_service_audit_tooling_sync.py` that imports the real `SERVICES`
tuple from `scripts/check-uv-locks.py`, parses `.pre-commit-config.yaml`'s
`uv-lock-check` hook's `files:` regex into a service set, greps `pr-checks.yml`'s
`python-audit` job for `Audit <service> dependencies` step names, and greps
`.claude/commands/pre-merge.md`'s Step 2 loop — then asserts all four derived sets are
equal (call out `services/workflows`'s pre-existing absence from `pre-merge.md`'s loop
as a named, documented exception if it isn't fixed here too, rather than silently
excluding it from the equality check).

## Risks / Trade-offs

| Risk | Mitigation |
| --- | --- |
| `bloomcli/CHANGELOG.md`'s `[Unreleased]` section is stale/incomplete relative to what's actually merged | `tasks.md` §0 cross-checks it against the canonical PR/issue list (#397/#408, #411/#458, #407/#508, #433) before bumping the version — corrected first if any gap is found. |
| **This is the first GHCR-push workflow in the repo — no proven local auth/tag pattern to fall back on** (corrected from the original draft, which incorrectly claimed working prior art; see Context) | Treated explicitly as a real, first-of-its-kind risk: `GITHUB_TOKEN` + `packages: write` is the standard mechanism independent of Bloom-specific history; a documented PAT fallback exists if the first push 403s; blast radius is contained (nothing consumes the pushed tags yet) so any failure is fix-forward, never an emergency rollback (Decision 3). |
| `docker/metadata-action`'s `type=semver` would silently mishandle bloomctl's PEP 440 versions | Decision 3: tags are derived explicitly via the same prefix-stripping shell logic `release-bloomcli.yml` already uses, fed to `metadata-action` as `type=raw`, not `type=semver`. |
| A malformed/mismatched release tag pushes a mistagged GHCR image even though `release-bloomcli.yml` correctly aborts | Decision 3's `validate-tag` job gates `build-and-push` on the `release` trigger only, replicating `release-bloomcli.yml`'s own tag/version check. |
| `bloomcli` is added to the 4 CI-audit locations once but a future new service repeats the same gap | Decision 7's sync-guard test (`test_service_audit_tooling_sync.py`) asserts all 4 locations stay equal going forward, not just today. |
| New workflow's push/release-triggered paths can't be exercised by PR CI | Matches the exact justification already accepted for `release-bloomcli.yml`/`version-bloomcli.yml`: pytest shape tests are the pre-merge gate for the push-only workflow; Dockerfile build+CVE-scan validation itself happens via the `pr-checks.yml` extension (Decision 6), which unlike the workflow-shape tests genuinely does exercise a real build on every PR. |
| Image builds but no PyPI release is ever cut (automation stops at "prepared") | Explicit, intentional: cutting a real PyPI release is irreversible and stays a human action. `tasks.md` documents the exact manual steps so nothing is lost between "prepared" and "done." |
| Bloomctl's dependency set changes later and needs native build deps after all | `cryptography`'s manylinux wheel coverage is a current fact, not a permanent guarantee; if a future dependency needs native compilation, the Dockerfile gains an `apt-get` block then, the same way `bloommcp`'s did. |
| `bloomcli/uv.lock` drifts silently after future version bumps | Decision 7 adds `bloomcli` to `check-uv-locks.py`/pre-commit so this is caught automatically going forward, not just fixed once here. |
| Confusion between this change's `bloomcli-packaging` capability and the unrelated `image-publishing` capability once both are eventually archived | Both `design.md` (this file) and `proposal.md` state explicitly that these are separate, non-overlapping capabilities (different services, different consumers, different trigger semantics) rather than one extending the other — see the capability-boundary note below. |

**Capability-boundary note (`bloomcli-packaging` vs. `image-publishing`):** kept as two
separate capabilities rather than extending `image-publishing`, because (a)
`image-publishing`'s requirement text hard-codes exactly three named services
(`bloom-web`, `langchain-agent`, `bloommcp`) in its normative language, so adding a
fourth would require a full-text MODIFIED delta rather than a clean ADDED addition; (b)
bloomctl has genuinely different trigger semantics (no PR-push path — see Decision 3 —
and a release-triggered version tag) that `image-publishing` doesn't model at all; and
(c) bloomctl's "PyPI Release Readiness" requirement has no analog in `image-publishing`
at all (that capability doesn't publish to any package index). This is a deliberate,
narrower-and-distinct capability, not an oversight.

## Open Questions

- **None blocking.** The original handoff's open question (PyPI vs. git-source vs.
  monorepo-source install) is resolved by Decision 1. The tag-derivation ambiguity
  surfaced during review is resolved by Decision 3's explicit `type=raw` approach. The
  false-precedent framing surfaced during review is corrected throughout this file's
  Context section.
