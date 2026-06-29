---
title: bloom-mcp — splitting the server into per-owner sub-servers
date: 2026-06-29
status: proposal — pending Elizabeth review
author: Benfica
scope: bloommcp server layout only — no change to the tool contract, persistence, or analyze delegation
---

# bloom-mcp — per-owner sub-servers

## 1. Goal

Split the bloom-mcp server into sections, one per owner, so each group of
tools has a clear owner and a clear name. For example:

- `sleap_analyze` — the analyze-backed analysis tools
- `phenotyping_segmentation` — Lin's segmentation functions

It is still **one server, one container, one deploy**. We change two
things: how the tools are *organized and named inside the server*, and we
give each section its own URL so a Claude Desktop user can load just one
section's tools instead of the whole list.

## 2. What this is NOT

This is **not** the operation-bucket split (`qc` / `stats` / `viz`) we set
aside earlier. That groups tools by *what they do*. This groups tools by
*who owns them*. They are different ideas and do not conflict:

| Grouping | Example sections | Why |
| --- | --- | --- |
| By operation (set aside) | `qc`, `stats`, `viz` | give the LLM a small per-task tool list |
| **By owner (this doc)** | `sleap_analyze`, `phenotyping_segmentation` | clear ownership and naming per tool family |

## 3. Why do this

1. **Clear ownership.** Each owner keeps their own sub-server file. Lin
   can add `phenotyping_segmentation` tools without touching anyone
   else's files, so reviews stay clean.
2. **Easy to read.** Each tool name starts with its section
   (`sleap_analyze_*`, `phenotyping_segmentation_*`), so anyone — a person
   or the LLM — can see which family a tool belongs to.
3. **Load one section in Claude Desktop.** Each section gets its own URL,
   so a Claude Desktop user can connect to just one section — e.g. only
   the segmentation tools — instead of the whole list. See §4.3.

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

mcp.mount("sleap_analyze", sleap_analyze_mcp)
mcp.mount("phenotyping_segmentation", seg_mcp)
```

Each section's tools show up with the section name in front —
`sleap_analyze_<name>`, `phenotyping_segmentation_<name>`. This is the
combined surface the agent uses.

### 4.3 A separate URL per section (so Claude can load one section)

A single URL gives a client the whole tool list at once. To let a Claude
Desktop user load just one section, we give each sub-server its own path
on the **same** server:

```python
from starlette.applications import Starlette
from starlette.routing import Mount

combined = mcp.http_app(path="/mcp")          # everything — the agent uses this
analyze  = sleap_analyze_mcp.http_app(path="/mcp")
seg      = seg_mcp.http_app(path="/mcp")

app = Starlette(
    routes=[
        Mount("/all", app=combined),
        Mount("/sleap_analyze", app=analyze),
        Mount("/phenotyping_segmentation", app=seg),
    ],
    lifespan=combined.lifespan,
)
# same container, one port
```

- The agent connects to `/all/mcp` and gets every section.
- A Claude Desktop user adds `/phenotyping_segmentation/mcp` as a
  connector to load only the segmentation tools. Each section URL is a
  separate connector they can turn on or off.

## 5. What stays the same

- One server, one container, one deploy.
- Same transport (`streamable-http`), same server key (`bloom-tools`),
  same `BLOOM_MCP_URL`, same `BLOOMMCP_API_KEY` auth.
- The agent still finds tools the same way
  (`MultiServerMCPClient(...).get_tools()`); it just sees the new names.
- No change to the tool contract, persistence, or analyze delegation.

## 6. What changes in the code

| File | Change |
| --- | --- |
| `bloom_mcp/server.py` | build each section with `mcp.mount(...)`, then serve them through a small Starlette app with one path per section (`/all`, `/sleap_analyze`, `/phenotyping_segmentation`) |
| `bloom_mcp/sections/*.py` | one file per section, each owning its sub-server and tools |

## 7. Questions for review

1. **Naming:** are `sleap_analyze` and `phenotyping_segmentation` the
   right first two sections?,(answer if this plan is approved.)
3. **Paths:** OK with `/all`, `/sleap_analyze`, `/phenotyping_segmentation`
   as the section URLs?(answer if this plan is approved.)
5. **Phase 2:** this is scoped to server layout only — does the `mount()`
   approach clash with anything in your Phase 2 direction?
