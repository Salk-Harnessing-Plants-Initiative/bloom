---
title: bloom-mcp — splitting the server into per-package sub-servers
date: 2026-06-29
status: proposal — direction approved by Elizabeth (PR #362); naming settled (by package)
author: Benfica
scope: bloommcp server layout only — no change to the tool contract, persistence, or analyze delegation
---

# bloom-mcp — per-package sub-servers

## 1. Goal

Split the bloom-mcp server into sections, one per **package**, so each group
of tools maps to the package it wraps and has a clear owner. For example:

- `sleap_roots_analyze` — tools wrapping the `sleap-roots-analyze` package
- `phenotyping_segmentation` — Lin's segmentation package

It is still **one server, one container, one deploy**. We change two things:
how the tools are *organized and named inside the server*, and we give each
section its own URL so a Claude Desktop user can load just one section's
tools instead of the whole list.

**Naming: by package, not owner.** Dev-separation still comes from each owner
having their own section file — but the section *name* follows the package
it wraps. Tool names are our permanent wire contract, so a package name stays
stable when a tool changes hands, matches the pinned dependency, and is the
natural unit for a future autopop generator (one package in → one section
out). `sleap_roots_analyze` also disambiguates from the separate
`sleap-roots` (trait extraction) package, which may get its own section later.

## 2. What this is NOT

This is **not** the operation-bucket split (`qc` / `stats` / `viz`) we set
aside earlier. That groups tools by *what they do*. This groups tools by
*the package they come from*. They are different ideas and do not conflict:

| Grouping | Example sections | Why |
| --- | --- | --- |
| By operation (set aside) | `qc`, `stats`, `viz` | give the LLM a small per-task tool list |
| **By package (this doc)** | `sleap_roots_analyze`, `phenotyping_segmentation` | stable naming + clear ownership per package |

## 3. Why do this

1. **Clear ownership.** Each owner keeps their own section file. Lin can add
   `phenotyping_segmentation` tools without touching anyone else's files, so
   reviews stay clean.
2. **Easy to read.** Each tool name starts with its section
   (`sleap_roots_analyze_*`, `phenotyping_segmentation_*`), so anyone — a
   person or the LLM — can see which package a tool belongs to.
3. **Load one section in Claude Desktop.** Each section gets its own URL, so a
   Claude Desktop user can connect to just one section — e.g. only the
   segmentation tools — instead of the whole list. See §4.3.

## 4. How it works

### 4.1 Each section is its own small server

```python
# bloom_mcp/sections/phenotyping_segmentation.py  — Lin owns this file
seg_mcp = FastMCP("phenotyping-segmentation")

@seg_mcp.tool
def segment_plate(...): ...
```

### 4.2 The main server pulls them together with `mount()`

```python
# bloom_mcp/server.py
mcp = FastMCP("bloom-tools", auth=auth_provider)

mcp.mount("sleap_roots_analyze", sleap_roots_analyze_mcp)
mcp.mount("phenotyping_segmentation", seg_mcp)
```

Each section's tools show up with the section name in front —
`sleap_roots_analyze_<name>`, `phenotyping_segmentation_<name>`. This is the
combined surface the agent uses.

### 4.3 A separate URL per section (so Claude can load one section)

A single URL gives a client the whole tool list at once. To let a Claude
Desktop user load just one section, we give each sub-server its own path on
the **same** server:

```python
from starlette.applications import Starlette
from starlette.routing import Mount

combined = mcp.http_app(path="/mcp")                       # everything — the agent uses this
analyze  = sleap_roots_analyze_mcp.http_app(path="/mcp")
seg      = seg_mcp.http_app(path="/mcp")

app = Starlette(
    routes=[
        Mount("/all", app=combined),
        Mount("/sleap_roots_analyze", app=analyze),
        Mount("/phenotyping_segmentation", app=seg),
    ],
    lifespan=combined.lifespan,
)
# same container, one port
```

- The agent connects to `/all/mcp` and gets every section.
- A Claude Desktop user adds `/phenotyping_segmentation/mcp` as a connector to
  load only the segmentation tools. Each section URL is a separate connector
  they can turn on or off.

## 5. Shared contract — the real guarantee

The section boundary is **organizational, not a second quality bar.** Every
section's tools — whoever wrote them — go through the same shared contract:

- the `@as_mcp_tool` decorator, which stamps provenance, maps errors to
  `BloomMCPError`, and propagates the seed (#306, merged);
- the `ExperimentReader` / `ResultStore` persistence ports (#307, merged).

So when Lin's segmentation tools arrive, they **import that same shared
module** — they don't get a private quality bar. The split changes where
tools *live* and how they're *named*, never the contract they meet.

## 6. What stays the same

- One server, one container, one deploy.
- Same transport (`streamable-http`), same server key (`bloom-tools`), same
  `BLOOM_MCP_URL`, same `BLOOMMCP_API_KEY` auth.
- The agent still finds tools the same way
  (`MultiServerMCPClient(...).get_tools()`); it just sees the new names.
- No change to the tool contract, persistence, or analyze delegation.

## 7. What changes in the code

| File | Change |
| --- | --- |
| `bloom_mcp/server.py` | build each section with `mcp.mount(...)`, then serve them through a small Starlette app with one path per section (`/all`, `/sleap_roots_analyze`, `/phenotyping_segmentation`) |
| `bloom_mcp/sections/*.py` | one file per section, each owning its sub-server and tools |
| `pyproject.toml` | each section declares its package as a **pinned dependency** (e.g. `sleap-roots-analyze>=0.1.0a3`) |

**Packaging implication:** a section needs a real package to import. Lin's
segmentation functions must first be released as a proper pip-installable,
pinned package (the same release treatment `sleap-roots-analyze` got) before
the `phenotyping_segmentation` section has anything clean to depend on.

## 8. Sequencing

The restructure rewrites `server.py`, which also registers the in-flight
`qc_clean` tool (#338, PR #356). #356 is currently mid-merge-conflict, so
**land or rebase it first, then restructure** — otherwise `server.py` is a
three-way collision. Not a blocker, just ordering.

## 9. Open questions for review

1. **Naming:** settled — sections are named by package
   (`sleap_roots_analyze`, `phenotyping_segmentation`).
2. **Paths:** OK with `/all`, `/sleap_roots_analyze`,
   `/phenotyping_segmentation` as the section URLs?
3. **Phase 2:** confirmed compatible — this complements the roadmap; the only
   `mount()` there is the deferred URL-namespace-for-versioning (a different
   axis).
