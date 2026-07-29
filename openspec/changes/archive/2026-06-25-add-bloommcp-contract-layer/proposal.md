## Why

Every granular bloom-mcp tool (Tiers 3–4: `pca_analysis`, `clustering`) needs the **same**
three guarantees on every call: validated Pydantic I/O, structured errors instead of raw
tracebacks, and a complete provenance record persisted with the result. Building those
guarantees per-tool would duplicate boilerplate and let provenance drift between tools.
Today there is **no** contract layer — `bloommcp/src/bloom_mcp/contract/` does not exist —
and the deployed manifest (`storage/schema.py`, schema **v2**) lacks the fields that
stochastic, reproducibility-critical analyses require: it has no `seed`, no `agent`, no
per-artifact `output_sha256`/logical `key`, records only `bloommcp` + `supabase` in
`code_versions`, and carries no exact-environment pointer.

This is **bloom-mcp Phase 2 · Tier 1 — the contract layer** (roadmap Tier 1; builds on
Tier 0's installable package, PR #313). It builds the `@as_mcp_tool` decorator and the
canonical `Provenance` model, and bumps the manifest **schema v2 → v3 (additive)** so the
contract's provenance has a single home in the manifest `VersionEntry` — **one provenance
path, not two**. See issue
[#306](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/306) and the
persistence design (`bloommcp/docs/2026-06-15-bloom-mcp-phase2-persistence-design.md` §4).

## What Changes

- **Add the contract package** `bloommcp/src/bloom_mcp/contract/` with three modules:
  - `errors.py` — `BloomMCPError(code, message, remedy)`: a structured, agent-safe error.
    The decorator maps declared exceptions to it; the agent **never** sees a raw traceback.
  - `provenance.py` — the canonical `Provenance` Pydantic model, computed **once** per
    call, plus a `to_version_entry(...)` mapping into the manifest `VersionEntry` (v3).
  - `wrap.py` — the `@as_mcp_tool` decorator: **our own** glue layered over FastMCP's
    `mcp.tool()` (FastMCP stays the MCP framework; Pydantic stays I/O validation — we
    wrap, we do not replace). On every call it validates Pydantic input/output, maps
    exceptions → `BloomMCPError`, **resolves and propagates the seed into the delegated
    `perform_*` call as `random_state=` and records it in `Provenance`** (an explicit
    kwarg-injection contract, not name-inference), and stamps the contract-time
    `Provenance`. Registration onto a `FastMCP` instance happens through a `register(mcp)`
    seam at server-wiring time — **not** at decoration time (the deployed pattern; the
    `mcp` instance does not exist when a tool module is imported, and Tier 1 does not touch
    `server.py`). The first real registration lands in Tier 3.
  - `models.py` — a **stub Tier-1 Pydantic params/input/output model** (carrying
    `seed: Optional[int]`) for the decorator to validate against. #191 (Pydantic input
    models) is OPEN/unmerged, so Tier 1 supplies a minimal stub; real per-tool models
    arrive with the granular tools (Tiers 3/4).
- **Do NOT seed globally.** The decorator MUST NOT call `np.random.seed()` — that would
  duplicate/fight `sleap-roots-analyze`'s per-estimator seeding and would not reach UMAP's
  numba RNG. *Determinism of the function* is upstream's (CI-guarded); *reproducibility of
  the stored artifact* is ours, via the recorded `seed` field.
- **Bump the manifest schema v2 → v3 (additive; old v2 manifests still read).** Extend
  `storage/schema.py` `VersionEntry` + `CodeVersions` and bump `CURRENT_SCHEMA_VERSION`:
  - `seed` (random_state) — **critical**; Tiers 3/4 are stochastic. Record the
    **resolved** seed actually used (resolve a concrete integer when none is supplied), so
    a persisted stochastic run is never `seed: null`.
  - `agent` / actor — record `bloom_agent` now (real per-user identity deferred).
  - per-artifact `output_sha256` + logical storage `key`, carried in **new optional
    sibling collections** on `VersionEntry` (`output_sha256: dict[str,str]`,
    `output_keys: dict[str,str]`, keyed by the same logical output name) — the existing
    `outputs: dict[str,str]` field is **retained unchanged** so v2 string-valued `outputs`
    still loads under `extra="forbid"` (re-typing `outputs` would NOT be additive).
    `output_sha256` is **app-computed** (hex SHA-256 over the pre-upload bytes — *not* the
    S3/MinIO ETag) and is populated **at commit by the `ResultStore` (Tier 2)**, not at
    contract time: the artifact bytes do not exist until the tool writes to staging. Tier 1
    only defines the fields + the mapping; the contract-time `Provenance` leaves them empty.
  - `code_versions` extended with **`sleap-roots-analyze` + `sleap-roots-contracts`**
    (Benfica, PR #310). **Installed-only, for every entry** (incl. `bloommcp`/`supabase`):
    record a version only for an *actually pip-installed* distribution — omit it rather than
    emit `importlib.metadata`'s `"unknown"`. This makes the existing `supabase: "unknown"`
    default an omit-when-absent optional field (benign, additive). analyze/contracts qualify
    now that Tier 0 made them real deps.
  - `environment` pointer — an exact-repro key resolved by precedence: container image
    digest (from `BLOOM_MCP_IMAGE_DIGEST`, `sha256:…`) → the `bloom-mcp` release version
    whose committed `uv.lock` reproduces the env via `uv sync` → a `uv.lock` content hash.
    Optional in unit/dev env; a **persisted** run (Tier 2) must carry an identifier that
    actually resolves to a reproducible env. `seed` pins the RNG; only the locked env pins
    the library *math* (`numpy`/`scipy`/`sklearn`).
  - Capture `params` **faithfully** — including the resolved feature/column-role selection
    and determinism-governing params (`svd_solver`, `n_components`, `n_init`, …): the PCA/
    cluster numbers depend on the exact matrix fed to `perform_*`, which `input_sha256` (a
    digest of the *source* CSV) does not pin.
  - Retain all existing v2 `VersionEntry` fields (`id`, `created_at`, `tool`, `params`,
    `based_on_version` lineage, `code_versions`, `outputs`, `user_label`, `version_dir`).
    Note `input_sha256` lives on the manifest's `ExperimentBlock`, **not** the
    `VersionEntry` — it is preserved there, unchanged.
- **Define the `Provenance → VersionEntry` mapping and unit-test it** (no live Supabase,
  no live write). The mapping fills the contract-time fields; per-artifact
  `output_sha256`/`key` are left for the `ResultStore` (Tier 2), which performs the live
  manifest write later.
- **Serialization round-trips assert exact equality** (`==`). The upstream `rtol=1e-6` /
  exact-integer tolerance is reserved for the golden *recomputation* oracle in Tiers 3/4 —
  applying a tolerance to a pure JSON round-trip would mask a precision-loss bug.
- **No hand-maintained per-tool version** (versioning is package-SemVer + stable tool
  names; `_v2`/URL-namespace/api-diff are deferred per the design).

## Impact

- **New capability:** `bloommcp-tool-contract`.
- **Affected code:**
  - New: `bloommcp/src/bloom_mcp/contract/{__init__,wrap,provenance,errors,models}.py`.
  - Modified: `bloommcp/src/bloom_mcp/storage/schema.py` (v3 `VersionEntry` sibling
    collections + optional `seed`/`agent`/`environment`, `CodeVersions` omit-on-absent
    fields, `CURRENT_SCHEMA_VERSION = 3`, docstring), `bloommcp/src/bloom_mcp/storage/
    code_versions.py` (installed-only resolution for all entries + analyze/contracts).
    `storage/manifest.py`'s `validate_schema` already accepts any version `<=` known, so
    old v2 manifests still read after the bump.
  - New tests: `bloommcp/tests/contract/` (decorator I/O + register seam, error envelope,
    seed propagation, provenance round-trip, Provenance→VersionEntry mapping, code-versions
    installed-only, environment pointer, schema-v3 round-trip, v2 back-compat) built against
    a **stub tool + stub params model**, plus a new `bloommcp/tests/fixtures/manifest_v2.json`
    fixture.
- **Out of scope (Tier 1):** no real analysis tools (`pca_analysis`/`clustering` are
  Tiers 3/4 — tested here against a stub); **no live persistence write** (Tier 2's
  `ResultStore`); no agent-surface/`server.py` registration change (Tier 3 registers the
  first real tool). The seed → `random_state=` propagation is *defined and unit-tested at
  the wrapper boundary* here; its determinism oracle runs against real `perform_*` calls
  in Tiers 3/4.
- **Builds on:** Tier 0's installable package (#313, merged to `staging`) and unifies with
  the deployed `storage/schema.py` manifest. **#191 (Pydantic input models) is unmerged** —
  Tier 1 does not block on it; it ships a minimal stub params model and the real per-tool
  models arrive with the granular tools (Tiers 3/4). Consuming `sleap-roots-analyze`'s
  serializable result dataclasses (`PCAResult`/`ClusterResult`, #149/#151 — now **merged**)
  rather than reinventing a JSON projection is likewise deferred to Tiers 3/4: Tier 1
  records provenance over a stub, so there is no real result type to consume yet.
- **Branch/PR target:** `staging` (staging-first repo); branch off `origin/staging`.
