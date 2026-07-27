## 1. Backend: single source of truth + minimal test infra for `langchain/`

`langchain/` currently has zero test infrastructure (no `conftest.py`, no test dependency
group, no CI step runs pytest against it). This section adds the minimal amount needed to make
task 1.2 real, not aspirational.

- [x] 1.1 Add a `test` extra to `langchain/pyproject.toml` (mirrors `bloommcp/pyproject.toml`'s
      `[project.optional-dependencies].test` pattern):
      `pytest>=8.3`, `pytest-asyncio>=0.24` (`httpx>=0.27.0`, needed for `fastapi.testclient`,
      is already a runtime dependency). Add `langchain/tests/__init__.py` and
      `langchain/tests/conftest.py` with a `client` fixture that: sets `JWT_SECRET` to a fixed
      test value (via `monkeypatch.setenv`, before importing `server`/`deps`, since
      `deps.py:22-24` raises at import time if it's unset), imports `server`, constructs
      `fastapi.testclient.TestClient(server.app)`, and overrides
      `app.dependency_overrides[deps.get_current_user]` to return a fixed test user id — no
      real JWT or Supabase connection needed. (Also required, discovered while implementing:
      `BLOOM_PLOTS_DIR` — `server.py` calls `os.makedirs(PLOTS_DIR)` at *import* time and its
      default `/app/data/PLOTS_DIR` isn't writable outside a container; the fixture points it
      at `tmp_path` instead. `langchain/uv.lock` re-synced; `scripts/check-uv-locks.py` green.)
- [x] 1.2 (test first) Add `langchain/tests/test_mcp_tools_endpoint.py`: monkeypatch
      `deps.mcp_tools` to a small fixed list of fake tool objects (name/description pairs)
      covering all 3 foundational names and at least 2 non-foundational names; assert
      `GET /langchain/mcp-tools` returns `foundational: true` for the foundational ones and
      `false` for the rest. Confirmed red (`KeyError: 'foundational'`) before task 1.4 landed
      the field.
- [x] 1.3 Create `langchain/helpers/foundational_tools.py` (alongside the existing
      `db_url.py` / `plot_renderer.py` / `sse_events.py` / `trait_name_resolver.py`
      shared-helpers convention): move `ALWAYS_INCLUDE_MCP_TOOLS` and `_is_always_included`
      here from `langchain/routes/chat.py:24-34`, renaming the latter to a public
      `is_foundational_tool` (same set, same prefix-matching logic — no behavior change).
      Update `routes/chat.py` to import both names from the new module instead of defining
      them; its own `_resolve_agent` logic (lines 43-83) is otherwise untouched.
- [x] 1.4 In `langchain/server.py`'s `get_mcp_tools` (line 209), add
      `"foundational": is_foundational_tool(t.name)` to each tool dict, importing
      `is_foundational_tool` from `langchain.helpers.foundational_tools` — not from
      `routes.chat`'s internals.
- [x] 1.5 Add a small `MCPToolInfo` (`name: str`, `description: str`, `foundational: bool`)
      and `MCPToolsResponse` (`tools: list[MCPToolInfo]`) pair to `langchain/schemas.py`
      (matching the existing `ModelsResponse` pattern at `schemas.py:28-29`); set it as
      `get_mcp_tools`'s `response_model` so the new field's presence/type is enforced by
      FastAPI itself, not only by the task 1.2 test.
- [x] 1.6 Run green locally: `cd langchain && uv run --extra test pytest tests/ -v` — 2 passed.
      This is a new, local-only gate — no CI job currently runs pytest against `langchain/`,
      and wiring one up is out of scope for this change (the same way
      `devendor-bloommcp-analysis`'s `V.3` documents its `ruff`/`black` check as local-only,
      not CI-enforced).

## 2. Frontend: filter on `foundational` via a unit-tested pure function

- [x] 2.1 (test first) Create `web/components/mcp-chat-client.helpers.ts`, following the
      `best-match-sort.ts` colocated-pure-function convention:
      `export function filterPickerTools(tools: MCPTool[]): MCPTool[] { return tools.filter((t) => !t.foundational); }`
      (`MCPTool` type, with the new `foundational: boolean` field, defined here and imported
      into `mcp-chat-client.tsx`). Added `mcp-chat-client.helpers.test.ts` covering: empty
      list, all-foundational, all-non-foundational, and a mixed list — 4 passed.
- [x] 2.2 Add `foundational: boolean` to the `MCPTool` interface (moved into
      `mcp-chat-client.helpers.ts`; the local interface in `mcp-chat-client.tsx:24-27` was
      removed in favor of the import).
- [x] 2.3 Delete `HIDDEN_TOOLS` (line 266) and `isHidden()` (lines 271-273) from
      `mcp-chat-client.tsx`; replace the `.filter(...)` call (line 274) with
      `filterPickerTools(data.tools || [])` from the new helpers file.
- [x] 2.4 Run green: `cd web && npx vitest run components/mcp-chat-client.helpers.test.ts` — 4
      passed. Also ran `npx tsc --noEmit` for the whole `web/` workspace: zero errors touching
      either changed file (pre-existing, unrelated errors in `app/api/gitlab/*` and two
      `*.test.ts` files under `lib/config/` are untouched by this change).
- [ ] 2.5 Manually verify in the browser that the tool picker still hides the 3 foundational
      tools and shows the rest, driven by the live `/langchain/mcp-tools` response (not a
      local list). **Not done** — requires the full Docker stack (Supabase auth, langchain
      service, web) running live; out of reach in this session. Flagging for manual
      verification before merge.

## 3. Drift-guard test: retire the dead `HIDDEN_TOOLS` parse, strengthen the regression guard

- [x] 3.1 (test first) In `bloommcp/tests/test_devendor_invariants.py`, add a test asserting
      `web/components/mcp-chat-client.tsx` contains no array/Set/object literal holding all
      three of `"list_available_experiments"`, `"load_experiment_data"`, and
      `"list_existing_analyses"` as string literals together — a **content-based** check, not
      an identifier-name check. Confirmed red against the pre-change file content (via
      `git show HEAD:...`, checked out to a scratch path and run through the same regex logic
      standalone, without touching the working tree) and green against the fixed file.
- [x] 3.2 Implemented tasks 2.1-2.3; the task 3.1 test is green against the current tree.
- [x] 3.3 Deleted `_parse_hidden_tools()` and the `hidden_tools` half of
      `test_tool_name_lists_match_live_registry` — kept the `always_include`-vs-live-registry
      assertion. Updated `_parse_always_include_mcp_tools` to read from
      `langchain/helpers/foundational_tools.py` (moved there per task 1.3) instead of
      `langchain/routes/chat.py`.
- [x] 3.4 Full `bloommcp` test suite green: `uv run --frozen --extra test pytest` — 676 passed,
      26 skipped (live-Supabase/live-persistence smoke tests, expected), 0 failed.

## 4. Scope closure + validation

- [x] 4.1 File a follow-up GitHub issue tracking the still-open soft suggestion from #485's
      ask #3 — deriving `CONTEXT_MCP`'s tool descriptions from the live tool registry's own
      `description` fields, rather than the hand-written strings that remain there today — so
      it isn't silently lost when #485 closes. Filed as
      [#538](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/538).
- [x] 4.2 `openspec validate refactor-foundational-tool-list --strict` passes.
- [x] 4.3 `ruff check` (`ruff@0.9.9`, matching `.pre-commit-config.yaml`'s pin) and
      `black --check --target-version py311` (`black@26.3.1`, same pin) both green on every
      file this change touches. (`server.py`/`schemas.py` have pre-existing black-formatting
      debt in lines this change doesn't touch — confirmed by diffing black's complaints
      against the actual edit — left alone rather than folded into this diff.) `tsc --noEmit`
      green for the `web` changes. No lint command exists for `web/` to run (no
      `eslint.config.js`, no `lint` script in `web/package.json`) — noting rather than
      fabricating one.
- [x] 4.4 Other consumers of `GET /langchain/mcp-tools`: confirmed during proposal review by
      repo-wide grep — `web/components/mcp-chat-client.tsx:254` is the only caller. No further
      action needed.
- [x] 4.5 **Pre-merge sequencing gate (hard, not advisory):** ran `openspec list` —
      `devendor-bloommcp-analysis` still shows `43/53 tasks`, not archived. No reconciliation
      needed yet; flagging the dependency to its owner before either change merges remains the
      outstanding action.
