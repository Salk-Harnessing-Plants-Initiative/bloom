## ADDED Requirements

### Requirement: Custom Image Publishing in CI

CI MUST build container images for every Bloom custom service (`bloom-web`, `langchain-agent`, `bloommcp`) on every push to the `staging` branch and push them to `ghcr.io/salk-harnessing-plants-initiative/<service>`. Custom service images MUST NOT be built on the deploy host as part of a `staging` deploy.

#### Scenario: Push to staging triggers image builds

- **GIVEN** a commit is pushed to the `staging` branch
- **WHEN** the CI workflow runs
- **THEN** an image is built for each of `bloom-web`, `langchain-agent`, and `bloommcp` from the source at that commit

#### Scenario: Each custom image is built with its required context

- **WHEN** the `build-images` job runs
- **THEN** `bloom-web` is built with `context: .` (repo root) and `file: web/Dockerfile.bloom-web.prod` so workspace `packages/*` references resolve
- **AND** `langchain-agent` is built with `context: ./langchain` and `file: ./langchain/Dockerfile`
- **AND** `bloommcp` is built with `context: ./bloommcp` and `file: ./bloommcp/Dockerfile`

#### Scenario: Staging deploy never builds custom services

- **WHEN** the staging deploy job runs
- **THEN** it does not invoke `docker compose build` for `bloom-web`, `langchain-agent`, or `bloommcp`

#### Scenario: Image build failure aborts the staging deploy

- **GIVEN** a commit is pushed to `staging`
- **WHEN** any one of the three custom-service image builds fails
- **THEN** the deploy job does not run
- **AND** the failed build's error is surfaced in the workflow summary

### Requirement: Image Tag Scheme

Every custom-service image MUST be pushed with both an immutable `sha-<short-git-sha>` tag (where `<short-git-sha>` is the first 7 characters of `${{ github.sha }}`) and the mutable `staging` tag pointing at the same digest, so rollback has a content-addressable handle and staging deploys have a moving default.

#### Scenario: Both tags are pushed for every build

- **WHEN** a commit `abcd1234efgh` is built
- **THEN** `ghcr.io/salk-harnessing-plants-initiative/bloom-web:sha-abcd123` exists in GHCR
- **AND** `ghcr.io/salk-harnessing-plants-initiative/bloom-web:staging` resolves to the same digest

#### Scenario: SHA tags are immutable in workflow

- **WHEN** `tests/unit/test_deploy_workflow_ghcr_shape.py` parses the `build-images` job
- **THEN** no `docker/build-push-action` step passes `--force`, `force_push: true`, or any flag that would allow overwriting an existing immutable tag

### Requirement: Compose References Custom Services by GHCR Image

`docker-compose.prod.yml` MUST declare each Bloom custom service with an `image:` field of the form `ghcr.io/${IMAGE_NAMESPACE}/<service>:${IMAGE_TAG:-staging}`, never a `build:` field, so production and staging both pull from GHCR and the namespace is parameterizable.

#### Scenario: Compose file resolves the custom services to GHCR images

- **WHEN** `docker-compose.prod.yml` is parsed
- **THEN** the `bloom-web`, `langchain-agent`, and `bloommcp` services each have an `image:` value matching `^ghcr\.io/\${IMAGE_NAMESPACE}/[^:]+:\${IMAGE_TAG:-staging}$`
- **AND** none of those three services have a `build:` block

#### Scenario: IMAGE_TAG defaults to staging when unset (structural)

- **GIVEN** `IMAGE_TAG` is unset and `IMAGE_NAMESPACE=salk-harnessing-plants-initiative` is set
- **WHEN** `docker-compose.prod.yml` is parsed as text
- **THEN** each custom service's `image:` line matches the regex with `${IMAGE_TAG:-staging}`

#### Scenario: IMAGE_TAG selects a specific SHA on demand (behavioral)

- **GIVEN** `IMAGE_TAG=sha-abcd123` and `IMAGE_NAMESPACE=salk-harnessing-plants-initiative` are exported in the environment
- **WHEN** `docker compose -f docker-compose.prod.yml config` is invoked as a subprocess
- **THEN** the rendered YAML for each custom service contains `image: ghcr.io/salk-harnessing-plants-initiative/<service>:sha-abcd123`
- **AND** no service in the rendered YAML retains a `${IMAGE_TAG...}` placeholder

#### Scenario: IMAGE_TAG defaults to staging in behavioral test

- **GIVEN** `IMAGE_TAG` is explicitly unset and `IMAGE_NAMESPACE=salk-harnessing-plants-initiative` is set
- **WHEN** `docker compose -f docker-compose.prod.yml config` is invoked
- **THEN** the rendered YAML for each custom service contains `:staging` as the tag

#### Scenario: IMAGE_NAMESPACE defaults to the canonical org slug

- **GIVEN** `IMAGE_NAMESPACE=salk-harnessing-plants-initiative` is set in `.env.prod.defaults`
- **WHEN** `docker compose config` is invoked with that defaults file
- **THEN** the resolved image refs use `ghcr.io/salk-harnessing-plants-initiative/...`

### Requirement: Third-Party Images Pinned by Digest

Every `image:` reference in `docker-compose.prod.yml` that points at a third-party (non-custom-built) service MUST include an `@sha256:<digest>` suffix in addition to its human-readable tag, and CI MUST reject any change that introduces an unpinned third-party image.

#### Scenario: All third-party images are digest-pinned

- **WHEN** `docker-compose.prod.yml` is parsed
- **THEN** every `image:` value whose registry path is not `ghcr.io/${IMAGE_NAMESPACE}/*` matches the regex `^[^@]+:[^@]+@sha256:[a-f0-9]{64}$`

#### Scenario: Adding a new third-party image without a digest fails CI

- **GIVEN** a contributor adds a new `image: postgres:16` line (no digest) to `docker-compose.prod.yml` and opens a pull request
- **WHEN** the Python test `tests/unit/test_compose_thirdparty_pinned.py` runs in the `python-audit` CI job
- **THEN** the test fails and names the offending image in the failure message

### Requirement: PR CI Stack Builds Locally Via Overlay

PR CI MUST be able to bring up the full compose stack from PR branches that have no corresponding GHCR images, by using a `docker-compose.ci.yml` overlay that restores `build:` blocks for the three custom services. Production compose stays `image:`-only.

#### Scenario: CI overlay declares build for each custom service

- **WHEN** `docker-compose.ci.yml` is parsed
- **THEN** `bloom-web`, `langchain-agent`, and `bloommcp` each have a `build:` block
- **AND** the build context and dockerfile match the per-service paths from "Each custom image is built with its required context" above

#### Scenario: CI overlay covers every GHCR-referenced custom service

- **GIVEN** `docker-compose.prod.yml` lists N services with `image: ghcr.io/${IMAGE_NAMESPACE}/...`
- **WHEN** `tests/unit/test_compose_ci_overlay_parity.py` runs
- **THEN** for each of those N services, `docker-compose.ci.yml` declares a `build:` block

#### Scenario: pr-checks.yml uses both compose files via COMPOSE_FILES env

- **WHEN** the `compose-health-check` job in `.github/workflows/pr-checks.yml` invokes any `docker compose` command
- **THEN** that command uses the job-level `$COMPOSE_FILES` env var (set to `-f docker-compose.prod.yml -f docker-compose.ci.yml`) so the 6+ compose commands inside the job don't drift apart

### Requirement: Staging Deploy Pulls From GHCR Before Up

The staging deploy job in `.github/workflows/deploy.yml` MUST authenticate to GHCR and run `docker compose pull` before `docker compose up`, and MUST fail fast on pull or auth failure rather than fall through to an attempt to build locally.

#### Scenario: Staging deploy authenticates to GHCR first

- **WHEN** the staging deploy job runs
- **THEN** a `docker login ghcr.io` step runs successfully against the configured `GHCR_READ_TOKEN` credentials
- **AND** the login step runs before any `docker compose` command

#### Scenario: Staging deploy pulls before up

- **WHEN** the staging deploy job runs after login
- **THEN** a `docker compose -f docker-compose.prod.yml --env-file .env.staging pull` step runs and succeeds before the `docker compose ... up -d` step

#### Scenario: Up step omits --build

- **WHEN** the staging deploy job's `docker compose ... up -d` step is parsed
- **THEN** the command does not include `--build`
- **AND** the command does include `--remove-orphans --wait --wait-timeout 600`

#### Scenario: Pull failure aborts the deploy

- **GIVEN** the staging deploy step `docker compose pull` exits non-zero
- **WHEN** the workflow executes
- **THEN** the subsequent `docker compose up -d` step does not run
- **AND** the workflow summary identifies the pull failure

#### Scenario: GHCR auth failure aborts the deploy

- **GIVEN** the `GHCR_READ_TOKEN` secret is missing or revoked
- **WHEN** the staging deploy job runs
- **THEN** the `docker login ghcr.io` step fails with a non-zero exit code
- **AND** no subsequent compose command runs

### Requirement: Rollback Uses Prior SHA Tag Or Aborts

The rollback paths in `.github/workflows/deploy.yml` (both staging and production) MUST capture the previous `IMAGE_TAG` value alongside the previous git SHA, and on rollback MUST pull and bring up that prior `sha-<short>` image. If the prior `IMAGE_TAG` handle is missing, the rollback MUST abort with a clear "manual recovery required" error rather than fall back to the mutable `staging` tag (which would re-pull the image that just failed forward-deploy).

#### Scenario: Staging forward deploy captures previous IMAGE_TAG

- **WHEN** the staging deploy job's "Save previous SHA" step (around `deploy.yml:575`) executes successfully
- **THEN** the value of `IMAGE_TAG` used by the previous successful deploy is written to `${STAGING_DEPLOY_PATH}.state/previous_image_tag` next to the existing `.state/previous_sha` write at line 580

#### Scenario: Production forward deploy captures previous IMAGE_TAG

- **WHEN** the production deploy job's "Save previous SHA" step (around `deploy.yml:152`) executes successfully
- **THEN** `IMAGE_TAG` is written to `${PROD_DEPLOY_PATH}.state/previous_image_tag` next to the existing `.state/previous_sha` write at line 157, symmetric with the staging side

#### Scenario: Rollback restores the previous image tag

- **GIVEN** a forward deploy has failed and the rollback branch executes
- **AND** `${PROD_DEPLOY_PATH}.state/previous_image_tag` exists and is non-empty
- **WHEN** the rollback step runs
- **THEN** `IMAGE_TAG` is exported from the file's contents
- **AND** `docker compose pull` is invoked with that `IMAGE_TAG`
- **AND** `docker compose up -d --remove-orphans --wait --wait-timeout 300` runs without `--build`

#### Scenario: Missing previous_image_tag aborts rollback

- **GIVEN** `${PROD_DEPLOY_PATH}.state/previous_image_tag` does not exist or is empty
- **WHEN** the rollback step runs
- **THEN** the rollback step exits non-zero with the GitHub Actions error annotation `::error::No previous_image_tag captured — manual recovery required (do NOT auto-fallback to staging)`
- **AND** no `docker compose pull` or `up -d` is attempted
- **AND** the workflow summary instructs the operator to choose a SHA manually (via `git log` or GHCR's package UI) and re-run with `workflow_dispatch` setting `IMAGE_TAG=sha-<chosen>`

### Requirement: Manual Recovery via workflow_dispatch.inputs.image_tag

The `deploy.yml` workflow MUST declare an `image_tag` input on `workflow_dispatch` so the abort-on-missing-handle rollback flow has a concrete manual-recovery path. When the input is set, it MUST override the freshly-built `IMAGE_TAG`; when unset, the workflow MUST use `build-images.outputs.image_tag` as before.

#### Scenario: workflow_dispatch declares image_tag input

- **WHEN** `.github/workflows/deploy.yml`'s `on.workflow_dispatch.inputs` block is parsed
- **THEN** it declares an `image_tag` input with `required: false` and a description naming "manual rollback when previous_image_tag is missing" as the use case

#### Scenario: Explicit image_tag override wins over build-images output

- **GIVEN** an operator runs `gh workflow run deploy.yml -f environment=staging -f image_tag=sha-abcd123`
- **WHEN** the deploy job's env block resolves `IMAGE_TAG`
- **THEN** `IMAGE_TAG` is `sha-abcd123`
- **AND** the freshly-built `build-images.outputs.image_tag` is ignored

#### Scenario: Blank image_tag falls through to build-images output

- **GIVEN** an operator runs `gh workflow run deploy.yml -f environment=staging` without `-f image_tag=...`
- **WHEN** the deploy job's env block resolves `IMAGE_TAG`
- **THEN** `IMAGE_TAG` is `${{ needs.build-images.outputs.image_tag }}` (the freshly-built tag)

### Requirement: BLOOM_IMAGE_SHA Available to Custom Services

`docker-compose.prod.yml` MUST inject the resolved `IMAGE_TAG` into custom services as `BLOOM_IMAGE_SHA`, so per-service code can read it. *(Per-result stamping in scientific outputs is deferred to a follow-up issue; this requirement covers only the env-injection plumbing.)*

#### Scenario: Compose passes BLOOM_IMAGE_SHA to every custom service

- **WHEN** `docker-compose.prod.yml` is parsed
- **THEN** the `bloom-web` service's `environment:` block includes `BLOOM_IMAGE_SHA: ${IMAGE_TAG:-staging}`
- **AND** the `langchain-agent` service's `environment:` block includes `BLOOM_IMAGE_SHA: ${IMAGE_TAG:-staging}`
- **AND** the `bloommcp` service's `environment:` block includes `BLOOM_IMAGE_SHA: ${IMAGE_TAG:-staging}`

#### Scenario: BLOOM_IMAGE_SHA resolves to the deployed tag at runtime

- **GIVEN** `IMAGE_TAG=sha-abcd123` is exported
- **WHEN** `docker compose -f docker-compose.prod.yml config` is invoked
- **THEN** the rendered YAML shows `BLOOM_IMAGE_SHA: sha-abcd123` in each custom service's `environment:` block
