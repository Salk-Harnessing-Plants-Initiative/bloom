## Why

`bloomctl`'s write-back capability is now fully complete in source — the ingest CLI
(bloom #397/#408), the scoped credential's RPC grant (#470), and blob upload
(#407/#508) have all merged. But bloomctl has never actually shipped:

- `bloomcli/pyproject.toml` still declares `version = "0.1.0a1"`, the same version
  published to PyPI at `[0.1.0a1] - 2026-06-30`. Every feature added since —
  `cyl download-for-predict` (#411/#458), `cyl ingest-result` (#397/#408), blob upload
  (#407/#508), and the `cyl` command-group reorg (#433) — sits undocumented-as-released
  in `bloomcli/CHANGELOG.md`'s `[Unreleased]` section.
- There is no `Dockerfile` anywhere under `bloomcli/`, and no CI workflow publishes a
  bloomctl container image to GHCR. `bloomctl` is PyPI-only today.

This blocks the next tier of the sleap-roots ↔ Bloom integration program: A4's per-scan
Argo DAG (in `sleap-roots-pipeline`) needs a runnable `bloomctl` container image to add
a write-back step calling `bloomctl cyl ingest-result`. That DAG-wiring work is a
separate change in a separate repo and is **out of scope here** — this change only
unblocks it by making the image and the release exist.

**Note on precedent (corrected after adversarial review):** `bloomcli/Dockerfile`
mirrors `bloommcp/Dockerfile`'s shape — a real, working in-repo precedent. The GHCR
*publishing workflow*, however, has **no working precedent anywhere in this repo yet**:
the `add-ghcr-image-publishing` change (PR #268) only shipped its own "Foundation"
phase, not the actual GHCR build/push cutover — `deploy.yml` has no GHCR job today, and
`docker-compose.prod.yml`'s custom services still build locally. This change is
therefore the **first** workflow in this repo to actually push an image to GHCR; it
adopts the tag/namespace scheme `image-publishing` *specifies* (for future consistency
once that change eventually lands), not a scheme already proven to work here. See
`design.md`'s Context section and Decision 3 for the full correction and the resulting
risk posture.

## What Changes

- **New `bloomcli/Dockerfile` + `bloomcli/.dockerignore`** — `python:3.11-slim`
  (digest-pinned) + digest-pinned `uv` binary, non-root `bloom` user, **no apt
  packages** (bloomctl's dependencies — `click`, `rich`, `httpx`, `supabase`,
  `python-dotenv`, `cryptography`, `sleap-roots-contracts` — are all pure-Python /
  prebuilt-wheel; unlike `bloommcp`, nothing here needs a native build toolchain).
  Two-layer `uv sync --frozen --no-dev --no-cache` (deps layer cached independently of
  source changes), then `ENTRYPOINT ["bloomctl"]` (exec form; this is an
  invoked-per-DAG-step CLI image, not a long-running service — no
  `EXPOSE`/`HEALTHCHECK`). Built **from monorepo source** (`context: ./bloomcli`),
  matching `bloom-web`/`langchain-agent`/`bloommcp` — this decouples the image's
  freshness from PyPI-release timing.
- **`pr-checks.yml`'s existing `docker-build` job gains `bloomcli` as a sixth image** —
  build-and-Trivy-scan-without-push, exactly like the five existing custom-service
  images. This gives the new Dockerfile real pre-merge validation and CVE scanning using
  an already-proven job, instead of inventing a parallel PR-time path.
- **New `.github/workflows/docker-build-bloomcli.yml`** — a dedicated, **push-only**
  workflow (no `pull_request` trigger — PR-time validation is `pr-checks.yml`'s job,
  above). Path-filtered to `bloomcli/**`. Triggers: push to `staging` (builds + pushes
  `sha-<short>` + mutable `staging` tags), `release: published` (also pushes a version
  tag derived by stripping the release tag's `bloomctl-v`/`v` prefix — the same logic
  `release-bloomcli.yml` already uses — fed to `docker/metadata-action` as `type=raw`,
  **not** `type=semver`, since bloomctl's PEP 440 versions aren't strict semver), and
  `workflow_dispatch` (`sha-<short>` only). **No trigger on push to `main`** — this
  repo's `main` is a slower-moving production/consolidation branch, not an integration
  branch; nothing currently builds custom images on push to `main`. Namespace:
  `ghcr.io/salk-harnessing-plants-initiative/bloomctl`. Auth via `secrets.GITHUB_TOKEN`
  + `packages: write` (standard GHCR-push mechanism; no Bloom-specific PAT precedent
  exists to lean on, since nothing has pushed to this namespace before — see
  `design.md` Decision 3 for the documented fallback if this 403s).
- **Real release — version 0.1.0a2** — verify `bloomcli/CHANGELOG.md`'s `[Unreleased]`
  section is accurate against #397/#408, #411/#458, #407/#508, #433 (correcting it if
  not), bump `bloomcli/pyproject.toml`'s version directly in this branch (not via the
  separate `version-bloomcli.yml` dispatch-and-PR flow, to keep proposal + implementation
  in one PR per this project's convention), regenerate `bloomcli/uv.lock` (it
  self-references the package's own version), and move the changelog entry to
  `## [0.1.0a2] - 2026-07-23`. **Cutting the actual GitHub Release** (which fires
  `release-bloomcli.yml` and publishes to real PyPI) is a distinct, irreversible,
  externally-visible action — this change prepares everything needed for it but the
  Release itself is cut by the user after merge, not automated here.
- **Close the pre-existing `bloomcli` CI-audit gap** — add `bloomcli` to
  `.pre-commit-config.yaml`'s `uv-lock-check` hook, `scripts/check-uv-locks.py`'s
  `SERVICES` tuple, a new "Audit bloomcli dependencies" step in `pr-checks.yml`'s
  `python-audit` job, and `.claude/commands/pre-merge.md`'s per-service loops — this is
  the first change to give `bloomcli` real Dockerfile/CI packaging, and the gap directly
  causes the `uv.lock`-drift risk above if left unfixed.
- **New tests** — `tests/unit/test_bloomcli_dockerfile_shape.py` and
  `tests/unit/test_docker_build_bloomcli_workflow_shape.py` (pytest-based, matching
  `tests/unit/test_release_bloomcli_workflow_shape.py`'s existing pattern, since the new
  workflow's push-triggered paths can't be exercised by PR CI either), plus a new
  **permanent** regression guard `bloomcli/tests/test_changelog_version_sync.py`
  asserting `pyproject.toml`'s version has a matching changelog heading — the same check
  `release-bloomcli.yml` performs at release time, now also enforced on every PR.

## Impact

- **Affected specs:** NEW capability `bloomcli-packaging` — covers the Dockerfile, its
  GHCR publishing workflow, and PyPI release-readiness for `bloomctl`. Kept separate
  from `image-publishing` (different services, different trigger semantics, no PyPI
  analog there — see `design.md`'s capability-boundary note).
- **Affected code:**
  - New `bloomcli/Dockerfile`, `bloomcli/.dockerignore`.
  - New `.github/workflows/docker-build-bloomcli.yml`.
  - `.github/workflows/pr-checks.yml` — `docker-build` job gains a sixth (`bloomcli`)
    image; `python-audit` job gains a `bloomcli` `pip-audit` step.
  - `.pre-commit-config.yaml` — `uv-lock-check` hook's `files:` regex gains `bloomcli`.
  - `scripts/check-uv-locks.py` — `SERVICES` tuple gains `bloomcli`.
  - `.claude/commands/pre-merge.md` — per-service audit/build loops gain `bloomcli`.
  - `bloomcli/pyproject.toml` — version bump `0.1.0a1` → `0.1.0a2`.
  - `bloomcli/uv.lock` — regenerated to match the version bump.
  - `bloomcli/CHANGELOG.md` — `[Unreleased]` → `[0.1.0a2] - 2026-07-23`.
  - `bloomcli/README.md` — new "Container image" section.
  - `bloomcli/RELEASE_PROCESS.md` — one-line addition noting a published Release also
    triggers `docker-build-bloomcli.yml`.
  - New `tests/unit/test_bloomcli_dockerfile_shape.py`,
    `tests/unit/test_docker_build_bloomcli_workflow_shape.py`,
    `bloomcli/tests/test_changelog_version_sync.py`.
- **Out of scope (explicitly deferred, tracked elsewhere or not yet tracked):**
  - Wiring the built image into `sleap-roots-pipeline`'s Argo DAG (Phase 2 — separate
    change, separate repo, depends on this change's image existing).
  - Landing `add-ghcr-image-publishing`'s own deferred PR-3 (compose-services GHCR
    cutover) — a separate, pre-existing, unstarted piece of work this change does not
    depend on or perform.
  - Production tag-pin selection / staging→production image promotion (`image-publishing`
    capability, Week 3 of bloom #107 — pre-existing deferral, not this change's job).
  - Image retention/GC policy.
  - `bloom #17` (Supabase scoped credential) / `bloom #398` (non-interactive
    `bloomctl auth` CLI wiring) and the `cyl_pipeline_runs`/`cyl_pipeline_run_scans`
    queue tables — unrelated concerns for a later change.
  - Actually cutting the GitHub Release / triggering the real PyPI publish — prepared by
    this change, executed by the user afterward.
