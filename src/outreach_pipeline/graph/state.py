"""Agent state schema."""
import operator
from typing import Annotated

from langchain.agents import AgentState as BaseAgentState


class OutreachState(BaseAgentState):
    """Extends base agent state with pipeline-specific fields.

    BaseAgentState already provides `messages` with add_messages annotation.
    """
    current_engagement: dict | None = None
    pipeline_progress: Annotated[list[dict], operator.add] = []
    # Short-term memo — persists in checkpointed state across turns.
    # Survives message trimming. The agent writes key facts here.
    memo: str = ""
