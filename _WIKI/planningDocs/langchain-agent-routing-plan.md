# LangChain Agent — Routing Architecture & Delivery Plan

**Audience:** bloom-agent team
**Status:** Living plan — current source of truth for the agent-routing track
**Author:** Benfica

---

## 1. Introduction

I'm currently in the middle of migrating the MCP architecture into sub-routes.
I initially had every tool sent to the agent on every turn — error-prone at my tool count;
Tool-selection accuracy drops sharply past ~15-20 tools on my production Qwen3.5-9B, and Anthropic publishes the same pattern with an even tighter threshold.
As per Anthropic's [Advanced tool use](https://www.anthropic.com/engineering/advanced-tool-use) blog I shouldn't exceed ~10 tools per turn.

I've currently got every tool registered to the freeform bucket; the migration moves those tool calls into specialized sub-routes.

I am migrating the agent from a single freeform `create_react_agent` (~25 tools per turn) to a **two-tier routed StateGraph**:

**Tier 1 — Top-level routes** (the `top_router` classifies every request into one of these four buckets):

| Route           | Tool source                                  | Status                                  | PR                                                                       |
| --------------- | -------------------------------------------- | --------------------------------------- | ------------------------------------------------------------------------ |
| `phenotyping` | `cyl_tools + context_tools` (~6-8 tools)   | ✗ local have to raise PR.              | TBD                                                                      |
| `scrna`       | `scrna_tools + context_tools` (~4-6 tools) | ✗ local planning doc have to raise PR. | TBD                                                                      |
| `analysis`    | (subgraph — see Tier 2)                     | ⚠ Subgraph drafted                     | [#202](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/pull/202) |
| `freeform`    | all tools (~25, no-wedge fallback)           | ✓ Shipped                              | merged (`feat(agent): wrap create_react_agent in explicit StateGraph`) |

**Tier 2 — Analysis sub-routes** (the `analysis_router` classifies into one of these six buckets, all bloommcp tools):

| Sub-route             | Tool count | Tool source                                                       | Status         | PR                                                                       |
| --------------------- | ---------- | ----------------------------------------------------------------- | -------------- | ------------------------------------------------------------------------ |
| `qc`                | 12         | qc_tools + outlier_tools +`list_existing_analyses`              | ⚠ Draft       | [#203](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/pull/203) |
| `stats`             | ~5-7       | stats_tools (descriptive, ANOVA, heritability)                    | ✗ Not started | TBD                                                                      |
| `dimred_cluster`    | ~6-8       | dimred_tools + clustering_tools (PCA, k-means, GMM, hierarchical) | ✗ Not started | TBD                                                                      |
| `viz`               | ~5-7       | viz_tools (plot histograms, boxplots, correlation matrix, etc.)   | ✗ Not started | TBD                                                                      |
| `correlation`       | ~8         | correlation_tools (cross-experiment, power, redundancy)           | ✗ Not started | TBD                                                                      |
| `analysis_freeform` | ~39        | all MCP (sub-tier no-wedge fallback)                              | ⚠ Draft       | [#202](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/pull/202) |

> **⚠ Legacy notice:** The bloommcp tool families above (`qc_tools`, `outlier_tools`, `stats_tools`, `dimred_tools`, `clustering_tools`, `viz_tools`, `correlation_tools`) are **slated as waste and set for replacement** by `sleap-roots-analyze`-backed wrappers per [bloommcp Phase 2 PR #310](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/pull/310). so the draft PR will be discarded in a cleanup.
>
> Phase 2 adds the new wrappers **alongside** the existing tools; retirement of the legacy modules is **gated on this work** (Elizabeth's PR body: *"retiring `source/*` + the workflow tools is deferred post-slice**).
>
> Tool **names** are permanent contracts. The leaf allow-lists in this document update to point at the new wrapper names (e.g., Tier 3 `pca_analysis`, Tier 4 `clustering`) as those tiers land. See §9 for the planned PR that codifies this transition.

The foundation has shipped. Six PRs are in flight (one merge-ready, five draft).

I'm planning a follow-up PR to bring the bucket (sub-route) architecture into bloommcp itself — so the leaf partition becomes the canonical contract consumed by **both** Claude (via per-leaf MCP entry points like `bloom-mcp-qc`, `bloom-mcp-stats`) **and** Qwen (via the langchain sub-routes that I have partially built). One source of truth, two consumers, same routing benefit at both doors. See **§9** for the detailed PR plan.

### Status overlay

Legend: ✓ shipped to staging · ⚠ draft PR open · ✗ not started

```
                              User request
                                    │
                                    ▼
                          context_loader   ✓
                                    │
                                    ▼
                            top_router   ✓          prompt fix: ⚠ #207
                                    │
       ┌────────────┬────────────────┼────────────────┐
       ▼            ▼                ▼                ▼
   phenotyping    scrna           analysis         freeform   ✓
       ✗            ✗             ⚠ #202           (fallback)
                                    │
                                    ▼
                            analysis_router   ⚠ #202
                                    │
       ┌─────────┬───────────┬──────┼──────┬─────────────┬──────────────────┐
       ▼         ▼           ▼      ▼      ▼             ▼                  ▼
       qc      stats   dimred_cluster   viz   correlation     analysis_freeform
    ⚠ #203      ✗            ✗          ✗         ✗                ⚠ #202
   (12 tools)                                                      (fallback)
```

> **Heads-up:** PRs #202 and #203 will be **cleared and reworked** under the new proposal — see §9. The partition moves into bloommcp itself; both leaves rebuild against the new `sleap-roots-analyze`-backed tool names (Tier 3 `pca_analysis`, Tier 4 `clustering`, …) once Phase 2 lands them.

**Cross-cutting (orthogonal to the routing graph):**

| Status        | PR                                                                       | Scope                                                                    |
| ------------- | ------------------------------------------------------------------------ | ------------------------------------------------------------------------ |
| draft PR open | [#208](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/pull/208) | `ask_user` HITL — backend (tool + `interrupt()` + `/chat/resume`) |
| draft PR open | [#209](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/pull/209) | `ask_user` HITL — web UI handler                                      |
| draft PR open | [#210](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/pull/210) | SSE classifier-token filter (ready to merge)                             |

This doc is the **single source of truth** for what's shipped, what's drafted, and what's left to delegate.

---

## 2. Status snapshot

| Layer                                                                                  | Status                        | Owner     | PRs                                                                      |
| -------------------------------------------------------------------------------------- | ----------------------------- | --------- | ------------------------------------------------------------------------ |
| **StateGraph foundation** (`context_loader` → `top_router` → `freeform`) | ✓ Shipped                    | Benfica   | merged (`feat(agent): wrap create_react_agent in explicit StateGraph`) |
| **Top router** (4 routes, freeform fallback)                                     | ✓ Shipped                    | Benfica   | merged (PR #201 + #200 + commit `38d8acd`)                             |
| **Top router prompt fix** (wave-comparison routing)                              | ⚠ Draft                      | Benfica   | [#207](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/pull/207) |
| **Analysis sub-router** + `analysis_freeform` fallback                         | ⚠ Draft                      | Benfica   | [#202](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/pull/202) |
| **`qc_leaf`** (12 tools — first Tier 3 leaf)                                  | ⚠ Draft                      | Benfica   | [#203](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/pull/203) |
| **`ask_user` HITL** (backend)                                                  | ⚠ Draft                      | Benfica   | [#208](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/pull/208) |
| **`ask_user` HITL** (web UI)                                                   | ⚠ Draft                      | Benfica   | [#209](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/pull/209) |
| **SSE classifier-token filter**                                                  | ✓ Ready to merge             | Benfica   | [#210](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/pull/210) |
| **`stats_leaf`**                                                               | ✗ Removed — folded into §9 | —        | —                                                                       |
| **`dimred_cluster_leaf`**                                                      | ✗ Removed — folded into §9 | —        | —                                                                       |
| **`viz_leaf`**                                                                 | ✗ Removed — folded into §9 | —        | —                                                                       |
| **`correlation_leaf`**                                                         | ✗ Removed — folded into §9 | —        | —                                                                       |
| **`phenotyping_subgraph`**                                                     | ✗ Not started                | TBD       | —                                                                       |
| **`scrna_subgraph`**                                                           | ✗ Not started                | TBD       | —                                                                       |
| **Parallel recipes** (QC fan-out demo)                                           | ✗ Removed — folded into §9 | —        | —                                                                       |
| **Eval harness** (per-route regression tests)                                    | ✗ Not started                | TBD       | —                                                                       |
| **bloommcp Phase 2 Tier 0** (package baseline)                                   | ⚠ Draft                      | egao28    | [#313](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/pull/313) |
| **bloommcp Phase 2 design doc**                                                  | ⚠ Draft                      | Elizabeth | [#310](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/pull/310) |
| **sleap-roots-contracts pin + drift CI**                                         | ⚠ Draft                      | Elizabeth | [#304](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/pull/304) |

---

## 3. The architecture (as actually designed)

Two routers, two tiers.

 The **top router** picks the domain — `phenotyping`, `scrna`, `analysis`, or `freeform`.

Inside the `analysis` branch, a **second router** picks the operation type — `qc`, `stats`, `dimred_cluster`, `viz`, `correlation`, or `analysis_freeform`. Each leaf sees only its slice of the tool surface.

### How tool surfaces shrink

| Route                         | Tool source                                  | Tool count today | Tool count after leaves land |
| ----------------------------- | -------------------------------------------- | ---------------- | ---------------------------- |
| freeform (today)              | `generic + scrna + cyl + context + 39 MCP` | ~64              | unchanged (kept as fallback) |
| phenotyping                   | `cyl_tools + context_tools`                | —               | ~6-8                         |
| scrna                         | `scrna_tools + context_tools`              | —               | ~4-6                         |
| analysis → qc                | filtered MCP (qc + outlier + list_existing)  | —               | **12** (PR #203)       |
| analysis → stats             | filtered MCP (stats family)                  | —               | ~5-7 (TBD)                   |
| analysis → dimred_cluster    | filtered MCP (PCA + clustering)              | —               | ~6-8 (TBD)                   |
| analysis → viz               | filtered MCP (plot tools)                    | —               | ~5-7 (TBD)                   |
| analysis → correlation       | filtered MCP (correlation_tools.*)           | —               | ~8 (TBD)                     |
| analysis → analysis_freeform | all MCP                                      | —               | ~39 (no-wedge fallback)      |

**Net effect at full rollout:** the LLM picks from 5-12 tools on a typical request, 25 on freeform fallback — instead of 25 on every request today.

---

## 4. What's shipped (foundation)

These have already merged to `staging` or `main`.

| Commit / PR                                                                    | What it shipped                                                                                              | Key file                                                                                      |
| ------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------- |
| `feat(agent): wrap create_react_agent in explicit StateGraph`                | Typed `AgentState`, parent graph, freeform leaf                                                            | [agent.py:359-413](../../langchain/agent.py#L359), [graph/state.py](../../langchain/graph/state.py) |
| `feat(agent): deterministic context-loader node before any LLM call` (#200)  | Tool-set + system-message injection before any LLM call                                                      | [graph/context_loader.py](../../langchain/graph/context_loader.py)                               |
| `feat(agent): top-level router with single function call fallback` (#201)    | `top_router` node, `RouteDecision` Pydantic model with structured output, 4-way fallback to `freeform` | [graph/router.py](../../langchain/graph/router.py)                                               |
| `fix(agent): hide router's classification stream from chat UI`               | First-cut classifier-token suppression in SSE                                                                | (superseded by #210)                                                                          |
| `tune(agent): canonical token counter + 70%-window threshold for Qwen3.5-9B` | Token counting fix relevant to small-model context windows                                                   | [agent.py token logic](../../langchain/agent.py)                                                 |
| `fix(agent): single merged SystemMessage` (#195)                             | Qwen3.5-9B doesn't tolerate multiple system messages — merge them                                           | [agent.py](../../langchain/agent.py)                                                             |

**Architectural invariant established:** every route currently dispatches to `freeform` ([agent.py:407-411](../../langchain/agent.py#L407)) — wiring is real, behavior is unchanged for users until leaves land. This is the **no-wedge guarantee**: any leaf can fall back to `freeform` and the system still works.

**Already-shipped bloommcp dependency:** `qc_leaf` consumes the versioned-storage layer + `list_existing_analyses` MCP tool from bloommcp Storage Phase A + B-1 (PRs #198, #199 — merged). No agent-side blocker here.

---

## 5. What's in flight (draft PRs)

### Critical-path stack

The leaf rollout is built bottom-up as a PR stack. Stack base is `feat/agent-top-router`.

```
staging
   └── feat/agent-top-router  (#201, merged)
         ├── #207  fix(agent): top-router prompt — wave-comparison routing
         └── feat/agent-analysis-router (#202)  ← analysis subgraph scaffold
               └── feat/agent-qc-leaf (#203)    ← first Tier 3 leaf
```

| PR                                                                                 | What it does                                                                                                                                                                                                              | Files          | Test coverage                           | Blocker for                                         |
| ---------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------- | --------------------------------------- | --------------------------------------------------- |
| **[#207](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/pull/207)** | Sharpens `top_router` prompt: single Supabase query → `phenotyping`; load-dataframe-and-compute → `analysis`. Adds boundary-case few-shots.                                                                       | 11             | —                                      | None (independent fix); merge first to unblock #202 |
| **[#202](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/pull/202)** | New `analysis_subgraph` node containing `analysis_router` + `analysis_freeform` fallback. Every analysis sub-route initially dispatches to `analysis_freeform`. Wiring only — no leaf added yet.                 | 15 (+1111/-21) | top_router (7), analysis_router (11)    | All Tier 3 leaves                                   |
| **[#203](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/pull/203)** | `qc_leaf` — 12 tools (6 qc + 5 outlier + `list_existing_analyses`). Replaces `"qc" → analysis_freeform` with `"qc" → qc_leaf`. One-line edit to dispatch dict. Specialized prompt with 4 data-integrity rules. | 5 (+336/-2)    | qc_leaf (9) — 32/32 green when stacked | Parallel-recipes demo                               |
| **[#208](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/pull/208)** | `ask_user(question)` tool + `interrupt()` + `POST /langchain/chat/resume` endpoint + `post_model_hook` safety net catching empty AIMessages. Eliminates "silent termination."                                     | Multiple       | TBD                                     | UI handler (#209)                                   |
| **[#209](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/pull/209)** | Web UI handler: SSE `ask_user` event → render question → route next message to `/chat/resume`. Pending-clarification state, placeholder swap.                                                                       | Web app        | TBD                                     | Visible HITL UX                                     |
| **[#210](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/pull/210)** | Filter `top_router` + `analysis_router` from `on_chat_model_stream` so classifier JSON (`{"route":"freeform"}`) stops leaking into the bot bubble. Denylist not allowlist — future leaves stream by default.     | 2-3            | Local smoke vs Spark                    | Clean UX once #202 ships                            |

### Merge order recommendation

1. **#210** — independent SSE bug fix; can merge now. Unblocks clean test-output reading on every other PR.
2. **#207** — top-router prompt fix; independent. Catches existing routing bug; ship before #202 so I'm not chaining a known-bad classifier.
3. **#202** — analysis subgraph + sub-router scaffold. After this, every leaf is a one-line dispatch-dict edit.
4. **#203** — qc_leaf. First specialized leaf. Proves the leaf pattern works end-to-end.
5. **#208 + #209** — HITL stack. Independent of the routing track but should land before the next leaves so unanswered prompts don't fall into silent termination.

---

## 6. What's left to build

### Bins for the §9 partition (classification intent for Phase 2 wrappers)

The four analysis sub-routes (`stats`, `dimred_cluster`, `viz`, `correlation`) no longer ship as standalone leaf PRs.

As Phase 2's `sleap-roots-analyze`-backed wrappers land (Tier 3 `pca_analysis`, Tier 4 `clustering`, then the rest), I will classify each new tool into one of these bins inside `bloom_mcp/tool_groups.py` — see §9 for the partition PR itself.

The table below records the **classification intent** for each bin. Legacy candidate tools are listed for reference only; the actual bin contents are finalized as the new Phase 2 tool names land.

| Bin                | Operation type                              | Legacy candidates (earmarked for retirement)       |
| ------------------ | ------------------------------------------- | -------------------------------------------------- |
| `qc`             | Data cleaning + outlier detection / removal | `qc_tools` + `outlier_tools`                   |
| `stats`          | Descriptive + inferential statistics        | `stats_tools` (descriptive, ANOVA, heritability) |
| `dimred_cluster` | Dimensionality reduction + clustering       | `dimred_tools` + `clustering_tools`            |
| `viz`            | Visualization                               | `viz_tools` (plots)                              |
| `correlation`    | Cross-experiment correlation                | `correlation_tools`                              |

### Top-level domain leaves (2 PRs)

These promote the `phenotyping` and `scrna` routes from "dispatch to freeform" to real specialized leaves. Smaller scope than analysis sub-leaves — just native tools, no bloommcp MCP filtering.

| Leaf                     | Tool source                                      | OpenSpec slug                  |
| ------------------------ | ------------------------------------------------ | ------------------------------ |
| `phenotyping_subgraph` | genomic data + vectordb-based tools (~6-8 tools) | `add-phenotyping-leaf` (TBD) |
| `scrna_subgraph`       | `scrna_tools + context_tools` (~4-6 tools)     | `add-scrna-leaf` (TBD)       |

---

## 7. Merge Plan for Benfica

Tasks ordered by what unblocks the most downstream work and what can run in parallel.

### Now — independent fixes (not affected by any proposals)

- [ ] **Merge #210** (SSE classifier-token filter). Independent fix, no dependencies. Unblocks clean test reading for every other PR.
- [ ] **Merge #207** (top-router prompt fix). Independent. Catches an existing bug; ship before #202.

### Next — critical-path leaf rollout

- [ ] **Merge #202** (analysis subgraph + sub-router). After this, every leaf is a one-line edit.
- [ ] **Merge #203** (qc_leaf). First specialized leaf. Proves the pattern.
- [ ] **Merge #208 + #209** (HITL stack). Should land before more leaves so silent-termination is closed off.

### Parallel — analysis bins (folded into §9)

The four analysis bins (`stats`, `dimred_cluster`, `viz`, `correlation`) no longer ship as separate leaf PRs. They are folded into the §9 PR — the partition + per-leaf MCP entry points get built once Phase 2's wrappers land. See §9 for the rollout shape and the bloommcp-side asks.

### Then — top-level domain leaves

- [ ] **`genomic_subgraph`** — owner: TBD
- [ ] **`scrna_subgraph`** — owner: TBD

---

## 8. Planned PR — Move tool buckets into bloommcp

**Goal:** define the bucket partition (`qc`, `stats`, `dimred_cluster`, `viz`, `correlation`) inside bloommcp itself, so the same partition serves **both**:

- the **langchain agent** (today's bloom-agent product), and
- any **direct Claude / MCP client** (Claude Desktop, future external users).

**Can ship now — does not block on Phase 2.**

### Why

Today, each langchain leaf carries its own bucket definition (a `frozenset` of tool names). That works for the bloom-agent product, but it has three problems:

1. **Claude Desktop sees all 39 tools flat** — external MCP clients get no bucket benefit at all.
2. **The bucket and bloommcp can drift apart** — if bloommcp renames a tool, langchain's bucket silently breaks at runtime.
3. **No CI cross-check** that the buckets and bloommcp's actual tool inventory agree.

Moving the buckets into bloommcp fixes all three with one PR.

### Why this can ship now (not gated on Phase 2)

The bucket definitions are about **tool names**, not implementations. I can ship the partition with **today's** tool names (`detect_outliers_mahalanobis`, `run_pca`, `clean_experiment_data`, etc.). When Phase 2 wrappers land (Tier 3 `pca_analysis`, Tier 4 `clustering`), I just add the new names to the relevant bucket — a one-line edit per name.

**The partition concept is permanent; bucket contents evolve.** So this PR is not gated on Elizabeth's Phase 2 timing.

### What changes

**In bloommcp** (3 small additions + 1 modification):

| File                         | Status        | What it does                                                             |
| ---------------------------- | ------------- | ------------------------------------------------------------------------ |
| `bloom_mcp/tool_groups.py` | **NEW** | The 5 bucket constants as `frozenset` of tool names                    |
| `bloom_mcp/cli.py`         | **NEW** | Per-bucket entry-point dispatchers                                       |
| `pyproject.toml`           | modified      | Registers `bloom-mcp-qc`, `bloom-mcp-stats`, etc. as console scripts |
| `server.py`                | modified      | Accepts an `allowed_tool_names` filter for filtered registration       |

**In langchain** (one-line change per leaf):

Each leaf factory in `langchain/graph/leaves/*.py` swaps its hardcoded `frozenset` for `from bloom_mcp.tool_groups import <NAME>_LEAF_TOOL_NAMES`. The factory becomes a 3-line import + filter. Truth lives in bloommcp.

### What this enables

| Consumer                                       | Today                                   | After this PR                                                       |
| ---------------------------------------------- | --------------------------------------- | ------------------------------------------------------------------- |
| **bloom-agent product** (Qwen via /chat) | Hardcoded allow-list per leaf           | Imports canonical buckets — same behavior, no drift risk           |
| **Claude Desktop user**                  | Sees all 39 tools flat                  | Mounts `bloom-mcp-qc` (~12 tools), `bloom-mcp-stats` (~5), etc. |
| **Tests / CI**                           | Can't verify bucket ⊆ registered tools | Single set comparison catches drift on every PR                     |

### Dependencies

- ✓ **Not gated on bloommcp Phase 2** — bucket NAMES are stable; bucket CONTENTS evolve as new wrappers land
- **Sequencing:** I'll ship these PRs first before working on §8, so the partition is proven in-product before going external:
   - **#210** — SSE classifier-token filter (ready to merge)
   - **#207** — top-router prompt fix
   - **#202** — analysis subgraph + sub-router
   - **#203** — `qc_leaf` (the first real leaf using a bucket allow-list)
   - **#208 + #209** — `ask_user` HITL stack
- ✓ Independent of top-level domain leaves (`genomic_subgraph` / `scrna_subgraph`) — those use native langchain tools

### Out of scope (explicit)

- **Retiring legacy bloommcp modules** (`outlier_detection.py`, etc.) — bloommcp side's call, gated on this work
- **Splitting bloommcp into multiple Docker containers** — the entry-point pattern works without container split
- **Publishing bloommcp to PyPI** — internal git-install via `uv add git+...` is fine

## Appendix: references

- **Anthropic published guidance**:
- - [Advanced tool use](https://www.anthropic.com/engineering/advanced-tool-use) — 10+ tool threshold, Tool Search Tool, MCP accuracy numbers
  - [Writing effective tools for AI agents](https://www.anthropic.com/engineering/writing-tools-for-agents) — "more tools don't always lead to better outcomes"
- **bloommcp Phase 2**:

  - [Elizabeth&#39;s Phase 2 design doc PR #310](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/pull/310)
  - [egao28&#39;s Tier 0 baseline PR #313](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/pull/313)
  - [Elizabeth&#39;s contracts pin PR #304](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/pull/304)
