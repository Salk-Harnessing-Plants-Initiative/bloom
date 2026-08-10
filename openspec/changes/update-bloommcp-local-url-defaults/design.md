## Context

Issue #642 identifies two independent frictions in bloommcp's fully-local mode:

1. `bloommcp/docs/storage-backends.md`'s "Two ways to use it" quick-start already omits
   `BLOOM_EXPERIMENT_LOCAL_ROOT` (a prior change already excised it), but the 2-var
   (`BLOOM_STORAGE_BACKEND` + `BLOOM_LOCAL_ROOT`) example it shows is **currently broken**:
   `BLOOM_PLOTS_URL` stays unconditionally required by `experiment_utils.validate_env()`, so
   following the doc literally fails boot.
2. `BLOOM_STORAGE_URL` / `BLOOM_PLOTS_URL` must point at _something_, but bloommcp itself never
   serves the directories they name — only `docker-compose.dev.yml`'s separate `langchain-agent`
   container happens to. Standalone (`uv run bloom-mcp`), any `output_links[...].url` or plot URl
   built from these vars 404s.

Three prior changes already touch this exact seam and are fully implemented in the current tree
but **not yet archived**: `add-bloommcp-local-root` (introduced `BLOOM_LOCAL_ROOT`, made the three
`BLOOM_*_DIR` vars conditionally optional under it — explicitly declaring `BLOOM_PLOTS_URL`
"unaffected... stays unconditionally required in every mode" as a Non-Goal),
`add-bloommcp-local-experiment-reader` (backend-aware boot gate skipping `validate_supabase_env()`
in local mode), and `add-bloommcp-signed-url-download` (introduced `create_signed_url` /
`BLOOM_STORAGE_URL`, explicitly declaring "standing up an HTTP static file server for the local
backend's storage root" a Non-Goal). This change is the first to actually close both of those
named Non-Goals — it is new scope, not a reversal of a considered decision.

Because those three changes are unarchived, `openspec/specs/bloommcp-packaging/spec.md` and
`openspec/specs/bloommcp-storage-backend/spec.md` are stale relative to shipped code. This
change's spec deltas are written against the _actual current behavior_ (code +
still-unarchived deltas), not the stale canonical text — see each delta file's note.

## Goals / Non-Goals

- Goal: the 2-var quick-start (`BLOOM_STORAGE_BACKEND=local` + `BLOOM_LOCAL_ROOT`) boots
  successfully and produces `output_links`/plot URLs that actually resolve, with no docker-compose
  and no separately-run static file server.
- Goal: `BLOOM_STORAGE_URL`'s lazy per-call default benefits every local-backend configuration
  (not just the `BLOOM_LOCAL_ROOT` tier), since it carries no boot-time requiredness to preserve.
- Non-Goal: changing the granular explicit-override tier's stricter contract. Setting
  `BLOOM_STORAGE_LOCAL_ROOT` / `BLOOM_PLOTS_DIR` directly (without `BLOOM_LOCAL_ROOT`) keeps
  `BLOOM_PLOTS_URL` unconditionally required at boot, unchanged.
- Non-Goal: authenticating the new static routes. See Decision 3.
- Non-Goal: reconciling the archive backlog on `add-bloommcp-local-root` /
  `add-bloommcp-local-experiment-reader` / `add-bloommcp-signed-url-download` /
  `add-bloommcp-signed-url-key-scoping` / `update-dev-local-mode-toggle`.
- Non-Goal: changing the server's bind host/port (`0.0.0.0:8811`, hardcoded in `main()`) or
  adding a configurable host/port env var.

## Decisions

- **Decision 1 — one shared self-serve base URL, reusing `BLOOMMCP_PUBLIC_URL`.** Add
  `storage_backend.self_serve_base_url() -> str`: `BLOOMMCP_PUBLIC_URL` when set (already the
  var naming bloommcp's own externally-reachable address, today used only for OAuth discovery
  in `bloom_mcp/auth.py`), else the hardcoded `http://localhost:8811` (the only bind port that
  exists — no env var configures it). Both `create_signed_url`'s `BLOOM_STORAGE_URL` default
  (`{base}/output`) and `experiment_utils`'s `BLOOM_PLOTS_URL` default (`{base}/plots`) call this
  one function, so the two defaults can never disagree about which host:port bloommcp is
  reachable at.

  - Alternative considered: a new dedicated env var (e.g. `BLOOMMCP_SELF_SERVE_URL`). Rejected —
    `BLOOMMCP_PUBLIC_URL` already means exactly this ("bloommcp's own address, reachable by the
    client"); a second var for the same concept would be exactly the kind of redundant
    configuration issue #642 is about removing, not adding.

- **Decision 2 — asymmetric gating between the two URL defaults, driven by _when_ each is
  resolved, not by inconsistency.**

  - `BLOOM_STORAGE_URL`'s default lives inside `LocalStorageBackend.create_signed_url`, called
    only at runtime, only on an instance that already exists because `BLOOM_STORAGE_BACKEND=local`
    was selected. There is no import-time concern, so the default applies whenever the var is
    unset, in _any_ local-backend configuration (granular override or `BLOOM_LOCAL_ROOT`-derived).
  - `BLOOM_PLOTS_URL` is a **frozen module-level constant** (`experiment_utils.PLOTS_URL`),
    resolved once at import time — the same treatment `PLOTS_DIR` already gets via
    `_resolve_plots_dir()`. Its resolver (`_resolve_plots_url()`, new, mirrors
    `_resolve_plots_dir()` exactly) MUST reuse the existing `_fully_local_root()` gate — which
    only calls `is_local_backend()` (and therefore only reads `BLOOM_STORAGE_BACKEND`) when
    `BLOOM_LOCAL_ROOT` is itself set — or it would break the package's spec'd side-effect-free
    import contract (`bloommcp-storage-backend` spec's "Import stays side-effect-free" scenario:
    `import bloom_mcp.server` must read no env var when `BLOOM_LOCAL_ROOT` is unset). This is why
    the `BLOOM_PLOTS_URL` default is narrower — tied to the `BLOOM_LOCAL_ROOT` convenience tier —
    while `BLOOM_STORAGE_URL`'s is broader. Both defaults still only ever activate when the
    backend actually is `local`.

- **Decision 3 — the new `/output` / `/plots` static routes are unauthenticated,** matching
  `/health`'s existing precedent and the already-shipped `langchain-agent` `StaticFiles` mounts in
  `docker-compose.dev.yml`. FastMCP's `BLOOMMCP_API_KEY` / OAuth check
  (`mcp = FastMCP(..., auth=auth_provider)`) lives inside the FastMCP-generated sub-apps
  (`combined_app`, each section's `http_app()`), not in `IdentityMiddleware` (which only verifies
  an _optional_ `X-Bloom-Identity` header for usage attribution) or at the top-level `Starlette`
  routing layer a plain `StaticFiles` `Mount` sits at — so there is no existing hook to attach
  bearer-token enforcement to without new plumbing. Local mode's documented threat model
  (`storage-backends.md`: "This is a dev / power-user path... driving bloommcp directly from
  Claude Code / Claude Desktop offline") is a solo developer on their own machine; the
  `BLOOMMCP_API_KEY` + `BLOOM_STORAGE_BACKEND=local` combination is not a documented supported
  configuration today. User-confirmed choice (see conversation): accept this as matching
  precedent rather than adding new auth plumbing for an unsupported combination.

  - Consequence: `IdentityMiddleware._action_from_path` categorizes requests to `/output`/`/plots`
    as `"combined"` (neither path is a `SECTIONS` key) — usage recording still fires (harmless,
    arguably informative), just not attributed to a specific section. Not changed by this
    proposal.

- **Decision 4 — mount unconditionally in local mode, not conditionally on whether the URL var
  was overridden.** `build_app()` mounts `/output` and `/plots` whenever `is_local_backend()` is
  true, regardless of whether `BLOOM_STORAGE_URL`/`BLOOM_PLOTS_URL` are explicitly set to point
  elsewhere. Simpler (one boolean gate, no cross-referencing which vars are set) and harmless if
  unused (an operator who deliberately points `BLOOM_STORAGE_URL` at a different server is
  unaffected; the extra local mount just serves the same files at a second, self-hosted URL
  nobody is required to use).

  - `StaticFiles(..., check_dir=False)` is used for both mounts: `main()` calls
    `validate_storage_backend()` / `_validate_dirs()` (which create these directories) before
    `build_app()` runs, so in the real boot path the directories already exist by mount time —
    but `check_dir=False` keeps `build_app()` itself independently callable (as existing tests
    already do, e.g. `test_sections_scaffold.py`) without requiring that ordering.

- **Decision 5 — `/output` serves `storage_backend`'s already-resolved local root (whichever
  tier resolved it), not a second, independent path.** A new public
  `storage_backend.local_output_root() -> Path` thinly wraps the existing private
  `_resolve_local_root()` so `server.py` can mount exactly the directory `LocalStorageBackend`
  itself writes to and reads from — the mounted content and the storage backend's content are
  the same directory by construction, never two roots that could drift apart.

## Risks / Trade-offs

- Serving local files over unauthenticated HTTP is a real, if modest, exposure if
  `BLOOM_STORAGE_BACKEND=local` is ever combined with a network-reachable bind and a
  security-sensitive expectation — mitigated by matching documented scope (solo dev/offline) and
  existing precedent (Decision 3); not eliminated.
- `BLOOMMCP_PUBLIC_URL` reuse means a misconfigured value (pointing at a host that isn't actually
  this bloommcp instance) would produce a self-served-looking URL that 404s elsewhere — no worse
  than today's `BLOOM_STORAGE_URL`/`BLOOM_PLOTS_URL` misconfiguration risk, and this var already
  exists for exactly this "how do I reach myself" purpose.

## Migration Plan

Purely additive defaults and new mounts, gated on `BLOOM_STORAGE_BACKEND=local`; the default
(Supabase) backend and any explicitly-set `BLOOM_STORAGE_URL`/`BLOOM_PLOTS_URL` are unaffected. No
data migration. No breaking change; no rollback beyond reverting the change.

## Open Questions

None outstanding — the one design decision requiring a judgment call (static-route auth) was
confirmed with the user before this proposal was written (Decision 3).
