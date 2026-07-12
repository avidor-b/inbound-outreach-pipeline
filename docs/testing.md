# Testing

Tests are divided by cost and external dependencies. Start with deterministic checks, then opt into browser or model-backed workflows when their prerequisites are available.

## Setup

From the repository root:

```bash
python -m venv venv
source venv/bin/activate
python -m pip install -e ".[dev]"

cd frontend
npm ci
cd ..
```

Use synthetic fixtures only. Do not point tests at a real CRM, mailbox, database, vector index, or deployed application.

## Make targets

| Target | Purpose | Prerequisites | Suitable for ordinary CI |
|---|---|---|---|
| `make test-fast` | Focused Python regressions and frontend type checking | Python and frontend dependencies | Yes |
| `make test-security` | Security regressions, dependency audit, Terraform formatting, whitespace | Python, npm, and Terraform | Yes, with dependency caches |
| `make test-all` | `test-fast` followed by `test-security` | Same as above | Yes |
| `make test-precommit` | Focused tests plus staging UI checks | Running synthetic staging stack | Optional |
| `make test-ui` | Browser-level staging smoke and thread switching | Running synthetic staging stack | Optional |
| `make test-full` | End-to-end draft and approval workflow | Staging stack and an LLM key | Manual; may incur cost |
| `make test-prod-smoke` | Read-only route checks against an explicitly supplied deployment URL | Authorized test deployment | Protected/manual only |

`make test-prod-smoke` is retained as an operator tool. It must not run against an unknown host or as part of pull-request CI. Supply `PROD_BASE_URL` explicitly and verify that the target is an environment you are authorized to test.

## Local sequence

Run the deterministic checks first:

```bash
make test-fast
make test-security
```

For UI coverage, start the isolated stack and seed fictional records:

```bash
docker compose -f docker-compose.staging.yml up -d --build
venv/bin/python scripts/seed_staging.py
make test-ui
```

If you changed compose healthchecks or local `.env` values, recreate the services plus Chroma explicitly:

```bash
docker compose -f docker-compose.staging.yml up -d --no-build --force-recreate staging-chroma staging-server staging-frontend
```

Run the full LLM-backed workflow only after reviewing provider cost and data handling:

```bash
make test-full
```

Stop the stack when finished:

```bash
docker compose -f docker-compose.staging.yml down
```

## Focused pytest runs

```bash
venv/bin/python -m pytest tests/test_policies.py -q
venv/bin/python -m pytest tests/test_security_regressions.py -q
venv/bin/python -m pytest tests/test_resource_catalog.py -q
```

The test suite expects a dedicated test database configured by `tests/conftest.py`. Never substitute a production database URL. Database fixtures should be isolated and resettable.

## Browser tests

Browser scripts live under `tests/ui/` and default to the local staging ports. Keep their base URLs configurable through environment variables and use reserved domains or localhost in fixtures.

Browser traces, videos, screenshots, console logs, and storage-state files can contain cookies and personal data. They are local artifacts, must remain ignored, and should be sanitized before attaching them to an issue.

## Suggested CI shape

A minimal pull-request workflow can run:

```yaml
jobs:
  checks:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
          cache: npm
          cache-dependency-path: frontend/package-lock.json
      - run: python -m venv venv
      - run: venv/bin/python -m pip install -e ".[dev]"
      - run: npm ci --prefix frontend
      - run: make test-fast
```

Add security and container jobs only after installing their required tools. Keep model-backed, outbound-network, deployment, and real-service checks behind manual approval and scoped secrets.

## Fixture rules

- Use reserved domains such as `example.com` or the `.example` TLD.
- Use synthetic IDs with an obvious prefix such as `demo-` or `test-`.
- Do not copy real inbound messages into regression tests.
- Do not use employee, customer, prospect, account, cloud, or CRM identifiers.
- Keep fixed test credentials local to the isolated stack and label them as non-production.
- Assert that sending adapters are mocked unless the test is an explicitly approved integration test.

## What was not verified automatically

A fresh environment still needs its Python, Node, browser, Docker, and Terraform dependencies installed before every target can run. Documentation checks should therefore distinguish command and link validation from a completed end-to-end test run.
