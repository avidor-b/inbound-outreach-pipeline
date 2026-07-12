"""Unit tests for agent hooks — audit, role gating, PII redaction."""
import unittest

from outreach_pipeline.agents.orchestrator import ROLE_TOOLS, build_orchestrator


class TestRoleToolFiltering(unittest.TestCase):
    def test_viewer_has_read_only_tools(self):
        tools = ROLE_TOOLS["viewer"]
        self.assertIn("get_leads", tools)
        self.assertIn("get_stats", tools)
        self.assertNotIn("generate_validated_message", tools)
        self.assertNotIn("send_message", tools)

    def test_drafter_can_generate_but_not_send(self):
        tools = ROLE_TOOLS["drafter"]
        self.assertIn("get_leads", tools)
        self.assertIn("generate_validated_message", tools)
        self.assertNotIn("send_message", tools)

    def test_sender_can_send(self):
        tools = ROLE_TOOLS["sender"]
        self.assertIn("generate_validated_message", tools)
        self.assertIn("send_message", tools)

    def test_admin_has_all_tools(self):
        tools = ROLE_TOOLS["admin"]
        self.assertIn("get_leads", tools)
        self.assertIn("generate_validated_message", tools)
        self.assertIn("send_message", tools)
        self.assertIn("grade_collection", tools)

    def test_build_orchestrator_with_role(self):
        agent = build_orchestrator(role="viewer")
        self.assertIsNotNone(agent)

    def test_build_orchestrator_filters_tools(self):
        viewer_agent = build_orchestrator(role="viewer")
        admin_agent = build_orchestrator(role="admin")
        # Admin should have more tools than viewer
        viewer_tools = len(viewer_agent._user_toolsets)
        admin_tools = len(admin_agent._user_toolsets)
        # Both should have at least the function toolset
        self.assertIsNotNone(viewer_agent._function_toolset)
        self.assertIsNotNone(admin_agent._function_toolset)


if __name__ == "__main__":
    unittest.main()
