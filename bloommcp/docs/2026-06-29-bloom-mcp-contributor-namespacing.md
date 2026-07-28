# bloom-mcp contributor namespacing: the `sections/` layout

_Referenced by `bloom_mcp/sections/__init__.py` and `_WIKI/BLOOMMCP/adding-a-section-tool.md`
as "the design doc" — created retroactively by `devendor-bloommcp-analysis` (task 0.2), which
found the reference dangling with no doc behind it._

## Why sections exist

Before sections, every granular tool lived as a loose module under `tools/*.py`, wired into
the combined MCP surface with a per-module `register(mcp)` call hand-added to `server.py`.
That worked while bloom-mcp had one contributor and a handful of tools, but it does not scale
to multiple contributors adding tools independently: every new tool required an edit to the
shared `server.py`, and nothing distinguished "Benfica's phenotyping-segmentation tools" from
"the sleap-roots-analyze wrappers" except a naming convention no tooling enforced.

**A section is a per-contributor (or per-package) FastMCP sub-server.** Each section:

- owns a folder under `bloom_mcp/sections/<name>/`, one file per tool;
- exposes a `section` FastMCP instance from its `__init__.py`;
- is added once to the `SECTIONS` dict in `bloom_mcp/sections/__init__.py` — the *only*
  `server.py`-adjacent wiring a new section needs, and it never changes again as tools are
  added inside that section.

The server (`server.py`) mounts every section into the combined `/mcp` surface (each tool
appears namespaced `<section>_<tool>`, e.g. `phenotyping_segmentation_summarize_trait`) and
also serves each section at its own path (e.g. `/phenotyping_segmentation/mcp`), so a Claude
Desktop user can load just one contributor's tools instead of the whole combined surface.

See [`adding-a-section-tool.md`](../../_WIKI/BLOOMMCP/adding-a-section-tool.md) for the
concrete four-piece recipe (input model, output model, `@as_mcp_tool`-wrapped function,
registration) with a worked example.

## The one-section-per-package convention, and where it bends

The original convention (Benfica's, from the `phenotyping_segmentation` section) is one
section per contributor/package: a section wraps a coherent unit of ownership, not a grab-bag
of unrelated tools. `devendor-bloommcp-analysis` introduces one deliberate exception —
**`sleap_roots` as a family umbrella**, not a single-package section:

- The granular tools that delegate to `sleap-roots-analyze` (`pca_analysis`, `qc_clean`,
  `qc_inspect`, `remove_outliers`, `clustering`, and the 5 surviving plotting tools) all wrap
  the same upstream package, which would normally argue for a `sleap_roots_analyze` section
  name. But the user's mental model is the root-phenotyping *pipeline* as a whole — extraction
  via `sleap-roots` feeding analysis via `sleap-roots-analyze` — so the section is named
  `sleap_roots` and organized with two subgroups:

  ```
  sections/sleap_roots/
  ├── __init__.py            # section sub-server + register()
  ├── analysis/              # wraps sleap-roots-analyze (populated now)
  │   ├── pca_analysis.py  qc_clean.py  qc_inspect.py  remove_outliers.py  clustering.py
  │   └── plot_*.py         # 5 surviving plotting tools, one file each
  └── extraction/            # reserved for future sleap-roots trait-extraction tools (empty)
  ```

  `sleap_roots_traits` was considered and rejected — it collides with the separate
  `sleap-roots-traits` pipeline repository, which these tools do not wrap.

- The cross-cutting discovery tools (`list_available_experiments`, `load_experiment_data`,
  `list_existing_analyses`) are **not** `sleap-roots-analyze` wrappers — they are thin shims
  over `experiment_utils` / the result store. Filing them under `sleap_roots` would misrepresent
  them, so they live in a small `core` section instead, and remain in the agent's
  always-included tool set regardless of namespacing (see the tool-name drift guard,
  `test_tool_name_lists_match_live_registry`, in `bloommcp/tests/test_devendor_invariants.py`).

**If a future contributor's tools don't share this "pipeline family" framing, default back to
one section per package** — the umbrella is the exception for a specific pipeline shape, not a
new blanket convention.

## Consequence for hand-maintained tool-name lists

One list matches MCP tool names by exact string: `ALWAYS_INCLUDE_MCP_TOOLS`
(`langchain/helpers/foundational_tools.py`), consumed by `langchain/routes/chat.py` to always
include these tools in the agent's toolset regardless of routing, and by
`langchain/server.py`'s `GET /langchain/mcp-tools` to mark each tool `foundational: bool` in
its response. The web client (`web/components/mcp-chat-client.tsx`) no longer keeps a second,
independent list (the retired `HIDDEN_TOOLS`) — it filters its tool picker on that
`foundational` field instead (see `refactor-foundational-tool-list`). Section namespacing
changes a tool's *registered* name (e.g. `list_available_experiments` becomes
`core_list_available_experiments`), so the list matches **prefix-aware** (exact name, or
`<anything>_<base name>`) rather than requiring an exact literal — otherwise a namespacing
change would silently empty the always-included/foundational set with no test failure. A
drift-guard test in `bloommcp/tests/test_devendor_invariants.py` parses the list's source and
asserts every name it carries resolves to a live tool in the mounted registry, plus a
content-based check that the web client hasn't reintroduced a hand-list of its own.
