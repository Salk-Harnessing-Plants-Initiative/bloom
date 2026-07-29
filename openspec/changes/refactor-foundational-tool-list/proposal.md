## Why

Two hand-authored lists in two languages name the same 3 foundational MCP tools
(`list_available_experiments`, `load_experiment_data`, `list_existing_analyses`), and
nothing but a static cross-repo test keeps them from disagreeing with each other:

1. `ALWAYS_INCLUDE_MCP_TOOLS` (`langchain/routes/chat.py:24-28`) — forces these tools into
   the agent's toolset regardless of `tool_set`/`mcp_tool_names` routing, via the
   prefix-aware `_is_always_included()` helper (`chat.py:31-34`).
2. `HIDDEN_TOOLS` (`web/components/mcp-chat-client.tsx:266-270`) — hides the same tools from
   the tool-picker UI, via an equivalent prefix-aware `isHidden()` helper
   (`mcp-chat-client.tsx:271-273`).

`bloommcp/tests/test_devendor_invariants.py`'s `test_tool_name_lists_match_live_registry`
(line 350) statically parses both lists (`_parse_always_include_mcp_tools` at line 315,
`_parse_hidden_tools` at line 335) and checks each, independently, against the live mounted
tool registry. That guard catches either list going stale (naming a retired tool) — it does
**not** assert the two lists are equal to each other, and (confirmed during proposal review)
would not catch either list being renamed or its literal tool-name strings being moved into a
new identifier — so a future edit to one (e.g. adding a newly-added foundational tool to
`ALWAYS_INCLUDE_MCP_TOOLS` but forgetting `HIDDEN_TOOLS`) would pass CI while silently
desyncing backend inclusion from frontend visibility.

The frontend already fetches the tool list from the backend's own `GET /langchain/mcp-tools`
endpoint (`langchain/server.py:208-216`, imported as `chat_routes` at `server.py:33`). That
response can carry foundational status directly, making `HIDDEN_TOOLS` an unnecessary second
copy of information the backend already computes.

Per GitHub issue #485, ask #3 — rewriting `CONTEXT_MCP` (`langchain/tools/context_tools.py`)
to stop hand-listing retired workflow/correlation tools — is **already done** on `staging`
(confirmed while researching this proposal, and independently re-confirmed during review):
`CONTEXT_MCP` (line 85) now reads "Every other MCP tool ... is discovered dynamically from
the live tool registry — do not rely on a hand-listed catalog here," with no
`run_*_workflow`, `inspect_data_quality`, or `correlation_tools`/`viz_tools` references.
`inspect_data_quality` has also already been dropped from both `ALWAYS_INCLUDE_MCP_TOOLS` and
`HIDDEN_TOOLS`. Ask #3 also floats a softer, optional suggestion — deriving `CONTEXT_MCP`'s
tool descriptions from the live registry's own `description` fields instead of the
hand-written strings that remain there today — which is **not** done and is **not** in this
proposal's scope either; task 4.1 below tracks it as a follow-up so it isn't silently lost
when #485 closes. This proposal's scope is issue #485's asks #1 and #2: expose a
`foundational` field on `/langchain/mcp-tools` and delete `HIDDEN_TOOLS` in favor of it.

**Sequencing note:** `devendor-bloommcp-analysis` (43/53 tasks — all C-series/P-series/P3-series
code tasks complete; what remains is Phase 0's `0.1` (an external sign-off ping to the
package's other maintainer) and `0.3` (a historical note whose actual fix already landed as
`P2.0`, checked), plus the `V.1`-`V.8` validation/CI-gate checklist) already specifies and
ships today's two-hand-list-plus-drift-guard arrangement as its intended design, in
`bloommcp-tool-sections/spec.md`'s "Always-included selection tracks the core tools'
registered names" and "The web client's hidden-tools list and the routing prompt stay in
sync with namespacing" scenarios. This proposal goes further than that design — collapsing
the second hand-list entirely rather than merely drift-guarding it — which directly revises
behavior that change specifies. `bloommcp-tool-sections` is not yet archived into
`openspec/specs/`, so there is no archived base to write a `MODIFIED Requirements` delta
against yet; this proposal's delta is written as `ADDED Requirements` instead, with no
in-spec cross-reference to the other change (that provenance note lives here, not in the
archived requirement text). Task 4.5 makes reconciliation a **hard pre-merge gate**, not an
advisory note: if `devendor-bloommcp-analysis` archives first, its superseded "hidden-tools"
scenario must be manually removed/rewritten from the archived `bloommcp-tool-sections` spec
as part of *this* change's own archive step, so the two never coexist as contradictory
requirements in `openspec/specs/`.

## What Changes

- `langchain/helpers/foundational_tools.py` (**new**) becomes the single source of truth:
  `ALWAYS_INCLUDE_MCP_TOOLS` and a public `is_foundational_tool()` move here from
  `langchain/routes/chat.py` (same set, same prefix-matching logic, no behavior change),
  alongside the existing `db_url.py`/`plot_renderer.py`/`sse_events.py`/
  `trait_name_resolver.py` shared-helpers convention. `routes/chat.py` imports from it instead
  of defining it.
- `langchain/server.py`'s `GET /langchain/mcp-tools` handler (`get_mcp_tools`, line 209) adds
  a `foundational: bool` field to each returned tool object, computed via
  `is_foundational_tool()` from the new helpers module — not by reaching into another route
  module's private internals. A small `MCPToolInfo`/`MCPToolsResponse` pair is added to
  `langchain/schemas.py` (matching the existing `ModelsResponse` pattern) as the endpoint's
  `response_model`, so the new field's presence/type is enforced by FastAPI itself.
- Minimal pytest infrastructure is added to `langchain/` (currently has none): a `test` extra
  in `langchain/pyproject.toml`, and `langchain/tests/` with a fixture-backed test proving the
  new field's values. This is a new local-only gate (not wired into CI in this change — see
  task 1.6).
- `web/components/mcp-chat-client.tsx` deletes `HIDDEN_TOOLS` and `isHidden()`; the tool
  picker's filter is extracted into a small pure function in a new
  `web/components/mcp-chat-client.helpers.ts` (following the existing
  `best-match-sort.ts`/`.test.ts` colocated-pure-function convention), unit-tested with
  Vitest (already wired in `web/`, zero new dependencies). The `MCPTool` interface
  (`mcp-chat-client.tsx:24-27`) gains `foundational: boolean`.
- `bloommcp/tests/test_devendor_invariants.py`'s `test_tool_name_lists_match_live_registry`
  drops `_parse_hidden_tools()` and the `hidden_tools` half of the check — `HIDDEN_TOOLS` no
  longer exists to parse. The guard becomes single-sided: `ALWAYS_INCLUDE_MCP_TOOLS` against
  the live registry, since it's the only hand-list left. A new, **content-based** (not just
  identifier-name) test asserts no array/Set/object literal in `mcp-chat-client.tsx` contains
  all three foundational tool-name strings together, so a future regression (someone
  reintroducing the duplication under a different name) fails loudly instead of slipping past
  a narrower "is `HIDDEN_TOOLS` still called `HIDDEN_TOOLS`" check.

Out of scope (already resolved on `staging` before this proposal): rewriting `CONTEXT_MCP`'s
prose to stop hand-listing retired tools; dropping `inspect_data_quality` from either list.
Tracked as a follow-up, not silently dropped: deriving `CONTEXT_MCP`'s tool descriptions from
the live registry (task 4.1).

## Impact

- Affected specs: `bloommcp-tool-sections` (adds a requirement that supersedes the
  not-yet-archived "hidden-tools list ... stay in sync" scenario from
  `devendor-bloommcp-analysis` — see Sequencing note above and the hard gate at task 4.5)
- Affected code: `langchain/helpers/foundational_tools.py` (new), `langchain/routes/chat.py`,
  `langchain/server.py`, `langchain/schemas.py`, `langchain/pyproject.toml`,
  `langchain/tests/` (new), `web/components/mcp-chat-client.tsx`,
  `web/components/mcp-chat-client.helpers.ts` (new, with its test),
  `bloommcp/tests/test_devendor_invariants.py`
- Confirmed during review: `web/components/mcp-chat-client.tsx:254` is the only consumer of
  `GET /langchain/mcp-tools` in the repo — no other caller needs updating.
