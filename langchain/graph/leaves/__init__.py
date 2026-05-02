"""Tier 3 specialized analysis leaves.

Each leaf is a focused ReAct loop with a narrow tool surface and a dedicated
prompt. Leaves plug into the analysis subgraph at the destinations of the
analysis_router's conditional edge.

Today: only qc. Future leaves (stats, dimred_cluster, viz, correlation) follow
the same factory + prompt + filter pattern.
"""
from .qc import QC_LEAF_TOOL_NAMES, build_qc_leaf, filter_qc_tools

__all__ = ["QC_LEAF_TOOL_NAMES", "build_qc_leaf", "filter_qc_tools"]
