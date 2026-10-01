# Bloom Workflows API

A small FastAPI service intended to host workflow-related HTTP endpoints.

## On-demand vs queued generation

This service's video endpoint is the **on-demand** path: a user action (e.g. a
"generate video" button, or a CLI call) triggers `POST …/scans/{id}/video`, the
video is generated **synchronously in the request**, and the signed URL is
returned right away. Best for a single scan a user is looking at now.

**Batch / workflow-driven** generation (cyl scan, graviscan, or any pipeline
producing many videos) is a separate, **job-queue-based** mechanism (submit a
job → a worker processes it → poll/subscribe for the result), not this route.
That async path is intentionally kept out of this endpoint. `services/video-worker`
and its `video_jobs` queue were the first attempt at it and are retired, commented
out in place.

This service also hosts a **third**, distinct dispatch path: `POST /pipeline`
(the A4 sleap-roots pipeline trigger, bloom #11/#404 — see below) enumerates
scans and enqueues work via a new `pgmq`-backed queue (`cyl_pipeline_dispatch`),
not the `video_jobs`/`pg_notify` mechanism above. Phase 1 enumerates/enqueues;
Phase 2 (`dispatch_worker.py`, a separate process — see "Pipeline dispatch
worker" below) claims each enqueued batch and submits it to Argo as a
Kubernetes `Workflow` CRD, via the K8s API directly (not the `argo` CLI, not
the in-cluster-only Argo Server). Phase 3 (`status_poller.py`, another
separate process — see "Pipeline status poller" below, plus the read-only
`GET /runs/{id}` route) periodically checks each submitted batch's real Argo
outcome and progresses the run past dispatch outcome to `running`/`complete`.

Run locally

```bash
cd services/workflows
uv sync
uv run uvicorn main:app --host 0.0.0.0 --port 5100 --reload
```

Or via the dev stack (runs as the `workflows` service):

```bash
docker compose -f docker-compose.dev.yml --env-file .env.dev up -d --build workflows
```

Interactive docs (Swagger) are auto-generated at http://localhost:5100/docs
Behind Caddy the application routes are served under `/workflows/*` (e.g.
`<domain>/workflows/cyl/experiments/{id}/scans/{id}/video`). `/health` is
internal-only and not exposed through the public proxy.

## Endpoints

| Method | Path                                                     | Auth                 | Purpose                                                                                                |
| ------ | -------------------------------------------------------- | -------------------- | ------------------------------------------------------------------------------------------------------ |
| GET    | `/health`                                                | none (internal-only) | Liveness — kept for the in-container probe; **not** exposed via the public proxy                       |
| POST   | `/cyl/experiments/{experiment_id}/scans/{scan_id}/video` | Supabase user JWT    | Generate a scan's video, upload to Storage                                                             |
| POST   | `/pipeline` (external: `/workflows/pipeline`)            | Supabase user JWT    | Trigger an A4 sleap-roots pipeline run for a scan/wave/experiment/explicit scan list                   |
| GET    | `/runs/{run_id}` (external: `/workflows/runs/{run_id}`)  | Supabase user JWT    | Read a pipeline run's current status + its scans — a plain DB read, does **not** itself query Argo/K8s |

### Video generation

A video is **per scan** — one cylinder scan has many frames (~72 rotation
images), and that set of frames is one video. An experiment has _many_ scans, so
the route takes **both** ids: `scan_id` identifies the video, and `experiment_id`
scopes it (the scan must belong to that experiment, else 404).

`POST /experiments/{experiment_id}/scans/{scan_id}/video` logs in as a dedicated
**least-privilege Supabase app user** (see below), then validates the scan
belongs to the experiment (`cyl_scans_extended`), reads the scan's images
(`cyl_images`, capped at 72), downloads each frame from the images bucket,
decimates, encodes H.264 with ffmpeg, uploads the MP4 to the videos bucket,
returns a signed download URL, and (if configured) inserts a record row.
Synchronous — mirrors `services/video-worker` but runs in the request.

```bash
# Request: experiment 123, scan 456 — requires the caller's Supabase user JWT
curl -X POST http://localhost:5100/cyl/experiments/123/scans/456/video \
  -H "Authorization: Bearer <supabase-user-jwt>" \
  -H "apikey: <anon-key>"

# Response:
# {"experiment_id": 123, "scan_id": 456, "frames": 72, "path": "cyl-videos/456.mp4", "download_url": "https://.../456.mp4?..."}
```

### Rendering one video from a container

`plate_video_worker.py` and `cyl_video_worker.py` render a single item through the
same code these routes call, from a container built on this image. Both sit behind a
compose profile, so `docker compose up -d` never starts them — and never builds them
either, which is why the commands below pass `--build`.

> **Dev only until the render queue lands.** These commands are not approved to run
> against staging or production before then — not even for an item nobody appears to be
> using. The per-plate and per-scan locks that keep two renders off one object key live
> in the service's own process and do not hold against a second container, so a container
> render alongside a click on the same item can leave the recorded frame count describing
> the other render's file, and the page then treats that video as current and never offers
> to remake it. Nobody can see the collision coming either: the plate page's progress
> endpoint reads a record held in the service's process, so it shows nothing while a
> container render runs, and a scientist seeing no progress is being invited to click.

```bash
# Dev — the only environment these are approved for until the queue lands.
docker compose -f docker-compose.dev.yml --env-file .env.dev \
  run --rm --no-deps --build plate-video-worker \
  python plate_video_worker.py render --experiment 1886 --plate Plate_19 --wave 13

docker compose -f docker-compose.dev.yml --env-file .env.dev \
  run --rm --no-deps --build cyl-video-worker \
  python cyl_video_worker.py render --experiment 1 --scan 456
```

Once the queue lands and the hold is lifted, the same commands take the stack's own
project name and env file — `-p bloom_v2_prod … --env-file .env.prod` from
`/data/bloom/production`, `-p bloom_v2_staging … --env-file .env.staging` from
`/data/bloom/staging`, both against `-f docker-compose.prod.yml`. The project name is
not optional there: the compose file pins the production name, so a staging run without
`-p` reaches the production stack. A render outlives an SSH session, so run it under
`tmux` or `nohup`.

Each flag earns its place. `--no-deps` because the stack is already up, and `run` would
otherwise start — and can recreate — Kong and Postgres. `--build` because a deploy never
builds a profiled service, so without it a render uses whatever image the first run
happened to bake. With the stack down, the render fails at its first request; bring the
stack up rather than retrying.

Use `--wave none` for a plate with no wave; omitting `--wave` means the same thing.

**Exit codes.** `0` rendered or kept, `1` failed — this service, storage or the database
did not answer, or something else holds the item, so a retry may help — and `3` refused,
meaning the renderer declined and retrying will not change that. `2` is argparse's own
usage error, which is why refusal is not 2. The word a command prints is derived from
the code it exits with, so the two always agree.

Both take the classification from the route, with one deliberate exception: a plate whose
stored key and identity disagree is answered 500 by the route, but it cannot come right on
a second attempt, so the command refuses it rather than inviting a retry.

One cylinder failure is raised after the object was uploaded — signing its URL is the last
step before the row is written. Only that one adds a line saying the video was stored and
its record was not updated.

While a container render runs, the plate page's progress endpoint shows nothing: it
reads a record held in the service's own process.

#### Where the frame ceilings come from

Every bound in `plate_encode.py` is derived from the scanner's own configuration,
not chosen as a round number. The scanner fixes its regions in millimetres and its
resolutions to a fixed set, so there are eighteen possible images, and the largest
is 519 MB decoded and 124 MB on the wire. Read that arithmetic before changing any
of those numbers: a ceiling below 124 MB refuses frames a scanner really produces,
and one above the decoded size stops bounding the render's memory.

### Pipeline trigger

`POST /pipeline` (bloom #11/#404, Phase 1 of 3 — see
`openspec/changes/archive/2026-08-03-add-cyl-pipeline-trigger/design.md` for the full phasing and
dedup-mechanism rationale) validates `{target_level, target_id | scan_ids,
params}`, enumerates the target's scans via `cyl_scans_extended`, computes an
**informational** dedup preview (`reused_count` — every scan is enqueued
regardless of this preview's outcome; the real skip-if-done decision is made
cluster-side), writes `cyl_pipeline_runs`/`cyl_pipeline_run_scans`, chunks the
scans into batches, and enqueues each batch via `enqueue_cyl_pipeline_batch`.
This route itself does not submit anything to Argo/Kubernetes — a separate
worker (`dispatch_worker.py`, Phase 2, see below) claims each enqueued batch
and submits it.

`params` is recorded on the run and hashed for the preview, but not yet applied: the cluster
resolves species/mode/age from each scan's own metadata (bloom #897). Send `{}` unless you are
testing the preview itself. The planned web UI (OpenSpec change `add-cyl-pipeline-ui`)
will call this route through a `POST /api/cyl/pipeline` proxy.

Large targets (bloom #901):

- **Id filters are batched.** Every `in.(…)` id filter (the `scan_ids` existence check, and the
  preview's `cyl_scan_traits` and `cyl_trait_sources` lookups) is split by rendered length under
  a 4000-character budget (`postgrest_batches.py`, copied from bloomctl's
  `bloomcli/src/bloomctl/_postgrest.py`), and the results are merged. An unsplit list of about
  1,340 small ids gets `414 URI Too Long` from the gateway (measured for PR #650). By the same
  8 KB request-line limit that is about 1,160 four-digit scan ids (an estimate: each costs 7 bytes
  once its comma is encoded as `%2C`).
- **The preview is skipped for `params: {}`.** The stored hash is written by traits, which
  requires the full resolved species/mode/age, so the hash of `{}` matches none of the sources the
  pipeline writes; the route returns `reused_count: 0` without querying.
- **Non-empty `params` still pay for the preview** (left open on #901). It reads every trait row
  of the requested scans' sources, about 1,035 per source, reducing each batch to distinct
  `(scan_id, source_id)` pairs as it arrives. Each batch is its own statement under the
  `statement_timeout` (8 s, set on `authenticator`; `bloom_workflows` sets none), so a large
  experiment can take many seconds, or fail with 500 before anything is written if one batch
  times out.

```bash
# Request: trigger every scan in experiment 123 — requires the caller's Supabase user JWT
curl -X POST http://localhost:5100/pipeline \
  -H "Authorization: Bearer <supabase-user-jwt>" \
  -H "apikey: <anon-key>" \
  -H "Content-Type: application/json" \
  -d '{"target_level": "experiment", "target_id": 123, "params": {}}'

# Response (reused_count is always 0 for params: {}; see bloom #584/#897/#901):
# {"pipeline_run_id": 42, "scan_count": 30, "reused_count": 0}
```

`pipeline_run_id` here is Bloom's integer `cyl_pipeline_runs.id`, the value the write-back
RPC stamps as `cyl_trait_sources.cyl_pipeline_run_id`. It is not the producer's text
`provenance.pipeline_run_id`.

### Cell Ranger trigger

Starts Cell Ranger runs of the scRNA pipeline in `argo/scrna/`, **one sample per run**. A sample is one 10x library: a first-level folder under `raw_reads/` in the scRNA workflows bucket (`bloomv2-workflows`), holding all its lanes and re-sequencing runs. Separate captures are separate runs. A reference is a first-level folder under `reference_genome/` that contains `reference.json`.

- `POST /scrna/cellranger/runs` takes `{"sample": ..., "reference": ...}`. The sample is also Cell Ranger's run id, so it must match `^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$`; the reference must match `^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$`; neither may contain `__` (422 otherwise). An optional `"metadata"` object (the dataset's species, name, accession and other details, at most 64 KB) is stored as given in `rnaseq_runs.metadata`, for loading the results later. An optional `"sra_runs"` list (1 to 9 distinct SRA run IDs matching `^[SED]RR[0-9]{6,10}$`, one lane each, in order) imports the sample from SRA under the new name `sample` (422 otherwise). It calls `request_scrna_cellranger_run`, which writes the run to `rnaseq_runs` (`workflow_type` `scrna-cellranger`, the names and any `sra_runs` in `params`) and one `rnaseq_dispatch` message in a single transaction, and returns 201. The function's refusals come back as 409 (a name already registered, or one still being imported from SRA) or 422 (a bad value), with its message.
- `GET /scrna/cellranger/runs/{run_id}` returns the run's `rnaseq_runs` row; a run of another workflow type is a 404.
- `GET /scrna/cellranger/runs/{run_id}/logs?step=<step>` returns the end of one step's log (`fetch-sra`, `stage-reference`, `stage`, `qc`, `count`, `preprocess`, `cluster`, `build-h5ad` or `cleanup`): the last 2,000 lines, trimmed to their last 1 MiB, of the `main` container of the pod the status poller recorded in `step_pods`, read from the Kubernetes API as `bloom-pipeline`. It answers `{run_id, step, pod, log, truncated}`; 422 for an unknown step, 404 for an unknown run or a step that hasn't started, 409 while the step's pod is waiting to run (queued, pulling its image, starting), and 410 once the pod is gone (its Workflow is removed 24 hours after the run finishes).

This service does not read the bucket. The pipeline checks that the reference and the FASTQs exist and that the FASTQs are named the Illumina way (`<prefix>_S1_L001_R1_001.fastq.gz`, R1 and R2 for every lane, any prefix), and fails the run with exit 3 (no reference), 4 (no FASTQs) or 7 (misnamed FASTQs) if not. The final `.h5ad` goes to `runs_output/<sample>__<reference>__<user id>/h5ad/`, so one sample can be counted against several references, and two users running the same pair get separate folders. This route does not submit anything to Argo; runs stay `queued` until a dispatch worker picks them up.

```bash
curl -X POST http://localhost:5100/scrna/cellranger/runs \
  -H "Authorization: Bearer <supabase-user-jwt>" \
  -H "Content-Type: application/json" \
  -d '{"sample": "tinygex", "reference": "tiny_ref"}'

# 201 {"run_id": 1, "sample": "tinygex", "reference": "tiny_ref", "run_key": "tinygex__tiny_ref__<user id>"}
```

### RNA-seq dispatch worker

`rnaseq_worker.py` runs as the always-on `rnaseq-worker` container and sends queued RNA-seq runs to Argo. Every type's runs are rows of `rnaseq_runs` with messages on the shared `rnaseq_dispatch` queue, so the worker uses the same three database functions for all of them (`claim_rnaseq_run`, `complete_rnaseq_run`, `fail_rnaseq_run`). Each type it handles is one entry in `rnaseq_workflows.py` saying how to build its Workflow body; Cell Ranger (`scrna-cellranger`) is the first.

Each pass, the worker claims the next queued run of any type, builds its Workflow with the entry for the run's `workflow_type`, submits it to the Kubernetes API as the `bloom-pipeline` account (the same credentials as `cyl-pipeline-worker`), and records the outcome:

- accepted: the run becomes `submitted` with the Workflow's name;
- refused because a Workflow with that name exists: an earlier attempt submitted it without recording it, so the run is recorded as `submitted` with that name;
- rejected for any other reason: the run becomes `failed` with "Argo Workflow submission failed" (the detail is only in this service's log);
- the K8s settings are missing: the run is left queued and comes back once they are fixed;
- the run's type has no entry in `rnaseq_workflows.py`: the run becomes `failed` with a message naming the type.

A Cell Ranger Workflow takes `sample` and `reference` from the run's `params` and runs the registered `cellranger-count-template`: `stage-reference`, then `sample-pipeline` with the run's `run_key` as its run id. A run with `sra_runs` also runs `fetch-sra` alongside `stage-reference`, given the run IDs comma-separated, and `sample-pipeline` waits for both, so its final `.h5ad` goes to `runs_output/<run_key>/h5ad/`. Its name is fixed per run (`scrna-cellranger-<environment>-<run id>-<hash of run_key>`), so the same run is never submitted twice. It carries the labels `workflow-type`, `rnaseq-run-id` (the `rnaseq_runs` id, the same label for every workflow type) and `environment`. It runs as `bloom-workflow` with the GHCR pull secret, and is deleted 24 hours after it finishes, so its step logs can still be read the next day (`WORKFLOWS_RNASEQ_TTL_SECONDS` overrides it; the sleap-roots `WORKFLOWS_K8S_TTL_SECONDS` doesn't apply). Tests compare the body with `argo/scrna/cellranger/cellranger-count-workflow.yaml` and the template's inputs, so a change to either shows up as a failing test.

The worker reads the same settings as `cyl-pipeline-worker` (`WORKFLOWS_WORKER_POLL_SECONDS`, `WORKFLOWS_DISPATCH_VT_SECONDS`, `WORKFLOWS_DISPATCH_MAX_READS`, `WORKFLOWS_K8S_*`) and adds none. Its compose environment equals `cyl-pipeline-worker`'s (a test enforces it), so it also receives `CYL_PIPELINE_TRIGGER_ENABLED` and `WORKFLOWS_K8S_PIPELINE_*`, but it ignores them: RNA-seq dispatch is not gated by that switch.


### RNA-seq status poller

`rnaseq_status_poller.py` runs as the always-on `rnaseq-status-poller` container and follows each `submitted` or `running` run in `rnaseq_runs` until it ends. Every `WORKFLOWS_STATUS_POLL_SECONDS` (default 15), it reads the run's Argo Workflow by the name the worker recorded (`k8s_client.get_workflow`, as `bloom-pipeline`), turns it into the run's status with the reader for its workflow type, and records it with `update_rnaseq_run_status`. That function only moves a run forward, never changes a finished run, and writes nothing for an unchanged report, so polling sends no Realtime update unless something changed. One poller runs per environment.

For Cell Ranger, the reader (`rnaseq_status.py`) works from the Workflow's `status.nodes`:

- **current step**: the step that is running, or the last one to start: `fetch-sra` (SRA imports only), `stage-reference`, `stage`, `qc`, `count`, `preprocess`, `cluster`, `build-h5ad` or `cleanup`;
- **step pods**: each started step's pod, named `<workflow>-<template>-<numeric end of the node id>`; for a retried step, the latest attempt;
- **outcome**: `succeeded`, or `skipped` when the stage step reports the results already exist, or `failed` with the failed step's exit code and a message: exit 3 "No reference at reference_genome/<reference>/", exit 4 "No FASTQs at raw_reads/<sample>/", exit 5 "Cell Ranger failed; its log is at /hpi/hpi_dev/users/bfernando/scrna/runs/<run_key>/logs/count.log" (the cluster's shared folder, kept after a failure), exit 6 for a sample name Cell Ranger can't use, exit 7 "The FASTQs in raw_reads/<sample>/ must be named like <name>_S1_L001_R1_001.fastq.gz, …", exits 13–15 from the analysis steps (too few cells, no count matrix, a part that doesn't fit), and "Step <step> failed (exit N)" otherwise. `fetch-sra` has its own messages for 6 and 7 and for 10 (a download or storage check failed), 11 (no 10x barcode or cDNA read) and 12 (the sample folder holds other FASTQs).

Once an SRA import's `fetch-sra` step has succeeded, and the run is recorded as running or succeeded, the poller registers the sample with `register_rnaseq_sample`, passing the step's `fastq-count` and `total-bytes` outputs. It does this once per run; the function is idempotent, so a restarted poller repeating it is harmless. A name that conflicts (23505) is logged once and not retried; other errors are retried on the next poll.

A Workflow that no longer exists fails its run with "The workflow was removed before its result was recorded". One run's error is logged and the sweep goes on; missing K8s settings stop the sweep until they are fixed. The reader is tested against real Workflows from `runai-busch-lab` (`tests/fixtures/argo/`).

### Pipeline dispatch worker

`dispatch_worker.py` (bloom #11/#404, Phase 2 of 3 — see
`openspec/changes/add-cyl-pipeline-dispatch/design.md`) is a standalone
process, not an HTTP route — deployed as its own `cyl-pipeline-worker`
container (same image as this service). It polls `claim_cyl_pipeline_batch`,
and for each claimed batch:

1. Constructs a `Workflow` CRD (`k8s_client.build_workflow_body`) by loading a
   vendored, CI-drift-checked copy of `sleap-roots-pipeline`'s canonical
   `sleap-roots-pipeline.yaml` (`vendored/sleap-roots-pipeline.yaml`, pin
   recorded in the sibling `SLEAP_ROOTS_PIPELINE_REF` — a CI job checks the copy
   against the _pinned commit_, which catches "the copy and the pin disagree",
   not "upstream has moved on"; see bloom #737) and applying exactly
   six overrides on top of it: the batch's own `scan-ids`; attribution
   labels — `submitted-by: bloom-pipeline`/`pipeline-run-id`/`batch-index`/
   `environment`, **merged** into the vendored file's own labels rather than
   replacing them (mandatory — raw K8s API submission gets none of Argo's
   automatic `creator` label); a `ttlStrategy` (the submitting identity has no
   `delete` RBAC, so Argo's own controller must clean up completed Workflows
   instead — this override is dispatch-only, deliberately never added to the
   shared file); `metadata.namespace`, forced to the configured
   `WORKFLOWS_K8S_NAMESPACE` (see below); and this environment's stage
   directories and credential (see "Each environment's own directories and
   credential" below). Everything else — the DAG (which
   references the five already-registered `WorkflowTemplate`s:
   `sleap-roots-images-downloader-template` → `sleap-roots-predictor-template`
   → `sleap-roots-trait-extractor-template` → `sleap-roots-write-back-template`
   → `sleap-roots-exit-gate-template`), the volume set, `spec.entrypoint`,
   `spec.serviceAccountName` — passes through from the vendored file unmodified.
2. POSTs it directly to the K8s API server
   (`{WORKFLOWS_K8S_API_URL}/apis/argoproj.io/v1alpha1/namespaces/{WORKFLOWS_K8S_NAMESPACE}/workflows`)
   with a Bearer token + CA cert — not the `argo` CLI, not the Argo Server.
3. Records the outcome via `complete_cyl_pipeline_batch` (success) or
   `fail_cyl_pipeline_batch` (failure — terminal for now, no automatic retry),
   or, for a refused batch, `fail_cyl_pipeline_batch` before any submission (see
   below).

**Each environment's own directories and credential (bloom#863).** Prod and
staging submit into the same namespace, so each sets its own stage root and
Supabase credential Secret in its `.env.*.defaults`
(`WORKFLOWS_K8S_PIPELINE_HOSTPATH_ROOT`, `WORKFLOWS_K8S_PIPELINE_SECRET_NAME`;
the committed values live there, not here). The three stage volumes become
`<root>/input`, `<root>/predictions` and `<root>/traits`, and
`bloom-credentials` mounts that Secret. Staging's values equal the vendored
file's own, so its Workflows are unchanged. Every run in one environment
shares that environment's directories, which skip-if-done depends on. The
vendored volume set is checked as a closed contract: anything but those three
hostPaths and that one Secret is a configuration error, so a volume added
upstream can't reach the cluster pointing at shared storage.

**Refusal.** Unless `CYL_PIPELINE_TRIGGER_ENABLED` is exactly `true` (the
same switch bloom-web reads, read here at start-up) and both values above are
present and valid, the worker fails every batch it claims at once, with "Pipeline
dispatch is turned off in this environment" or "Pipeline dispatch is not
configured in this environment", and submits nothing. Which variable, and why,
is logged at WARNING. Turning the switch off is not a pause: batches already
queued fail too, and the worker reads it only at start-up, so it must be
recreated (`docker compose up -d`) for a change to take effect. This covers
every batch on the queue, whether its run
came from the web trigger or a direct `POST /workflows/pipeline`; a manual
`argo submit` is outside it.

**Namespace is a single hardcoded value for v1** (`WORKFLOWS_K8S_NAMESPACE`,
default `runai-busch-lab`) — no `lab`/`project` column exists on any
scan/experiment/wave table to resolve a per-request namespace from. A known
v1 limitation, not a bug. This value now reaches the K8s API **twice**: as
the submission URL's namespace segment (as before), and — since bloom #737 —
also forced into the submitted object's own `metadata.namespace`, overwriting
whatever the vendored file hardcodes there. Both are always built from the
same `NAMESPACE` variable, so they can't disagree; the Kubernetes API rejects
a submission whose body namespace differs from the URL's.

**`bloom-pipeline` vs. `bloom-workflow` — two different ServiceAccounts, easy
to confuse by name.** `bloom-pipeline` (provisioned below) is the identity
this worker authenticates as to _submit_ Workflows to the K8s API.
`bloom-workflow` (`spec.serviceAccountName`, set inside the submitted object,
now loaded from the vendored file rather than hardcoded here) is the identity
each DAG step's own _pod_ runs as once Argo's controller picks it up, needed
so each step can report results back to Argo (`workflowtaskresults
create`/`patch`). This change doesn't alter either identity or its RBAC —
only how the value `"bloom-workflow"` reaches the submitted object.

```bash
cd services/workflows
uv run python dispatch_worker.py
```

### Pipeline status poller

`status_poller.py` (bloom #11, Phase 3 of 3 — see
`openspec/changes/add-cyl-pipeline-status-polling/`) is a standalone process,
distinct from `dispatch_worker.py` above — deployed as its own
`cyl-status-poller` container (same image). Where `dispatch_worker.py` reacts
to new pgmq messages, this poller runs on a fixed wall-clock cadence
(`WORKFLOWS_STATUS_POLL_SECONDS`, default 15s) regardless of dispatch
activity, sweeping every `cyl_pipeline_runs` row still `'submitted'`/
`'running'`/`'partial'` (a `'partial'` run may still have genuinely-dispatched
batches whose real Argo outcome hasn't been checked yet — it is not excluded
merely because Phase 2 already settled its dispatch outcome). For each such
run it fetches the real Argo phase of every distinct `argo_workflow_name`
among that run's scans (`k8s_client.get_workflow_status` — a read-only `GET`,
not the `create` `dispatch_worker.py` does), computes `done_count`/`failed_count`
from the same fetch (a plain count of that run's `cyl_pipeline_run_scans` rows
by `status` — `'written'`/`'reused'` vs. `'failed'`; see the `cyl-trait-writeback`
capability for what actually writes those per-scan values now), and writes the
status plus both counts via `update_cyl_pipeline_run_status`, progressing the
run to `'running'`/`'complete'` (or a real-outcome `'failed'`/`'partial'`) —
values `claim`/`complete`/`fail_cyl_pipeline_batch` (Phase 2) never reach, since
those only ever describe dispatch outcome. This write happens **every cycle a
candidate run reaches this point, whether or not its overall status has
changed** (`fix-cyl-pipeline-run-scan-status` removed an earlier same-value
skip for a reconfirmed `'running'` status): `done_count`/`failed_count` can
advance between cycles even while the run stays `'running'`, so skipping the
write on an unchanged status would freeze the UI's "N/M scans done" display at
whatever it read on the run's first `'running'` cycle. `update_cyl_pipeline_run_status`
remains cheap and idempotent, so writing every cycle is not a scaling concern
at this program's poll interval and run volume.

Before writing a run's status whenever the computed conclusion is anything
other than `'running'`, the poller also reconciles that run's leftover
`'queued'` scan rows: since a terminal status write drops the run from this
poller's candidate set for good, a scan still `'queued'` at that point can
only mean write-back never ran for it at all (its workflow failed before
reaching write-back, or the write-back container never started), and this is
the last chance to close it out. It does so via
`fail_cyl_pipeline_run_scans_without_result` (one call per distinct
`argo_workflow_name` with a leftover `'queued'` row), then re-deriving
`done_count`/`failed_count` from a fresh read of that run's scan rows before
the status write — not by incrementing the counts `_fetch_effective_phases`
already returned, since that snapshot was taken before this cycle's K8s
lookups and the reconciliation call itself even ran, and can go stale if a
scan's write-back genuinely resolved in that window. If the reconciliation
call itself fails, the status write is skipped entirely for that run this
cycle — it remains a candidate and is retried next cycle, the same isolation
already given to every other per-run failure — rather than writing a
terminal status while leaving those rows permanently unresolved. This
reconciliation is deliberately **not** gated on whether some other workflow
in the run is unresolved (404'd) this cycle: `get_workflow_status` returns
`None` only on a clean 404, which is normally a permanent condition (the
Workflow object no longer exists), not a transient one — a genuine transient
K8s failure raises `K8sStatusError` instead, an entirely separate path this
loop already isolates per-run. A prior attempt to add such a gate was
reverted after two review passes traced it letting an ordinary, expected
TTL-GC'd sibling workflow stall a run's reconciliation and status write
forever (see `openspec/changes/fix-cyl-pipeline-run-scan-status/design.md`'s
Decision 6 addendum 8). Like `update_cyl_pipeline_run_status` below, the
reconciliation RPC call also treats a `PGRST202` (function-signature-not-found)
response as an expected, transient condition during the brief window between
this deploy's app code going live and its migration actually applying —
logged quietly, without marking the poll cycle unclean.

The rollup rule that maps a run's per-workflow phases to one status is
specified normatively in the `cyl-pipeline-status-polling` OpenSpec capability
spec's "Rollup rule..." requirement — not restated here. See that change's
`design.md` for why the computation happens in Python rather than SQL (a
deliberate departure from Phase 2's own "aggregate in SQL" precedent).

### Reading a run's outcome: use the counts, not `status`

**If you are building a UI or any other consumer over `cyl_pipeline_runs`, read
this section first.** `status` is a _batch-level_ outcome. It answers "did the
Argo Workflows reach a terminal success phase", not "did every requested scan
produce a result", and the two diverged when the pipeline DAG gained its
terminal exit gate (`sleap-roots-pipeline#56`). Branch on
`done_count`/`failed_count`, or on the per-scan `cyl_pipeline_run_scans` rows.
Concretely:

- **`'complete'` does not imply `failed_count == 0`.** A producer that isolates
  some scans' failures and completes the rest exits `3`; the gate accepts that
  code, so the Workflow is `Succeeded` and the run is `'complete'` — with real
  failures in `failed_count`.
- **`'complete'` does not even imply that _any_ scan succeeded.** The exit code
  has no floor: one scan failing and every scan failing both exit `3`. A
  totally-failed batch therefore reads `'complete'` with `done_count = 0`. This
  is the case most likely to mislead a UI, because it is exactly what a shared
  mount being unavailable or a credential being revoked looks like.
- **`'failed'` does not imply nothing was written.** Each envelope's per-scan
  `'written'` update commits in its own transaction, so a write-back that
  ingested some scans and then exited non-zero leaves `done_count > 0` on a
  `'failed'` run. An automated consumer that re-dispatches on `'failed'` will
  re-dispatch work that already succeeded.
- **`'partial'` no longer means what its name suggests.** It no longer arises
  from partial failure _within_ a batch — only from terminal phases differing
  across a multi-batch run. Do not treat its absence as "nothing was partial".
- **The counts can be absent, not just zero.** When any of a run's workflows
  404s (normally because it was TTL-GC'd), the poller withholds a `'complete'`
  conclusion and skips the run's status write entirely rather than concluding
  from incomplete information. A GC'd workflow 404s permanently, so a run whose
  batches finished more than `WORKFLOWS_K8S_TTL_SECONDS` apart can sit at its
  previous status with the counts never updated. **Render that as "unknown",
  not as zero** — it is the one case where "read the counts" is not by itself
  sufficient advice.

A zero-scan run is set to `'complete'` at enumerate time by the trigger route
and never dispatched, so it never reaches the rollup at all.

```bash
cd services/workflows
uv run python status_poller.py
```

### Auth model — two independent layers

**Layer 1 — caller auth (who may call):** application routes require the
caller's **Supabase user JWT** (`Authorization: Bearer`). The service validates
it by delegating to Supabase (`GET /auth/v1/user`), so it **never needs
`JWT_SECRET`**. A coarse per-user rate limit (`429` when exceeded) is shared
across every application route in this service (the video-encode route and the
`/pipeline` trigger route both call the same `enforce_rate_limit`); it is
enforced per process, so the effective limit scales with workers/replicas
rather than being a hard global quota.
`/health` is internal-only and not publicly exposed.

**Layer 2 — service identity (what the server may touch):** the service holds
**no privileged credential** — it signs into Supabase as a dedicated app user
(`WORKFLOWS_SUPABASE_EMAIL` / `_PASSWORD`) flagged `is_workflows` in its
service-role-only `raw_app_meta_data`. On login, `custom_access_token_hook`
stamps the token's Postgres `role` claim to `bloom_workflows`, so **its grants
and storage policies are the boundary**. The app user needs only:

- `SELECT` on `cyl_scans_extended`, `cyl_images`
- read on the images bucket, write on the videos bucket
- column-level `INSERT(scan_id, path)` / `UPDATE(path)` on `cyl_scan_videos`
- `SELECT`/`INSERT` on `cyl_pipeline_runs`/`cyl_pipeline_run_scans` — no direct
  `UPDATE`, by design: `claim_cyl_pipeline_batch`/`complete_cyl_pipeline_batch`/
  `fail_cyl_pipeline_batch` (below) write `argo_workflow_name`/`status`/
  `attempts`/`error_message`/`submitted_at`/`completed_at` as `SECURITY
DEFINER`, under the function owner's privileges, so `bloom_workflows` itself
  never needs a table-level grant to get those columns written
- `SELECT (scan_id, source_id)` on `cyl_scan_traits`, `SELECT (id, metadata)` on
  `cyl_trait_sources` (the pipeline-trigger dedup preview's all-sources join),
  and `SELECT (id)`-only existence-check access on `cyl_waves`/`cyl_experiments`
- `SELECT` on `gravi_scans`, `gravi_images`, `gravi_scan_sessions` and
  `gravi_plate_videos`, and read on the graviscan-images bucket — the plate
  time-lapse frame set, the run it belongs to, and what a stored video covers
- `EXECUTE` on `enqueue_cyl_pipeline_batch`, `claim_cyl_pipeline_batch`,
  `complete_cyl_pipeline_batch`, `fail_cyl_pipeline_batch`,
  `update_cyl_pipeline_run_status`

Note this grant list is shared by **three processes** now: the `workflows` API
(this route), the separate `cyl-pipeline-worker` container
(`dispatch_worker.py`), and the separate `cyl-status-poller` container
(`status_poller.py`) — all three authenticate as the same `bloom_workflows` app
user. The first three grants are set up by the migration
`…_create_workflows_role.sql`; the pipeline-trigger ones by
`…_create_cyl_pipeline_runs.sql` (bloom #11/#404, Phase 1); the
claim/complete/fail functions by `…_add_cyl_pipeline_dispatch_functions.sql`
(Phase 2); `update_cyl_pipeline_run_status` by
`…_add_cyl_pipeline_run_status_polling.sql` (Phase 3); the gravi reads by
`…_workflows_read_gravi.sql`.

## Provisioning (per environment)

1. Create the Supabase auth user (Studio → Authentication, or the Auth Admin API) with an email + password. Studio is behind Kong's basic-auth, so the Studio route needs the `DASHBOARD` credential (the deploy secrets `PROD_/STAGING_DASHBOARD_USERNAME` and `_PASSWORD`); without it, use the Auth Admin API.
2. Flag it as the workflows identity in its **service-role-only** `raw_app_meta_data` — e.g. the Auth Admin API (`PUT /admin/users/{id}` with `app_metadata: { "is_workflows": true }`) or `UPDATE auth.users SET raw_app_meta_data = COALESCE(raw_app_meta_data, '{}'::jsonb) || '{"is_workflows": true}' WHERE email = '…';`. On login, `custom_access_token_hook` maps this flag to a `bloom_workflows` role claim, so the token is scoped to the migration's grants rather than broad `authenticated`. (Setting `auth.users.role` directly does **not** work — the hook overwrites the claim.)
3. Set the deploy secrets `PROD_/STAGING_WORKFLOWS_SUPABASE_EMAIL` and `_PASSWORD`.
4. For `cyl-pipeline-worker` (Phase 2): set the deploy secrets
   `PROD_/STAGING_WORKFLOWS_K8S_TOKEN`, `_CA_CERT`, `_API_URL` for the
   `bloom-pipeline` ServiceAccount. **`_CA_CERT` must be stored with literal
   `\n` escape sequences in place of real newlines** — this repo's
   secret-injection pipeline (`deploy.yml`'s heredoc → `.env.prod`/
   `.env.staging` → `scripts/validate_env.sh` → docker-compose `--env-file`)
   is line-oriented and cannot carry a genuinely multi-line value; a real PEM
   certificate's embedded newlines would break every one of those tools if
   stored raw. `k8s_client.py` un-escapes before constructing the TLS
   verification context.
5. `bloom-pipeline` needs, in the Workflows' namespace: `create`, `get` and `list` on
   Argo `workflows` (the dispatch workers and status pollers), and `get` on
   `pods/log` (the Cell Ranger step logs route in the `workflows` service). Check the
   last with `kubectl auth can-i get pods --subresource=log -n runai-busch-lab
   --as=system:serviceaccount:runai-busch-lab:bloom-pipeline`, which must print `yes`;
   without it every log request answers 502.
6. For the pipeline itself (bloom#863), before switching the environment on with
   `CYL_PIPELINE_TRIGGER_ENABLED=true`: a **separate** Supabase account for the
   cluster's stage-in and write-back (also `is_workflows`, but not the service's
   own user from steps 1–2), stored as a RunAI Generic secret (Credentials → Generic
   secret, Project-scoped to busch-lab; RunAI prefixes the name `genericsecret-`)
   holding `credentials.txt`, whose name is the environment's
   `WORKFLOWS_K8S_PIPELINE_SECRET_NAME`; and the three directories under its
   `WORKFLOWS_K8S_PIPELINE_HOSTPATH_ROOT` on the `/hpi/hpi_dev` NFS. Nothing creates
   either, and a missing one leaves the pods `Pending`, not `Failed`. Neither
   `bloom-pipeline` nor `argo-user` can read Secrets, so check the secret in the
   RunAI console.

## Configuration

| Env var                         | Default                 | Notes                                                                                                                                                                                                                                                                                                        |
| ------------------------------- | ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `WORKFLOWS_CORS_ORIGINS`        | `http://localhost:3000` | Comma-separated browser origins allowed (frontend)                                                                                                                                                                                                                                                           |
| `SUPABASE_URL`                  | –                       | Supabase gateway URL (login + caller-JWT validation)                                                                                                                                                                                                                                                         |
| `SUPABASE_ANON_KEY`             | –                       | Supabase anon key                                                                                                                                                                                                                                                                                            |
| `WORKFLOWS_SUPABASE_EMAIL`      | –                       | App user's email (least-privilege identity)                                                                                                                                                                                                                                                                  |
| `WORKFLOWS_SUPABASE_PASSWORD`   | –                       | App user's password                                                                                                                                                                                                                                                                                          |
| `WORKFLOWS_IMAGES_BUCKET`       | `images`                | Storage bucket to read frames from                                                                                                                                                                                                                                                                           |
| `WORKFLOWS_VIDEOS_BUCKET`       | `videos`                | Storage bucket to write the MP4 to                                                                                                                                                                                                                                                                           |
| `WORKFLOWS_VIDEO_TABLE`         | `cyl_scan_videos`       | Record table (`scan_id -> path`)                                                                                                                                                                                                                                                                             |
| `WORKFLOWS_RATE_LIMIT`          | `5`                     | Max requests per user per window, per process, shared across all application routes (429 over)                                                                                                                                                                                                               |
| `WORKFLOWS_RATE_WINDOW_SECONDS` | `60`                    | Rate-limit window                                                                                                                                                                                                                                                                                            |
| `WORKFLOWS_PUBLIC_SUPABASE_URL` | –                       | Public base that replaces the internal `SUPABASE_URL` host in signed URLs, so `download_url` works for outside callers (set to `NEXT_PUBLIC_SUPABASE_URL`). Unset → the internal URL is returned unchanged.                                                                                                  |
| `WORKFLOWS_K8S_TOKEN`           | –                       | `cyl-pipeline-worker` **and** `cyl-status-poller`. Bearer token for the `bloom-pipeline` ServiceAccount — a real credential, eagerly required (raises before any network call if missing)                                                                                                                    |
| `WORKFLOWS_K8S_CA_CERT`         | –                       | `cyl-pipeline-worker` **and** `cyl-status-poller`. PEM cluster CA, stored with literal `\n` escapes (see Provisioning above) — a real credential, eagerly required                                                                                                                                           |
| `WORKFLOWS_K8S_API_URL`         | –                       | `cyl-pipeline-worker` **and** `cyl-status-poller`. K8s API server base URL (`https://<host>:6443`) — a real credential, eagerly required                                                                                                                                                                     |
| `WORKFLOWS_K8S_NAMESPACE`       | `runai-busch-lab`       | `cyl-pipeline-worker` **and** `cyl-status-poller`. Single hardcoded namespace for v1 (not a credential — never eagerly required)                                                                                                                                                                             |
| `WORKFLOWS_K8S_TTL_SECONDS`     | `3600`                  | `cyl-pipeline-worker` only. `ttlStrategy.secondsAfterCompletion` on every submitted Workflow, since the submitting identity has no `delete` RBAC (not a credential — never eagerly required)                                                                                                                 |
| `WORKFLOWS_K8S_ENV_LABEL`       | `dev`                   | `cyl-pipeline-worker` only. `environment` label on every submitted Workflow — prod and staging share the `runai-busch-lab` namespace and both `run_id` sequences start at 1, so this is what disambiguates them for a future reconciliation sweep (not a credential — never eagerly required)                |
| `WORKFLOWS_K8S_PIPELINE_HOSTPATH_ROOT` | – | `cyl-pipeline-worker` (`rnaseq-worker` receives it and ignores it). This environment's stage root: the three stage volumes become `<root>/input`, `/predictions`, `/traits` (bloom#863). An absolute POSIX path; no default. Missing or invalid, every claimed batch fails "not configured" |
| `WORKFLOWS_K8S_PIPELINE_SECRET_NAME` | – | `cyl-pipeline-worker` (`rnaseq-worker` receives it and ignores it). The Kubernetes Secret `bloom-credentials` mounts — this environment's own Supabase pipeline credential (bloom#863). No default. Missing or invalid, every claimed batch fails "not configured" |
| `CYL_PIPELINE_TRIGGER_ENABLED` | – | `cyl-pipeline-worker` (and bloom-web; `rnaseq-worker` receives it and ignores it). On only for exactly `true`; otherwise every claimed batch fails "turned off" and nothing is submitted. Read at start-up |
| `WORKFLOWS_WORKER_POLL_SECONDS` | `5`                     | `cyl-pipeline-worker` only. Idle sleep between empty-queue polls, and the retry interval for the startup Supabase connection check                                                                                                                                                                           |
| `WORKFLOWS_STATUS_POLL_SECONDS` | `15`                    | `cyl-status-poller` only. Sleep between sweep cycles, and the retry interval for the startup Supabase connection check. Not wired into either compose file's `environment:` block, matching `WORKFLOWS_WORKER_POLL_SECONDS`'s own treatment — the code-side default governs every deployed environment today |
| `WORKFLOWS_DISPATCH_VT_SECONDS` | `60`                    | `cyl-pipeline-worker` only. pgmq visibility timeout passed to `claim_cyl_pipeline_batch` — how long a claimed batch stays hidden from other claimants before redelivery                                                                                                                                      |
| `WORKFLOWS_DISPATCH_MAX_READS`  | `5`                     | `cyl-pipeline-worker` only. Poison-message threshold passed to `claim_cyl_pipeline_batch` — a batch redelivered more than this many times is dead-lettered (marked failed) instead of claimed again                                                                                                          |

> `ffmpeg` must be present in the runtime image — the Dockerfile copies a digest-pinned static `ffmpeg` binary (avoids apt's ffmpeg pulling in vulnerable GPU/TLS libraries).
> Caller auth is delegated to Supabase (`/auth/v1/user`), so `JWT_SECRET` is **not** needed by this service.
