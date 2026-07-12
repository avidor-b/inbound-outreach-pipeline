# Security Policy

## Supported versions

Security fixes are applied to the current default branch. Until stable releases
are published, older snapshots and deployment branches are unsupported.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability or exposed secret.
Use GitHub's private vulnerability reporting flow from the repository's
Security tab. Include the affected version or commit, impact, reproduction
steps, and any suggested mitigation. Avoid attaching real customer data or
active credentials.

If private vulnerability reporting is unavailable, open a public issue that
contains no sensitive details and asks a maintainer to establish a private
channel.

Maintainers will acknowledge a report when practical, investigate it, and
coordinate disclosure after a fix is available. Response and release timing
depends on severity and maintainer availability.

## Exposed credentials

Treat every committed credential as compromised even if the commit is later
removed. Revoke or rotate it at the provider first, then remove it from every
published ref and artifact. Terraform state and local environment files may
contain plaintext secrets and must never be attached to an issue or release.
