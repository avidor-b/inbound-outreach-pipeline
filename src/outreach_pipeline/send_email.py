#!/usr/bin/env python3
"""Send an approved email draft via HubSpot SMTP API.

Requires:
  1. 'transactional-email' scope on your HubSpot private app
  2. An SMTP token created in HubSpot (Settings → Marketing → Transactional Email → SMTP API Tokens)
  3. 'email_sending_enabled: true' in config.yaml
  4. sending_email, smtp_token_id, smtp_token_password in config.yaml

Without these, the script will refuse to send and explain what's missing.
"""
import argparse
import json
import smtplib
import sys
from email.mime.text import MIMEText

from outreach_pipeline.config import load_config
from outreach_pipeline.organization import get_organization_profile

SMTP_HOST = "smtp.hubapi.com"
SMTP_PORT = 587


def parse_args(args=None):
    p = argparse.ArgumentParser(description="Send email via HubSpot SMTP")
    p.add_argument("--contact-id", help="HubSpot contact ID (required unless --to is used)")
    p.add_argument("--subject", required=True, help="Email subject")
    p.add_argument("--body", required=True, help="Email body (plain text)")
    p.add_argument("--to", help="Override recipient address (for test sends — bypasses contact lookup)")
    p.add_argument("--dry-run", action="store_true", help="Validate without sending")
    return p.parse_args(args)


def check_config(config):
    """Verify sending is enabled and configured. Returns (ok, message)."""
    if not config.get("email_sending_enabled"):
        return False, (
            "Email sending is disabled. To enable, add to config.yaml:\n"
            "  email_sending_enabled: true\n"
            "  sending_email: sender@example.com\n"
            "  sender_name: Kelly Smith\n"
            "  smtp_token_id: <token id from HubSpot>\n"
            "  smtp_token_password: <token password from HubSpot>\n"
            "\nCreate an SMTP token: HubSpot → Settings → Marketing → "
            "Transactional Email → SMTP API Tokens"
        )
    missing = [f for f in ("sending_email", "smtp_token_id", "smtp_token_password")
               if not config.get(f)]
    if missing:
        return False, f"Missing in config.yaml: {', '.join(missing)}"
    return True, "OK"


def _get_contact_props(contact_id):
    """Fetch firstname and email from HubSpot at send time.

    PII is never stored in the local DB — fetched fresh via tools/pii.py.
    """
    from outreach_pipeline.tools.pii import fetch_send_fields
    return fetch_send_fields(contact_id)


def resolve_placeholders(text, first_name, sender_name):
    """Replace [First Name] and [Name] with real values."""
    return text.replace("[First Name]", first_name).replace("[Name]", sender_name)


def send_email(contact_id, subject, body, dry_run=False, to_override=None):
    """Send email via HubSpot SMTP. Returns result dict."""
    config = load_config()
    if to_override:
        first_name = "there"
        to_email = to_override
        if contact_id:
            first_name, _ = _get_contact_props(contact_id)
    else:
        if not contact_id:
            return {"success": False, "error": "--contact-id is required when --to is not specified", "sent": False}
        first_name, to_email = _get_contact_props(contact_id)
    profile = get_organization_profile()
    sender_name = config.get("sender_name", f"The {profile.name} Team")
    subject = resolve_placeholders(subject, first_name, sender_name)
    body = resolve_placeholders(body, first_name, sender_name)

    ok, msg = check_config(config)
    if not ok:
        return {"success": False, "error": msg, "sent": False}

    if not to_email:
        return {"success": False, "error": f"No email address on file for contact {contact_id}", "sent": False}

    if dry_run:
        return {
            "success": True, "sent": False, "dry_run": True,
            "contact_id": contact_id, "to": to_email, "subject": subject,
            "message": "Dry run — email would be sent via HubSpot SMTP",
        }

    msg_obj = MIMEText(body, "plain", "utf-8")
    msg_obj["Subject"] = subject
    msg_obj["From"] = f"{sender_name} <{config['sending_email']}>"
    msg_obj["To"] = to_email

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(config["smtp_token_id"], config["smtp_token_password"])
            server.send_message(msg_obj)
        return {"success": True, "sent": True, "contact_id": contact_id, "subject": subject}
    except smtplib.SMTPException as e:
        return {"success": False, "sent": False, "error": str(e)}


def main():
    args = parse_args()
    if args.to and args.contact_id:
        print(json.dumps({"success": False, "error": "--to and --contact-id are mutually exclusive. Use --to only for test sends without a contact."}))
        sys.exit(1)
    result = send_email(args.contact_id, args.subject, args.body, args.dry_run, args.to)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("success") else 1)


if __name__ == "__main__":
    main()
