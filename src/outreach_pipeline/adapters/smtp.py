"""SMTP adapter — wraps send_email.py."""
from __future__ import annotations

from typing import Any


class SMTPAdapter:
    """Unified interface to email sending via HubSpot SMTP."""

    def __init__(self, config: dict[str, Any] | None = None):
        if config is None:
            from outreach_pipeline.config import load_config
            config = load_config()
        self.config = config

    def send(
        self,
        contact_id: str | None,
        subject: str,
        body: str,
        dry_run: bool = False,
        to_override: str | None = None,
    ) -> dict[str, Any]:
        """Send an email. Returns result dict with success/error fields."""
        from outreach_pipeline.send_email import send_email
        return send_email(contact_id, subject, body, dry_run, to_override)

    def check_config(self) -> tuple[bool, str]:
        """Verify sending is enabled and configured."""
        from outreach_pipeline.send_email import check_config
        return check_config(self.config)
