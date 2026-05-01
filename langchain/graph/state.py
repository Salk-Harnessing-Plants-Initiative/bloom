"""Typed state schema for the Bloom agent graph.

The state is the shared memory that flows through every node in the graph.
For Tier 0 it carries only the conversation `messages`; the `route` and
`analysis_route` fields are reserved here so subsequent tiers (top router,
analysis sub-router) can write to them without changing the schema.

State-merging notes:

* `messages` uses LangGraph's `add_messages` reducer — without it, every
  node return would OVERWRITE the message list and we'd lose history.
* `route` and `analysis_route` are scalar fields with last-write-wins
  semantics by default. That's acceptable today because no node writes
  them yet. When the routers land (`add-top-router-with-fallback` and
  `add-analysis-router-with-fallback`), revisit whether to add a custom
  reducer that no-ops on `None` so a downstream subgraph can't accidentally
  clobber a prior router's decision. Tracked as a known issue in
  `add-stategraph-foundation/proposal.md`.
"""
from typing import Annotated, Literal, Optional, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


# Top-level routing destinations. Reserved for `add-top-router-with-fallback`.
TopRoute = Literal["phenotyping", "scrna", "analysis", "freeform"]

# Analysis sub-router destinations. Reserved for `add-analysis-router-with-fallback`.
AnalysisRoute = Literal[
    "qc",
    "stats",
    "dimred_cluster",
    "viz",
    "correlation",
    "analysis_freeform",
]


class AgentState(TypedDict):
    """Shared state for the Bloom agent graph.

    Tier 0 only populates `messages`. Router fields are declared so that
    later tiers can set them without altering the schema.
    """

    messages: Annotated[list[BaseMessage], add_messages]
    route: Optional[TopRoute]
    analysis_route: Optional[AnalysisRoute]
