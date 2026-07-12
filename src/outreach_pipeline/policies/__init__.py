"""Policy layer — business rules that determine whether operations may proceed.

Design rule: If a check should run for many operators, put it in middleware.
If it protects only one operator's correctness, keep it inside that operator.
If it protects an external entry point, keep it in the hook but delegate here.
"""
from outreach_pipeline.policies.codes import Codes  # noqa: F401
from outreach_pipeline.policies.decisions import PolicyDecision  # noqa: F401
from outreach_pipeline.policies.contact import check_contact_policies  # noqa: F401
from outreach_pipeline.policies.send import check_send_policies  # noqa: F401
from outreach_pipeline.policies.scrape import check_scrape_policies  # noqa: F401
