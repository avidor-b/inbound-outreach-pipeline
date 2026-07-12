"""Guard the OUTREACH_MODEL default against configuration drift.

Since the model is picked via `${OUTREACH_MODEL:-<default>}` across
multiple files, a partial update silently wins or loses depending on
deploy order (compose default vs env-file default vs code fallback).
That is the exact class of bug this test prevents.

Two assertions:
  1. Every file that carries a OUTREACH_MODEL default agrees on the
     same literal value — partial updates fail the build.
  2. That agreed value matches `EXPECTED_MODEL` below — so changing
     the model forces an explicit decision in this test, which makes
     the intent land in git history instead of staying in chat.

To change models:
  - Update `EXPECTED_MODEL` below.
  - Update the default in every file listed in `_DEFAULT_SOURCES`.
  - Update the deployment's secret or parameter store.
  - Restart the service so it reloads the environment.

The deployment environment remains the source of truth at runtime; this test
only keeps code-level defaults consistent for fresh installations.
"""
from __future__ import annotations

import re
from pathlib import Path


# Update me when intentionally switching models. A change here has to
# be paired with updates across _DEFAULT_SOURCES and deployment config; see
# the module docstring for the full recipe.
EXPECTED_MODEL = "openai:gpt-4.1-mini"

_REPO = Path(__file__).resolve().parent.parent

# (relative path, regex with one capture group = the default value).
# Every file here MUST carry the same OUTREACH_MODEL default.
_DEFAULT_SOURCES: list[tuple[str, str]] = [
    # Python code fallback when the env var is missing.
    ("src/outreach_pipeline/graph/agent.py",
     r'OUTREACH_MODEL", "([^"]+)"'),
    # Deployment compose default.
    ("docker-compose.yml",
     r"OUTREACH_MODEL: \$\{OUTREACH_MODEL:-([^}]+)\}"),
    # Staging compose default — must match the deployment default so local /
    # staging runs don't accidentally use a different model.
    ("docker-compose.staging.yml",
     r"OUTREACH_MODEL: \$\{OUTREACH_MODEL:-([^}]+)\}"),
    # The example .env shown to new contributors.
    (".env.example",
     r"^OUTREACH_MODEL=(\S+)"),
]


def _read(rel: str) -> str:
    return (_REPO / rel).read_text()


def _extract(rel: str, pattern: str) -> str:
    m = re.search(pattern, _read(rel), re.MULTILINE)
    assert m, f"Could not find OUTREACH_MODEL default in {rel} (pattern {pattern!r})"
    return m.group(1).strip()


def test_outreach_model_defaults_agree_across_files():
    """Every file that declares a OUTREACH_MODEL default must agree."""
    values = {rel: _extract(rel, pattern) for rel, pattern in _DEFAULT_SOURCES}
    unique = set(values.values())
    assert len(unique) == 1, (
        "OUTREACH_MODEL defaults diverge across files — a deploy can "
        "silently pick the wrong model depending on which default "
        "wins (compose > env > code). Sync them all:\n  "
        + "\n  ".join(f"{path}: {val!r}" for path, val in values.items())
    )


def test_outreach_model_matches_expected():
    """The agreed value must equal EXPECTED_MODEL. Changing the model
    has to update this constant too — that's the point: the decision
    lands in code, not in chat."""
    actual = _extract(
        "src/outreach_pipeline/graph/agent.py", r'OUTREACH_MODEL", "([^"]+)"'
    )
    assert actual == EXPECTED_MODEL, (
        f"OUTREACH_MODEL default is {actual!r} but this test pins "
        f"EXPECTED_MODEL = {EXPECTED_MODEL!r}. If the change is "
        "intentional, update EXPECTED_MODEL in this file together "
        "with the file defaults."
    )
