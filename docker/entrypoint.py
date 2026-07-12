#!/usr/bin/env python3
"""Outreach container entrypoint.

In production, pulls secrets from SSM Parameter Store under the configured
path prefix (defaults to /outreach/) and exports them as environment
variables BEFORE exec'ing the application command.

In dev (ENVIRONMENT != production), runs as a no-op pass-through so the
image is usable locally via docker compose without AWS credentials.

Existing env vars (e.g. ones set via `docker run -e` or compose `environment:`)
are never overridden — this preserves the ability to override an SSM
parameter for one-off runs.

Usage (built into the Docker image):
    ENTRYPOINT ["/usr/bin/tini", "--", "outreach-entrypoint"]
    CMD ["gunicorn", ...]
"""
from __future__ import annotations

import os
import sys


def _is_production() -> bool:
    return os.environ.get("ENVIRONMENT", "").lower() == "production"


def _ssm_path_prefix() -> str:
    return os.environ.get("AWS_SSM_PATH", "/outreach/")


def _load_ssm_params(path_prefix: str) -> int:
    """Fetch parameters recursively from SSM, export to env, return count.

    Parameter names are the portion of the SSM name after the final '/' —
    e.g. '/outreach/DATABASE_URL' -> 'DATABASE_URL'. Values are decrypted
    (assumes SecureString params). Existing env vars are NOT overwritten,
    so compose-level overrides keep working.
    """
    import boto3  # imported lazily so dev image never needs boto3

    client = boto3.client("ssm")
    paginator = client.get_paginator("get_parameters_by_path")
    loaded = 0
    for page in paginator.paginate(
        Path=path_prefix, Recursive=True, WithDecryption=True,
    ):
        for param in page.get("Parameters", []):
            key = param["Name"].rsplit("/", 1)[-1]
            if key in os.environ:
                continue  # Respect explicit overrides
            os.environ[key] = param["Value"]
            loaded += 1
    return loaded


def main() -> None:
    argv = sys.argv[1:]
    if not argv:
        sys.stderr.write(
            "outreach-entrypoint: no command given. "
            "Expected `outreach-entrypoint <command> [args...]`.\n"
        )
        sys.exit(2)

    if _is_production():
        path = _ssm_path_prefix()
        try:
            count = _load_ssm_params(path)
            sys.stderr.write(
                f"outreach-entrypoint: loaded {count} SSM parameter(s) "
                f"from {path}\n"
            )
        except Exception as exc:  # noqa: BLE001 — we want to log all
            sys.stderr.write(
                f"outreach-entrypoint: failed to load SSM parameters from "
                f"{path}: {type(exc).__name__}: {exc}\n"
            )
            # Hard-fail — the app will not have the secrets it needs.
            sys.exit(3)

    # Replace this process with the app. exec preserves PID 1, so tini
    # and the orchestrator see the actual app process.
    os.execvp(argv[0], argv)


if __name__ == "__main__":
    main()
