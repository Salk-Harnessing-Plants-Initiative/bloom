---
title: bloom-mcp Phase 2 — persistence & provenance architecture (ports + adapters)
date: 2026-06-15
status: draft — approved in brainstorm 2026-06-15, pending written-spec review + Benfica note
amends:
  - 2026-06-15-bloom-mcp-phase2-design.md  (supersedes §4 data_access + §6 versioning's persistence assumptions)
  - 2026-06-04-bloom-mcp-data-access-design.md  (demoted to the deferred DB-direct READ adapter; its "MCP read-only / persistence moves up" premise is now the deferred end-state, not the slice)
repos: salk-bloom (bloommcp subproject), sleap-roots-analyze
---

# bloom-mcp Phase 2 — persistence & provenance design

## 0. Why this exists

The Phase 2 design (2026-06-15) and the data-access design (2026-06-04) both assumed the MCP is **pure read + compute + return**, holds **no write credentials**, and that traits come from new **DB-direct RPCs** — with persistence "moving up" to the Bloom/agent layer. Inspecting the deployed prototype contradicts all three:

- It **reads inputs from Supabase Storage** (`read_input_csv` → `bloommcp-data/bloommcp_input/<name>`) and reads tables via PostgREST as `bloom_agent` — **not** local CSVs, **not** the proposed DB-direct trait RPCs.
- It **writes versioned outputs to Supabase Storage** via `AnalysisWriter` → `bloommcp_output/<tool_class>_<stem>/v<N>_<date>_<slug>/` + a `manifest.json` catalog — backed by **deployed RLS write policies** (`agent_insert/update_bloommcp_data`, migration `20260605000000`).
- It uses **one identity** — a single `BLOOM_AGENT_KEY` JWT (`role=bloom_agent`) — for both reads and writes.
- The manifest is **already a provenance record** (schema v2: `tool`, `params`, `input_sha256`, `code_versions`, `based_on_version` lineage, `outputs`, `created_at`, `user_label`), and the agent can already see prior runs via the `list_existing_analyses` tool.

Benfica shipped a deliberate persistence subsystem with its own migration. This design reconciles Phase 2 to that reality.

## 1. Decision (research-grounded)

**Build on the deployed Supabase persistence model, isolated behind ports** ("hybrid, plan relayer"). Research (MCP spec + ecosystem; scientific/workflow systems; service architecture — full notes §9) supports this:

- MCP **permits side-effecting/writing tools** (the `readOnlyHint`/`destructiveHint`/`idempotentHint` annotations exist for them); for **large** artifacts the idiomatic pattern is **persist to storage + return a handle/link**, not inline base64.
- The **workflow/experiment world** (MLflow's backend-store + artifact-store split; W&B's content-addressed versioned `v1/v2` artifacts + lineage; Snakemake hashing inputs+params+env; CWLProv per-run `manifest.json`) **validates a compute layer owning a versioned artifact store + run manifest** — exactly `AnalysisWriter`.
- **Architecture:** a read-only MCP with persistence owned by the orchestrator (which holds user identity) is the clean **end-state**, but ripping persistence out now is unjustified and loses cohesion (the tool that computed a result holds the exact provenance to record it). The correct move is to fight over the **boundary, not the location**: keep the writer, put it behind a narrow port, make the end-state a one-adapter swap.

## 2. Architecture — two ports, swappable adapters

```
 granular tools (pca_analysis, clustering)  +  tools/workflows/ (coarse, existing — repointed)
        │  depend on interfaces only (never import supabase directly)
        ▼
 contract/      @as_mcp_tool · Provenance (canonical) · BloomMCPError
 data_access/   ExperimentReader (port)  ← SupabaseReader [current]   · FakeReader
 result_store/  ResultStore (port)       ← SupabaseResultStore [wraps AnalysisWriter] · FakeResultStore
        ▼
 Supabase  (Storage `bloommcp-data` + PostgREST, as bloom_agent)
```

A **port** is a narrow `Protocol`/ABC describing what tools need; an **adapter** is the concrete implementation. The current Supabase behaviour becomes the *current* adapter; the deferred futures (DB-direct reads; orchestrator-owned writes) are *future* adapters behind the same ports.

### The two ports

```python
class ExperimentReader(Protocol):                 # READ
    def list_experiments(self) -> list[ExperimentMeta]: ...
    def load_experiment(self, name) -> ExperimentData: ...   # wide DataFrame + column roles

class ResultStore(Protocol):                       # WRITE
    def create_run(self, experiment, tool, params, provenance) -> RunHandle: ...  # allocate v<N> + staging
    def commit(self, run: RunHandle, outputs: dict[str, Path]) -> StoredRun: ...  # upload + manifest entry; returns links
    def list_runs(self, experiment, tool_class) -> list[StoredRun]: ...
    def get_run(self, experiment, tool_class, version="latest") -> StoredRun: ...
```

`SupabaseReader` and `SupabaseResultStore` are the existing `read_input_csv` / `AnalysisWriter` + `AnalysisDir` code **moved behind the interface, not rewritten**. `FakeReader`/`FakeResultStore` are in-memory, enabling the full test suite with **no live Supabase**.

**Storage stack (why the adapter is engine-agnostic).** The Supabase adapter talks only to the **Supabase Storage API** (logical bucket `bloommcp-data`, authed by the `bloom_agent` JWT). **MinIO is the S3 object store *behind* storage-api** (physical bucket `bloom-storage`, `STORAGE_BACKEND: s3`) — bloommcp never addresses MinIO directly, holds no MinIO endpoint/credentials, and stores only **logical keys** (`bloommcp_output/<tool_class>_<stem>/<v>/<file>`). Consequences carried below: artifact identifiers carry **no MinIO/S3 ETag or versionId**; `resource_link`s are **Supabase Storage URLs** (not MinIO URLs); CAS uses **storage-api** conditional writes, not the raw MinIO SDK. If MinIO were swapped for AWS S3/GCS behind storage-api, none of this design changes.

## 3. File layout

```
src/bloom_mcp/
  contract/        wrap.py (@as_mcp_tool), provenance.py, errors.py
  data_access/     ports.py (ExperimentReader) · supabase_reader.py · fake_reader.py
  result_store/    ports.py (ResultStore) · supabase_store.py (← AnalysisWriter/AnalysisDir) · fake_store.py
  storage/         relocated Supabase primitives (supabase_client.py, manifest.py, versioning.py, schema.py, code_versions.py)
  tools/           pca_analysis.py, clustering.py  + workflows/ (existing, repointed to the ports)
  server.py  cli.py
```

## 4. Provenance unification

One canonical `Provenance` model lives in `contract/provenance.py` and is computed **once** per tool call by `@as_mcp_tool`. The `ResultStore` adapter persists it **into** the manifest — there is no second, parallel provenance system. The manifest `VersionEntry` is bumped **schema v2 → v3 (additive; old manifests still read)** to carry the fields the provenance consensus (W3C PROV / Snakemake / EngMeta) has and v2 lacks:

| Field | In slice? | Note |
|---|---|---|
| `seed` (random_state) | **yes — critical** | Tiers 3/4 are stochastic (PCA/clustering); reproducible goldens require it. |
| `agent` / actor | yes | Records `bloom_agent` now; real per-user identity is **deferred** (§8). |
| `output_sha256` per artifact | **yes** | **App-computed** (hash bytes before upload) — *not* the S3/MinIO ETag, which is MD5/multipart-dependent and not reliably surfaced through storage-api. Makes runs content-addressed; hardens golden-test integrity. |
| logical storage `key` per artifact | **yes** | Store the full Supabase logical key (`bloommcp_output/.../<file>`) per output, not only the relpath — makes each run self-describing/portable/auditable (today the key is reconstructed). No MinIO/physical-bucket id. |
| existing (`tool`, `params`, `input_sha256`, `code_versions`, lineage, timestamps, `outputs`) | yes | Already present in v2; retained. |

Provenance computed once (`contract`), persisted once (manifest v3).

## 5. Tool contract, errors, return shape

`@as_mcp_tool` validates Pydantic I/O, maps exceptions to a structured `BloomMCPError` (`code` + `message` + `remedy`, never raw), seeds RNGs from `params.seed`, stamps `Provenance`, and registers with FastMCP. Tools return **small structured results inline + `resource_link`s** to stored artifacts — never inline base64 CSVs/plots. The links are **Supabase Storage URLs minted by the `ResultStore` adapter** (signed or public-bucket URL via storage-api, keyed off the logical key) — **not** MinIO/physical-bucket URLs; the adapter owns URL minting so tools stay storage-agnostic. Annotations: writing granular tools `readOnlyHint: false`; `list_existing_analyses`/read tools `readOnlyHint: true`.

## 6. Data flow (a granular call)

`agent → pca_analysis(experiment, params, seed) → ExperimentReader.load_experiment(name) → DataFrame → sra.perform_pca_analysis(...) → PCAResult → ResultStore.create_run(provenance) → write biplot + loadings to staging → commit() → StoredRun(version, links) → tool returns {explained_variance … inline} + resource_links + provenance → agent.`

## 7. Testing

Five patterns per tool, all runnable against `FakeReader` + `FakeResultStore` (no live Supabase): **schema round-trip**, **provenance presence** (incl. `seed`), **property/invariants** (hypothesis), **error-envelope**, **golden reproduction through the MCP tool** (PC1≈86.1% / PC2≈5.8% / PC3≈4.0% via the #120 turface_19 fixtures). Plus one real-adapter integration smoke against Storage. Tools appear in `tools/list` (FastMCP `Client`). Validated on Claude Desktop.

## 8. Deferred — with explicit triggers

- **DB-direct trait reads** — the 2026-06-04 data-access design (source-aware Bloom RPCs, canonical rename, one-source-per-frame) becomes a **future `ExperimentReader` adapter**. *Trigger:* integration sub-project #2 (Bloom schema ⇄ contracts) + the Benfica RPC/auth conversation.
- **Orchestrator-owned / per-user-identity writes + real RLS** — the single shared `bloom_agent` write identity makes row-level security decorative today (authz lives in the app, no per-user attribution). The clean **end-state** is a **future `ResultStore` adapter** where the orchestrator owns the write and threads real user identity. *Trigger:* per-user attribution / least-privilege becomes a requirement. The port makes this a one-adapter swap.
- **Manifest compare-and-swap** — `AnalysisWriter` has no CAS/`flock` (its own docstring documents the single-writer assumption); concurrent `v<N>` writers can clobber. *Currently safe* (one container, one process). *Trigger:* bloom-mcp scales past a single instance → add conditional-write inside the adapter **via the Supabase Storage API's conditional-upload support** (storage-api in front of MinIO) — not the raw MinIO/S3 SDK, which bloommcp never touches.

## 9. Research grounding (so deferrals are conscious; sources)

- **MCP allows write tools; persist-and-link for large artifacts.** Tool annotations (`readOnlyHint`/`destructiveHint`/`idempotentHint`) presume side-effecting tools; results may return `resource_link`/embedded `resource`; Anthropic recommends persisting large outputs and returning a handle (persist-to-disk threshold). No MCP-standard provenance vocabulary — define your own in structured content. Keep the *server/session* stateless (treat storage + manifest as source of truth), which aligns with the stateless-HTTP direction. — modelcontextprotocol.io spec (tools/resources), anthropic.com/engineering (code-execution-with-mcp, writing-tools-for-agents).
- **Compute layer owning a versioned artifact store + run manifest is well-precedented.** MLflow (backend store + artifact store), W&B (content-addressed versioned artifacts + lineage), Snakemake (hash inputs+params+env), DataLad, CWLProv/RO-Crate (`manifest.json` catalog). Among *MCP servers specifically* there is little prior art for a versioned output store (most stay read-only/inline) — reflects MCP's youth, and the base64→URL debate shows convergence toward store+link. — mlflow.org, docs.wandb.ai, snakemake.readthedocs.io, w3.org/TR/prov-dm, github cwlprov.
- **Service architecture: keep the writer, isolate the boundary.** Read-only-MCP + orchestrator-owned persistence is the clean end-state (caller holds user identity → RLS has teeth), but reversibility comes from ports-and-adapters, not from moving the code now. Shared service identity defeats RLS + prevents attribution; single-instance manifest without CAS is a lost-update hazard. — hexagonal architecture (Wikipedia/AWS), CQS/CQRS (AWS), AWS multi-writer S3 conditional writes, service-account security write-ups.

## 10. Open items

- Send Benfica the note: building on his `storage/AnalysisWriter` (not removing it); isolating it behind a `ResultStore` port; the read-only/per-user-identity end-state is deferred-with-trigger, not this slice. Confirm the manifest v3 additive bump is fine.
- Reconcile downstream artifacts to this design: Phase 2 design (§4/§6), data-access design (header → deferred read adapter), roadmap (`bloommcp/docs/roadmap.md` / PR #310), issues #305 (add `storage/` relocation) / #306 (Provenance↔manifest) / #307 (two ports, Supabase-backed not CSV) + Tier 2 reframed as the persistence layer, and the Notion Epic Phase 2 section.
