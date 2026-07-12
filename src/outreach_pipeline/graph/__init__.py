"""LangGraph agent — entry point for langgraph.json.

Builds a StateGraph with a deterministic router and 6 specialist subgraph nodes.
Each specialist is a create_agent CompiledStateGraph that runs as a subgraph,
meaning its internal tool calls stream to the parent graph's event stream.

Internal subgraph messages (tool calls, draft iterations, structured outputs)
leak to the parent state. The server.py MessagesSnapshot filter removes them
by keeping only user messages and the last assistant text per turn.

Two modes:
- ``langgraph dev``: imports ``graph`` (uncompiled builder). The dev server
  adds its own in-memory checkpointer automatically.
- Production (FastAPI): call ``compile_graph()`` with an ``AsyncPostgresSaver``
  for durable checkpointing.
"""
from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

from outreach_pipeline.graph.router import route_to_specialist, router_node
from outreach_pipeline.graph.state import OutreachState

from outreach_pipeline.graph.agents.admin import admin_agent
from outreach_pipeline.graph.agents.drafting import draft_agent
from outreach_pipeline.graph.agents.enrichment import enrich_agent
from outreach_pipeline.graph.agents.pipeline_agent import pipeline_node
from outreach_pipeline.graph.agents.query import query_agent
from outreach_pipeline.graph.agents.scoring import score_agent

_SPECIALISTS = ["query", "enrich", "score", "draft", "pipeline", "admin"]

_retry = RetryPolicy(max_attempts=3)

_builder = StateGraph(OutreachState)
_builder.add_node("router", router_node)
_builder.add_node("query", query_agent, retry_policy=_retry)
_builder.add_node("enrich", enrich_agent, retry_policy=_retry)
_builder.add_node("score", score_agent, retry_policy=_retry)
_builder.add_node("draft", draft_agent, retry_policy=_retry)
_builder.add_node("pipeline", pipeline_node, retry_policy=_retry)
_builder.add_node("admin", admin_agent, retry_policy=_retry)

_builder.add_edge(START, "router")
_builder.add_conditional_edges(
    "router",
    route_to_specialist,
    {name: name for name in _SPECIALISTS},
)
for _name in _SPECIALISTS:
    _builder.add_edge(_name, END)

# Export the uncompiled builder so langgraph dev can add its own checkpointer.
# This is required for interrupt() to work — the dev server wraps the builder
# with a Postgres checkpointer at serve time.
graph = _builder


def compile_graph(checkpointer=None, store=None):
    """Compile the graph with an explicit checkpointer (for production).

    Usage (FastAPI lifespan):
        async with AsyncPostgresSaver.from_conn_string(DB_URL) as saver:
            await saver.setup()
            compiled = compile_graph(checkpointer=saver)
    """
    return _builder.compile(checkpointer=checkpointer, store=store)


__all__ = ["graph", "compile_graph"]
