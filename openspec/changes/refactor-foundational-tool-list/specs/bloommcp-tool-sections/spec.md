## ADDED Requirements

### Requirement: Foundational MCP Tools Are Marked by the Backend, Not Duplicated in the Frontend

The system SHALL expose foundational-tool status as a `foundational` field on each tool
object returned by `GET /langchain/mcp-tools`, computed from a single shared
`ALWAYS_INCLUDE_MCP_TOOLS` selection used to always include those tools in the agent's
toolset regardless of `tool_set`/`mcp_tool_names` routing. The web client SHALL filter its
tool picker using that field and SHALL NOT maintain its own hand-authored list of
foundational tool names.

#### Scenario: Backend marks foundational tools in the tool-list response

- **WHEN** a client calls `GET /langchain/mcp-tools`
- **THEN** each returned tool object includes `foundational: true` for
  `list_available_experiments`, `load_experiment_data`, and `list_existing_analyses` — or, if
  the section-namespacing migration is live for a given tool, its namespaced `core_*`
  equivalent — and `foundational: false` for every other tool

#### Scenario: Web client filters on the backend's field, not a local list

- **WHEN** the web client fetches the tool list to populate the tool picker
- **THEN** it hides exactly the tools with `foundational: true` using the field from the
  response, and no array, Set, or object literal in `mcp-chat-client.tsx` hand-lists the
  foundational tool names for this purpose

#### Scenario: A renamed or newly-added foundational tool needs no frontend change

- **WHEN** a tool is added to, removed from, or renamed within the shared
  `ALWAYS_INCLUDE_MCP_TOOLS` selection
- **THEN** the web client's tool picker reflects the change automatically on its next fetch of
  `GET /langchain/mcp-tools`, with no edit required in `mcp-chat-client.tsx`
