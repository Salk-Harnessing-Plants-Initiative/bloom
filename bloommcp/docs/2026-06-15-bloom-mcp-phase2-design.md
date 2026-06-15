---
title: bloom-mcp Phase 2 — thin delegating MCP server (re-brainstorm)
date: 2026-06-15
status: draft — pending user + Benfica review
supersedes: §2–§9 of 2026-05-11-metcalf-2026-evelyn-bloom-mcp-design.md (re-brainstormed in the new state)
amended-by: 2026-06-15-bloom-mcp-phase2-persistence-design.md (§4 data_access + §5/§6 persistence — the deployed prototype reads from AND writes versioned outputs to Supabase Storage; this doc's "storage-agnostic / CSV-backed / versioned-writer-in-data_access" framing is corrected there to two ports — ExperimentReader read + ResultStore write — over the deployed Supabase adapter)
repos: salk-bloom (bloommcp subproject), sleap-roots-analyze, sleap-roots-contracts
---

# bloom-mcp Phase 2 — design

Re-brainstorm of Phase 2 ("harden the `bloommcp` prototype into a thin, validated MCP server") in the **new state**: the serializable result types (analyze #127–#130 + the #119 `CrossPlatformPCResult`) are **in-flight** — a reviewed, ready stack (open PRs #149/#150/#151) of *additive* adapters over the dict the public `perform_*` functions already return — and `sleap-roots-contracts 0.1.0a1` has landed. So the typed-result pattern is established and the wrappers will be thin: the delegated functions are already public in `sleap-roots-analyze` v0.1.0a2, and the tools consume the upstream result types once their release lands (interim local adapter possible but avoid duplicating it). This doc re-derives the architecture from that state and from what the bloom agent actually assumes; it references but updates §2–§9 of the master design spec.

## 1. Goal & scope

**Goal:** evolve `bloommcp` into a thin MCP server that **delegates all analysis to `sleap-roots-analyze`** (eventually retiring the vendored `source/` copies — **deferred to after Stage 1 / Tiers 0–4; Benfica confirmed on PR #310**; the slice **adds** granular tools alongside the existing workflow tools), with a uniform tool contract (provenance + structured errors) and a real test stack — proven via a **depth vertical slice**, not breadth.

**In scope (the slice):** the contract layer; a reusable, storage-agnostic data-access layer; 1–2 **fast granular** tools delegating to analyze's typed results; the 5 test patterns; validation on Claude Desktop.

**Out of scope (deferred, with triggers — see §8):** breadth (≥10 tools); async/long-running pipeline tools; `find_tools` / RAG-MCP; URL-namespace API versioning; the Phase-3 autopop generator. Auth/RBAC unchanged.

## 2. Constraints from the bloom agent (current consumer)

From `salk-bloom/langchain/`:
- The agent connects via `MultiServerMCPClient(...).get_tools()` — **dynamic discovery** (MCP `tools/list`). It does **not** hardcode tool names or parse fixed output shapes; the LLM picks by name+description and reads results as text/JSON. So renaming tools / switching string→structured output does **not** break discovery — the agent re-discovers registered tools on startup.
- Hard constraints to preserve: server key `bloom-tools`, **streamable-http** transport, URL via `BLOOM_MCP_URL`, **Bearer `BLOOMMCP_API_KEY`** auth. (Auth kept stable.)
- The agent runs **Qwen3.5-9B on a DGX Spark** (262K context) — a small model: tool-use is fragile and **tool-count-sensitive** (selection/distraction, not context). **The agent is not live right now**, which relaxes migration risk. Even so, the slice **adds** granular tools *alongside* the existing workflow tools; retiring the bespoke `run_X_workflow` tools + `source/*` is **deferred to after Stage 1 (Tiers 0–4) — Benfica confirmed on PR #310** (deleting `source/*` now would break the booting server, whose workflow tools module-level-import it).

## 3. Architecture

Every tool is a **thin delegation that re-orchestrates nothing**:

```
tool = data_access(resolve experiment + Bloom IO)
     + @as_mcp_tool(auto Provenance + BloomMCPError)
     + delegate → sleap-roots-analyze (fast function → typed result)
```

The **agent-facing surface is small, flat, and fast** — granular analysis functions on loaded data (seconds), which is robust for *both* Qwen3.5-9B (small surface) and Claude (precision). `sleap-roots-analyze` already owns the real DAG pipelines (`QCPipeline`/`VizPipeline`/`CrossPlatformPipeline`); bloom-mcp must **not** re-implement orchestration (the prototype's bespoke `run_X_workflow` tools did, over the vendored `source/`). Those bespoke workflows are **left in place for the slice** and retired later (**after Stage 1 / Tiers 0–4 — Benfica confirmed on PR #310**) — replaced by exposing analyze's real pipelines, not re-orchestration. (Deleting `source/*` now would break the booting server, which module-level-imports them.)

**Granularity decision:** granular function tools are the foundation (composable, precise, 1:1 with the typed results, generator-friendly). Benfica's prototype consolidated into coarse `run_X_workflow` tools because a weak local LLM (Qwen-9B) does better with fewer, coarser tools — a **real but weak-model-bounded** finding (see §9). It's honored as good default hygiene (small surface) and via the future `find_tools`/coarse-subset path, not by permanently coarsening the surface around the weakest model.

## 4. Components (`src/bloom_mcp/`, flat layout)

> **Persistence corrected — see `2026-06-15-bloom-mcp-phase2-persistence-design.md`.** The deployed prototype already reads from *and* writes versioned outputs to Supabase Storage as `bloom_agent`. So the IO layer is **two ports** (read + write) over the deployed Supabase adapter — not a single "storage-agnostic data_access" with a CSV-backed writer. The layout + bullets below reflect that.

```
src/bloom_mcp/
  contract/        # @as_mcp_tool, Provenance (unified with the manifest), BloomMCPError
  data_access/     # ExperimentReader port + SupabaseReader adapter + FakeReader  (READ)
  result_store/    # ResultStore port + SupabaseResultStore (wraps AnalysisWriter) + FakeResultStore  (WRITE)
  storage/         # relocated Supabase primitives (supabase_client, manifest, versioning, schema)
  tools/           # hand-written fast tools (flat — no v1/, no generated/manual)
  server.py        # FastMCP, auth, /health (unchanged transport/auth)
  cli.py
```

- **`contract/`** — `@as_mcp_tool` wrapper that, on every call, validates Pydantic I/O, maps exceptions to a structured `BloomMCPError` (code + message + remedy), and stamps a `Provenance` block. **Provenance is the canonical record, computed once and persisted into the manifest `VersionEntry`** (schema v2→v3 additive: `seed`, `agent`, `output_sha256` on top of the existing `tool`/`params`/`input_sha256`/`code_versions`/lineage) — one provenance path, not two; **no hand-maintained per-tool version**.
- **`data_access/`** (READ) — `ExperimentReader` port (`load_experiment(name)`, `list_experiments()`); the **current adapter `SupabaseReader`** reads `bloommcp_input/` CSVs from Supabase Storage + tables via PostgREST as `bloom_agent` (relocated from `source/experiment_utils.py` / `read_input_csv`). The **DB-direct adapter** (the separate `2026-06-04-bloom-mcp-data-access-design.md`: source-aware Bloom RPCs + canonical rename) is **gated on integration sub-project #2** and drops in behind the same port later.
- **`result_store/`** (WRITE) — `ResultStore` port (`create_run`/`commit`/`list_runs`/`get_run`); the **current adapter `SupabaseResultStore`** wraps the deployed `AnalysisWriter`/`AnalysisDir` (versioned `bloommcp_output/<tool_class>_<stem>/v<N>/` + `manifest.json`). The **orchestrator-owned / per-user-identity adapter** (real RLS) is deferred-with-trigger. Tools depend on the port, never on `supabase`.
- **`tools/`** — flat module of fast, hand-written tools, each delegating to one analyze function, **persisting a versioned run via `ResultStore`**, and returning small structured results inline + `resource_link`s. (No `v1/` namespace, no `generated/manual` split — those anticipate deferred work; see §8.)
- **`server.py`** — unchanged transport (streamable-http :8811), Bearer auth, `/health`; registers the tools.

## 5. Data flow

`agent → MCP tool → ExperimentReader.load_experiment(name) → DataFrame → sra.<fn>(df, …) → typed result → ResultStore.create_run(provenance) → write artifacts to staging → commit() → StoredRun(version, links) → @as_mcp_tool wraps (Provenance + BloomMCPError) → small structured result inline + resource_links → agent.` Artifacts (CSVs/plots) are persisted as a versioned run via the `ResultStore` write port; only handles/links cross the wire, never inline blobs.

## 6. Versioning & provenance (the three levers)

MCP has **no per-tool version field** — tools are identified by **name**. "Tool-API versioning" is therefore conventions, with three levers:

1. **Package SemVer + permanently-stable tool names — the contract, and all we set up now.** `bloom-mcp`'s PyPI version is the contract; consumers pin a range (`bloom-mcp>=1,<2`); a breaking change → **major bump**. **Tool names never get a `_v2`** — `pca_analysis` stays `pca_analysis`; its behavior changes with the package major version. Setup = `pyproject.toml` version + `CHANGELOG.md` + stable names.
2. **URL namespace (`/v1/mcp` vs `/v2/mcp`) — the escape hatch for concurrent multi-version *hosting only*.** Used *instead of* `_v2` names if a hosted server ever must serve old + new clients simultaneously. Mechanically a `server.py` routing change (mount two FastMCP surfaces); the real cost is maintaining parallel surfaces. Addable later with no rework. **Deferred** until concurrent un-lockstep hosting is real.
3. **Drift-guard enforcement ("api-diff") — at first publish with adopters.** Snapshot the emitted tool JSON Schemas and fail a PR on incompatible change without a major bump — the *same* mechanism `sleap-roots-contracts` already uses for its schemas. **Deferred** to the publish milestone.

(`_v2` tool-name versioning is **explicitly rejected** — dominated by package SemVer for the non-concurrent case and by the URL namespace for the concurrent case.)

**Provenance** (distinct from API versioning) is **unified with the deployed manifest** rather than a parallel block: the canonical `Provenance` (computed in `contract/`) is persisted into the manifest `VersionEntry`, bumped **schema v2→v3 additive** to add `seed` / `agent` / `output_sha256`. See `2026-06-15-bloom-mcp-phase2-persistence-design.md` §4.

## 7. Testing

Five patterns per tool: **schema round-trip** (Pydantic ↔ JSON), **provenance presence**, **property/invariants** (hypothesis), **error-envelope** (declared failures → `BloomMCPError`, never raw), **golden-value reproduction** (reuse #120 fixtures + analyze's typed results). Plus an explicit **validation step: run the slice against Claude Desktop** (capable model) and confirm it's sane on **Qwen3.5-9B** (small surface) — the evidence for the granular direction.

## 8. Deferred — with triggers and room left in the design

- **Async / long-running pipeline tools.** `sleap-roots-analyze` has only ~3 pipelines and they run for minutes; a *synchronous* MCP tool that blocks for minutes is bad UX. Long pipelines stay **batch (run-all)** for now. *Trigger:* wanting them agent-accessible → an async-job tool pattern (kick off → poll → fetch). *Room:* the `@as_mcp_tool` response model is not sync-only; `data_access`'s versioned writer is already job-like; more analyze pipelines can be added.
- **`find_tools` / RAG-MCP.** *Trigger:* the Phase-3 generator emits a large granular catalog (past ~10–15 visible tools). *Where:* **server-side `find_tools`** is the portable option (the agent isn't Claude; Anthropic's Tool Search is client-side/Claude-only — opt-in `defer_loading` on the API/Desktop, on-by-default in Claude Code). The bloom agent may *additionally* pre-filter client-side for its weak model. *Room:* the flat `tools/` registry + rich descriptions are indexable; a version namespace or `generated/` subdir can be introduced then.
- **Phase-3 autopop generator + `generated/manual` split.** Out of Phase 2.
- **URL namespace + api-diff gate** — see §6.

## 9. Research grounding (recorded so deferrals are conscious; full notes in memory)

- **Tool-count:** keep the *active/visible* surface ~**10–15**; Anthropic optimizes past ~10 tools / ~10K tokens of defs; selection degrades mildly 5–15, **accelerates past ~30–50**; irrelevant tools distract.
- **Model tier:** small/open models are tool-count-fragile (Llama-3.1-8B fails at 46 tools, succeeds at 19 — selection, not context); frontier models (Claude) tolerate far larger surfaces. **"Few coarse tools" is a weak-model artifact, not a durable law.**
- **Discovery patterns:** ToolUniverse (~2,223 tools) hides them behind a `tool_finder` meta-pattern; BioMCP keeps a small coarse surface; consensus = small coarse default + search-first discovery for big catalogs.
- **Sources:** anthropic.com/engineering/advanced-tool-use; platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool; gorilla.cs.berkeley.edu/leaderboard.html (BFCL); arxiv.org/html/2411.15399v1; github.com/mims-harvard/ToolUniverse; github.com/genomoncology/biomcp; writer.com/engineering/rag-mcp; microsoft.com/en-us/research/blog/tool-space-interference-in-the-mcp-era.

## 10. Tier plan (the vertical slice)

Each tier = one OpenSpec change + PR, TDD. (Repo already has root-level `openspec/` + `.claude/commands` — Tier 0 is **not** "openspec init".)

0. **Tooling baseline** — restructure `bloommcp/source/` + `tools/` + **`storage/` + `supabase_client.py`** → `src/bloom_mcp/`; introduce `sleap-roots-analyze>=0.1.0a2` + `sleap-roots-contracts[pandas]>=0.1.0a1`; add the test stack + #120 fixtures; decide bloommcp's own `openspec/` scope (mirroring `web/openspec`) vs the root.
1. **Contract layer** (`contract/`): `@as_mcp_tool`, `Provenance` **unified with the manifest `VersionEntry` (schema v2→v3 additive: seed/agent/output_sha256)**, `BloomMCPError`.
2. **Persistence layer** — `data_access/` (`ExperimentReader` read port + `SupabaseReader` adapter + `FakeReader`) **and** `result_store/` (`ResultStore` write port + `SupabaseResultStore` wrapping the deployed `AnalysisWriter` + `FakeResultStore`); repoint the existing workflows to the ports. (Was "data-access CSV impl" — corrected per the persistence design.)
3. **First granular tool** — **ADD** `pca_analysis` → `PCAResult`, **persisting a versioned run via `ResultStore`** + returning `resource_link`s, register in `server.py`, full 5 patterns; **leave** `source/pca.py` + `dimred_workflow` in place (retirement deferred).
4. **Second granular tool** — **ADD** `clustering` → `KMeans`/`GMMResult` (polymorphic result), **persist via `ResultStore`**, register in `server.py`; **leave** `source/clustering.py` + `clustering_workflow` in place (retirement deferred).

## 11. Open items

- **Validate the direction with Benfica** (backup mentor + prototype author) before implementation: granular-default + retiring bespoke workflows + coarse-for-weak-models. Ask *why* he consolidated into workflows (catch missing context); credit the prototype; frame as evolution.
- **Roadmap** for these tiers committed in **salk-bloom** (per decision); a **copy/pointer of this design** dropped in `bloommcp/docs/` (vault remains source of truth).
- Resync the Notion design-spec mirror (§2–§9) after this lands.
