# Design: isolate-cyl-pipeline-environments

## Context

Prod and staging share one Argo namespace (`runai-busch-lab`) and one deploy file
(`docker-compose.prod.yml`, differentiated by `.env.*.defaults`). They also share three things that
must be per-environment: the Supabase credential mounted into the pipeline, the three stage
directories, and, through those directories, the `scan_<id>` keyspace.

`WORKFLOWS_K8S_ENV_LABEL` already differs per environment, but it is only a label
(`k8s_client.py:224-229`). The decisions below were made with the author on 2026-09-30, and revised
after `/review-openspec`.

## Goals / Non-Goals

**Goals**

- A Bloom-dispatched prod Workflow mounts prod's credential and writes only under prod's stage root.
  The same holds for staging.
- Within one environment, every run shares that environment's stage directories. Skip-if-done dedup
  depends on that (srp#37, srp#71).
- No batch claimed from the `cyl_pipeline_dispatch` queue reaches Argo from an environment that isn't
  switched on and fully configured.

**Non-Goals**

- Changing `scan_key`, bloomctl, predict, the trait extractor, contracts, SQL, or the upstream
  Workflow.
- A runtime marker that detects a misconfigured root (D5).
- Preventing submissions made outside Bloom. Any holder of the shared `bloom-pipeline` or `argo-user`
  identity can `argo submit` a Workflow that mounts any secret or path in the namespace (srp
  `docs/cluster-identities.md:39-41`).
- Argo concurrency limits (srp#98), run-id provenance (bloom#864), and RNA-seq dispatch
  (`rnaseq_worker.py` builds its own body and is not gated).

## Decisions

### D1. Bloom overrides the volumes at dispatch, not upstream

`build_workflow_body` rewrites the three `hostPath.path` values and `bloom-credentials.secret.secretName`
in the parsed body, the same pattern `metadata.namespace` already uses.

Alternatives considered:

- **Parameterise the paths upstream and re-vendor.**
  - This needs an upstream PR, plus an edit to upstream `scripts/check_manifests.py`
    (`origin/main:219-226`). That check rejects any hostPath whose `path` lacks the literal
    `/pipeline_orchestration_tests/a4_poc/`.
  - It's also unverified whether Argo substitutes `{{workflow.parameters.*}}` inside `spec.volumes`.
- **Put the environment in `scan_key`.**
  - `scan_key` is part of `compute_idempotency_key`'s payload (sleap-roots-contracts `identity.py`)
    and of predict's own identity key. Every existing key would change. That means a one-time GPU
    recompute, duplicate `cyl_trait_sources` rows, and new storage object paths.
  - It would also need changes in bloomctl, predict and the trait extractor. Both of the latter define
    `_SCAN_KEY_FORBIDDEN = frozenset('./\\:*?"<>|')`: `sleap_roots_predict/output_contract.py:50`
    and `trait_extractor/manifest.py:27`.
  - It still wouldn't isolate the credential.

Overriding at dispatch leaves the vendored file byte-identical to upstream at the pin, so
`check_vendored_workflow_drift.py` stays green. The cost is that, for Bloom runs, the vendored file's
literal paths and secret no longer describe what is submitted. The spec and
`services/workflows/README.md` carry the authoritative list. Upstream's header comment is handled as
a follow-up (tasks 5.4).

### D2. One root per environment; a closed volume set

`WORKFLOWS_K8S_PIPELINE_HOSTPATH_ROOT` holds one path. A module constant maps each stage volume to its
sub-directory:

| Volume | Path |
|---|---|
| `images-input-dir` | `<root>/input` |
| `predictions-output-dir` | `<root>/predictions` |
| `traits-output-dir` | `<root>/traits` |

These names match today's `a4_poc` layout, so staging's root is the `a4_poc` directory and its paths
are unchanged. One root, rather than three variables, means an environment can't end up with its
directories split across trees. That split would break the predictions→traits hand-off silently.

`spec.volumes` is treated as a **closed set over every volume type**. It must hold exactly four
entries with unique names:

- the three above, each a mapping with a `hostPath` key whose value has a `path`;
- `bloom-credentials`, a mapping with a `secret` key.

Any other volume, of any type (`nfs`, `persistentVolumeClaim`, `projected`, `emptyDir`…), raises
`K8sConfigError` before any network call. So does a missing, renamed, duplicated or mistyped one, and
so does a `spec.volumes` that isn't a list of mappings. This is the existing structural-drift class.
An upstream addition therefore can't pass through pointing at shared storage, and the check can't
crash with a raw `KeyError`/`TypeError`. Overrides are applied in place, by volume name, not by
index.

### D3. Validation is platform-independent

The root, the secret and the switch are resolved once at import by
`_resolve_pipeline_hostpath_root`, `_resolve_pipeline_secret_name` and
`_resolve_pipeline_dispatch_enabled`. Like `_resolve_ttl_seconds`, they never raise at import: an
invalid value resolves to `None` (or `False` for the switch). A sibling records **why** it is
invalid, so the refusal can log the reason.

`build_workflow_body` reads the module globals at call time, so tests monkeypatch them as they do
`NAMESPACE` today. Tests never `importlib.reload(k8s_client)`, because a reload would rebind the
exception classes that `dispatch_worker` and the pollers import by name.

**Root rules:**

- Use string operations on the `/`-split segments only, never `Path`, `PurePosixPath` or `os.path`. On Windows,
  `Path('/hpi/x').is_absolute()` is `False` (checked 2026-09-30), so the developer's machine and CI
  would disagree.
- Non-empty, absolute, not `/`, with no trailing `/` and no empty segment (`//`).
- No whitespace or control characters.
- No `.` or `..` segment, including a trailing one, checked on the string's `/`-split segments.

**Secret rule:** `re.fullmatch` of an RFC 1123 subdomain, at most 253 characters. `fullmatch`, not
`match` with `$`, because `$` accepts a trailing newline.

**Switch rule:** `True` only for exactly `"true"`, matching bloom-web's `isPipelineTriggerEnabled`.

There is no path-prefix allowlist (e.g. `/hpi/hpi_dev/`). Only the env-file author controls the
value, and the existence and identity of the directories is an operator precondition anyway (§6). A
pod whose `hostPath` or `Secret` is missing sits `Pending`, not `Failed` (srp
`docs/cluster-identities.md:155-156, 244-250`). Validation catches only the obvious malformations
before they become a hang.

### D4. Refusal fails the batch at once, with a curated message

`build_workflow_body` raises `K8sDispatchRefusedError(reason)` **first**, before reading the
vendored file:

- with `reason="off"` when the switch is off;
- with `reason="unconfigured"` when the root or the secret is unresolved.

The switch is checked first. The exception is a sibling of `K8sConfigError`, not a subclass of it or
of `K8sSubmissionError`.

`dispatch_worker.process_one` catches it in its own branch, ahead of `K8sConfigError`, and does the
following:

- **Logs at WARNING**, with the run and batch id, the variable name(s), and why each is invalid.
  This is logged at refusal time, not at import, because import-time logging runs before
  `basicConfig` and is lost.
- **Calls `fail_batch` with a fixed message mapped from `reason`.** It never uses `str(exc)`:
  - `"Pipeline dispatch is turned off in this environment"`
  - `"Pipeline dispatch is not configured in this environment"`
- **If the `fail_batch` RPC itself errors**, it logs and leaves the claim for redelivery, the same as
  the submission-failure path (`dispatch_worker.py:110-124`).

**Why fail at once, including for "unconfigured".** The existing rule leaves a `K8sConfigError`
unsettled "rather than permanently failing real scans over a deploy/ops mistake"
(`dispatch_worker.py:92-95`). That rule doesn't buy much recovery:

- An unsettled claim redelivers every `WORKFLOWS_DISPATCH_VT_SECONDS` (60).
- It is dead-lettered on the claim that finds `read_ct > p_max_reads` (5), the 6th, about five
  minutes in.
- The scans are then failed anyway, with "dead-lettered after 6 deliveries (poison message)".

So "unsettled" means the scans fail about five minutes later, with a misleading reason, unless the
env file is fixed and the worker recreated within that window. The author chose failing at once,
with an accurate reason, for both causes. The cost is that a broken env-file edit in a switched-on
environment permanently fails whatever it claims until the edit is fixed. Members re-trigger the run
after the fix.

**Unchanged:** missing cluster API credentials (`WORKFLOWS_K8S_TOKEN`/`_CA_CERT`/`_API_URL`, raised
later by `submit_workflow`) and vendored structural drift both keep the unsettled `K8sConfigError`
behaviour. The "Submission outcome is recorded" requirement is MODIFIED to name refusal as a third
outcome and to scope its unsettled rule to those cases.

**This replaces bloom#983.**

- Every batch on the queue is dispatched only by this worker. That covers batches enqueued through
  the web proxy and through a direct `POST /workflows/pipeline`.
- A `bloom_admin` insert into the run tables enqueues nothing, because there's no trigger and
  `enqueue_cyl_pipeline_batch` is executable only by `bloom_workflows` and `service_role` (checked
  on staging 2026-10-01: not by `anon`, `authenticated`, `bloom_user`, `bloom_writer` or
  `bloom_admin`). So it was never a dispatch path. A `service_role` enqueue still reaches Argo
  only through this worker.
- Unlike #983's option 1 as written ("runs would stay `queued`"), a refused run is created and then
  fails within one poll. The author accepted being refused after creation rather than at the HTTP
  layer.

### D5. The stage-in skip is isolated by construction, not by a new comparison

bloom#863's acceptance checklist asks that `scan_is_already_staged` "compares something that
distinguishes environments, not `scan_key` alone". With per-environment roots, stage-in only ever
reads its own environment's `<root>/input/scan_<id>/`. Prod's `scan_42` and staging's `scan_42` are
then different files, so no comparison could match across environments.

The remaining exposure is misconfiguration, for example prod's root pointed at staging's tree. It is
covered by tests:

- `test_env_disambiguating_values_differ` gains both keys;
- a new test asserts neither root contains the other, compared on path segments;
- the committed values are pinned.

A hand-edit of `.env.prod` on the deploy host, or an NFS symlink, bypasses those tests.

Two runtime alternatives were rejected:

- **A root marker file in bloomctl.** It needs a bloomctl release, an upstream image re-pin and an
  `argo template update` that affects both environments.
- **Comparing the sidecar's `image_ids` against the current environment's freshly fetched ids.** It
  needs a bloomctl change, and so the same release chain.

The author chose not to add either.

### D6. The worker reuses #965's switch

#965 (merged to `staging` as `618cbeb8`) added `CYL_PIPELINE_TRIGGER_ENABLED` to both defaults files
and to bloom-web. This change:

- passes the same key to `cyl-pipeline-worker`;
- rewrites its comment in the defaults files to name both readers, keeping one definition per file;
- extends #965's `test_pipeline_trigger_is_on_in_staging_and_off_in_prod`.

The semantics match bloom-web's (exactly `true`), but the timing doesn't. bloom-web reads per request;
the worker reads at start-up. Either way the container must be recreated, which
`docker compose up -d` does when its environment changes (`deploy.yml:319-320`, `:1253-1254`).

The compose parity test (`tests/unit/test_rnaseq_worker_container.py:43-52`) requires `rnaseq-worker`'s
environment to equal `cyl-pipeline-worker`'s. So `rnaseq-worker` also receives the three keys, which
it ignores. Its comment and `services/workflows/README.md:241` say so, so nobody assumes RNA-seq
dispatch is gated.

**Dev compose defaults:**

- the switch defaults to `true`, as #965's bloom-web default does;
- the root and secret default to blank, so a dev stack refuses with "not configured" unless both are
  set deliberately.

Dev's database also numbers scans independently, so defaulting dev to staging's tree would recreate
this bug.

## Risks / Trade-offs

- **The vendored file no longer describes what Bloom submits.** Mitigated by the spec, the README, and
  tests that pin the submitted paths and secret per environment.
- **Pending, not Failed, if prod is switched on before it is provisioned.** Validation can't prove
  existence. The mitigation is the §6 order: provision, verify existence, promote with the switch off,
  then flip. The flip is its own PR, so it can't ride in with code.
- **Host-side hand-edits and NFS symlinks bypass the tests (D5).** Accepted. §6 checks the real paths
  with `readlink -f`.
- **A prod write credential lands in a shared namespace.** Any `argo-user` or `bloom-pipeline` holder
  can mount any Secret there, as is already true of the staging credential. §6 gives the prod account
  only the grants the recipe lists, and records the exposure.
- **Prod is exposed until promotion.** Prod can dispatch with the staging secret today. This change
  protects it only once promoted. §6.0 asks the author whether to stop prod's `cyl-pipeline-worker`
  until then.
- **`argo template update` is shared across environments,** and so are the templates' image pins.
  §6.3 checks that prod's DB accepts what those templates emit before the flip.
- **Rollback after the flip.** Reverting this PR alone, once prod's switch is `true`, restores the
  original bug and re-enables bloom-web's run actions in prod. Set prod's switch back to `false` first,
  or in the same promotion, and confirm `cyl-pipeline-worker` was recreated: it reads the switch only
  at start-up. Switching off fails any batches still queued; it is not a pause.
- **A Secret pointing at the wrong Bloom.** Nothing at dispatch checks which Bloom instance a
  credential Secret targets. A prod Secret holding staging's `BLOOM_API_URL` would stage staging's
  images into prod's directories, and once fixed, the skip-if-done cache would keep them. Mitigated
  operationally: §6.3(e) verifies the Secret's target host and signs in against prod before the
  flip, and §6.3(f) wipes prod's directories if a run ever used an unverified credential.

## Migration Plan

1. Merge to `staging`. Staging's values equal today's, so no visible change. Verify with a run on a
   TEST-E2E scan (experiment 12880747; real experiments lack image bytes, bloom#985).
2. The author provisions prod and verifies it (§6.3).
3. **Promotion 1** (staging→main) takes prod live with the switch `false`. A prod run fails at once
   with "turned off" (§6.4).
4. The one-line flip PR merges to `staging`, where it is inert.
5. **Promotion 2** lands the flip. The acceptance pair of runs follows (§6.6).
