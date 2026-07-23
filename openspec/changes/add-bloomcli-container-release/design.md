## Context

### Where bloomctl is today

- `bloomcli/pyproject.toml:3` — `version = "0.1.0a1"`, matching the one and only
  published PyPI release (`[0.1.0a1] - 2026-06-30` in `bloomcli/CHANGELOG.md`).
- `bloomcli/CHANGELOG.md`'s `[Unreleased]` section lists four real, already-merged
  features: the `cyl download`→`cyl.download` command-group reorg, `cyl datasets
  list/get/create`, `cyl experiments list`, `cyl ingest-result` (#397), `cyl
  ingest-result --predictions-dir` (blob upload, #407), and `cyl download-for-predict`
  (#411).
- No `Dockerfile` exists anywhere under `bloomcli/` (confirmed via glob at design time).
  `bloomctl` is PyPI-only.
- `.github/workflows/release-bloomcli.yml` already exists and is release-gated: it
  publishes to PyPI via OIDC trusted publishing only when a GitHub Release is
  *published* (or, without publishing, on `workflow_dispatch` as a dry run). It has
  never fired for a real release beyond `0.1.0a1`.
- `.github/workflows/version-bloomcli.yml` already exists: a `workflow_dispatch` that
  bumps `bloomcli/pyproject.toml`'s version and opens a PR for it.
- The (already-merged via PR #268, but not yet `openspec archive`d) `image-publishing`
  capability governs GHCR publishing for `bloom-web`/`langchain-agent`/`bloommcp`: CI
  builds on push to `staging` only, tags `sha-<short-git-sha>` (immutable) + `staging`
  (mutable), namespace `ghcr.io/salk-harnessing-plants-initiative/<service>`. Production
  deploys reuse an already-built `sha-<short>` image via a manually-pinned `IMAGE_TAG` —
  automatic staging→production promotion is itself deferred (Week 3 of bloom #107).
  `main` is **not** a build trigger for any existing custom image.
- `bloommcp/Dockerfile` is the closest in-repo precedent for "Python CLI/service + uv +
  GHCR" shape: digest-pinned `python:3.11-slim` base + digest-pinned `uv` binary copy,
  non-root `bloom` user, `apt-get` only for matplotlib/scipy native deps.
- `services/workflows/` (a FastAPI service) is *not* part of the `image-publishing`
  capability — `docker-compose.prod.yml` still declares it with `build:`, not `image:`.
  It is not a useful GHCR-CI precedent, only a Dockerfile-shape one (and its `apt-get`
  needs — ffmpeg — don't transfer to bloomctl either).
- Cross-program precedent: `talmolab/sleap-roots-predict`'s `docker-build.yml` builds
  on push to `main` (its own integration/default branch — the *analog* of this repo's
  `staging`, not this repo's `main`, which plays a different, production role), on
  `release: published`, on PRs (build-only, path-filtered), and on
  `workflow_dispatch`; tags via `docker/metadata-action`
  (`type=ref,event=branch`/`type=semver`/`type=sha,format=long`/`type=raw,value=latest,
  enable={{is_default_branch}}`); auth via `secrets.GITHUB_TOKEN` + `packages: write`
  (no separate PAT).

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
- The image is published to GHCR with the same tag scheme (`sha-<short>` + mutable
  branch tag) every other Bloom-published image uses, so Phase 2 can pin an immutable
  reference the same way the predict/traits containers are pinned.
- `bloomcli/CHANGELOG.md` and `bloomcli/pyproject.toml` accurately reflect everything
  that's shipped, ready for a real GitHub Release.
- CI shape (Dockerfile + workflow) is fenced by pytest tests, matching this repo's
  existing pattern for workflows that can't be exercised end-to-end in PR CI.

### Non-Goals

- Wiring the image into `sleap-roots-pipeline`'s Argo DAG (Phase 2 — separate change).
- Solving production image promotion generally (pre-existing `image-publishing`
  deferral; not reopened here).
- Building a non-interactive credential story for bloomctl-in-a-container (`bloom
  #398`/`#17` — separate, unstarted).
- Actually publishing the GitHub Release / triggering the real PyPI upload — prepared,
  not executed, by this change.

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
`uv sync --frozen --no-dev --no-cache`), but **omit the `apt-get` block entirely**.

**Why:** `bloommcp`'s `apt-get` installs `libfreetype6-dev`/`libpng-dev`/`libjpeg-dev`/
`pkg-config`/`gcc` for matplotlib/scipy native builds. `bloomctl`'s dependency set
(`click`, `rich`, `httpx`, `supabase`, `python-dotenv`, `cryptography>=48.0.1`,
`sleap-roots-contracts`) has no such need — `cryptography` ships manylinux wheels for
the target platforms, and nothing else in the tree touches native code.

### Decision 3: GHCR workflow shape — dedicated `docker-build-bloomcli.yml`, staging-triggered, no `main` trigger

**Chosen:** a new, standalone workflow file, structurally similar to
`sleap-roots-predict`'s `docker-build.yml` (per-PR path filter, `docker/metadata-action`
+ `docker/build-push-action`, `GITHUB_TOKEN` auth), but with **this repo's own**
trigger/tag scheme substituted in place of `sleap-roots-predict`'s:

| | `sleap-roots-predict` | this change |
|---|---|---|
| Integration-branch trigger | push to `main` (its default branch) | push to `staging` |
| Production-branch trigger | *(single-branch model — n/a)* | **none** (matches `image-publishing`) |
| Tags | `type=ref,event=branch` + semver + `sha,format=long` + `latest`-on-default-branch | `sha-<short>` + `staging` (mutable) + semver-on-release |
| Auth | `GITHUB_TOKEN` + `packages: write` | same |

**Why not fold into `deploy.yml`'s `build-images` job:** that job's entire purpose is
feeding `docker-compose.prod.yml`'s `pull`/`up` steps for the three services actually
deployed via compose. `bloomctl` is not a compose-deployed service — its consumer is
`sleap-roots-pipeline`'s Argo DAG, a different system entirely. Folding it in would
conflate two unrelated consumers in one workflow and couple bloomctl's build cadence to
the compose-deploy pipeline's for no reason.

**Why not mirror `sleap-roots-predict`'s branch triggers literally:** its `main` is that
repo's single integration branch (analogous to *this* repo's `staging`), not this
repo's `main` (a separate, slower-moving production/consolidation branch, 492+ commits
behind `staging` at design time). This repo's own `image-publishing` capability already
establishes the precedent that custom images build only on push to `staging`; production
deploys reuse a specific already-built `sha-<short>` tag rather than triggering a fresh
build on `main`. Mirroring that (rather than `sleap-roots-predict`'s branch names
literally) keeps `bloomctl`'s publishing behavior consistent with its sibling images in
*this* repo, which matters more here than cross-repo consistency with a different
program's single-branch model.

**Tag scheme detail:** `sha-<short-git-sha>` + mutable `staging` tag pushed on every
`staging` push (matching the `Image Tag Scheme` requirement other Bloom images already
follow); additionally, on `release: published`, the image is rebuilt and pushed with the
release's semver tag (e.g. `0.1.0a2`) so a specific PyPI-published version has a
matching, discoverable image tag. Nothing currently auto-pulls the mutable `staging`
tag for bloomctl (unlike the compose-deployed services) — it's pushed anyway for
consistency and discoverability at negligible cost; Phase 2's Argo WorkflowTemplate will
pin an explicit immutable tag (`sha-<short>` or the semver tag), a decision that belongs
to Phase 2's own design, not this one.

### Decision 4: Version bump lands in this PR directly; cutting the Release does not

**Chosen:** `bloomcli/pyproject.toml`'s version and `bloomcli/CHANGELOG.md`'s
`[Unreleased]`→`[0.1.0a2]` move are edited directly on this change's branch, rather than
via the separate `version-bloomcli.yml` dispatch-and-PR flow.

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

### Decision 5: Testing — pytest workflow/Dockerfile-shape tests, manual smoke test (not a CI gate)

**Chosen:** following `tests/unit/test_release_bloomcli_workflow_shape.py`'s existing
pattern, two new pytest files assert the new Dockerfile's and workflow's static shape
(trigger set, tag derivation, push-gating on event type, base image, absence of apt
packages, non-root user, `ENTRYPOINT` form). A manual `docker build` + `docker run
<image> --version` is a `tasks.md` item for the implementer to perform and report on,
not a CI gate — this repo has no existing precedent for *running* a built image inside
CI (only building it, plus Trivy static scanning), and adding that precedent is out of
scope here.

## Risks / Trade-offs

| Risk | Mitigation |
| --- | --- |
| `bloomcli/CHANGELOG.md`'s `[Unreleased]` section is stale/incomplete relative to what's actually merged | `tasks.md` §0 cross-checks it against #397/#408, #458, #508, #433 before bumping the version — corrected first if any gap is found. |
| New workflow's push/release-triggered paths can't be exercised by PR CI | Matches the exact justification already accepted for `release-bloomcli.yml`/`version-bloomcli.yml`: pytest shape tests are the only pre-merge gate; this is consistent with existing practice, not a new gap. |
| Image builds but no PyPI release is ever cut (automation stops at "prepared") | Explicit, intentional: cutting a real PyPI release is irreversible and stays a human action. `tasks.md` documents the exact manual steps so nothing is lost between "prepared" and "done." |
| Bloomctl's dependency set changes later and needs native build deps after all | `cryptography`'s manylinux wheel coverage is a current fact, not a permanent guarantee; if a future dependency needs native compilation, the Dockerfile gains an `apt-get` block then, the same way `bloommcp`'s did. |
| Confusion between this change's `staging`-triggered image builds and the unrelated, already-merged `image-publishing` capability | `design.md` (this file) explicitly cross-references and contrasts with it; `proposal.md` names it as prior art, not something this change modifies. |

## Open Questions

- **None blocking.** The one open question from the original handoff — whether to
  install from PyPI vs. git source vs. monorepo source in the container — is resolved
  by Decision 1 (monorepo source, decoupled from the release).
