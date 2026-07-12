# Contributing

Thank you for improving Outreach Pipeline. Keep changes focused, explain the
user-visible effect, and include tests for behavior changes.

## Before opening a pull request

1. Create a branch from the current default branch.
2. Follow the setup instructions in `README.md`.
3. Run `make test-fast` for ordinary application changes.
4. Run `terraform fmt -check terraform` for infrastructure changes.
5. Run `git diff --check` and review every file that will be committed.

Use `make test-all` only when you have the optional local services and API
credentials needed for the end-to-end workflow. Tests must never target a
production environment.

The optional `langgraph-dev` extra includes Elastic License 2.0 components,
which are not OSI-approved open-source dependencies. It is excluded from the
core runtime; review its license before installing, redistributing, or using it
to provide a hosted service.

## Data and secret policy

Do not contribute real customer, prospect, employee, or account data. Fixtures
must be fictional and use reserved domains such as `example.com` or
`example.invalid`. Do not commit API keys, tokens, passwords, private keys,
Terraform state, database files, generated indexes, exported spreadsheets, or
production resource identifiers. If a secret is committed accidentally,
rotate it immediately and report the incident privately as described in
`SECURITY.md`; deleting it in a later commit is not sufficient.

## Pull request checklist

- Describe the problem and the chosen approach.
- Add or update tests and documentation where relevant.
- Confirm that no generated, proprietary, personal, or credential-bearing data
  is included.
- Call out migrations, infrastructure changes, new network access, and new
  third-party dependencies explicitly.
- Keep dependency lock files in sync with their manifests.

By submitting a contribution, you agree that it is licensed under the
Apache License 2.0 and that you have the right to contribute it.
