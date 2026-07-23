## Why

`bloomctl`'s write-back capability is now fully complete in source — the ingest CLI
(bloom #397/#408), the scoped credential's RPC grant (#470), and blob upload
(#407/#508) have all merged. But bloomctl has never actually shipped:

- `bloomcli/pyproject.toml` still declares `version = "0.1.0a1"`, the same version
  published to PyPI at `[0.1.0a1] - 2026-06-30`. Every feature added since —
  `cyl download-for-predict` (#411), `cyl ingest-result` (#397/#408), blob upload
  (#407/#508), and the `cyl` command-group reorg (#433) — sits undocumented-as-released
  in `bloomcli/CHANGELOG.md`'s `[Unreleased]` section.
- There is no `Dockerfile` anywhere under `bloomcli/`, and no CI workflow publishes a
  bloomctl container image to GHCR. `bloomctl` is PyPI-only today.

This blocks the next tier of the sleap-roots ↔ Bloom integration program: A4's per-scan
Argo DAG (in `sleap-roots-pipeline`) needs a runnable `bloomctl` container image to add
a write-back step calling `bloomctl cyl ingest-result`. That DAG-wiring work is a
separate change in a separate repo and is **out of scope here** — this change only
unblocks it by making the image and the release exist.

Every other Bloom service that ships a container (`bloom-web`, `langchain-agent`,
`bloommcp`, and cross-program precedent in `sleap-roots-predict`) follows the same
shape: a `Dockerfile` + a GHCR-publishing CI workflow + immutable `sha-<short>` tags.
`bloomctl` currently has none of these — this change brings it to parity.

## What Changes

- **New `bloomcli/Dockerfile`** — `python:3.11-slim` (digest-pinned) + digest-pinned
  `uv` binary, non-root `bloom` user, **no apt packages** (bloomctl's dependencies —
  `click`, `rich`, `httpx`, `supabase`, `python-dotenv`, `cryptography`,
  `sleap-roots-contracts` — are all pure-Python / prebuilt-wheel; unlike `bloommcp`,
  nothing here needs a native build toolchain). Two-layer `uv sync --frozen --no-dev
  --no-cache` (deps layer cached independently of source changes), then
  `ENTRYPOINT ["bloomctl"]` (exec form; this is an invoked-per-DAG-step CLI image, not
  a long-running service — no `EXPOSE`/`HEALTHCHECK`). Built **from monorepo source**
  (`context: ./bloomcli`), matching `bloom-web`/`langchain-agent`/`bloommcp` — this
  decouples the image's freshness from PyPI-release timing.
- **New `.github/workflows/docker-build-bloomcli.yml`** — a dedicated workflow (not
  folded into `deploy.yml`'s `build-images` job, which is scoped to the three
  `docker-compose.prod.yml`-deployed services; `bloomctl` isn't deployed via compose,
  it's consumed by a different system entirely — the Argo DAG). Path-filtered to
  `bloomcli/**`. Triggers: push to `staging` (builds + pushes), `release: published`
  (builds + pushes, adds a semver tag), `workflow_dispatch` (builds + pushes), and pull
  requests touching `bloomcli/**` (builds only, no push — validates the Dockerfile).
  **No trigger on push to `main`** — this repo's existing (merged, not-yet-archived)
  `image-publishing` capability only builds on push to `staging`; production reuses an
  already-built `sha-<short>` image via a manual `IMAGE_TAG` pin (the automatic
  staging→production promotion path is itself still deferred, per that capability's
  design.md). Tags: `sha-<short-git-sha>` (immutable) + `staging` (mutable) on staging
  pushes, plus the semver version tag on a published Release — mirroring the existing
  `image-publishing` capability's tag scheme for consistency across every Bloom-published
  image, even though nothing currently auto-pulls a moving tag for bloomctl (Phase 2's
  Argo DAG will pin an explicit immutable tag). Namespace:
  `ghcr.io/salk-harnessing-plants-initiative/bloomctl`. Auth via `secrets.GITHUB_TOKEN`
  + `packages: write` (no new PAT needed).
- **Real release — version 0.1.0a2** — verify `bloomcli/CHANGELOG.md`'s `[Unreleased]`
  section is accurate against #397/#408, #458, #508, #433 (correcting it if not), bump
  `bloomcli/pyproject.toml`'s version directly in this branch (not via the separate
  `version-bloomcli.yml` dispatch-and-PR flow, to keep proposal + implementation in one
  PR per this project's convention), and move the changelog entry to
  `## [0.1.0a2] - 2026-07-23`. **Cutting the actual GitHub Release** (which fires
  `release-bloomcli.yml` and publishes to real PyPI) is a distinct, irreversible,
  externally-visible action — this change prepares everything needed for it but the
  Release itself is cut by the user after merge, not automated here.
- **New tests** — `tests/unit/test_bloomcli_dockerfile_shape.py` and
  `tests/unit/test_docker_build_bloomcli_workflow_shape.py`, pytest-based (matching
  this repo's existing pattern of testing CI/Dockerfile shape via pytest rather than
  shell — see `tests/unit/test_release_bloomcli_workflow_shape.py`), since this new
  workflow's push-triggered paths can't be exercised by PR CI either.

## Impact

- **Affected specs:** NEW capability `bloomcli-packaging` — covers the Dockerfile, its
  GHCR publishing workflow, and PyPI release-readiness for `bloomctl`.
- **Affected code:**
  - New `bloomcli/Dockerfile`.
  - New `.github/workflows/docker-build-bloomcli.yml`.
  - `bloomcli/pyproject.toml` — version bump `0.1.0a1` → `0.1.0a2`.
  - `bloomcli/CHANGELOG.md` — `[Unreleased]` → `[0.1.0a2] - 2026-07-23`.
  - New `tests/unit/test_bloomcli_dockerfile_shape.py`,
    `tests/unit/test_docker_build_bloomcli_workflow_shape.py`.
- **Out of scope (explicitly deferred, tracked elsewhere or not yet tracked):**
  - Wiring the built image into `sleap-roots-pipeline`'s Argo DAG (Phase 2 — separate
    change, separate repo, depends on this change's image existing).
  - Production tag-pin selection / staging→production image promotion (`image-publishing`
    capability, Week 3 of bloom #107 — pre-existing deferral, not this change's job).
  - Image retention/GC policy.
  - `bloom #17` (Supabase scoped credential) / `bloom #398` (non-interactive
    `bloomctl auth` CLI wiring) and the `cyl_pipeline_runs`/`cyl_pipeline_run_scans`
    queue tables — unrelated concerns for a later change.
  - Actually cutting the GitHub Release / triggering the real PyPI publish — prepared by
    this change, executed by the user afterward.
