# Bloom Agent Architecture Evolution

A plain-English walkthrough of where the Bloom agent started, where it ends up, and what each step in between actually changes. Written for the May 12 Talmo Lab presentation.

Each section answers: **what does this PR add, what flowed before, what flows after, why does it matter.**

---

## The 30-second version

We're rebuilding the Bloom AI assistant from a single big agent that sees all 74 tools at once into a hierarchical specialist team that routes each question to the right expert. Same data. Same Qwen LLM. Same tools. But the agent gets faster, more accurate, debuggable in real time, and capable of running known recipes in parallel.

The shape of the change:

```
BEFORE:                              AFTER:
                                     
   user                                user
    │                                   │
    ▼                                   ▼
 ┌──────────────────────┐         context_loader
 │ ONE giant agent       │              │
 │ with all 74 tools     │              ▼
 │ + one big prompt      │           top_router
 └──────────────────────┘              │
    │                                  ├── phenotyping_subgraph (~19 tools)
    ▼                                  ├── scrna_subgraph        (11 tools)
   answer                              ├── analysis_subgraph
                                       │      │
                                       │      ▼
                                       │   analysis_router
                                       │      ├── qc_leaf      (11)
                                       │      ├── stats_leaf   (5)
                                       │      ├── dimred_cluster (8)
                                       │      ├── viz_leaf     (7)
                                       │      ├── correlation  (8)
                                       │      └── analysis_freeform (39)
                                       └── freeform_subgraph (74, fallback)
```

Same API. Same chat UI. Internally: a clean hierarchy of LLM calls, each focused on one job.

---

## What's wrong with "today" (pre-refactor)

The Bloom agent today is one big LangChain ReAct agent with:
- **74 tools** loaded all at once (35 Supabase API tools + 39 sleap-roots-analyze MCP tools)
- **One giant system prompt** trying to describe everything
- **No routing** — every question reasons over the full 74-tool surface
- **No parallelism** — even when three independent tools could run together (a QC report needs quality + outliers + stats), the agent does them one at a time, three round-trips
- **No automated regression check** — if a prompt change breaks routing, no test catches it

Three concrete failures users actually hit:
1. **Tool confusion** — agent calls a stats tool when the user wanted a clustering tool, because both are vaguely about "analyzing the data"
2. **Slow responses** — ~30 seconds for a multi-step analysis when ~10 seconds is achievable
3. **Black-box debugging** — when an answer is wrong, there's no way to tell *why* without reading hundreds of log lines

---

## How to read this document

Every section follows the same shape:

> **What this PR adds:** one-sentence summary
>
> **Before:** what flowed before this PR
>
> **After:** what flows after
>
> **Why it matters:** the user-visible win

Tiers are sequential. Each tier preserves backward compatibility: at every step, a question that worked yesterday still works today, just routed through more named nodes.

---

# Tier 0 — Foundation (the plumbing)

The first two PRs introduce the *structure* of the new architecture without changing any *behavior*. Every question still ends up in a `freeform` leaf that's identical to today's agent. We're just making the structure visible and renamable.

## PR 1 — `add-stategraph-foundation`

**What it adds:** Replace the opaque "ReAct agent" with an explicit graph that has named nodes.

**Before:**
```
agent.ainvoke(messages) ──► [BLACK BOX] ──► answer
```
We pass messages in, get an answer out. Whatever happens inside is invisible. We can't add steps, can't observe transitions, can't replace pieces.

**After:**
```
START ──► freeform_subgraph ──► END
```
Same behavior. But now `freeform_subgraph` is a *named node* in a *named graph*. Every following PR adds more nodes between `START` and `freeform_subgraph`.

**Why it matters:** No user-visible change yet. But this is the foundation — every subsequent PR plugs into this graph.

### Implementation notes (what actually shipped)

**Branch:** `feat/agent-stategraph-foundation` (off `origin/staging`)

Three new files + one modified function:

| File | Lines | Purpose |
|---|---|---|
| `langchain/graph/__init__.py` | 9 | Package marker |
| `langchain/graph/state.py` | ~45 | `AgentState` TypedDict — `messages` with `add_messages` reducer + reserved `route` / `analysis_route` fields for next tiers |
| `langchain/graph/freeform.py` | ~55 | `build_freeform_subgraph(...)` factory that wraps the existing prebuilt ReAct verbatim |
| `langchain/agent.py::create_agent` | small edit | Returns a compiled `StateGraph` (with the freeform subgraph as its single node) instead of the raw prebuilt |

**Key design decisions for this PR:**

- **Behavior parity is byte-for-byte.** Same `SYSTEM_PROMPT`, same `tools`, same `pre_model_hook`. The only addition is a `StateGraph(AgentState)` wrapper with one node and edges `START → freeform → END`. The wrapper does nothing; it just gives subsequent PRs somewhere to attach.
- **Checkpointer lives on the OUTER graph.** `builder.compile(checkpointer=checkpointer)`. The inner freeform subgraph receives `checkpointer=None` so the parent's `messages` reducer is the canonical source of thread state. (Tradeoff documented inline: mid-subgraph time-travel debugging is lost vs. single-source thread state — we chose the latter for simplicity.)
- **`add_messages` reducer is critical.** Without it, every node return overwrites the message list and history is lost. State schema declares `messages: Annotated[list[BaseMessage], add_messages]`.
- **Reserved router fields are typed but unused.** `route: Optional[Literal["phenotyping","scrna","analysis","freeform"]]` and `analysis_route: Optional[Literal["qc","stats","dimred_cluster","viz","correlation","analysis_freeform"]]` are declared in the state schema so PR 3 (top router) and PR 6 (analysis router) can write to them without altering the schema.
- **Public `create_agent(...)` signature unchanged.** Callers (`langchain/routes/chat.py`, the LRU agent cache in `langchain/deps.py`) work without modification.

### Smoke test confirmation

After the refactor (agent restarted in dev compose):
```
GET /health → {"status":"ok","checkpointer":"postgres","mcp_tools":39}
agent logs   → "Application startup complete."
```

Pre-refactor and post-refactor health checks return identical JSON. The 39 MCP tools load identically.

### What this unlocks

- PR 2 (`add-context-loader-node`) inserts a node between `START` and `freeform`
- PR 3 (`add-top-router-with-fallback`) inserts a router that picks one of four destinations
- PR 4-11 replace the placeholder destinations with real specialist subgraphs
- PR 12 (`add-parallel-recipes`) adds `Send`-based fan-out inside specific leaves

None of these are possible without a real `StateGraph` to attach to.

### Talking points for May 12

- *"This is the plumbing. We swapped one black-box agent for a one-node graph that does exactly the same thing — but the graph is an open container. The next thirteen PRs all snap into this container."*
- *"No user sees a difference today. But this is what makes the parallel QC report demo possible in PR 12."*

---

## PR 2 — `add-context-loader-node`

**What it adds:** Replace a brittle "remember to call get_agent_context" string instruction with a deterministic node that always runs first.

**Before:**
The system prompt says *"Remember: call get_agent_context to learn about available data sources before answering."* The LLM may or may not comply. When it doesn't, the agent answers from stale assumptions and gets things wrong.

**After:**
```
START ──► context_loader ──► freeform_subgraph ──► END
```
The `context_loader` node ALWAYS runs first. It calls `get_agent_context` deterministically and injects the available datasets/schemas into the conversation. The LLM never has to "remember."

**Why it matters:** No more "the agent didn't know we had that dataset" failures. Predictable behavior on the first turn of every conversation.

---

# Tier 1 — Routing scaffold

## PR 3 — `add-top-router-with-fallback`

**What it adds:** A small, fast LLM call that classifies every question into one of four buckets before the heavy thinking starts.

**Before:** Every question goes straight to one big LLM that has to decide what kind of question it is AND how to answer it AND which of 74 tools to use, all in one go. That's three jobs at once on the most expensive part of the run.

**After:**
```
START ──► context_loader ──► top_router ──► [4 destinations] ──► END
```
The `top_router` node looks at the user's message and emits exactly one of: `phenotyping`, `scrna`, `analysis`, `freeform`. Specialized handling can now happen per category (later PRs land the actual specialists).

**Right now**, all four destinations still point at `freeform_subgraph` — the router classifies but every classification still ends up in the same place. **No user-visible change yet.**

**Why it matters:** This is the wiring for everything that follows. Once specialists exist, the router knows where to send each question.

> *Demo moment for May 12:* Show LangSmith trace with two side-by-side runs — one says `routed to: phenotyping`, the other says `routed to: analysis`. Audience instantly understands: *"Oh, the agent picks a category before it picks a tool."*

---

# Tier 2 — Domain specialists

This is where users start to feel the difference. The router now has somewhere to send each category.

## PR 4 — `add-phenotyping-subgraph`

**What it adds:** A specialist agent that ONLY sees the 19 phenotyping tools (plants, scans, accessions, traits) plus a phenotyping-tuned prompt.

**Before:** A question like *"Show me plants in accession Salk-1234"* makes the LLM look at all 74 tools and decide. Sometimes it picks `query_database` and writes a SQL-ish call. Sometimes `list_plants_tool`. Sometimes `get_plants_by_accession_tool` (the actually-correct one). Inconsistent.

**After:** The same question routes to `phenotyping_subgraph`, which only sees ~19 phenotyping tools. The prompt tells the LLM about plants/scans/waves and gives 2-3 example queries. The agent reliably picks `get_plants_by_accession_tool` because it's the obvious choice in a focused tool list.

**Why it matters:** Phenotyping queries become reliable. Less "agent guessed wrong" frustration.

---

## PR 5 — `add-scrna-subgraph`

**What it adds:** A specialist for single-cell RNA questions (datasets, genes, clusters, DE analysis). 11 tools.

**Before:** *"Top DE genes in cluster 3 of dataset 5"* gets reasoned in the same big-tool-soup as phenotyping queries. The agent might confuse "dataset" (scRNA term) with "experiment" (phenotyping term). Slow + brittle.

**After:** Routes to `scrna_subgraph`, scoped to scRNA tools only. The prompt explicitly distinguishes scRNA terminology from phenotyping. Reliable picks.

**Why it matters:** Scientists asking about gene expression get focused, accurate answers.

---

## PR 6 — `add-analysis-router-with-fallback`

**What it adds:** A second-level router *inside* the analysis subgraph, because the MCP analysis pool is too big (39 tools) for one focused agent. Same router pattern as the top one, scoped to analysis sub-categories.

**Before:** All 39 sleap-roots-analyze tools are mixed together in any "analysis" question.

**After:**
```
analysis_subgraph
  │
  ▼
analysis_router  ──► one of: qc | stats | dimred_cluster | viz | correlation | analysis_freeform
```

A QC question goes to `qc`, a stats question to `stats`, etc. Right now these still all dispatch to `analysis_freeform` (which has all 39 tools) — specialists land in Tier 3.

**Why it matters:** Sets up the hierarchy. The flagship demo (parallel QC report in PR 12) requires this routing layer to exist.

---

# Tier 3 — Five MCP analysis specialists

Each of these PRs replaces the placeholder "send everything to analysis_freeform" with a real specialist. They're independent — can ship in any order.

## PR 7 — `add-qc-leaf`

**What it adds:** A specialist for data quality + outlier detection. 11 tools.

**Before:** *"Run a QC report on experiment foo.csv"* lands in `analysis_freeform` with all 39 tools visible. The agent might over-engineer: load → inspect → run PCA → cluster → plot. Way more than asked.

**After:** Routes specifically to `qc_leaf` with only the 11 QC tools. The prompt describes the standard QC workflow (load → inspect_data_quality → detect outliers → optionally clean). The agent does exactly the QC steps, nothing more.

**Why it matters:** QC requests get clean, focused QC outputs. Sets up the parallel recipe in Tier 4.

---

## PR 8 — `add-stats-leaf`

**What it adds:** Statistics specialist (descriptive stats, ANOVA, heritability). 5 tools.

**Before/After:** Same pattern as QC. Stats questions get a stats-focused agent.

**Why it matters:** Heritability and ANOVA results come from a tool surface where the LLM can't accidentally pick a clustering tool instead.

---

## PR 9 — `add-dimred-cluster-leaf`

**What it adds:** Combined dimensionality-reduction + clustering specialist. 8 tools.

**Before:** PCA and clustering tools are mixed with stats and QC. The natural pipeline (run PCA, then cluster on principal components) isn't enforced.

**After:** A specialist whose prompt describes that pipeline explicitly. The LLM follows the natural order.

**Why it matters:** Dim-reduction analyses produce meaningful results because the right method order is built into the prompt.

---

## PR 10 — `add-viz-leaf`

**What it adds:** Visualization specialist. 7 plotting tools.

**Before:** A request like *"plot trait histograms"* could trigger the LLM to compute statistics first (out of habit), wasting time.

**After:** Routes to `viz_leaf` with only plot tools. The agent focuses on choosing the right plot type and returning a PNG path.

**Why it matters:** Plot requests are fast and don't get sidetracked.

---

## PR 11 — `add-correlation-leaf`

**What it adds:** Cross-experiment correlation specialist. 8 tools.

**Before:** Comparing experiments A and B is a multi-step reasoning task buried in `analysis_freeform`.

**After:** Routes to `correlation_leaf`. The prompt describes the standard workflow (list experiments → check power → run correlations → plot). One-step user requests get clean execution.

**Why it matters:** Cross-experiment science gets a reliable workflow. Sets up the parallel correlation matrix in Tier 4.

---

# Tier 4 — The headline (parallel recipes)

This is the slide-worthy moment for the May 12 talk.

## PR 12 — `add-parallel-recipes`

**What it adds:** Three named workflows that fan out independent tool calls in parallel using LangGraph's `Send` API: **QC report**, **viz dashboard**, **cross-experiment correlation matrix**.

**Before — the QC report:**
```
User: "Run a QC report on experiment foo"

Time 0s   ►  agent thinks
Time 2s   ►  call inspect_data_quality(foo)        (4s)
Time 6s   ►  agent thinks
Time 8s   ►  call detect_outliers_pca(foo)         (4s)
Time 12s  ►  agent thinks
Time 14s  ►  call get_trait_statistics(foo)        (4s)
Time 18s  ►  agent thinks
Time 20s  ►  composes answer

Total: ~20 seconds, ~9 LLM round trips
```

**After — same QC report:**
```
User: "Run a QC report on experiment foo"

Time 0s   ►  qc_leaf detects "QC report" trigger
Time 1s   ►  fan out 3 parallel branches:
              ├── inspect_data_quality(foo)        ┐
              ├── detect_outliers_pca(foo)         ├── all running at once
              └── get_trait_statistics(foo)        ┘
Time 5s   ►  all three complete, results joined
Time 6s   ►  agent composes answer

Total: ~6 seconds, 1 LLM round trip + tool dispatch
```

**Why it matters:** **3× faster, 3× fewer tokens.** The audience watches both runs side-by-side in LangSmith. The "after" trace is a wide tree (parallel branches); the "before" trace is a tall stack (sequential). Visual contrast is dramatic.

Same applies to viz dashboard (3 plots in parallel) and cross-experiment correlation (per-experiment stats in parallel).

> *Demo moment for May 12:* This is the headline. Run "QC report" twice — once on the old stack, once on the new — show the LangSmith traces side by side. Token counts and wall-clock both 3×.

---

# Tier 5 — Visibility + safety

The agent works at this point. These two PRs make it auditable and demonstrable.

## PR 13 — `add-eval-suite`

**What it adds:** 15-20 fixed test prompts that assert the right route + the right tools get called. Runs in CI on every PR.

**Before:** No automated test for routing. A change to the router prompt could silently misclassify dozens of user-facing requests, and the team finds out via support tickets.

**After:** Every PR runs the eval suite. If a routing decision regresses on a known case, CI fails the PR with a clear `expected_route: qc, observed: stats` message.

**Why it matters:** Refactor confidence. Proof to Talmo Lab that the system is regression-guarded.

---

## PR 14 — `add-router-trace-ui`

**What it adds:** A small badge in the chat UI that shows the routing decision in real time. *"routed to: analysis → qc"*.

**Before:** User sends a question, watches "Thinking..." spinner, gets an answer. The hierarchical routing happens but is invisible.

**After:** While the agent works, badges appear in the UI showing which subgraph picked up the question. Audience instantly sees the architecture.

**Why it matters:** Demo gold. The audience SEES the routing happen. Free architectural explanation, no slides.

> *Demo moment for May 12:* Send a question, point at the badge: *"That tells you the agent decided this is a QC question, sent it to the QC specialist, which has 11 tools instead of 74."*

---

# What this means for users

After all 15 PRs land:

| Capability | Before | After |
|---|---|---|
| QC report wall-clock | ~20s | ~6s |
| Tool-selection accuracy | Variable | High (focused tool surface per leaf) |
| Wrong-answer debugging | Black box | Visible routing decisions in LangSmith trace |
| New tool added | Goes into the soup | Explicitly assigned to a leaf |
| Regression risk | "Hope the prompt change didn't break anything" | Eval suite catches it on the PR |

**Same questions. Same data. Same Qwen LLM. New routing.** That's the story.

---

# May 12 demo script (suggested)

A 4-minute slot that lands every time:

### Scene 1 — set the bar (45s)
- Open chat UI on the OLD stack (today's monolith)
- Send: *"Run a QC report on experiment foo"*
- Watch tool badges accumulate slowly: `inspect_data_quality, detect_outliers_pca, get_trait_statistics`
- Open LangSmith trace alongside — show tall sequential stack
- Note: ~20s wall-clock, ~3 LLM round-trips
- One slide: *"Three independent operations on the same data — why are we doing them one at a time?"*

### Scene 2 — show the routed agent (60s)
- Switch to the NEW stack (post-refactor)
- Send the SAME question
- Point at the routing badge: *"routed to: analysis → qc"*
- Watch tool badges all appear AT ONCE: `inspect_data_quality, detect_outliers_pca, get_trait_statistics`
- Open LangSmith trace alongside — show wide parallel branches
- Note: ~6s wall-clock, 1 round-trip
- *"Same answer, three times faster. The agent recognized this as a QC report and ran the three independent pieces in parallel."*

### Scene 3 — show the safety brake (45s)
- Send a deliberately ambiguous prompt designed to make the agent loop
- Show LangSmith capturing the runaway pattern
- Note: *"In the OLD stack, this would burn tokens until FastAPI killed the connection at 60+ seconds."*
- *"In the NEW stack, the recursion limit catches the loop at step 25 in ~5 seconds with a clean message to the user."*

### Scene 4 — show observability (30s)
- Open LangSmith Tracing tab
- Click any recent run from the demo
- Walk through the tree: `top_router → analysis_router → qc_leaf → fan-out → answer`
- *"This is the audit trail. Every decision the agent makes is recorded. When a scientist tells us the answer was wrong, we can replay exactly what the LLM saw."*

### Scene 5 — what's next (30s)
- *"Today's demo runs on Qwen 9B locally. The same architecture works for the genomics knowledge graph we're building, the publications RAG, and the next analyses your lab needs."*
- *"Open question for the room: which sleap-roots analyses should land as the next specialist subgraphs?"*

---

# Glossary (in plain words)

- **Agent**: an LLM that can call tools and observe their outputs in a loop until it has an answer
- **Tool**: a function the LLM can call, like `list_plants_tool` or `inspect_data_quality`
- **Subgraph**: a smaller agent specialized for one kind of question
- **Router**: a fast LLM call whose only job is to pick which subgraph should handle a question
- **Leaf subgraph**: a subgraph with no further children — does the actual work
- **Freeform fallback**: a catch-all subgraph with all tools, used when the router is uncertain — preserves the old behavior as a safety net
- **`Send` (parallel API)**: LangGraph's primitive for dispatching multiple tool calls simultaneously
- **Eval suite**: a fixed set of test prompts whose expected behavior we assert in CI
- **State**: the message history + decision metadata flowing through the graph
- **MCP (Model Context Protocol)**: how the LangChain agent talks to the bloommcp server that wraps Elizabeth's sleap-roots-analyze tools

---

*This document is a personal reading aid for the May 12 Talmo Lab presentation. It is not part of any OpenSpec proposal or PR description. Update as PRs land so the "before/after" framing stays current.*
