// Pure helpers for the MCP tool picker, split out so they are unit-testable
// without rendering the chat client component.

export interface MCPTool {
  name: string;
  description: string;
  foundational: boolean;
}

// Foundational tools (list_available_experiments, load_experiment_data,
// list_existing_analyses — always available to the agent regardless of the
// tool picker) are hidden from the picker; the backend computes
// `foundational` from its own single source of truth
// (langchain/helpers/foundational_tools.py), so no tool names are
// hand-listed here.
export function filterPickerTools(tools: MCPTool[]): MCPTool[] {
  return tools.filter((t) => !t.foundational);
}
