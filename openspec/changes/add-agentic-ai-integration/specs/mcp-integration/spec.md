# MCP Integration Capability Specification

## ADDED Requirements

### Requirement: FastMCP Server

The system SHALL expose functions as MCP tools using FastMCP.

#### Scenario: MCP server initialization

- **WHEN** FastAPI service starts
- **THEN** system initializes FastMCP instance
- **AND** system mounts MCP app at `/ai/mcp` endpoint
- **AND** MCP server is accessible to agent clients

#### Scenario: Tool registration

- **WHEN** function is decorated with `@mcp.tool()`
- **THEN** FastMCP automatically registers function as tool
- **AND** FastMCP generates JSON schema from function signature
- **AND** tool is accessible via MCP protocol

#### Scenario: Tool invocation

- **WHEN** agent client calls MCP tool
- **THEN** FastMCP validates parameters against schema
- **AND** FastMCP invokes Python function
- **AND** FastMCP returns result to agent client

### Requirement: Core MCP Tools

The system SHALL provide core MCP tools for common operations.

#### Scenario: Database query tool

- **WHEN** agent calls `query_database` tool
- **AND** parameters include table name, filters, limit
- **THEN** tool validates table exists
- **AND** tool applies Guardrails validation
- **AND** tool executes PostgreSQL query
- **AND** tool returns results as JSON

#### Scenario: Generic job execution tool

- **WHEN** agent calls `execute_job` tool
- **AND** parameters include HTTP endpoint and params dict
- **THEN** tool validates endpoint URL format
- **AND** tool sends HTTP POST request
- **AND** tool returns job ID and status
- **AND** tool handles connection errors gracefully

#### Scenario: Job status check tool

- **WHEN** agent calls `check_job_status` tool
- **AND** parameters include endpoint and job ID
- **THEN** tool sends HTTP GET request to status endpoint
- **AND** tool returns current job status
- **AND** tool includes progress information if available

#### Scenario: File read tool

- **WHEN** agent calls `read_file` tool
- **AND** parameters include file path
- **THEN** tool validates path is within allowed directories
- **AND** tool reads file contents
- **AND** tool returns contents as string
- **AND** tool returns error if file not found or access denied

#### Scenario: File search tool

- **WHEN** agent calls `search_files` tool
- **AND** parameters include pattern and directory
- **THEN** tool searches for files matching pattern
- **AND** tool respects access control restrictions
- **AND** tool returns list of matching file paths

### Requirement: External MCP Server Integration

The system SHALL integrate with external MCP servers.

#### Scenario: Supabase MCP integration

- **WHEN** system starts
- **THEN** system connects to Supabase MCP server
- **AND** agent can query database via Supabase MCP
- **AND** agent can use Supabase-specific features

#### Scenario: Filesystem MCP integration

- **WHEN** system starts
- **THEN** system connects to Filesystem MCP server
- **AND** Filesystem MCP is configured with allowed directories
- **AND** agent can read/write files via Filesystem MCP
- **AND** access controls are enforced

#### Scenario: Git MCP integration

- **WHEN** system starts
- **THEN** system connects to Git MCP server
- **AND** agent can read repository files
- **AND** agent can search code across repository
- **AND** agent can view git history (read-only)

#### Scenario: Memory MCP integration (Phase 2+)

- **WHEN** Memory MCP is enabled
- **THEN** agent can store conversation context
- **AND** agent can retrieve relevant past context
- **AND** knowledge graph is persisted across sessions

### Requirement: Tool Parameter Validation

The system SHALL validate all tool parameters before execution.

#### Scenario: Type validation

- **WHEN** agent calls tool with incorrect parameter type
- **THEN** FastMCP validates against JSON schema
- **AND** system returns validation error
- **AND** error message describes expected type

#### Scenario: Range validation

- **WHEN** agent calls tool with out-of-range parameter
- **AND** parameter has min/max constraints
- **THEN** system validates against constraints
- **AND** system returns validation error if out of range

#### Scenario: Custom validation

- **WHEN** tool has custom validation logic
- **AND** agent provides parameters
- **THEN** custom validator runs before execution
- **AND** system blocks execution if validation fails
- **AND** system returns informative error message

### Requirement: Tool Error Handling

The system SHALL handle tool execution errors gracefully.

#### Scenario: Database connection error

- **WHEN** database query tool is called
- **AND** database connection fails
- **THEN** tool catches exception
- **AND** tool returns structured error response
- **AND** error includes recovery suggestions

#### Scenario: HTTP timeout

- **WHEN** execute_job tool makes HTTP request
- **AND** request times out
- **THEN** tool catches timeout exception
- **AND** tool returns timeout error
- **AND** error includes endpoint and timeout duration

#### Scenario: Permission denied

- **WHEN** read_file tool attempts to read restricted file
- **THEN** tool catches permission error
- **AND** tool returns access denied error
- **AND** error does not leak sensitive path information

### Requirement: Tool Response Formatting

The system SHALL format tool responses consistently.

#### Scenario: Successful operation

- **WHEN** tool execution succeeds
- **THEN** response includes `status: "success"`
- **AND** response includes result data
- **AND** response includes execution metadata (time, etc.)

#### Scenario: Failed operation

- **WHEN** tool execution fails
- **THEN** response includes `status: "error"`
- **AND** response includes error type and message
- **AND** response includes error code for programmatic handling
- **AND** response does not include sensitive debugging info

### Requirement: Tool Documentation

The system SHALL provide documentation for all MCP tools.

#### Scenario: Tool description

- **WHEN** agent queries available tools
- **THEN** each tool includes description
- **AND** description explains purpose and use cases
- **AND** description includes parameter documentation

#### Scenario: Parameter schema

- **WHEN** agent queries tool schema
- **THEN** system returns JSON schema for parameters
- **AND** schema includes types, constraints, defaults
- **AND** schema includes examples for complex parameters

#### Scenario: Usage examples

- **WHEN** developer views tool documentation
- **THEN** documentation includes usage examples
- **AND** examples show common use cases
- **AND** examples demonstrate error handling
