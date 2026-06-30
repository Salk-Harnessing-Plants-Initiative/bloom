# Adding a tool to a section

This is the guide for turning one of your analysis functions into a bloommcp
tool the agent (and Claude Desktop) can call. It walks through the worked
example already in the `phenotyping_segmentation` section so you can copy it.

If you want the bigger picture of *why* sections exist, read
[the design doc](../../bloommcp/docs/2026-06-29-bloom-mcp-contributor-namespacing.md).

## What a section is

Your tools live in **a folder you own** — one file per tool:
`bloommcp/src/bloom_mcp/sections/phenotyping_segmentation/`.

The server does two things with it automatically — you don't touch `server.py`:

- **mounts** it into the combined surface, so every tool shows up to the agent
  namespaced as `phenotyping_segmentation_<your_tool_name>`;
- **serves** it at its own URL `/phenotyping_segmentation/mcp`, so a Claude
  Desktop user can load only your tools.

## The shape of a tool

Every tool is four small pieces. Look at `summarize_trait` in the section file
— it's a complete, working example. The four pieces:

1. **An input model** (Pydantic) — the parameters the agent fills in. Each
   field's `description` is what the agent reads to know what to pass.
   ```python
   class SummarizeTraitParams(BaseModel):
       experiment: str = Field(..., description="CSV filename from list_available_experiments.")
       trait: str = Field(..., description="Trait column to summarize (e.g. root_area_mean).")
   ```
2. **An output model** (Pydantic) — the structured result. Return this, not a
   hand-built sentence.
   ```python
   class SummarizeTraitResult(BaseModel):
       experiment: str
       trait: str
       grouped_by: str
       by_accession: list[AccessionTraitStats]
   ```
3. **The function**, wrapped with `@as_mcp_tool`. Its **docstring is the tool
   description** the agent sees, so write it for the agent.
   ```python
   @as_mcp_tool(input_model=SummarizeTraitParams, output_model=SummarizeTraitResult)
   def summarize_trait(params, *, provenance):
       ...
       return SummarizeTraitResult(...)
   ```
4. **Registration** — your tool lives in its own file (e.g. `summarize_trait.py`);
   register it in the section's `__init__.py`:
   ```python
   from . import summarize_trait
   register(section, summarize_trait.summarize_trait)   # add new tools here
   ```

That's it. `@as_mcp_tool` gives you the shared contract for free: it validates
the inputs/outputs, stamps provenance, and turns any error into a clean
message. You never write that boilerplate.


See also: [writing-a-new-tool.md](./writing-a-new-tool.md) (workflow tools) and
[storage-workflow.md](./storage-workflow.md) (how persistence works).
