# Outreach Pipeline

Outreach Pipeline is a configurable reference implementation for researching inbound leads, scoring outreach fit, drafting emails, and routing every draft through human review. It combines a FastAPI/LangGraph backend, a Next.js/CopilotKit interface, PostgreSQL persistence, and optional ChromaDB retrieval.

The repository ships with fictional sample data only. It is not a turnkey sales system, a compliance certification, or a substitute for reviewing the privacy, anti-spam, scraping, and consumer-protection rules that apply to your organization.

## What is included

- Inbound contact ingestion through a CRM adapter, with a mock adapter for local use
- Intent classification, company enrichment, lead scoring, and email drafting
- Human approval, revision, and discard flows before sending
- Opt-out, duplicate-contact, send-limit, support, and event guardrails
- PostgreSQL-backed threads, checkpoints, and user-scoped memory
- Optional retrieval over content that you are entitled to use
- A small fictional product catalog and synthetic staging fixtures

The sample organization profile and product taxonomy are deliberately fictional. Replace them with your own reviewed configuration before using the project outside a demo environment.

## Architecture

```text
Browser
  -> Next.js / CopilotKit
  -> FastAPI AG-UI endpoint
  -> LangGraph agents and policy checks
  -> PostgreSQL (application data, checkpoints, memory)
  -> ChromaDB (optional approved-content index)

External adapters: CRM API, SMTP provider, LLM provider, web fetcher
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for component boundaries and [docs/cloud-architecture.md](docs/cloud-architecture.md) for a provider-neutral deployment checklist.

## Quick start

### Prerequisites

- Python 3.11 or newer
- Node.js 20 or newer
- Docker with Compose

### 1. Install dependencies

```bash
python -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"

cd frontend
npm ci
cd ..
```

On Windows, activate the virtual environment with `venv\Scripts\activate`.

### 2. Configure local secrets

```bash
cp .env.example .env
cp docker/config.default.yaml config.yaml
```

Keep `.env` and `config.yaml` local; both are ignored by Git. For the mock-data UI, no CRM token is required. Add an LLM provider key only when you want to exercise an LLM-backed path.

Important settings include:

- `DATABASE_URL`: PostgreSQL connection string
- `OUTREACH_MODEL`: provider and model name
- `OPENAI_API_KEY` or `GOOGLE_API_KEY`: key for the selected model
- `HUBSPOT_TOKEN`: optional CRM credential for the built-in adapter
- `JWT_SECRET`: random signing key outside disposable local demos
- SMTP settings in `config.yaml`: required only if sending is deliberately enabled

Never commit credentials, CRM exports, message transcripts, browser state, Terraform state, or generated databases.

### Organization profile

Brand, first-party domains, approved resource domains, product families, relevance terms, and company-fit guidance are centralized in the `organization` section of `config.yaml`. Start from the fictional defaults in `docker/config.default.yaml` and keep a deployment-specific copy private when it contains non-public business information.

```yaml
organization:
  name: "Your Organization"
  description: "a short factual description"
  primary_domains:
    - organization.example
  resource_domains:
    - docs.organization.example
  relevance_terms:
    - sample use case
  company_fit_guidance: "Describe the reviewed fit rubric."
  product_families:
    sample_product:
      display_name: "sample product"
      aliases:
        - Sample Product
      patterns:
        - "SAMPLE-[A-Z0-9-]+"
      url_patterns:
        - "sample[-_]?product"
```

Environment variables override file settings when configuration is injected by a platform:

- `OUTREACH_ORGANIZATION_NAME`
- `OUTREACH_ORGANIZATION_DESCRIPTION`
- `OUTREACH_PRIMARY_DOMAINS`
- `OUTREACH_RESOURCE_DOMAINS`
- `OUTREACH_RELEVANCE_TERMS`
- `OUTREACH_COMPANY_FIT_GUIDANCE`
- `OUTREACH_PRODUCT_FAMILIES_JSON`

`OUTREACH_PRODUCT_FAMILIES_JSON` must be a JSON object with the same family shape shown above. Treat complex regular expressions as code: review and test them before deployment.

### 3. Run the synthetic local stack

```bash
docker compose -f docker-compose.staging.yml up -d --build
venv/bin/python scripts/seed_staging.py
```

Open [http://localhost:3131](http://localhost:3131). The backend health endpoint is [http://localhost:8181/health](http://localhost:8181/health).

The staging compose file uses a mock CRM adapter and synthetic contacts. Email sending remains disabled unless you explicitly configure and enable it.

To stop the stack:

```bash
docker compose -f docker-compose.staging.yml down
```

Add `-v` only when you also intend to delete the local PostgreSQL and Chroma volumes.

## Approved content and retrieval

`resources/` contains a tiny fictional example. Replace it only with material that you own, license, or have permission to process and redistribute.

Do not commit raw CMS exports, private knowledge-base articles, confidential manuals, customer documents, or scraped copies of third-party websites. A public URL does not automatically grant redistribution rights.

To index approved local material:

```bash
index-materials --materials-dir resources/materials --reset
query-rag --query "fictional outdoor radio"
```

The sendable-resource catalog is `resources/catalog.json`. Keep its URLs public and its entries free of customer identifiers, tracking parameters, private portal links, and unpublished content. See [resources/README.md](resources/README.md) for the expected format.

The web-enrichment tools should be enabled only after you have reviewed the target site's terms, robots policy, rate limits, and the laws that apply to your use case.

## Pipeline lifecycle

1. **Ingest** contacts from the mock or configured CRM adapter.
2. **Classify** the inbound request and apply blocking rules.
3. **Enrich** with permitted public company context.
4. **Score** against your reviewed qualification rubric.
5. **Draft** using approved content and data-minimization rules.
6. **Review** through the human-in-the-loop interface.
7. **Send** only when an authorized user approves and sending is enabled.
8. **Monitor** replies and enforce opt-out requests.

Contacts, submissions, and engagements are separate records. Drafts attach to engagements and move through an approval lifecycle before they can be sent.

## Safety and privacy

The implementation includes useful controls, but deployers remain responsible for policy and legal review.

- Use synthetic data in development, tests, screenshots, and bug reports.
- Collect and retain only the fields required for the workflow.
- Do not place contact details, messages, CRM identifiers, or tenant-specific preferences in source-controlled files.
- Treat LLM prompts, traces, vector stores, logs, exports, and checkpoints as potentially sensitive.
- Review data-processing terms before sending data to an LLM, tracing service, CRM, or enrichment provider.
- Keep sending disabled until authentication, authorization, opt-out handling, rate limits, sender identity, and audit requirements are verified.
- Require a human decision for every real outbound message unless your legal and risk teams approve a different policy.
- Provide a deletion and retention process for contacts, drafts, traces, backups, and embeddings.

The repository's redaction and guardrail code is defense in depth, not proof that stored or generated content is free of personal data.

## Testing

The fastest local checks are:

```bash
make test-fast
make test-security
```

UI and full-workflow targets require the synthetic staging stack. Some full-workflow tests also call an LLM and may incur cost.

```bash
make test-ui
make test-full
```

See [docs/testing.md](docs/testing.md) for the test matrix, prerequisites, and CI guidance.

## Deployment

Local Docker Compose is sufficient for evaluation. The `terraform/` directory is an optional AWS-oriented example, not a required part of the application and not a ready-made production environment.

Before using Terraform:

- create your own remote-state backend and variables from the example files;
- review every resource, IAM policy, network rule, retention setting, and expected cost;
- use a domain, certificates, alert address, and cloud account that you control;
- keep state files and generated plans out of source control; and
- test restore, rotation, rollback, and deletion procedures in a non-production account.

Do not reuse infrastructure identifiers from another deployment. See [docs/cloud-architecture.md](docs/cloud-architecture.md) for a neutral checklist.

## Repository layout

```text
src/outreach_pipeline/   backend, agents, services, policies, and adapters
frontend/                Next.js user interface
tests/                   Python and browser regression tests
resources/               fictional, redistributable sample content
scripts/                 local setup, indexing, and test helpers
docker/                  container configuration
terraform/               optional infrastructure example
docs/                    architecture and testing guidance
```

## Adapting the reference implementation

Before connecting real systems, review at least these extension points:

- the central organization profile in `config.yaml` or `OUTREACH_ORGANIZATION_*` environment variables
- CRM and mail adapters in `src/outreach_pipeline/adapters/`
- scoring and drafting prompts in `src/outreach_pipeline/graph/`
- sender identity and safe defaults in `config.yaml`
- sample agent instructions under `.claude/`
- the resource catalog and approved retrieval corpus under `resources/`

Most branding, domain, relevance, and product-family changes should use the organization profile. Edit policy source only for advanced changes to intent behavior that the profile cannot express, and add focused regression tests for every policy change.

Keep organization-specific prompts, taxonomies, credentials, customer data, and deployment runbooks in private configuration rather than in a public fork.

## License

The repository source and fictional samples are licensed under the [Apache License 2.0](LICENSE). Third-party dependencies retain their own licenses.

The optional `langgraph-dev` extra includes packages distributed under the Elastic License 2.0, which is not an OSI-approved open-source license. It is excluded from the core/runtime lock file; review its terms before installing, redistributing, or using that optional development server.
