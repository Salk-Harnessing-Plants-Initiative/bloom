"""phenotyping_segmentation section — Lin's segmentation tools.

Wraps the (to-be-released) ``phenotyping_segmentation`` package as a pinned
dependency. This file is the ready slot for Lin's functions: add a tool with
``@section.tool`` and it appears automatically — namespaced as
``phenotyping_segmentation_<name>`` on the combined ``/mcp`` surface (for the
agent) and on its own ``/phenotyping_segmentation/mcp`` URL (for a Claude
Desktop client that wants only this section). No server.py edit is needed.

The section boundary is organizational, not a second quality bar: tools here
go through the same shared contract (``@as_mcp_tool`` for provenance +
``BloomMCPError`` + seed, and the ``_ports`` reader/store) as every other
bloommcp tool.

It is intentionally empty today.
"""

from fastmcp import FastMCP

from bloom_mcp.auth import auth_provider

section = FastMCP("phenotyping-segmentation", auth=auth_provider)


# --- Add tools below. Template (delete when the real ones land) -------------
#
# from bloom_mcp.contract import Provenance, as_mcp_tool
# from bloom_mcp.tools import _ports
#
# @as_mcp_tool(...)
# def segment_plate(params, *, provenance: Provenance):
#     frame = _ports.reader().load(...)   # read via the injected port
#     ...                                 # delegate to phenotyping_segmentation
#     return result
#
# def register(_mcp):  # only if you prefer the register() style used elsewhere
#     _mcp.tool()(segment_plate)
