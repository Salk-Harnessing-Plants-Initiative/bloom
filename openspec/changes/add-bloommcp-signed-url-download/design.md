## Context

Confirmed by reading the shipped code:

- `StorageBackend` (`bloommcp/src/bloom_mcp/storage_backend.py`) already defines six object-storage
  helpers (`upload_file`/`download_file`/`write_json`/`read_json`/`list_prefix`/`delete_files`) as a
  `Protocol`, with `SupabaseStorageBackend` and `LocalStorageBackend` implementations, selected via
  `BLOOM_STORAGE_BACKEND`. `bloom_mcp.supabase_client` re-exports each as a thin module-level function
  that lazily imports `active_backend()` and delegates — this is the exact pattern `create_signed_url`
  needs to follow as a seventh helper. **Note:** `openspec/specs/bloommcp-storage-backend/spec.md`'s
  "Storage Backend Interface" requirement still says "the exact five helpers," undercounting
  `delete_files` — pre-existing spec drift this change's `MODIFIED` delta (below) folds in while it's
  already touching that requirement's helper count, rather than compounding the drift a second time.
- `ResultStore.commit(run, outputs)` (`result_store/ports.py`) already returns a `StoredRun` carrying
  `output_keys: dict[str, str]` (logical name → object key) and `output_sha256: dict[str, str]`
  (logical name → hex SHA-256), computed by `_artifacts.hash_outputs` from the exact staged bytes.
  Neither is surfaced to the calling agent today.
- All 8 tools that persist via `ResultStore` follow the **identical** commit-then-return shape:
  `qc_clean`, `qc_inspect`, `pca_analysis`, `remove_outliers`, `descriptive_stats`,
  `cross_experiment_correlations`, `umap_analysis`, `clustering`. Only 5 of the 8 formally inherit
  `RunLinks`; `QCCleanResult`/`QCInspectResult`/`ClusteringResult` duplicate its four fields inline.
- **`RunLinks` itself is shipped code but not yet formal spec.** `refactor-consumer-seams` (the change
  that introduced `RunLinks`) is still unarchived (sits in `openspec/changes/`, not
  `openspec/changes/archive/`), and `openspec/specs/bloommcp-tool-contract/spec.md` has no
  `### Requirement:` block for it today — only a Purpose-line mention. This change's tool-contract
  delta therefore `ADD`s "RunLinks Output Signed URLs" as a standalone requirement rather than
  `MODIFIED`-ing a requirement that does not formally exist yet. Disclosed here so a reviewer isn't
  surprised that archiving both changes will need a manual fold into one coherent `RunLinks Base
  Model` requirement, rather than leaving two separate requirements each describing a subset of
  `RunLinks`'s fields.
- **Two, not one, test doubles stand in for the storage/result-store boundary**, and both need to grow
  a signed-url story:
  1. `FakeResultStore` (`result_store/fake_store.py`) — a full `ResultStore` stand-in, never uploads
     real bytes anywhere, never calls `storage_backend.active_backend()`.
  2. `_InMemoryObjectStore` / the `fake_supabase_storage` fixture (`bloommcp/tests/conftest.py`) — a
     dict-backed double for the **six** `bloom_mcp.supabase_client` module-level helpers, monkeypatched
     in via `pytest.fixture`. This backs the **real** `SupabaseResultStore` (not `FakeResultStore`) in
     roughly a dozen existing test files (`test_store_parity.py`, `test_supabase_result_store.py`, and
     every `test_*_tool.py` file that exercises persistence through the real store class). Once
     `SupabaseResultStore.commit()` calls `_sc.create_signed_url(...)` (this change), every test using
     this fixture falls through to the real, unconfigured Supabase client unless the fixture also grows
     a `create_signed_url` method — `conftest.py` explicitly pops `SUPABASE_URL`/`BLOOM_AGENT_KEY`, so
     that fallthrough raises immediately, not silently returns a wrong value.
- **`bloommcp`'s `SUPABASE_URL` points at the internal Docker network, not a URL an outside caller can
  reach.** `.env.prod.defaults`/`.env.staging.defaults` set `SUPABASE_URL=http://kong:8000` and
  `NEXT_PUBLIC_SUPABASE_URL` to the actual public host (e.g. `https://bloom.salk.edu/api`). A signed URL
  the Supabase Storage client generates is built against whatever base URL the client was constructed
  with (`SUPABASE_URL`) — so a naive `create_signed_url` would hand back a URL like
  `http://kong:8000/storage/v1/object/sign/...`, unreachable from anywhere outside the Docker network
  (unreachable by a chat client, a browser, or `curl` from a laptop). **This is not a new problem this
  change invents** — two sibling call sites already solve it: `services/workflows/video.py`'s
  `_to_public_url` (rewrites the internal host to `WORKFLOWS_PUBLIC_SUPABASE_URL`, wired in both
  `docker-compose.dev.yml` and `docker-compose.prod.yml` to `${NEXT_PUBLIC_SUPABASE_URL}`) and
  `web/lib/supabase/storage-url.ts`'s `toPublicStorageUrl` (same swap, regex-based). This change is the
  **third** independent instance of the identical pattern — see Decision 2.
- **`storage3==2.31.0` (the pinned version, confirmed by reading the installed package directly at
  `bloommcp/.venv/lib/python3.11/site-packages/storage3/_sync/file_api.py`) already implements both
  `create_signed_url(path, expires_in, options=None)` and a batch `create_signed_urls(paths,
  expires_in, options=None)`.** No dependency bump is needed — task 1.1's "confirm the pin supports
  this" resolves cleanly. The client method returns a `dict` (`{"signedURL": ..., "signedUrl": ...}`,
  key casing has drifted across `storage3` versions per `services/workflows/video.py`'s own
  "best-effort extraction across supabase-py versions" comment), **not** a bare string — the same
  extraction step both existing call sites already do.
- `list_existing_analyses` (`sections/core/list_existing_analyses.py`) calls
  `store.list_runs(experiment, tool_class)` across up to 8 tool classes and `dataclasses.asdict()`s
  every `StoredRun`, then `json.dumps()`s the result — cached only 30 seconds, called "at the start of
  any analysis session" per its own docstring. `OutputLink` is a **Pydantic** model, not a dataclass:
  `dataclasses.asdict()` does not know how to flatten a Pydantic instance nested inside a plain dict,
  so if `output_links` were ever non-empty on a `list_runs`-sourced `StoredRun` (a future regression of
  Decision 1), `json.dumps()` would hard-fail with `TypeError: Object of type OutputLink is not JSON
  serializable` — worth a regression test even though it is unreachable today.
- The manifest schema (`bloommcp-tool-contract`'s "Additive Manifest Schema v3") carries
  `output_sha256`/`output_keys` as the only per-artifact siblings; this change adds none.
- No `StorageBackend` method reports an object's byte size without downloading it, and the manifest
  records none today — see Decision 1 for why this change does not need one.

## Goals / Non-Goals

- **Goal:** every consumer-tool result includes a real, **working** (not merely present) signed/served
  URL plus the recorded `output_sha256`, per output artifact.
- **Goal:** the signed URL is actually reachable by the caller it's returned to — not just present as a
  string. A URL pointing at an internal Docker hostname satisfies the field's type but not its purpose.
- **Goal:** zero change to `manifest.json` bytes, schema version, or cross-backend parity.
- **Goal:** a concrete, documented, spec-backed inline-vs-link size number, with the data needed to
  apply it exposed per output.
- **Non-Goal:** signed URLs for anything other than the run a tool call just committed (Decision 1).
- **Non-Goal:** an inline-content return path (Decision 9 / proposal.md Non-Goals).
- **Non-Goal:** unifying the `RunLinks`-inheritance inconsistency across the 8 tool result models
  (Decision 6).
- **Non-Goal:** building an HTTP static file server for the local backend's storage root (Decision 3).
- **Non-Goal:** consolidating the three now-independent implementations of the internal-host-rewrite
  logic (`web/lib/supabase/storage-url.ts`, `services/workflows/video.py`, and this change) into one
  shared helper — a real DRY observation, but a cross-language/cross-service refactor orthogonal to
  shipping this capability.

## Decisions

- **Decision 1 (central): signed URLs are generated only inside `ResultStore.commit()`, never in
  `get_run`/`list_runs`.** The issue's acceptance criteria are scoped to "every consumer-tool result" —
  the Pydantic object a tool call itself returns, always freshly committed. `list_runs` (and thus
  `list_existing_analyses`, cached 30s, invoked "at the start of any analysis session") can return many
  historical versions across up to 8 tool classes; eagerly signing every output of every historical
  version on every call would multiply Storage API round-trips for a browse/discovery feature nobody
  has asked to make downloadable yet — the "file explorer" third of #388, deferred separately. This also
  sidesteps a real gap: byte size is not recorded in the manifest (only computed fresh at commit time
  from in-memory bytes — Decision 8), so `get_run`/`list_runs` have no size to report for a historical
  run without either a new manifest field or a fresh download to measure it. Scoping to commit-time
  avoids both.
- **Decision 2 (central, revised after review): `create_signed_url(key: str, expires_in: int) -> str`
  becomes the seventh `StorageBackend` method and the seventh `bloom_mcp.supabase_client` re-export**,
  following the existing delegate-to-active-backend pattern exactly. `SupabaseStorageBackend.
  create_signed_url` does three things, in order:
  1. Calls the storage client's `create_signed_url(path, expires_in)` and extracts the URL from its
     response — the call returns a `dict`, not a bare string, and the key casing
     (`signedURL`/`signed_url`/`signedUrl`) has drifted across `storage3` versions, so extraction uses
     the same best-effort `res.get("signedURL") or res.get("signed_url") or res.get("signedUrl")`
     fallback `services/workflows/video.py`'s `_signed_url` already uses. A response with none of those
     keys is treated as a failure (Decision 5), not a silently-`None` URL.
  2. **Rewrites the internal Docker-network host to a public base**, via a new `BLOOM_PUBLIC_SUPABASE_URL`
     env var and a small `_to_public_url`-shaped helper local to `storage_backend.py` (prefix-swap:
     `SUPABASE_URL` → `BLOOM_PUBLIC_SUPABASE_URL`, no-op if either is unset or the URL isn't on the
     internal host) — the **third** independent instance of a pattern this repo has already shipped
     twice (`services/workflows/video.py`'s `_to_public_url` + `WORKFLOWS_PUBLIC_SUPABASE_URL`;
     `web/lib/supabase/storage-url.ts`'s `toPublicStorageUrl` + `NEXT_PUBLIC_SUPABASE_URL`). Without
     this, every signed URL bloommcp returns in prod/staging would point at `http://kong:8000/...` —
     unreachable outside the Docker network, silently breaking the feature's entire purpose. Wired in
     both `docker-compose.dev.yml` and `docker-compose.prod.yml`'s `bloommcp` service blocks as
     `BLOOM_PUBLIC_SUPABASE_URL: ${NEXT_PUBLIC_SUPABASE_URL}`, mirroring the `workflows` service's
     identical existing line in both files verbatim.
  3. Returns the (possibly rewritten) URL as `str`.

  `bloom_mcp.supabase_client.create_signed_url` re-exports this the same way as its six siblings.
- **Decision 3: the local backend builds a served URL from a new `BLOOM_STORAGE_URL` env var**
  (`f"{BLOOM_STORAGE_URL.rstrip('/')}/{key}"` — the `rstrip` matters: without it, an operator setting
  `BLOOM_STORAGE_URL` with a trailing slash gets a double-slash URL), mirroring `BLOOM_PLOTS_URL`'s
  existing pattern for `PLOTS_DIR` conceptually, even though the two are different mechanisms
  (`PLOTS_DIR` is a separate legacy path; the object-storage local root has no serving mechanism today
  at all). `expires_in` is accepted (protocol parity) and ignored — documented, dev-only, no real
  credential/expiry enforcement, the same rhetorical shape as the local backend's already-documented
  Windows-atomicity caveat. When `BLOOM_STORAGE_URL` is unset, the local backend raises a
  `StorageBackendError`-shaped error (redacted, no absolute host path) rather than fabricating a
  `file://` URI, matching the local backend's existing no-path-leak discipline. **Non-goal:** standing
  up an HTTP server for that root; `BLOOM_STORAGE_URL` only supplies a base URL an operator has
  separately configured, exactly as `BLOOM_PLOTS_URL` already assumes today for `PLOTS_DIR`. Wired into
  `docker-compose.dev.yml`'s `bloommcp` service as `BLOOM_STORAGE_URL: ${BLOOM_STORAGE_URL:-}`
  (dev/local-backend-only; `docker-compose.prod.yml` needs no equivalent since production never sets
  `BLOOM_STORAGE_BACKEND=local`).
- **Decision 4: expiry is a hardcoded constant, `_SIGNED_URL_EXPIRES_SECONDS = 3600`** (1 hour), not a
  per-call parameter or env var. The issue's own framing is "a real **short-lived** Supabase signed
  URL" — an hour satisfies that while comfortably outlasting a single chat session. Not configurable —
  avoids a knob nobody has asked for. `storage3==2.31.0` also exposes a batch `create_signed_urls`
  (confirmed, not hypothetical, by reading the installed package) — using it to collapse the N
  per-commit signing calls into one is a real, available optimization, deliberately **not** adopted
  here: the issue's own acceptance criterion names the singular `create_signed_url(key, expires_in)`
  signature, and looping it per key keeps `StorageBackend`'s per-key contract exactly matching that ask
  and consistent with how `upload_file`/`delete_files` are already called per-key elsewhere. A future
  change can introduce a batch path if per-call latency proves material.
- **Decision 5: a failure to generate a usable signed URL for any output fails the whole `commit()`**,
  surfacing as `CommitFailedError` through the same best-effort-cleanup path an upload failure already
  takes — `OutputLink.url` stays a required `str`, never `Optional`. "Failure" includes both a raised
  exception from the storage client **and** a response missing every expected URL key (Decision 2's
  extraction step finding nothing to extract). **Alternative considered and rejected:** catch per-key
  and degrade to `url=None`. Rejected because the issue's acceptance criteria are unconditional ("every
  consumer-tool result includes a signed URL... per output... not a nice-to-have") — a result shipping
  `url=None` would satisfy the field's presence but not its contract. Generating/extracting a usable
  signed URL for an object that just uploaded successfully is not expected to fail independently at
  meaningfully higher odds than the upload itself, an already-accepted, already-retryable failure mode.
- **Decision 6: all 8 tool result models get `output_links` wired in uniformly**, regardless of whether
  they formally inherit `RunLinks` (5 of 8) or duplicate its fields inline (`qc_clean`, `qc_inspect`,
  `clustering`). Each gets the identical one-line addition (`output_links=stored.output_links`) at its
  existing `outputs=dict(stored.output_keys)` call site. Unifying the inheritance inconsistency itself
  is a separate, orthogonal refactor.
- **Decision 7 (revised after review): *both* of this codebase's test doubles for the storage/
  result-store boundary need their own signed-link behavior — neither calls a real backend.**
  - `FakeResultStore.commit()` synthesizes its own deterministic fake link
    (`f"fake://signed/{key}?expires_in={_SIGNED_URL_EXPIRES_SECONDS}"`) rather than calling
    `storage_backend.active_backend().create_signed_url(...)` — it never uploads real bytes to any
    backend, so calling the real seam would either hit whatever backend happens to be configured in the
    test process or fail against a key nothing ever wrote.
  - `_InMemoryObjectStore` (`bloommcp/tests/conftest.py`'s `fake_supabase_storage` fixture) gains its
    own `create_signed_url(key, expires_in)` method (same synthesized-string shape), added to the
    fixture's monkeypatch tuple alongside its existing six. Without this, every one of the roughly dozen
    existing test files that exercise the **real** `SupabaseResultStore` through this fixture would fall
    through to `bloom_mcp.supabase_client.create_signed_url`'s real-backend delegation the moment
    `SupabaseResultStore.commit()` starts calling it — and since `conftest.py` explicitly pops
    `SUPABASE_URL`/`BLOOM_AGENT_KEY`, that fallthrough raises immediately.

  In both cases `sha256`/`size_bytes` are computed for real (from the staged bytes each fake does have,
  via the same `hash_outputs`) — only the URL is synthesized.
- **Decision 8: `hash_outputs` gains a third returned dict, `output_size_bytes`** (`len(data)`, free —
  the bytes are already read into memory to hash; zero is a legal value — `validate_outputs` only
  rejects an empty *outputs dict*, not a zero-byte artifact, so `size_bytes` is documented as
  non-negative, not positive). `StoredRun` gains one new field, `output_links: dict[str, OutputLink]`,
  defaulting to `{}` on `StoredRun.from_version_entry` (used by `commit`/`get_run`/`list_runs` alike).
  Each adapter's `commit()` builds the real per-output `OutputLink`s and attaches them via
  `dataclasses.replace(stored, output_links=links)` — `get_run`/`list_runs` never populate it
  (Decision 1). None of this touches the manifest: `output_links` is computed at request time,
  never serialized into `VersionEntry`/`manifest.json` — preserving the existing
  byte-identical-manifest-across-backends guarantee untouched.
- **Decision 9: decide and document the inline-vs-link threshold as 100 KB (102,400 bytes), backed by a
  spec-level requirement, not just prose.** `bloommcp-storage-backend` already has a precedent for a
  doc-only, spec-backed requirement (`### Requirement: Documentation of Output Destinations`); this
  change extends that same requirement (via `MODIFIED`, full text preserved) with the threshold, the
  3600s expiry, and the `create_signed_url`/`output_links`/`BLOOM_STORAGE_URL`/`BLOOM_PUBLIC_SUPABASE_URL`
  documentation, rather than leaving the acceptance criterion backed only by a `tasks.md` checkbox.
  `storage-backends.md`'s doc text names the code constant (`_SIGNED_URL_EXPIRES_SECONDS`) for the
  3600s figure instead of restating "3600 seconds" as an independent fact, so the two can't drift; the
  100 KB figure has no code constant (never enforced — see Non-Goals) and is flagged in the doc as
  documentation-only. Byte size is chosen over row count because it is backend-agnostic and directly
  what would bloat a chat response; 100 KB covers a typical single-experiment trait-table CSV while
  staying small enough not to derail a chat message. Applying this to actually change a tool's response
  shape (return bytes/rows inline below the threshold) is **not** done here: every one of the 8 tool
  result models' docstrings already documents a deliberate, standing "never return matrices/scores/
  loadings inline" contract (e.g. `pca_analysis.py`). Reversing that is materially larger, orthogonal
  scope, and the right surface for it differs by client (chat vs. CLI vs. web) — outside bloommcp's
  tool-layer visibility. Deciding the number, backing it with a real requirement, and shipping the
  `size_bytes` data needed to apply it satisfies "decided and applied" without building a feature the
  issue's own Scope section never lists.

## Risks / Trade-offs

- **Added latency per tool call.** Every commit now makes one extra network call per output (typically
  2-4) to sign its URL. Accepted trade-off (Decision 4) — a confirmed-available batch endpoint
  (`create_signed_urls`) is deliberately deferred to keep `create_signed_url`'s per-key shape matching
  the issue's literal ask.
- **Decision 5's fail-closed choice lengthens the critical path**, and now applies identically across
  all 8 persisting tools at once (they all route through the same two `ResultStore` adapters). A
  systemic Storage API/signing regression (e.g. a `storage3` upgrade that changes the response's key
  casing again, defeating Decision 2's extraction fallback) would fail persistence tool-wide
  simultaneously, not tool-by-tool. No kill switch or staged rollout is built for this — accepted, same
  risk class as an existing upload failure, just now shared across every tool instead of one.
- **`BLOOM_STORAGE_URL` requires separately-configured serving infrastructure** the local backend
  doesn't build — documented, mirrors `BLOOM_PLOTS_URL`'s identical pre-existing gap for `PLOTS_DIR`.
- **Historical runs stay link-less.** `get_run`/`list_runs`/`list_existing_analyses` still return only
  object keys for prior versions after this ships (Decision 1) — an explicit, separately-scoped
  follow-up candidate.
- **The `RunLinks`-inheritance inconsistency (3 of 8 result models duplicate its fields inline) is left
  as-is** — orthogonal to this change's goal.
- **`refactor-consumer-seams` (the change that introduces `RunLinks` as formal spec) is still
  unarchived.** This change's tool-contract delta stands on shipped code, not an archived requirement —
  low risk (both changes validate independently today), but whoever archives either should fold the two
  into one coherent `RunLinks Base Model` requirement rather than leaving two fragments.
- **Three independent, unconsolidated implementations of the same internal-host-rewrite logic** now
  exist across `web/`, `services/workflows/`, and `bloommcp/` — a real DRY observation, explicitly not
  addressed here (see Non-Goals).

## Migration Plan

Additive/behavioral only — no manifest schema or byte change, no data migration, no dependency bump
(`storage3==2.31.0`, already pinned, already supports this — confirmed by reading the installed
package). Two new optional env vars require compose wiring as part of rollout, not just documentation:
`BLOOM_STORAGE_URL` (dev-only, `docker-compose.dev.yml`) and `BLOOM_PUBLIC_SUPABASE_URL` (prod +
staging + dev, both `docker-compose.prod.yml` and `docker-compose.dev.yml`, mirroring the `workflows`
service's existing lines exactly). Rollback = remove the new protocol method, the `OutputLink` field,
the 8 tools' one-line wiring, and the two compose env entries; nothing persisted needs unwinding since
`output_links` is never written to the manifest.

## Open Questions

- The 100 KB inline-vs-link threshold and the 3600s signed-URL expiry are proposed concrete defaults,
  open to reviewer pushback on the exact numbers but not blocking — both are cheap to change later since
  neither is persisted anywhere.
- Whether to eventually consolidate the three independent internal-host-rewrite implementations
  (`web/`, `services/workflows/`, this change) into one shared helper — not this change's job; noted as
  a candidate follow-up, not a decision this proposal needs to make.
