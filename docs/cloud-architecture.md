# Optional Cloud Deployment

The application can be evaluated entirely with local Docker Compose. The `terraform/` directory is an optional AWS-oriented example for teams that want a starting point for their own reviewed infrastructure.

It is not a production guarantee and contains no reusable account, domain, state, or operator identity. Create all deployment-specific values yourself.

## Provider-neutral topology

```text
Internet
  -> TLS load balancer or reverse proxy
     -> Next.js frontend
     -> FastAPI / LangGraph backend
        -> managed PostgreSQL
        -> private ChromaDB or another vector store
        -> managed secret store
        -> CRM, mail, and LLM providers
```

The frontend and backend may share one origin or use a narrowly configured cross-origin policy. PostgreSQL and the vector store should not be exposed directly to the public internet.

## Required design decisions

Before deploying, decide and document:

- tenant and user isolation boundaries;
- authentication, role assignment, and administrative access;
- allowed CRM, mail, model, tracing, and enrichment providers;
- where personal data, prompts, traces, embeddings, and backups reside;
- retention, deletion, export, and incident-response procedures;
- human approval requirements for outbound messages;
- ingress, egress, private-network, and firewall policy;
- availability, backup, restore, and disaster-recovery objectives; and
- cost limits, budgets, alerts, and ownership.

## Secrets and configuration

Store secrets in a managed secret service or deployment platform, not in Git, images, compose files, or Terraform variables committed to the repository.

Typical secrets include:

- database credentials;
- session-signing keys;
- CRM and SMTP credentials;
- LLM and tracing-provider keys;
- OAuth client secrets; and
- encryption or webhook signing keys.

Use a unique secret namespace for each environment. Grant the runtime identity read access only to the values it needs. Rotate credentials after personnel, provider, or incident changes and verify that logs do not print loaded values.

Non-secret settings can be supplied through environment variables or a mounted configuration file. Treat domain names, alert recipients, account numbers, resource IDs, and network ranges as deployment-specific values even when they are not secrets.

## Data stores

### PostgreSQL

Use encrypted storage and transport, restricted network access, automated backups, and a tested restore process. Separate development, test, staging, and production databases. Apply migrations with a controlled identity rather than granting the application unrestricted administrative access.

### Vector store

Keep the vector store private and persistent only when retrieval is enabled. Embeddings and metadata can disclose their source material; include them in access, retention, backup, and deletion controls. Rebuilding the index from an approved corpus is often safer than treating it as an irreplaceable database.

### Object and log storage

If reports, exports, traces, or audit logs are persisted, encrypt them, restrict access, set finite retention, and redact personal data. Do not store unencrypted CRM exports or browser artifacts in a public bucket.

## Network and application controls

- Terminate TLS with a certificate for a domain you control.
- Redirect cleartext traffic to HTTPS.
- Expose only the frontend and intended API routes.
- Keep databases and vector services on private networks.
- Use explicit CORS origins; do not reflect arbitrary origins.
- Apply request-size, timeout, redirect, and rate limits.
- Block server-side web fetches to loopback, private, link-local, and metadata-service addresses.
- Use secure, HTTP-only, same-site cookies and rotate session keys.
- Add readiness checks that verify dependencies without disclosing internals.
- Send security and audit events to a restricted logging destination.

## Terraform example

The Terraform directory is optional. If you use it:

1. Work in a cloud account created for your deployment.
2. Copy `terraform/backend.example.hcl` and `terraform/terraform.tfvars.example` to private, ignored files.
3. Create a new remote-state bucket and lock mechanism under your control.
4. Review `terraform plan` for resource names, IAM actions, network exposure, storage, backup, deletion protection, and cost.
5. Apply first in a disposable non-production environment.
6. Store the plan and state as sensitive deployment artifacts.
7. Test teardown and restore before onboarding real data.

Never reuse another deployment's backend, variables, state, domain, alert address, certificates, or resource identifiers. Do not publish plan files or state snapshots; they commonly contain infrastructure details and may contain credentials.

## Content bootstrap

The repository includes only a fictional sample corpus. A deployer must provide approved content separately and can then run:

```bash
index-materials --materials-dir resources/materials --reset
```

For a real deployment, consider delivering the approved corpus from private object storage or a release artifact rather than baking organization-specific material into a public image.

## Deployment checklist

Before real users or data are admitted, verify:

- [ ] all credentials are newly issued and stored outside Git;
- [ ] database and vector endpoints are private;
- [ ] authentication and role checks cover every sensitive route;
- [ ] outbound sending is disabled until an authorized reviewer enables it;
- [ ] opt-out, cooldown, rate-limit, and deletion flows are tested;
- [ ] logs, traces, prompts, checkpoints, and backups have retention limits;
- [ ] sample identities and content have been replaced with approved configuration;
- [ ] domain, TLS, CORS, OAuth callbacks, and cookie flags are correct;
- [ ] backups can be restored and deleted;
- [ ] cloud budgets and alerts have an owner; and
- [ ] legal, privacy, security, and email-deliverability reviews are complete.

## Release hygiene

Build public releases from a clean checkout containing only the intended root commit or exported tree. Do not publish local `.env` files, state, databases, vector indexes, checkpoints, traces, screenshots, reports, generated spreadsheets, or old repository refs.
