"""list_functions — the read-only database functions Claude can call, with their inputs."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from bloom_mcp.contract import as_mcp_tool
from bloom_mcp.data_access import function_access, page_links
from bloom_mcp.tools import _ports

from ._common import logs_call, reports_read_errors


class ListFunctionsParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ParamInfo(BaseModel):
    name: str
    type: str = Field(description="SQL type, e.g. bigint, text, bigint[].")
    required: bool


class FunctionInfo(BaseModel):
    name: str
    description: str
    params: list[ParamInfo]


class ListFunctionsResult(BaseModel):
    functions: list[FunctionInfo]


@as_mcp_tool(input_model=ListFunctionsParams, output_model=ListFunctionsResult)
@logs_call
@reports_read_errors
def list_functions(params: ListFunctionsParams) -> ListFunctionsResult:
    """List Bloom's read-only database functions: searches and per-experiment summaries.

    Call one with call_function. They answer some questions in one step that would take
    several table reads: finding genes by name, an experiment's traits, the proteins most
    similar to one protein. To find experiments by name, filter cyl_experiments or
    gravi_experiments on name with query_table instead.
    """
    served = _ports.table_reader().functions()
    return ListFunctionsResult(
        functions=[
            FunctionInfo(
                name=name,
                description=" ".join(
                    filter(None, (entry.description, page_links.link_note(name)))
                ),
                params=[
                    ParamInfo(name=p.name, type=p.type, required=p.required)
                    for p in served[name].params
                    if p.name != entry.page_input
                ],
            )
            for name, entry in sorted(function_access.READABLE_FUNCTIONS.items())
            if name in served
        ]
    )
