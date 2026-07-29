import { describe, it, expect } from "vitest";
import { filterPickerTools, type MCPTool } from "./mcp-chat-client.helpers";

const tool = (name: string, foundational: boolean): MCPTool => ({
  name,
  description: `${name} description`,
  foundational,
});

describe("filterPickerTools", () => {
  it("returns an empty list unchanged", () => {
    expect(filterPickerTools([])).toEqual([]);
  });

  it("drops every tool when all are foundational", () => {
    const tools = [tool("list_available_experiments", true), tool("load_experiment_data", true)];
    expect(filterPickerTools(tools)).toEqual([]);
  });

  it("keeps every tool when none are foundational", () => {
    const tools = [tool("qc_clean", false), tool("pca_analysis", false)];
    expect(filterPickerTools(tools)).toEqual(tools);
  });

  it("keeps only the non-foundational tools in a mixed list", () => {
    const tools = [
      tool("list_available_experiments", true),
      tool("qc_clean", false),
      tool("list_existing_analyses", true),
      tool("pca_analysis", false),
    ];
    expect(filterPickerTools(tools).map((t) => t.name)).toEqual(["qc_clean", "pca_analysis"]);
  });
});
