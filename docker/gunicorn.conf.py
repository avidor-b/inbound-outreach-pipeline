"""Gunicorn config for the Outreach server.

Runs uvicorn workers behind gunicorn's process manager — gives us graceful
worker recycling without locking into uvicorn's single-process model.

Tune via env vars from SSM / compose:
  WEB_WORKERS      number of worker processes (default 2 — single-box
                   t3.medium sweet spot). Scale with CPU count if you
                   move to a bigger instance: (2 x CPU) + 1.
  WEB_TIMEOUT      per-request timeout in seconds (default 120 — agent
                   turns can be long; keep this above max turn time).
  WEB_BIND         host:port to bind (default 0.0.0.0:8000).
"""
from __future__ import annotations

import os

bind = os.environ.get("WEB_BIND", f"0.0.0.0:{os.environ.get('PORT', '8000')}")
workers = int(os.environ.get("WEB_WORKERS", "2"))
worker_class = "uvicorn.workers.UvicornWorker"
timeout = int(os.environ.get("WEB_TIMEOUT", "120"))
graceful_timeout = int(os.environ.get("WEB_GRACEFUL_TIMEOUT", "30"))
keepalive = int(os.environ.get("WEB_KEEPALIVE", "5"))

# stdout/stderr — awslogs driver forwards to CloudWatch.
accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("WEB_LOG_LEVEL", "info")

# Pre-load the app to reduce memory per worker via copy-on-write. Safe for
# FastAPI; uvicorn doesn't need lifespan to run before fork.
preload_app = os.environ.get("WEB_PRELOAD", "false").lower() == "true"
