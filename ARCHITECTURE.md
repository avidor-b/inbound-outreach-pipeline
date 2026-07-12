# Architecture

Outreach Pipeline is a reference implementation for human-reviewed inbound-lead outreach. The design separates user interaction, orchestration, business policy, persistence, and external integrations so adopters can replace organization-specific behavior without rewriting the whole application.

## System context

```text
                         +----------------------+
                         |  CRM / mail provider |
                         +----------+-----------+
                                    |
+---------+     HTTPS / SSE     +----v-----+       +----------------+
| Browser | <-----------------> | FastAPI  | ----> | LLM provider   |
+----+----+                      +----+-----+       +----------------+
     |                                |
     | Next.js / CopilotKit           | LangGraph
     |                                v
     |                          +-----+------+      +----------------+
     +------------------------> | Policies,  | ---> | Web fetcher    |
                                | services,  |      +----------------+
                                | and tools  |
                                +--+------+--+
                                   |      |
                              +----v--+ +-v--------+
                              |Postgres| | ChromaDB |
                              +--------+ +----------+
```

The browser never calls the CRM, mail, vector, or model provider directly. The backend owns credentials, authorization, policy checks, and audit-relevant decisions.

## Major components

### Frontend

`frontend/` contains the Next.js interface. It provides authenticated chat, thread management, generative UI cards, and human-in-the-loop decisions. It communicates with the backend over ordinary HTTP routes and an AG-UI event stream.

The frontend should receive only the data necessary for the active user and task. Provider credentials and unrestricted CRM records must never be embedded in the client bundle.

### API and orchestration

`src/outreach_pipeline/server.py` exposes authentication, thread, contact, and agent endpoints. The graph under `src/outreach_pipeline/graph/` routes requests to query, enrichment, scoring, drafting, pipeline, and administrative paths.

The graph is an orchestration layer, not the policy authority. Actions that read sensitive data, persist a draft, delete a record, or send a message should pass through service and policy checks even when invoked outside the chat UI.

### Domain services and policies

- `services/` implements intake, enrichment, scoring, messaging, approvals, and monitoring.
- `policies/` classifies submissions and enforces contact, engagement, and sending rules.
- `validation/` checks draft tone and output structure.
- `tools/` exposes narrow operations to graph agents.

The included intent rules and product families are fictional examples. The central `OrganizationProfile` in `src/outreach_pipeline/organization.py` loads brand, domains, relevance terms, company-fit guidance, and product families from the `organization` section of `config.yaml`, with `OUTREACH_ORGANIZATION_*` and `OUTREACH_PRODUCT_FAMILIES_JSON` overrides. Organization-specific values belong in private deployment configuration or a private extension, not in a public distribution.

Policy-source edits are an advanced extension point for intent behavior that the profile cannot express. They should be paired with focused regression tests.

### Adapters

`adapters/` isolates CRM, SMTP, web, spreadsheet, and vector-store I/O. The mock CRM adapter provides synthetic data for local development.

Adapter implementations should define:

- minimum required scopes and whether an operation is read-only or mutating;
- request timeouts, retry limits, and rate limits;
- logging and redaction behavior;
- data retention and deletion expectations; and
- a deterministic mock for tests.

### Persistence

PostgreSQL stores application records, users, threads, LangGraph checkpoints, and user-scoped memory. ChromaDB is optional and stores embeddings for approved content.

Embeddings, checkpoints, traces, and derived summaries can still reveal source material. Treat them with the same access, retention, backup, and deletion controls as the source data.

## Request flow

### Interactive request

1. The frontend sends an authenticated message to the backend.
2. Middleware validates the session and resolves the user's thread.
3. The router selects a graph path.
4. Tools call domain services through narrow interfaces.
5. State changes are persisted to PostgreSQL.
6. Events stream back to the browser.
7. Destructive or outbound actions pause for an explicit user decision.

### Outreach workflow

1. The CRM adapter returns the minimum fields needed for intake.
2. Intake stores a redacted contact and submission record.
3. Classifiers route support, event, opt-out, vendor, and irrelevant requests away from drafting.
4. Enrichment retrieves permitted public company context.
5. Scoring produces structured dimensions and evidence.
6. Drafting retrieves only approved content and creates a candidate message.
7. Validation rejects prohibited claims, tracking references, and personal data in the body.
8. A user approves, revises, or discards the draft.
9. Sending re-checks authorization, opt-out, cooldown, rate, and recipient data at execution time.
10. Reply monitoring records only the minimum content required for follow-up.

## Data model

The central separation is:

- **Contact**: stable CRM identity and redacted organization context
- **Submission**: one inbound form or message and its classification evidence
- **Engagement**: the unit of work that moves through scoring, drafting, approval, and closure
- **Draft**: versioned outbound content attached to an engagement
- **Thread**: user-facing conversation and graph checkpoint reference
- **Artifact**: UI-facing result or approval object

Keeping submissions separate from contacts prevents a newer form fill from overwriting the evidence for an earlier engagement.

## Trust boundaries

### Personal data

The application attempts to avoid persisting names, email addresses, and phone numbers when they are not required. Some workflows fetch recipient data just in time from the CRM.

This is a minimization strategy, not a guarantee. Inbound messages, company research, drafts, logs, traces, checkpoints, database backups, and model output may contain personal or confidential information. Deployers must inventory those paths and implement retention and deletion controls.

### Model providers

Prompts can include CRM messages and enrichment context. Use a provider and account whose data-processing terms fit your use case. Disable tracing or redact payloads when traces would expose restricted data. Do not rely on the model to enforce authorization or sending policy.

### Web enrichment

Web retrieval crosses an external trust boundary. Apply URL validation, deny private and link-local destinations, limit redirects and response size, and review site terms and robots rules. Do not use enrichment to infer sensitive traits about individuals.

### Approved content

Retrieval content must be owned, licensed, or otherwise authorized for the intended use. Publicly reachable material is not necessarily redistributable. Raw CMS exports and unpublished pages should remain outside the repository.

## Safety invariants

- Real sending is off by default.
- A draft is not a send authorization.
- Every outbound action re-evaluates current policy and permissions.
- Opt-out requests take precedence over scoring and workflow state.
- Support and event requests do not enter the sales-drafting path.
- Contact data is scoped to the authenticated user or tenant boundary.
- Model output is validated before display and again before sending.
- Logs do not intentionally record secrets, full tokens, or unrestricted CRM payloads.
- Tests, screenshots, and examples use synthetic identities and reserved domains.

## Configuration and extension points

Public defaults live in `.env.example` and `docker/config.default.yaml`. Secrets belong in local environment files or a managed secret store. The main extension points are:

- organization profile: brand, first-party and resource domains, product families, relevance terms, and fit guidance
- CRM and mail adapters
- authentication and authorization policy
- advanced intent classification policy
- scoring rubric and prompts
- tone and claim validation
- approved content catalog
- retention, deletion, and audit implementation
- vector and tracing providers

Changes to a policy should include focused tests. Avoid putting customer names, contact IDs, employee details, private domains, or deployment identifiers into prompts and fixtures.

## Deployment model

The supported evaluation path is the synthetic Docker Compose stack. A deployment may run the frontend, API, PostgreSQL, and ChromaDB as containers or managed services, provided the same trust boundaries are preserved.

The repository includes optional Terraform as an example for one cloud provider. It is not required and should not be applied without creating new state and variable files, reviewing IAM and network policy, and estimating cost. See [docs/cloud-architecture.md](docs/cloud-architecture.md).

## Further reading

- [README.md](README.md) for setup and operating constraints
- [docs/testing.md](docs/testing.md) for test isolation and CI guidance
- [resources/README.md](resources/README.md) for safe content ingestion
