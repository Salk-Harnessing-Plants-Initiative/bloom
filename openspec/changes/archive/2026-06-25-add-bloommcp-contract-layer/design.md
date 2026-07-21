## Context

bloom-mcp delegates all analysis to `sleap-roots-analyze` behind a thin MCP surface. The
deployed prototype already reads from **and** writes versioned outputs to Supabase Storage
as `bloom_agent`, and its `manifest.json` (`storage/schema.py`, schema **v2**) is already a
provenance record. Tier 1 builds the uniform tool contract that every granular tool wraps,
and **unifies provenance with that manifest** rather than inventing a parallel record.
Source of truth: `bloommcp/docs/2026-06-15-bloom-mcp-phase2-persistence-design.md` (§4–§6)
and `2026-06-15-bloom-mcp-phase2-design.md` (§4, §6). Builds on Tier 0 (#313).

## Goals / Non-Goals

- **Goals:** `@as_mcp_tool` (Pydantic I/O validation, exceptions → `BloomMCPError`, seed
  recording + propagation, single `Provenance` stamp, FastMCP registration); a canonical
  `Provenance` model; manifest schema **v2 → v3 (additive)**; a unit-tested
  `Provenance → VersionEntry` mapping. All proven against a **stub tool**.
- **Non-Goals (deferred):** real tools `pca_analysis`/`clustering` (Tiers 3/4); live
  persistence write (Tier 2 `ResultStore`); `server.py` tool registration (Tier 3);
  `_v2`/URL-namespace/api-diff versioning; per-user write identity / real RLS.

## Decisions

- **`@as_mcp_tool` wraps FastMCP; registration is a shipped `register(mcp, *tools)` seam.**
  The decorator owns the contract guarantees but does **not** register at decoration time —
  the `mcp = FastMCP(...)` instance does not exist when a tool module is imported (and
  `server.py` imports the tools — registering at import would be circular). Tier 1 ships a
  trivial `register(mcp, *tools)` helper (each `mcp.tool()(tool)`) so the seam the spec
  mandates actually exists and tests exercise it against an in-process `FastMCP`; the first
  real `server.py` wiring lands in Tier 3. The wrapper carries an explicit single-`params`
  `__signature__` (accepted positionally **and** by keyword — no positional-only mismatch)
  so FastMCP builds a correct schema without seeing the injected kwargs; `functools.wraps`
  preserves `__wrapped__` for `inspect.unwrap`. *Alternatives rejected:* register at
  decoration (needs a live `mcp`, circular, forbidden by Tier 1's no-`server.py` scope); a
  bespoke registry (re-implements MCP machinery).
- **Seed-provenance integrity: record only what was applied.** The resolved seed is
  recorded in `Provenance` **only** when the delegate declares `random_state` (so the
  recorded value actually reached the computation). A non-stochastic tool records
  `seed=None`; a seed *provided* to a delegate that can't accept it raises `internal_error`
  rather than recording an unapplied seed (a reproducibility lie). Seeds are strict ints in
  `[0, 2**32)`, validated at the input model (out-of-range/float/bool → `invalid_input`).
  *Alternative rejected:* always stamping the resolved seed — produces a manifest claiming a
  seed that never reached a `**kwargs`/no-`random_state` delegate.
- **Errors never leak internals.** Declared (author opted-in) exceptions pass their message
  through; an undeclared exception or output-contract breach returns a fixed message + a
  correlation id with the detail logged server-side; input-validation errors surface only
  field locations + types, never values. Prevents paths/hosts/connection-strings/bucket
  keys reaching the LLM agent, beyond the existing "no raw traceback" guarantee.
- **Seed: resolve, record, propagate — never global re-seed.** The decorator resolves the
  seed (drawing a concrete integer when params carry none), forwards it to the delegated
  `perform_*` as `random_state=` via an **explicit kwarg-injection contract** (not
  name-inference), and records the *resolved* integer in `Provenance` (never null for a
  stochastic run). It does **not** `np.random.seed()` — that duplicates/fights upstream
  per-estimator seeding and doesn't reach UMAP's numba RNG. *Determinism of the function*
  is upstream's (CI-guarded); *reproducibility of the stored artifact* is ours (the
  recorded resolved seed). Caveat for Tiers 3/4: for non-randomized solvers (e.g.
  `sklearn.PCA` full SVD) the seed is inert, so `params` must also faithfully capture the
  determinism-governing parameters (`svd_solver`, `n_components`, `n_init`, …) and the
  resolved feature/column-role selection — `input_sha256` digests only the *source* CSV,
  not the column-selected matrix actually fed to `perform_*`.
- **One provenance path; per-artifact fields are commit-time.** `Provenance` is stamped
  once in `contract/` at **contract time** (around delegation) and persisted into the
  manifest `VersionEntry` by Tier 2's `ResultStore`. Per-artifact `output_sha256` / logical
  `key` are **not** knowable at contract time — the artifact bytes do not exist until the
  tool writes to staging (Tier 2 `commit()`) — so the contract-time `Provenance` leaves
  them empty and the `ResultStore` fills them into the *same* single entry at commit. Tier 1
  defines the model + mapping and unit-tests the mapping; no second provenance system.
- **Schema bump is additive (v2 → v3); `outputs` is retained, not re-typed.** The existing
  `outputs: dict[str,str]` stays a string map; per-artifact hashes/keys land in NEW optional
  sibling collections (`output_sha256: dict[str,str]`, `output_keys: dict[str,str]`).
  Re-typing `outputs` to a richer model would break loading v2 string-valued `outputs` under
  `extra="forbid"` — *not* additive. All new `VersionEntry`/`CodeVersions` fields are
  optional, so v2 manifests still validate; `validate_schema` already accepts version `<=`
  known, so only `CURRENT_SCHEMA_VERSION` advances to 3. *Alternative rejected:* a breaking
  v3 (drop/rename/re-type v2 fields) — orphans deployed manifests.
- **`output_sha256` is app-computed, not the object-store ETag.** Hex SHA-256 over the
  exact pre-upload bytes (at commit, Tier 2). The S3/MinIO ETag is MD5/multipart-dependent
  and not reliably surfaced through storage-api; bloommcp also never addresses MinIO
  directly (only logical Supabase keys). Carry-forward caveat for Tiers 3/4: matplotlib
  embeds non-deterministic metadata (timestamps) in plots — render deterministically (strip
  `CreationDate`) or hash the underlying data arrays, else plot hashes aren't reproducible.
- **`code_versions` is installed-only for every entry.** Record a version only for an
  actually pip-installed distribution — including `bloommcp`/`supabase`; omit rather than
  emit `importlib.metadata`'s `"unknown"` (which is why Benfica removed `sleap_roots_analyze`
  while it was vendored). This makes the existing `supabase: "unknown"` default an
  omit-when-absent optional field (benign, additive). analyze + contracts qualify now that
  Tier 0 made them real deps.
- **Two distinct provenance columns: trace vs. reproducer.** `code_versions` is the
  human-readable *trace* ("produced by bloom-mcp X + analyze Y"); the `environment` pointer
  (image digest / `bloom-mcp` version → committed `uv.lock`) is the exact *reproducer* that
  pins `numpy`/`scipy`/`sklearn` — the libs that also move PCA/cluster numbers. Both exist
  so a future golden drift is diagnosable to an env bump vs. a code change.

## Risks / Trade-offs

- **`extra="forbid"` + new fields.** v2 manifests lack the new keys (fine — additive,
  optional, and the back-compat test loads a real v2 fixture with string-valued `outputs`).
  The reverse — a v3 entry read by **old v2 code** — would trip `forbid`; this is a
  **deploy-ordering constraint** (upgrade readers before any writer emits v3), not just
  "out of scope," since a rollout may briefly run mixed versions. Tier 1 has no live writer,
  so the hazard is latent until Tier 2.
- **#191 (Pydantic input models) is unmerged.** The decorator needs an input model with a
  `seed` to validate/propagate. Mitigation: Tier 1 ships a minimal **stub params model**
  (`contract/models.py`); real per-tool models arrive in Tiers 3/4. Tier 1 is not blocked.
- **`output_sha256` ownership crosses the tier boundary.** Defined here, populated at commit
  by Tier 2's `ResultStore`. Risk: the Tier 1 mapping could encode an impossible
  expectation (a contract-time hash). Mitigation: the spec + tests assert per-artifact
  collections are **empty** at contract time, making the seam explicit.
- **`environment` pointer: present ≠ reproducible.** Image digest may be absent outside the
  container; a dev/editable install or a dirty lock resolves to a non-reproducible value.
  Mitigation: precedence (digest → `bloom-mcp` version → `uv.lock` hash); field optional so
  unit tests pass, but any **persisted** run (Tier 2) must carry an identifier that resolves
  to a reproducible env. The spec splits these (optional in unit; required-reproducible when
  persisted).
- **Stub-only validation.** Tier 1 never exercises a real `perform_*`. Mitigation: the
  seed resolution/propagation + the no-global-reseed invariant are asserted at the wrapper
  boundary here (against a fake delegate); the end-to-end determinism oracle is Tiers 3/4's
  responsibility (against real calls + the #120 goldens).
