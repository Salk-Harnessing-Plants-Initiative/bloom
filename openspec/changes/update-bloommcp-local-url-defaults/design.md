## Context

Issue #642 identifies two independent frictions in bloommcp's fully-local mode:

1. `bloommcp/docs/storage-backends.md`'s "Two ways to use it" quick-start already omits
   `BLOOM_EXPERIMENT_LOCAL_ROOT` (a prior change already excised it), but the 2-var
   (`BLOOM_STORAGE_BACKEND` + `BLOOM_LOCAL_ROOT`) example it shows is **currently broken**:
   `BLOOM_PLOTS_URL` stays unconditionally required by `experiment_utils.validate_env()`, so
   following the doc literally fails boot.
2. `BLOOM_STORAGE_URL` / `BLOOM_PLOTS_URL` must point at _something_, but bloommcp itself never
   serves the directories they name — only `docker-compose.dev.yml`'s separate `langchain-agent`
   container happens to. Standalone (`uv run bloom-mcp`), any `output_links[...].url` or plot URL
   built from these vars 404s.

Three prior changes already touch this exact seam and are fully implemented in the current tree
but **not yet archived**: `add-bloommcp-local-root` (introduced `BLOOM_LOCAL_ROOT`, made the three
`BLOOM_*_DIR` vars conditionally optional under it — explicitly declaring `BLOOM_PLOTS_URL`
"unaffected... stays unconditionally required in every mode" as a Non-Goal),
`add-bloommcp-local-experiment-reader` (backend-aware boot gate skipping `validate_supabase_env()`
in local mode), and `add-bloommcp-signed-url-download` (introduced `create_signed_url` /
`BLOOM_STORAGE_URL`, explicitly declaring "standing up an HTTP static file server for the local
backend's storage root" a Non-Goal).

Because those three changes are unarchived, `openspec/specs/bloommcp-packaging/spec.md` and
`openspec/specs/bloommcp-result-store/spec.md` are stale relative to shipped code. This
change's spec deltas are written against the _actual current behavior_ (code +
still-unarchived deltas), not the stale canonical text — see each delta file's note.

### Revision history — a design pivot mid-implementation

This proposal originally closed friction #2 by having `create_signed_url` default
`BLOOM_STORAGE_URL` to bloommcp's own address and having `build_app()` self-serve the local
output root over HTTP at `/output` (mirroring the `/plots` treatment below) — i.e., it closed
`add-bloommcp-signed-url-download`'s Non-Goal exactly as originally suggested. That version
shipped in a first PR (#643).

The issue author then posted a follow-up comment rejecting that direction for _outputs_
specifically: "Local backend must not require signed/served URLs — direct filesystem access
replaces them entirely... the caller already has direct filesystem access to the file bloom-mcp
just wrote." This proposal (and the implementation) were revised in place to match: `output_links`
now surfaces a resolved absolute filesystem path for the local backend instead of a URL, and the
`/output` self-serve mount was removed as dead code once nothing called it. See Decisions 2, 6,
and 7 below for the resulting design. The `/plots` self-serve mount is **unaffected** — the
follow-up comment did not ask for it to change, and plots have no "the caller already has direct
access" analogue today (nothing plot-producing runs on the same process/filesystem boundary as an
external agent the way output artifacts sometimes might, and changing 5 plotting tools' result
schemas plus the shared `_viz_shared.save_plot()` helper was explicitly out of scope for this
follow-up — user-confirmed).

## Goals / Non-Goals

- Goal: the 2-var quick-start (`BLOOM_STORAGE_BACKEND=local` + `BLOOM_LOCAL_ROOT`) boots
  successfully, produces a plot URL that actually resolves (self-served), and produces output
  links that actually resolve (a direct filesystem path) — with no docker-compose and no
  separately-run static file server, and with `BLOOM_STORAGE_URL` never needing to be set at all.
- Non-Goal: self-serving output artifacts over HTTP. Superseded by the direct-path design —
  see Revision History above.
- Non-Goal: changing how plots resolve. Plots keep the self-served-URL design; only outputs
  pivot to direct paths.
- Non-Goal: changing the granular explicit-override tier's stricter contract. Setting
  `BLOOM_PLOTS_DIR` directly (without `BLOOM_LOCAL_ROOT`) keeps `BLOOM_PLOTS_URL` unconditionally
  required at boot, unchanged. (The output-path behavior is unaffected by this tier distinction
  either way — it never depends on any URL var, in any local-backend configuration.)
- Non-Goal: authenticating the `/plots` static route. See Decision 3.
- Non-Goal: reconciling the archive backlog on `add-bloommcp-local-root` /
  `add-bloommcp-local-experiment-reader` / `add-bloommcp-signed-url-download` /
  `add-bloommcp-signed-url-key-scoping` / `update-dev-local-mode-toggle`.
- Non-Goal: changing the server's bind host/port (`0.0.0.0:8811`, hardcoded in `main()`) or
  adding a configurable host/port env var.

## Decisions

- **Decision 1 — one shared self-serve base URL for plots, reusing `BLOOMMCP_PUBLIC_URL`.** Add
  `storage_backend.self_serve_base_url() -> str`: `BLOOMMCP_PUBLIC_URL` when set (already the
  var naming bloommcp's own externally-reachable address, today used only for OAuth discovery
  in `bloom_mcp/auth.py`), else the hardcoded `http://localhost:8811` (the only bind port that
  exists — no env var configures it). `experiment_utils`'s `BLOOM_PLOTS_URL` default
  (`{base}/plots`) is its only remaining caller after the output-path pivot (Decision 2).

  - Alternative considered: a new dedicated env var (e.g. `BLOOMMCP_SELF_SERVE_URL`). Rejected —
    `BLOOMMCP_PUBLIC_URL` already means exactly this ("bloommcp's own address, reachable by the
    client"); a second var for the same concept would be exactly the kind of redundant
    configuration issue #642 is about removing, not adding.

- **Decision 2 — outputs get a direct filesystem path, not a URL; plots keep the self-served
  URL. This asymmetry is intentional, not an oversight.** Per the issue's follow-up comment, the
  local backend's output artifacts are files bloommcp itself just wrote on the same machine the
  caller can already read from — there is nothing to sign or serve. Plots are architecturally
  different in this codebase today: they are returned as a URL string built by
  `_viz_shared.save_plot()` and consumed by every plotting tool's result schema; changing that to
  a path would touch 5 tools' schemas and the shared helper, which the follow-up comment did not
  ask for (confirmed with the user — see conversation). So:

  - `SupabaseResultStore.commit()` branches on `is_local_backend()`: local backend calls
    `build_output_links(..., path_for=lambda key: str(local_output_root() / key))`; every other
    case (including the granular explicit-override tier) keeps
    `url_for=lambda key: _sc.create_signed_url(...)`, unchanged.
  - `_resolve_plots_url()` keeps its self-serve default under the `BLOOM_LOCAL_ROOT` tier
    (Decision 1), unchanged from the original proposal.
  - `LocalStorageBackend.create_signed_url` itself is **unchanged** from its still-unarchived
    `add-bloommcp-signed-url-download` contract (raises when `BLOOM_STORAGE_URL` is unset) — it
    is simply no longer called by `commit()` for the local backend. It remains reachable only for
    an operator who deliberately wants a real served URL from their own external server (the
    documented pre-existing "dev-only convenience" use case).

- **Decision 3 — the `/plots` static route is unauthenticated,** matching `/health`'s existing
  precedent and the already-shipped `langchain-agent` `StaticFiles` mount in
  `docker-compose.dev.yml`. FastMCP's `BLOOMMCP_API_KEY` / OAuth check
  (`mcp = FastMCP(..., auth=auth_provider)`) lives inside the FastMCP-generated sub-apps
  (`combined_app`, each section's `http_app()`), not in `IdentityMiddleware` (which only verifies
  an _optional_ `X-Bloom-Identity` header for usage attribution) or at the top-level `Starlette`
  routing layer a plain `StaticFiles` `Mount` sits at — so there is no existing hook to attach
  bearer-token enforcement to without new plumbing. Local mode's documented threat model
  (`storage-backends.md`: "This is a dev / power-user path... driving bloommcp directly from
  Claude Code / Claude Desktop offline") is a solo developer on their own machine; the
  `BLOOMMCP_API_KEY` + `BLOOM_STORAGE_BACKEND=local` combination is not a documented supported
  configuration today. User-confirmed choice: accept this as matching precedent rather than
  adding new auth plumbing for an unsupported combination.

  - Consequence: `IdentityMiddleware._action_from_path` categorizes requests to `/plots` as
    `"combined"` (not a `SECTIONS` key) — usage recording still fires (harmless, arguably
    informative), just not attributed to a specific section. Not changed by this proposal.
  - This decision no longer applies to outputs at all — there is no `/output` route.

- **Decision 4 — mount `/plots` unconditionally in local mode, not conditionally on whether
  `BLOOM_PLOTS_URL` was overridden.** `build_app()` mounts `/plots` whenever `is_local_backend()`
  is true, regardless of whether `BLOOM_PLOTS_URL` is explicitly set to point elsewhere. Simpler
  (one boolean gate) and harmless if unused.

  - `StaticFiles(..., check_dir=False)`: `main()` calls `validate_storage_backend()` /
    `_validate_dirs()` (which create the plots directory) before `build_app()` runs, so in the
    real boot path it already exists by mount time — but `check_dir=False` keeps `build_app()`
    itself independently callable (as existing tests already do, e.g. `test_sections_scaffold.py`)
    without requiring that ordering.

- **Decision 5 — `local_output_root()` stays public, repurposed from "what `/output` mounts" to
  "what `commit()` joins a key against."** The function itself (a thin wrapper over the private
  `_resolve_local_root()`) is unchanged; only its caller changed — `server.py`'s `build_app()` no
  longer calls it (the `/output` mount is gone), `result_store/supabase_store.py`'s `commit()`
  now does, joining it with each output key (`root / key`) to produce the path
  `OutputLink.path` carries. This keeps the mounted-content-equals-storage-backend-content
  invariant the original design wanted, just applied to path construction instead of HTTP
  serving.

- **Decision 6 — `OutputLink.url` becomes `Optional[str]`; a new `Optional[str] path` field is
  added; `build_output_links` takes `path_for` as an alternative to `url_for`, requiring exactly
  one.** Considered alternatives:
  - _Keep `url: str` required and put a path-shaped string in it for local backend_ (e.g. a
    `file://` URI). Rejected: the still-unarchived `add-bloommcp-signed-url-download` spec
    explicitly forbids fabricating a `file://` URI or otherwise leaking an absolute host
    filesystem path through `url`, and conflating "a signed URL" and "a raw path" under one
    string-typed field invites a caller to `urlopen()` a bare path by mistake.
  - _Add `path` but keep `url` required, populating it with some sentinel for local backend_
    (e.g. empty string). Rejected: an empty-string `url` is not meaningfully different from
    `None` for any consumer, and Pydantic's own `Optional` support already models "may be absent"
    more clearly than a sentinel value would.
  - Chosen: `url: Optional[str] = None`, `path: Optional[str] = None`, and `build_output_links`
    validates `(url_for is None) == (path_for is None)` raises — every call site must pick
    exactly one closure, so it is a caller-visible decision at the type-checker/runtime level,
    not something that silently defaults to a mixed or empty state. A separate explicit guard
    (`if url_for and not url: raise ValueError(...)`) preserves the pre-existing "a signing call
    that yields nothing usable must fail commit, not silently build a link with no URL"
    invariant — relaxing `url` to `Optional` removed Pydantic's own type-level enforcement of
    that invariant (`None` used to be a type error; now it is a legal value for the _other_
    branch), so the check has to live in `build_output_links` itself instead.

## Risks / Trade-offs

- Serving local _plot_ files over unauthenticated HTTP is a real, if modest, exposure if
  `BLOOM_STORAGE_BACKEND=local` is ever combined with a network-reachable bind and a
  security-sensitive expectation — mitigated by matching documented scope (solo dev/offline) and
  existing precedent (Decision 3); not eliminated. This risk no longer applies to output
  artifacts at all (no HTTP serving of them).
- `output_links[...].path` is an absolute host filesystem path returned to whatever MCP client
  is calling bloommcp. In the documented local-mode threat model (the calling agent — e.g. Claude
  Code — runs on the same machine as bloommcp, or otherwise already has filesystem access to the
  configured `BLOOM_LOCAL_ROOT`), this is not a new information disclosure; it is the intended
  mechanism. It would be a real concern only if fully-local mode were ever run with the caller on
  a _different_ machine from bloommcp itself — not a documented or supported configuration today.
  Two narrower variants of "different machine" are worth naming explicitly, since both are
  realistic even for a solo-dev/offline user who never leaves one physical host:
  - **Same host, different container.** `docker-compose.dev.yml`'s own pre-existing env-var
    comments already anticipate `BLOOM_STORAGE_BACKEND=local` being driven from a container
    other than `bloommcp` itself (e.g. `langchain-agent`) — that caller sees `bloommcp`'s
    container-internal path (`/app/data/LOCAL_ROOT/...`), which is meaningless (and typically
    inaccessible) outside `bloommcp`'s own container filesystem unless the two containers
    happen to share the same bind mount. This is a usability gap (the path may simply not
    resolve for that caller), not a new disclosure beyond what this design already accepts.
  - **The path leaves the machine entirely via the human, not the network.** A scientist who
    pastes a tool response into Slack, a GitHub issue, or a chat transcript shares the host
    username and `BLOOM_LOCAL_ROOT` project-layout embedded in the absolute path — something a
    signed URL (opaque, time-limited, revocable) never exposes. Still consistent with the
    documented single-machine threat model (nothing crosses the network unauthenticated), but
    a real, human-mediated exposure a signed URL avoids by construction; not mitigated further
    here, since redacting or shortening the path would defeat the feature's own purpose (direct
    filesystem access).
- `BLOOMMCP_PUBLIC_URL` reuse (for plots only, post-pivot) means a misconfigured value (pointing
  at a host that isn't actually this bloommcp instance) would produce a self-served-looking plot
  URL that 404s elsewhere — no worse than today's `BLOOM_PLOTS_URL` misconfiguration risk, and
  this var already exists for exactly this "how do I reach myself" purpose.

## Migration Plan

Purely additive: a new `OutputLink.path` field, a new `build_output_links` `path_for` parameter,
and the `/plots` mount, all gated on `BLOOM_STORAGE_BACKEND=local`. `OutputLink.url` becomes
`Optional` — a type widening, not a narrowing, so no existing non-local caller's assumption of a
populated `str` breaks. No data migration (output_links is never persisted to the manifest). No
breaking change; no rollback beyond reverting the change.

## Open Questions

None outstanding. Two design decisions requiring a judgment call — the `/plots` route's auth
(Decision 3) and this revision's scope boundary between outputs (pivot to path) and plots (keep
self-served URL) — were both confirmed with the user before being written here.
