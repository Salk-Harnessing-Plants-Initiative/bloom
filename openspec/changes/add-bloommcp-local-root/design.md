## Context

#389 (`bloommcp-storage-backend`) and #390 (`add-bloommcp-local-experiment-reader`, merged in
code via PR #405 but **not yet archived** — `openspec/specs/bloommcp-experiment-read/spec.md`
still describes `SupabaseReader`'s raw read as merely "deprecated" and does not mention
`LocalReader` or `BLOOM_EXPERIMENT_LOCAL_ROOT` at all) together made `BLOOM_STORAGE_BACKEND=local`
a genuinely offline mode: input via `LocalReader`, output via `LocalStorageBackend`, boot skipping
`validate_supabase_env()`. Each of those two changes independently added its own root-resolution
variable, each falling back to a *different* pre-existing required directory
(`BLOOM_TRAITS_DIR`, `BLOOM_OUTPUT_DIR`) that has nothing to do with local mode's own naming. A
third, orthogonal directory (`BLOOM_PLOTS_DIR`) is required in **every** mode regardless of
backend, because `_viz_shared.save_plot()` writes figures straight to disk outside the
`StorageBackend` abstraction entirely.

The result: `experiment_utils.validate_env()`'s `_REQUIRED_DIRS` check
(`bloommcp/src/bloom_mcp/experiment_utils.py:24-32,100-113`) unconditionally requires all three
of `BLOOM_TRAITS_DIR`, `BLOOM_OUTPUT_DIR`, `BLOOM_PLOTS_DIR` to be individually set and
pre-existing, **regardless of which reader/backend is actually selected** — this check runs via
`server.main()`'s `validate_data_env()` call *before* the fully-local/Supabase branch. So a fully
correctly-configured `BLOOM_EXPERIMENT_LOCAL_ROOT` + `BLOOM_STORAGE_LOCAL_ROOT` setup still boots
only if the three legacy vars are *also* wired. This is the concrete mechanism behind the issue's
"has to create and correctly wire three directories" complaint.

## Goals / Non-Goals

- **Goals:** one new, opt-in `BLOOM_LOCAL_ROOT` var that supplies a default for all three
  subpaths; make the three legacy vars conditionally optional in that specific combination;
  auto-create the three subfolders at boot so only the top-level folder needs to pre-exist;
  preserve every existing explicit-override and default-path contract byte-for-byte.
- **Non-Goals:** changing the default Supabase-backed path in any way; changing
  `BLOOM_PLOTS_URL`'s requiredness; touching `docker-compose.prod.yml`; deciding #478's
  `${VAR}`-interpolation question; re-litigating #390's reader/store coupling.

## Decisions

### Decision 1 — `BLOOM_LOCAL_ROOT` is a middle tier, not a replacement

Precedence per subpath (highest first):

| Subpath | 1. Explicit override | 2. `BLOOM_LOCAL_ROOT`-derived (new) | 3. Existing fallback |
|---|---|---|---|
| Input  | `BLOOM_EXPERIMENT_LOCAL_ROOT` | `<BLOOM_LOCAL_ROOT>/input`  | `BLOOM_TRAITS_DIR` |
| Output | `BLOOM_STORAGE_LOCAL_ROOT`    | `<BLOOM_LOCAL_ROOT>/output` | `BLOOM_OUTPUT_DIR` (deprecated bridge) |
| Plots  | `BLOOM_PLOTS_DIR`             | `<BLOOM_LOCAL_ROOT>/plots`  | *(none — required)* |

Tier 3 is unchanged for anyone who hasn't set `BLOOM_LOCAL_ROOT` — this is purely additive.

- **Alternative considered:** replace the three legacy fallbacks outright with `BLOOM_LOCAL_ROOT`
  as the *only* default. Rejected — it would silently repoint existing dev setups that already
  rely on `BLOOM_TRAITS_DIR`/`BLOOM_OUTPUT_DIR` (the mounted dev dirs), and the issue explicitly
  asks for `BLOOM_LOCAL_ROOT` to be additive ("explicit override still wins, for anyone who wants
  the split").

### Decision 2 — Every `BLOOM_LOCAL_ROOT` default is gated behind `is_local_backend()`, not just "is `BLOOM_LOCAL_ROOT` set"

Each of the three resolvers checks `BLOOM_STORAGE_BACKEND=local` explicitly before honoring
`BLOOM_LOCAL_ROOT`, rather than trusting the caller only invokes them in local mode.

- **Rationale:** `BLOOM_LOCAL_ROOT` may stay set in a shell profile or a commented-then-uncommented
  `docker-compose.dev.yml` block even when a user flips `BLOOM_STORAGE_BACKEND` back to `supabase`
  for one run — the default path must not silently change. For `resolve_experiment_local_root()`
  and `_resolve_local_root()` this duplicates a guarantee their callers (`LocalReader.__init__`,
  `_build_backend()`) already provide structurally, but the issue's own conditional
  ("When `BLOOM_STORAGE_BACKEND=local` and `BLOOM_LOCAL_ROOT` is set") asks for the function itself
  to be correct in isolation, and it makes each resolver directly unit-testable without threading
  through the composition root.

### Decision 3 — `PLOTS_DIR`'s new default reads `BLOOM_STORAGE_BACKEND` at `experiment_utils` import time, gated behind `BLOOM_LOCAL_ROOT` being set

`PLOTS_DIR` is a frozen module-level constant (`Path(os.getenv("BLOOM_PLOTS_DIR", ""))`) that six
plot-tool modules import by name at *their* import time
(`from bloom_mcp.experiment_utils import PLOTS_DIR, PLOTS_URL`) and tests monkeypatch directly
(`monkeypatch.setattr(eu, "PLOTS_DIR", tmp_path)`) — unlike `resolve_experiment_local_root()`,
there is no function consumers call, so the constant itself must already contain the
`BLOOM_LOCAL_ROOT`-derived value. Computing that requires `is_local_backend()`
(`bloom_mcp.storage_backend`), which `experiment_utils.py` does not import today (it defers even
`validate_storage_backend` to inside `validate_env()`, keeping the two modules siblings).

Confirmed safe to import: `storage_backend.py` has zero `bloom_mcp`-internal imports, so
`experiment_utils → storage_backend` introduces no cycle. But `bloommcp-storage-backend`'s
"Import stays side-effect-free" scenario states the import "succeeds without reading
`BLOOM_STORAGE_BACKEND` ... at import" — unconditionally. Making that read **conditional on
`BLOOM_LOCAL_ROOT` being set** (itself unset by default, and unset in every environment today)
means every current deployment sees the read added only in code that never executes for them —
zero observable change. `is_local_backend()` also never raises (a plain string compare against
`"local"`), so this opt-in read cannot itself turn an invalid `BLOOM_STORAGE_BACKEND` value into an
import-time crash (verified against `test_server_import_is_pure_with_invalid_backend`, whose
`BLOOM_STORAGE_BACKEND=locel` case is exactly this scenario).

- **Alternative considered:** turn `PLOTS_DIR` into a function like `resolve_experiment_local_root`,
  updating all 6+ import sites and every test that monkeypatches the constant. Rejected as
  disproportionate blast radius for this change — "Simplicity First" favors the smaller, additive
  edit; the import stays a frozen constant, exactly as today, just computed with one more input.
- **Consequence:** `bloommcp-storage-backend`'s "Import stays side-effect-free" scenario needs a
  MODIFIED carve-out (see spec delta) — this is called out explicitly, not silently relied upon.

### Decision 4 — Only the top-level `BLOOM_LOCAL_ROOT` folder must pre-exist; subfolders auto-create at boot validation

Boot-time validation (`validate_experiment_local_root()`, `validate_storage_backend()`,
`_validate_dirs()`) each `mkdir(parents=True, exist_ok=True)` their own `BLOOM_LOCAL_ROOT`-derived
subfolder if it's missing, after confirming the top-level `BLOOM_LOCAL_ROOT` itself exists and is
writable. This mirrors the exact idiom `_viz_shared.save_plot()` already uses for `PLOTS_DIR`
(`PLOTS_DIR.mkdir(parents=True, exist_ok=True)`), just run once at boot instead of at first write,
so a fully-local run never hits a late "directory does not exist" mid-analysis.

An **explicitly-set** granular var (`BLOOM_EXPERIMENT_LOCAL_ROOT` / `BLOOM_STORAGE_LOCAL_ROOT` /
`BLOOM_PLOTS_DIR`) keeps today's stricter "must already exist" contract — auto-create applies
**only** to the `BLOOM_LOCAL_ROOT`-derived tier, so a typo'd explicit override still fails loudly
rather than silently creating a directory at the wrong path.

- **Alternative considered:** auto-create the top-level `BLOOM_LOCAL_ROOT` folder too. Rejected —
  the issue explicitly asks for it to be "created once, by hand, by the user," and silently
  creating an arbitrary top-level path from a possibly-mistyped env var is a materially different
  (and riskier) action than populating known subfolders under a folder the user deliberately made.

### Decision 5 — `BLOOM_TRAITS_DIR` / `BLOOM_OUTPUT_DIR` / `BLOOM_PLOTS_DIR` become conditionally optional, not removed

`validate_env()`'s "missing required var" check drops these three from the required list **only**
when `BLOOM_STORAGE_BACKEND=local` and `BLOOM_LOCAL_ROOT` is set. In every other combination —
unset backend, `supabase`, or `local` without `BLOOM_LOCAL_ROOT` — they remain exactly as required
as today, so dev/staging/prod (which never set `BLOOM_LOCAL_ROOT`) see zero change.

This is safe because, in that specific combination, nothing reads the raw `BLOOM_TRAITS_DIR` /
`BLOOM_OUTPUT_DIR` values for I/O: `LocalReader` always resolves through
`resolve_experiment_local_root()` (never the bare `TRAITS_DIR` global) and `LocalStorageBackend`
always resolves through `_resolve_local_root()`. `SupabaseReader.list_experiments()` /
`raw_source_path()` do read the bare `TRAITS_DIR` global directly, but `SupabaseReader` is only
ever wired when the backend is *not* local — outside this change's gate.

### Decision 6 — `BLOOM_LOCAL_ROOT`'s own existence is validated once, with one clear error — and this check raises on not-writable, unlike the legacy per-dir check

Rather than let three independent validators each discover a missing `BLOOM_LOCAL_ROOT` and raise
three subtly different messages depending on which one runs first, boot validation checks
`BLOOM_LOCAL_ROOT` exists and is a writable directory once, up front (in `validate_env()`), before
any of the three subfolder-specific validators run. It distinguishes three failure shapes with
distinct messages: missing, exists-but-not-a-directory, and exists-but-not-writable.

This new check **raises** on not-writable. That is deliberately stricter than `_validate_dirs()`'s
existing per-dir loop, which only `logger.warning`s (does not raise) when `BLOOM_TRAITS_DIR` /
`BLOOM_OUTPUT_DIR` / `BLOOM_PLOTS_DIR` is not writable — a long-standing behavior kept for
backward compatibility with existing, possibly-loose dev setups. `BLOOM_LOCAL_ROOT` is a brand-new,
opt-in gate with no such compatibility burden, and a not-writable root makes every subsequent
subfolder auto-create (Decision 4) fail anyway — better to name the real cause immediately than
let it surface later as a confusing `mkdir` `PermissionError` from a different call site.

### Decision 7 — The `bloommcp-packaging` delta collides with the still-unarchived #390; #390 must archive first, or the collision must be reconciled before either archives

This change's `bloommcp-packaging` spec delta writes `## MODIFIED Requirements` for
`Server Boot Fail-Fast Preserved`, diffed against the **currently archived**
`openspec/specs/bloommcp-packaging/spec.md`. But `add-bloommcp-local-experiment-reader` (#390,
merged in code via PR #405, not yet archived) carries its **own**, independent `## MODIFIED
Requirements` block for the exact same requirement (renaming its scenario to "Misconfigured
Supabase-backend deploy fails at boot" and reframing the requirement around a companion `##
ADDED Requirements` block, `Backend-Aware Boot Gate`, that this change's delta does not currently
touch at all because it does not yet exist in the archived spec). OpenSpec's archiver replaces a
requirement wholesale with what a change's delta provides — it does not merge two independent
pending changes' edits to the same requirement. **Whichever of #390 or this change archives
second will silently discard the other's edits to `Server Boot Fail-Fast Preserved`.**

Concretely, `Backend-Aware Boot Gate` (from #390, once archived) will itself need a
`BLOOM_LOCAL_ROOT` carve-out too: its "Fully-local boot still fails fast on a missing data
directory" scenario asserts `BLOOM_OUTPUT_DIR` / `BLOOM_PLOTS_DIR` / `BLOOM_PLOTS_URL` are
unconditionally required in fully-local mode — which this change makes conditionally false. This
delta cannot validly `MODIFIED` a requirement (`Backend-Aware Boot Gate`) that does not yet exist
in the archived spec, so the fix has to happen at the point #390 archives, not by writing against
it preemptively here.

**Resolution this proposal expects** (tracked as task 8.1, not silently assumed): #390 SHOULD
archive before this change. Once it does, this change's `bloommcp-packaging` delta MUST be
rewritten to (a) MODIFY the newly-archived `Backend-Aware Boot Gate` with the `BLOOM_LOCAL_ROOT`
carve-out, and (b) MODIFY `Server Boot Fail-Fast Preserved` against #390's post-archive text
(which already reframes it as backend-aware) rather than the pre-#390 text this delta currently
targets. If this change must archive before #390 for some other reason, the roles reverse: #390's
own delta must be rewritten against this change's post-archive text instead. Either way, the two
changes' authors (both `egao28` today) MUST coordinate the ordering explicitly — this is a known,
disclosed risk, not a silent one.

- **Alternative considered:** fold this change's `BLOOM_LOCAL_ROOT` carve-out directly into #390's
  still-open delta files instead of writing a competing `MODIFIED` block here. Rejected for this
  proposal — #390 and #479 are separate GitHub issues with separate scopes, and blurring one
  change's files into another's makes both harder to review and archive independently. Sequencing
  (Decision above) keeps the two changes' file ownership clean while still avoiding data loss.

## Risks / Trade-offs

- **Widens `experiment_utils`'s import surface to include `storage_backend`.** Mitigation:
  verified no cycle (`storage_backend.py` imports nothing from `bloom_mcp`); gated behind
  `BLOOM_LOCAL_ROOT` so the edge is dormant — never exercised — for every environment that hasn't
  opted in.
- **A subfolder deleted mid-session (e.g. a user removes `input/` by hand) is silently recreated
  empty on the next boot,** which could look like data-loss recovery rather than an error.
  Accepted: matches the existing `PLOTS_DIR` auto-create idiom (also silent), and `BLOOM_LOCAL_ROOT`
  is an opt-in local/dev feature, not a production durability guarantee.
- **Explicit-override-vs-`BLOOM_LOCAL_ROOT`-derived asymmetry** (must-pre-exist vs. auto-create) is
  a distinction a future reader could miss. Mitigated by stating it explicitly in each validator's
  docstring, this design doc, and dedicated tests (tasks 4.1/4.4).

## Migration Plan

Additive and opt-in — no data migration. `BLOOM_LOCAL_ROOT` unset ⇒ byte-for-byte unchanged
behavior in every mode (Supabase default, and `local` without `BLOOM_LOCAL_ROOT`). Rollback is
unsetting the var. **Archive ordering — two distinct issues, both with #390
(`add-bloommcp-local-experiment-reader`):**

1. `bloommcp-experiment-read`'s archived spec predates #390 (`LocalReader`,
   `BLOOM_EXPERIMENT_LOCAL_ROOT`) entirely, so this change's `Local Input Root Resolution`
   requirement is filed as ADDED rather than MODIFIED — safe (additive), but once #390 also
   archives its own `LocalReader Adapter` requirement, the capability will carry two overlapping
   descriptions of the same resolution mechanism, one of them (the 2-tier one) stale. Tracked as
   task 8.2.
2. `bloommcp-packaging`'s `Server Boot Fail-Fast Preserved` is independently `MODIFIED` by **both**
   #390 and this change, with different resulting text — a genuine silent-overwrite risk at
   archive time, not merely a staleness one. See Decision 7. Tracked as task 8.1, blocking.

#390 SHOULD archive before this change in both cases; task 8.1 is a hard prerequisite for this
change's own archive, not a soft note.

## Open Questions

- None blocking.
