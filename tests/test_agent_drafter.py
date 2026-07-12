"""Unit tests for agents/drafter.py — email drafting specialist."""
import unittest
from unittest.mock import patch, AsyncMock, MagicMock

from pydantic import BaseModel


class MessageDraftOutput(BaseModel):
    """Expected structured output from drafter agent."""
    subject: str
    body: str


class TestDrafterAgent(unittest.TestCase):
    def test_drafter_agent_exists(self):
        from outreach_pipeline.agents.drafter import drafter_agent
        self.assertIsNotNone(drafter_agent)

    def test_drafter_has_function_toolset(self):
        from outreach_pipeline.agents.drafter import drafter_agent
        # Verify agent has a function toolset (registered via @tool_plain)
        self.assertIsNotNone(drafter_agent._function_toolset)

    def test_drafter_output_type(self):
        from outreach_pipeline.agents.drafter import MessageDraftOutput
        draft = MessageDraftOutput(subject="Hi", body="Hello there")
        self.assertEqual(draft.subject, "Hi")


if __name__ == "__main__":
    unittest.main()
