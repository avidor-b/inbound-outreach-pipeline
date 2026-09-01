# syntax=docker/dockerfile:1.7
#
# Outreach server — FastAPI + LangGraph agent. See docs/cloud-architecture.md.
#
# Build:
#   docker build -t outreach-server .
# Run (dev-style, no SSM):
#   docker run --rm -p 8000:8000 \
#     -e DATABASE_URL=postgresql://... \
#     -e JWT_SECRET=... \
#     outreach-server
#
# Two-stage build keeps the runtime image slim: the builder installs the
# package and all transitive wheels; the runtime only carries the
# installed site-packages and the app source.

# ---------- Builder ----------
# Base image pinned to a specific patch release — bumping the tag is an
# explicit, auditable change. Do not revert to the floating `3.11-slim`.
FROM python:3.14.7-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Build deps — removed from runtime stage.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        git \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Install dependencies from a frozen lock file — no dependency
# resolution at build time, no surprise upgrades. To add/bump a dep:
# update pyproject.toml, run
# `uv pip compile pyproject.toml --extra dev --python-version 3.11 --universal --output-file requirements.lock.txt`,
# review the diff, commit. This layer is cached until the lock file changes.
COPY requirements.lock.txt ./
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --prefix=/install -r requirements.lock.txt

# Project source + own-package install (no deps — they're already in).
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY resources ./resources
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --prefix=/install --no-deps .


# ---------- Runtime ----------
FROM python:3.14.7-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    CONFIG_PATH=/etc/outreach/config.yaml \
    ENVIRONMENT=production \
    PORT=8000

# libpq for asyncpg, curl for healthcheck probing from inside the container.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        curl \
        tini \
    && rm -rf /var/lib/apt/lists/*

# Non-root runtime user.
RUN useradd --create-home --shell /bin/bash outreach
WORKDIR /app

# Bring across the wheels and executables installed in the builder.
COPY --from=builder /install /usr/local

# Application source — the src layout is importable via PYTHONPATH.
COPY --chown=outreach:outreach src ./src
COPY --chown=outreach:outreach resources ./resources
COPY --chown=outreach:outreach LICENSE ./LICENSE
# Hooks + skills — live outside src/ but are loaded at runtime:
#   .claude/hooks/tone_guard.py — imported by validation/tone.py via sys.path
#   .claude/skills/outreach-pipeline/email-guidelines.md — read by draft_helpers.py
COPY --chown=outreach:outreach .claude/hooks ./.claude/hooks
COPY --chown=outreach:outreach .claude/skills ./.claude/skills

# Container tooling — entrypoint, gunicorn config.
COPY --chown=outreach:outreach docker/entrypoint.py /usr/local/bin/outreach-entrypoint
COPY --chown=outreach:outreach docker/gunicorn.conf.py /app/gunicorn.conf.py
RUN chmod +x /usr/local/bin/outreach-entrypoint

# Baseline config.yaml lives at CONFIG_PATH. Operators mount a real one
# over this, or override values via env. The baseline is safe (no secrets)
# and carries just the non-sensitive tuning knobs.
RUN mkdir -p /etc/outreach
COPY --chown=outreach:outreach docker/config.default.yaml /etc/outreach/config.yaml

USER outreach

# Pre-download the ONNX embedding model so fresh containers don't pay
# the ~30s download cost on first chroma query. DOWNLOAD_PATH hardcoded
# in chromadb is Path.home() / ".cache" / "chroma" / "onnx_models" /
# "all-MiniLM-L6-v2" — running as `outreach` here puts the cache at
# /home/outreach/.cache/chroma/… which is where the runtime expects it.
# Non-fatal on build failure (network blips): the runtime still
# downloads on first use, just slowly. We want the fast path baked in.
RUN python -c "from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2; ONNXMiniLM_L6_V2()._download_model_if_not_exists()" || true

EXPOSE 8000

# tini reaps zombie processes; entrypoint loads SSM secrets then exec's
# gunicorn. Gunicorn manages uvicorn workers — see gunicorn.conf.py.
ENTRYPOINT ["/usr/bin/tini", "--", "outreach-entrypoint"]
CMD ["gunicorn", "-c", "/app/gunicorn.conf.py", "outreach_pipeline.server:app"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://localhost:${PORT}/health || exit 1
