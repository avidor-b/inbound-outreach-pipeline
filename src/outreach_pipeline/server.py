"""FastAPI server — Google OAuth, thread management, AG-UI endpoint.

Auth: Google OAuth → JWT in httpOnly cookie → auto-sent with all requests.
CopilotKit uses credentials="include" to receive the cookie.

Deployment: Nginx reverse proxy on EC2, same domain for frontend + backend.
"""
from __future__ import annotations

from dotenv import load_dotenv
load_dotenv()  # Load .env before anything reads os.environ

import os
import re as _re
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Depends, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from outreach_pipeline.db.threads import (
    create_thread, get_thread,
    list_threads, add_message, list_messages,
    create_user, get_user,
    get_artifact, update_artifact_status, list_artifacts,
    record_approval,
)
from outreach_pipeline.db.pool import get_pool

# --- Config ---

# ENVIRONMENT gates "required in production" checks. Defaults to 'dev' so
# local langgraph dev / uvicorn continues to work without extra setup.
# Set ENVIRONMENT=production in the cloud entrypoint (reads from SSM).
ENVIRONMENT = os.environ.get("ENVIRONMENT", "dev").lower()
IS_PRODUCTION = ENVIRONMENT == "production"

JWT_SECRET = os.environ.get("JWT_SECRET", "")
if not JWT_SECRET:
    if IS_PRODUCTION:
        raise RuntimeError(
            "JWT_SECRET is required in production. Set it via env var "
            "(e.g. from SSM Parameter Store /outreach/JWT_SECRET). "
            "A missing JWT_SECRET means sessions can be forged."
        )
    import warnings
    JWT_SECRET = "dev-only-insecure-secret"
    warnings.warn("JWT_SECRET not set — using insecure default. Set JWT_SECRET env var in production.")

JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = 24
COOKIE_NAME = "outreach_session"
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "false").lower() == "true"  # True on EC2 with HTTPS
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:3000")

# Email allowlist — comma-separated. If set, only these emails can sign
# up or log in. Unset = anyone can (local dev). Checked after email
# normalization so casing doesn't matter.
_allowed_raw = os.environ.get("ALLOWED_EMAILS", "").strip()
ALLOWED_EMAILS: set[str] | None = (
    {e.strip().lower() for e in _allowed_raw.split(",") if e.strip()}
    if _allowed_raw else None
)

# Google OAuth
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")


def _allowed_cors_origins() -> list[str]:
    """Build the CORS allowlist.

    Dev: includes localhost:3000 automatically so `npm run dev` Just Works.
    Prod: only FRONTEND_URL (+ anything in CORS_EXTRA_ORIGINS for multi-domain
    setups). Never auto-appends localhost in prod — if a dev machine is
    allowed it's explicit, not accidental.
    """
    origins: list[str] = []
    if FRONTEND_URL:
        origins.append(FRONTEND_URL)
    extra = os.environ.get("CORS_EXTRA_ORIGINS", "").strip()
    if extra:
        origins.extend(o.strip() for o in extra.split(",") if o.strip())
    if not IS_PRODUCTION and "http://localhost:3000" not in origins:
        origins.append("http://localhost:3000")
    return origins


# --- Lifespan ---

@asynccontextmanager
async def lifespan(app):
    """Production lifespan: init DB pool + Postgres checkpointer + store."""
    from outreach_pipeline.db.pool import init_pool, close_pool, _get_database_url
    # Announce the active model at startup — CloudWatch Logs can then
    # answer "which model is prod really running?" in one grep without
    # needing to SSM-exec into the box.
    import logging
    from outreach_pipeline.graph.agent import MODEL as _OUTREACH_MODEL
    logging.getLogger("outreach").warning(
        "startup: OUTREACH_MODEL=%s", _OUTREACH_MODEL,
    )
    await init_pool()

    # Warm up ChromaDB embedding model — first query loads the model (~5s cold start).
    # Doing it here avoids the penalty on the first user request.
    try:
        from outreach_pipeline.adapters.chromadb_adapter import ChromaDBAdapter
        _rag = ChromaDBAdapter()
        await _rag.aquery("warmup", top_k=1, collection_name="sales_materials")
    except Exception:
        pass  # Non-fatal — first query will just be slow

    db_url = _get_database_url()

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from langgraph.store.postgres import AsyncPostgresStore
    async with AsyncPostgresSaver.from_conn_string(db_url) as checkpointer:
        await checkpointer.setup()
        async with AsyncPostgresStore.from_conn_string(db_url) as store:
            await store.setup()

            from outreach_pipeline.graph import compile_graph
            compiled = compile_graph(checkpointer=checkpointer, store=store)
            app.state.graph = compiled

            # AG-UI endpoint with auth
            from ag_ui_langgraph import LangGraphAgent as AGUIAgent
            from ag_ui_langgraph.endpoint import RunAgentInput
            from ag_ui.encoder import EventEncoder
            from starlette.responses import StreamingResponse

            # Install our ag_ui_langgraph patches — JSON-tolerant message
            # normalizer + STATE_SNAPSHOT slimmer. See patches.py for
            # rationale on each.
            from outreach_pipeline.patches import apply_ag_ui_patches
            apply_ag_ui_patches()

            agent = AGUIAgent(name="outreach", graph=compiled)

            @app.post("/copilotkit")
            async def copilotkit_endpoint(request: Request):
                import copy
                user = await get_current_user(request)
                body = await request.json()
                body.setdefault("context", [])
                input_data = RunAgentInput(**body)

                req_agent = copy.copy(agent)
                req_agent.config = copy.deepcopy(agent.config or {})
                req_agent.config.setdefault("configurable", {})
                req_agent.config["recursion_limit"] = 75

                # Authenticated identity is part of LangGraph config so tools
                # and checkpoint ownership cannot fall back to an anonymous
                # shared "default" user.
                user_id = user.id
                req_agent.config["configurable"]["user_id"] = user_id

                # Enforce thread ownership. Threads are tied to the user
                # that first touches them. Because the frontend stores
                # thread metadata in localStorage, a user switching on
                # the same browser could otherwise reuse the prior
                # user's thread IDs and load their checkpointed history.
                thread_id = getattr(input_data, "thread_id", None) or body.get("threadId")
                if thread_id and user_id:
                    from outreach_pipeline.db.threads import (
                        get_thread as _get_thread, create_thread as _create_thread,
                    )
                    existing = await _get_thread(thread_id)
                    if existing:
                        if existing.get("user_id") and existing["user_id"] != user_id:
                            raise HTTPException(
                                status_code=403,
                                detail="Thread belongs to a different user",
                            )
                    else:
                        # Claim the thread for this user on first use.
                        pool = get_pool()
                        await pool.execute(
                            "INSERT INTO threads (id, title, user_id, status, created_at, updated_at) "
                            "VALUES ($1, $2, $3, 'active', $4, $4) "
                            "ON CONFLICT (id) DO NOTHING",
                            thread_id, "", user_id, datetime.now(timezone.utc),
                        )

                accept = request.headers.get("accept")
                encoder = EventEncoder(accept=accept)

                def _extract_last_ai_text(snapshot_event) -> str:
                    """Pull the last AI message's text from a STATE_SNAPSHOT.

                    Used to synthesize TEXT events when the agent responded
                    via a direct AIMessage return (no LLM streaming).
                    """
                    snap = getattr(snapshot_event, "snapshot", None)
                    if not snap:
                        return ""
                    msgs = snap.get("messages", []) if isinstance(snap, dict) else []
                    for m in reversed(msgs):
                        mtype = ""
                        content = ""
                        if hasattr(m, "type"):
                            mtype = m.type
                            content = getattr(m, "content", "")
                        elif isinstance(m, dict):
                            mtype = m.get("type", m.get("role", ""))
                            content = m.get("content", "")
                        if mtype in ("ai", "assistant") and content:
                            # Skip tool-call-only messages (no human-readable text)
                            tc = (getattr(m, "tool_calls", None) if hasattr(m, "tool_calls")
                                  else m.get("tool_calls") if isinstance(m, dict) else None)
                            if tc:
                                continue
                            return content if isinstance(content, str) else str(content)
                    return ""

                from ag_ui.core import EventType as _EvType
                import re as _server_re
                _JSON_OBJECT_RE = _server_re.compile(r'^\s*\{.*\}\s*$', _server_re.DOTALL)
                # Tools whose internal LLM calls should not stream text
                _SILENT_TOOLS = {"score_engagement", "enrich_contact", "draft_email", "delete_contact"}

                # Tools that use useRenderTool cards — keep their TOOL_CALL_RESULT
                # so the card can transition to "complete" with result data.
                _CARD_TOOLS = {"score_engagement", "enrich_contact"}

                async def event_generator():
                    active_silent_tools: int = 0
                    silent_tool_ids: set[str] = set()
                    suppressed_msg_ids: set[str] = set()
                    _last_snapshot = None  # deferred STATE_SNAPSHOT
                    _text_emitted = False  # track whether TEXT events were emitted
                    _run_started = False  # track whether RUN_STARTED was emitted

                    async for event in req_agent.run(input_data):
                        etype = getattr(event, "type", None)

                        # Skip events CopilotKit doesn't need — reduces
                        # event count by ~70% and prevents Firefox freeze.
                        if etype in (
                            _EvType.RAW,               # internal LangGraph events
                            _EvType.MESSAGES_SNAPSHOT,  # causes UI cards to disappear
                            _EvType.STEP_STARTED,       # internal node transitions
                            _EvType.STEP_FINISHED,      # internal node transitions
                        ):
                            continue

                        # Deduplicate RUN_STARTED. When a thread has active
                        # interrupts, ag_ui_langgraph emits RUN_STARTED twice
                        # — once from agent.run() and again inside
                        # events_to_dispatch. The client's verifyEvents
                        # rejects the second one ("run is still active"),
                        # killing the whole stream. Swallow any RUN_STARTED
                        # after the first; reset on RUN_FINISHED.
                        if etype == _EvType.RUN_STARTED:
                            if _run_started:
                                continue
                            _run_started = True

                        # Defer STATE_SNAPSHOT — hold latest, emit once at RUN_FINISHED
                        if etype == _EvType.STATE_SNAPSHOT:
                            _last_snapshot = event
                            continue

                        # Track silent tool execution
                        if etype == _EvType.TOOL_CALL_START:
                            name = getattr(event, "tool_call_name", "")
                            if name in _SILENT_TOOLS:
                                active_silent_tools += 1
                                # Only suppress TOOL_CALL_RESULT for non-card tools
                                if name not in _CARD_TOOLS:
                                    tcid = getattr(event, "tool_call_id", "")
                                    if tcid:
                                        silent_tool_ids.add(tcid)
                        elif etype == _EvType.TOOL_CALL_RESULT:
                            tcid = getattr(event, "tool_call_id", "")
                            if tcid in silent_tool_ids:
                                silent_tool_ids.discard(tcid)
                                if active_silent_tools > 0:
                                    active_silent_tools -= 1
                                continue  # suppress result for non-card silent tools
                            if active_silent_tools > 0:
                                active_silent_tools -= 1

                        # While silent tools are running, suppress text events
                        if active_silent_tools > 0 and etype in (
                            _EvType.TEXT_MESSAGE_START,
                            _EvType.TEXT_MESSAGE_CONTENT,
                            _EvType.TEXT_MESSAGE_END,
                        ):
                            if etype == _EvType.TEXT_MESSAGE_START:
                                mid = getattr(event, "message_id", "")
                                if mid:
                                    suppressed_msg_ids.add(mid)
                            continue

                        # Suppress START/CONTENT/END for already-flagged messages
                        if etype in (_EvType.TEXT_MESSAGE_START, _EvType.TEXT_MESSAGE_CONTENT, _EvType.TEXT_MESSAGE_END):
                            mid = getattr(event, "message_id", "")
                            if mid in suppressed_msg_ids:
                                if etype == _EvType.TEXT_MESSAGE_END:
                                    suppressed_msg_ids.discard(mid)
                                continue

                        # Track whether any TEXT events were emitted (unsuppressed)
                        if etype == _EvType.TEXT_MESSAGE_END:
                            _text_emitted = True

                        # Before RUN_FINISHED: emit deferred snapshot + synthesize
                        # TEXT events if none were streamed. This covers graph nodes
                        # that return AIMessage directly (pipeline_node, etc.)
                        # without going through the LLM — ag_ui_langgraph only
                        # emits TEXT from on_chat_model_stream, so non-LLM responses
                        # would be invisible to CopilotKit without this.
                        if etype == _EvType.RUN_FINISHED:
                            if _last_snapshot:
                                yield encoder.encode(_last_snapshot)

                            if not _text_emitted and _last_snapshot:
                                _synth_text = _extract_last_ai_text(_last_snapshot)
                                if _synth_text:
                                    from ag_ui.core import (
                                        TextMessageStartEvent,
                                        TextMessageContentEvent,
                                        TextMessageEndEvent,
                                    )
                                    _synth_id = str(uuid.uuid4())
                                    yield encoder.encode(TextMessageStartEvent(
                                        message_id=_synth_id, role="assistant",
                                    ))
                                    yield encoder.encode(TextMessageContentEvent(
                                        message_id=_synth_id, delta=_synth_text,
                                    ))
                                    yield encoder.encode(TextMessageEndEvent(
                                        message_id=_synth_id,
                                    ))

                            _last_snapshot = None
                            _run_started = False  # next run will start fresh

                        yield encoder.encode(event)

                return StreamingResponse(
                    event_generator(),
                    media_type=encoder.get_content_type(),
                )

            @app.get("/copilotkit/health")
            def copilotkit_health(user: User = Depends(get_current_user)):
                return {"status": "ok", "agent": {"name": agent.name}}

            # Suggester agent — serves CopilotKit's dynamic-suggestion
            # mechanism. The main Outreach graph routes by intent and
            # its specialists don't expose `copilotkitSuggest`, so the
            # built-in `providerAgentId` is pointed here instead.
            from outreach_pipeline.graph.suggester import build_suggester_graph
            suggester_graph = build_suggester_graph()
            suggester_agent = AGUIAgent(name="suggester", graph=suggester_graph)

            @app.post("/copilotkit-suggester")
            async def copilotkit_suggester_endpoint(request: Request):
                import copy
                from ag_ui.core import EventType as SuggEvType
                user = await get_current_user(request)
                body = await request.json()
                body.setdefault("context", [])
                input_data = RunAgentInput(**body)

                req_agent = copy.copy(suggester_agent)
                req_agent.config = copy.deepcopy(suggester_agent.config or {})
                req_agent.config.setdefault("configurable", {})
                req_agent.config["configurable"]["user_id"] = user.id

                accept = request.headers.get("accept")
                encoder = EventEncoder(accept=accept)

                async def suggester_event_generator():
                    _run_started = False
                    async for event in req_agent.run(input_data):
                        etype = getattr(event, "type", None)
                        # Drop noise; we only care about RUN + TOOL events
                        if etype in (
                            SuggEvType.RAW,
                            SuggEvType.MESSAGES_SNAPSHOT,
                            SuggEvType.STATE_SNAPSHOT,
                            SuggEvType.STATE_DELTA,
                            SuggEvType.STEP_STARTED,
                            SuggEvType.STEP_FINISHED,
                        ):
                            continue
                        if etype == SuggEvType.RUN_STARTED:
                            if _run_started:
                                continue
                            _run_started = True
                        if etype == SuggEvType.RUN_FINISHED:
                            _run_started = False
                        yield encoder.encode(event)

                return StreamingResponse(
                    suggester_event_generator(),
                    media_type=encoder.get_content_type(),
                )

            yield

    await close_pool()


app = FastAPI(title="Outreach Pipeline API", version="0.1.0", lifespan=lifespan)

# GZIP compression — reduces 143KB JSON payloads to ~15KB
from fastapi.middleware.gzip import GZipMiddleware
app.add_middleware(GZipMiddleware, minimum_size=1000)


# --- Rate limiting ---------------------------------------------------------
# Guards /auth/* against credential stuffing. Keyed by client IP. Tight
# budget (5/min for login, 3/min for signup) — legitimate users retry
# once or twice; bots pound thousands.
#
# Implemented as a dependency (not slowapi's decorator) because the
# `from __future__ import annotations` directive on this module turns
# every route parameter into a ForwardRef, which slowapi's wrap-based
# decorator can't resolve. A plain Depends() side-steps the whole
# annotation-resolution dance.

import time as _time_mod
from collections import deque

_RATE_BUCKETS: dict[tuple[str, str], deque[float]] = {}

def _client_ip(request: Request) -> str:
    # Prefer X-Forwarded-For left-most entry when behind the ALB; fall
    # back to the socket peer for direct hits (local dev).
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"

def _rate_limit(scope: str, per_minute: int):
    def dep(request: Request) -> None:
        ip = _client_ip(request)
        key = (scope, ip)
        now = _time_mod.monotonic()
        bucket = _RATE_BUCKETS.setdefault(key, deque())
        cutoff = now - 60.0
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= per_minute:
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded ({per_minute}/min). Try again shortly.",
            )
        bucket.append(now)
    return dep


# --- Security headers -------------------------------------------------------
# Defence-in-depth. HSTS reinforces the DNS-validated HTTPS deployment;
# browsers cache it, so every response should carry it. Other headers close
# common clickjacking / MIME-sniff / referrer-leak gaps.
#
# Implemented as a pure-ASGI middleware (not BaseHTTPMiddleware) so SSE
# streams aren't buffered. BaseHTTPMiddleware wraps the response body
# through an internal queue, which holds every byte until the stream
# closes — for /copilotkit that means the whole agent turn materialises
# before anything reaches the browser. This version only inspects the
# response-start message to append headers; body chunks flow straight
# through.

_SECURITY_HEADERS = (
    (b"strict-transport-security", b"max-age=63072000; includeSubDomains"),
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
    (
        b"content-security-policy",
        (
            b"default-src 'self'; "
            b"script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
            b"style-src 'self' 'unsafe-inline'; "
            b"img-src 'self' data: https:; "
            b"connect-src 'self' https:; "
            b"frame-ancestors 'none'; "
            b"base-uri 'self'; "
            b"form-action 'self'"
        ),
    ),
)


class SecurityHeadersMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                existing = {k.lower() for k, _ in message.get("headers", [])}
                headers = list(message.get("headers", []))
                for name, value in _SECURITY_HEADERS:
                    if name not in existing:
                        headers.append((name, value))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)


app.add_middleware(SecurityHeadersMiddleware)


# --- Health + readiness probes ---
# Registered at module scope (not in lifespan) so they respond even if the
# agent graph or DB pool fails to initialize — /health for ALB liveness,
# /ready for readiness gating. Keep both cheap.

@app.get("/health")
async def health() -> dict:
    """Liveness probe — just confirms the process is up and responding.

    Used by the ALB target group health check. Does NOT touch the DB so
    that transient DB issues don't flap the target group.
    """
    return {"status": "ok"}


@app.get("/ready")
async def ready(response: Response) -> dict:
    """Readiness probe — confirms the app can actually serve traffic.

    Checks the Postgres pool is initialized and answering. Returns 503 if
    the DB isn't reachable; ALB / orchestrator should withhold traffic
    until this goes green.
    """
    import asyncio
    try:
        pool = get_pool()
    except RuntimeError:
        response.status_code = 503
        return {"status": "not_ready", "reason": "db pool not initialized"}

    try:
        # Tight timeout — readiness must be fast. If Postgres is slow,
        # we'd rather fail readiness than block the probe.
        async with asyncio.timeout(2.0):
            val = await pool.fetchval("SELECT 1")
        if val != 1:
            response.status_code = 503
            return {"status": "not_ready", "reason": "db sanity check failed"}
    except (asyncio.TimeoutError, Exception) as e:
        response.status_code = 503
        return {"status": "not_ready", "reason": f"db: {type(e).__name__}"}

    return {"status": "ready"}

# CORS — needed for credentials: "include" from frontend on different port.
# Pinned to the verbs + headers actually used; wildcards with
# allow_credentials=True widen any CSRF/CORS gap unnecessarily.
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_cors_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Requested-With"],
)


# --- JWT + Cookie helpers ---

def _create_token(user_id: str, email: str, role: str) -> str:
    import jwt
    import secrets
    payload = {
        "sub": user_id,
        "email": email,
        "role": role,
        "jti": secrets.token_urlsafe(16),
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRY_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def _decode_token(token: str) -> dict:
    import jwt
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid token")


async def _decode_and_check_revocation(token: str) -> dict:
    """Decode + reject if the jti has been revoked. Async so callers
    can hit the DB. Pool is available because any request past the
    router/lifespan hook has init_pool()'d."""
    payload = _decode_token(token)
    jti = payload.get("jti")
    if not jti:
        # Pre-revocation-feature tokens (no jti) are still accepted
        # until they expire; they can't be revoked, but they can't be
        # forged either.
        return payload
    try:
        pool = get_pool()
        row = await pool.fetchrow("SELECT 1 FROM revoked_tokens WHERE jti=$1", jti)
        if row:
            raise HTTPException(status_code=401, detail="Token revoked")
    except RuntimeError:
        # DB not initialised (e.g. langgraph dev before first request).
        # Fail open — the authn layer still validated the signature.
        pass
    return payload


async def _revoke_token(token: str):
    """Insert the jti into revoked_tokens and opportunistically GC
    expired rows. Silently no-ops for legacy tokens without jti."""
    import jwt
    try:
        payload = jwt.decode(
            token, JWT_SECRET, algorithms=[JWT_ALGORITHM],
            options={"verify_exp": False},
        )
    except jwt.PyJWTError:
        return
    jti = payload.get("jti")
    exp = payload.get("exp")
    sub = payload.get("sub", "")
    if not jti or not exp:
        return
    try:
        pool = get_pool()
        await pool.execute(
            """INSERT INTO revoked_tokens (jti, user_id, expires_at)
               VALUES ($1, $2, to_timestamp($3))
               ON CONFLICT (jti) DO NOTHING""",
            jti, sub, exp,
        )
        # Best-effort GC so the table stays bounded.
        await pool.execute("DELETE FROM revoked_tokens WHERE expires_at < NOW()")
    except RuntimeError:
        pass


def _set_auth_cookie(response: Response, token: str):
    """Set JWT as httpOnly cookie."""
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
        max_age=JWT_EXPIRY_HOURS * 3600,
        path="/",
    )


def _extract_user_id(request: Request) -> str:
    """Extract user_id from cookie (primary) or Authorization header (fallback)."""
    # Try httpOnly cookie first
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        # Fallback: Authorization header (for dev/testing)
        auth = request.headers.get("authorization", "")
        if auth.startswith("Bearer ") and len(auth) > 7:
            token = auth[7:]
    if token:
        try:
            payload = _decode_token(token)
            return payload.get("sub", "default")
        except Exception:
            pass
    return "default"


class User(BaseModel):
    id: str
    email: str
    name: str
    role: str


_ROLE_CAPABILITIES: dict[str, set[str]] = {
    "admin": {"*"},
    # Standard authenticated SDR workflow. These are still explicit authz
    # boundaries so sensitive routes cannot accidentally become login-only.
    "user": {
        "contacts:write",
        "contacts:read_pii",
        "engagements:mark_sent",
        "threads:approve",
    },
    "sender": {
        "contacts:write",
        "contacts:read_pii",
        "engagements:mark_sent",
        "threads:approve",
    },
    "viewer": set(),
}


def _has_capability(user: User, capability: str) -> bool:
    caps = _ROLE_CAPABILITIES.get(user.role, set())
    return "*" in caps or capability in caps


def require_capability(capability: str):
    async def dependency(user: User = Depends(get_current_user)) -> User:
        if not _has_capability(user, capability):
            raise HTTPException(status_code=403, detail="Insufficient privileges")
        return user
    return dependency


def require_role(*roles: str):
    async def dependency(user: User = Depends(get_current_user)) -> User:
        if user.role not in set(roles):
            raise HTTPException(status_code=403, detail="Insufficient role")
        return user
    return dependency


async def get_current_user(request: Request) -> User:
    """Extract user from cookie or Authorization header.
    Verifies the token isn't in the revocation table."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        auth = request.headers.get("authorization", "")
        if auth.startswith("Bearer ") and len(auth) > 7:
            token = auth[7:]
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = await _decode_and_check_revocation(token)
    user_row = await get_user(payload["sub"])
    if not user_row:
        raise HTTPException(status_code=401, detail="User not found")
    return User(id=user_row["id"], email=user_row["email"],
                name=user_row["name"], role=user_row["role"])


# --- Google OAuth ---

@app.get("/auth/google/login", dependencies=[Depends(_rate_limit("google_login", 10))])
async def google_login(request: Request):
    """Redirect to Google consent screen."""
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=501, detail="Google OAuth not configured — set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET")
    from authlib.integrations.starlette_client import OAuth
    oauth = OAuth()
    oauth.register(
        name="google",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )
    redirect_uri = str(request.url_for("google_callback"))
    return await oauth.google.authorize_redirect(request, redirect_uri)


@app.get("/auth/google/callback")
async def google_callback(request: Request):
    """Handle Google OAuth callback — create/login user, set cookie, redirect."""
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=501, detail="Google OAuth not configured")
    from authlib.integrations.starlette_client import OAuth
    oauth = OAuth()
    oauth.register(
        name="google",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )
    token_data = await oauth.google.authorize_access_token(request)
    userinfo = token_data.get("userinfo", {})
    email = userinfo.get("email", "").strip().lower()
    name = userinfo.get("name", email.split("@")[0])

    if not email:
        raise HTTPException(status_code=400, detail="Google did not return an email")

    # Find or create user
    pool = get_pool()
    row = await pool.fetchrow("SELECT * FROM users WHERE email=$1", email)
    if row:
        user_id = row["id"]
        role = row["role"]
    else:
        user_id = str(uuid.uuid4())
        role = "user"
        await create_user(user_id, email, name, role=role)

    jwt_token = _create_token(user_id, email, role)
    response = RedirectResponse(url=FRONTEND_URL, status_code=302)
    _set_auth_cookie(response, jwt_token)
    return response


# --- Fallback email auth (dev/testing) ---

_EMAIL_RE = _re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")


def _validate_email(email: str) -> str:
    email = email.strip().lower()
    if not email or not _EMAIL_RE.match(email):
        raise HTTPException(status_code=422, detail="Invalid email address")
    return email


MIN_PASSWORD_LENGTH = 8


class LoginRequest(BaseModel):
    email: str
    password: str = ""


class SignupRequest(BaseModel):
    email: str
    name: str
    password: str = ""


@app.post("/auth/login", dependencies=[Depends(_rate_limit("login", 5))])
async def login(req: LoginRequest, response: Response):
    """Email + password login. Returns cookie + JSON."""
    import bcrypt
    email = _validate_email(req.email)
    if ALLOWED_EMAILS and email not in ALLOWED_EMAILS:
        raise HTTPException(status_code=403, detail="This email is not authorized")
    pool = get_pool()
    row = await pool.fetchrow("SELECT * FROM users WHERE email=$1", email)
    if not row:
        raise HTTPException(status_code=401, detail="Unknown email or wrong password")
    user = dict(row)
    if not req.password:
        raise HTTPException(status_code=401, detail="Password required")
    pw_hash = user.get("password_hash", "")
    if not pw_hash:
        raise HTTPException(
            status_code=401,
            detail="This account has no password. Please sign up again with a password.",
        )
    if not bcrypt.checkpw(req.password.encode(), pw_hash.encode()):
        raise HTTPException(status_code=401, detail="Unknown email or wrong password")
    token = _create_token(user["id"], user["email"], user["role"])
    _set_auth_cookie(response, token)
    return {"token": token, "user": {"id": user["id"], "email": user["email"],
                                      "name": user["name"], "role": user["role"]}}


@app.post("/auth/signup", dependencies=[Depends(_rate_limit("signup", 3))])
async def signup(req: SignupRequest, response: Response):
    """Email + password signup. Returns cookie + JSON."""
    import bcrypt
    email = _validate_email(req.email)
    if ALLOWED_EMAILS and email not in ALLOWED_EMAILS:
        raise HTTPException(status_code=403, detail="This email is not authorized")
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Name is required")
    if not req.password or len(req.password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=422,
            detail=f"Password must be at least {MIN_PASSWORD_LENGTH} characters",
        )
    pw_hash = bcrypt.hashpw(req.password.encode(), bcrypt.gensalt()).decode()
    pool = get_pool()
    existing = await pool.fetchrow("SELECT id FROM users WHERE email=$1", email)
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")
    user_id = str(uuid.uuid4())
    await create_user(user_id, email, name, role="user", password_hash=pw_hash)
    token = _create_token(user_id, email, "user")
    _set_auth_cookie(response, token)
    return {"token": token, "user": {"id": user_id, "email": email,
                                      "name": name, "role": "user"}}


@app.get("/auth/me")
async def me(user: User = Depends(get_current_user)):
    return {"id": user.id, "email": user.email, "name": user.name, "role": user.role}


@app.post("/auth/logout")
async def logout(request: Request, response: Response):
    """Clear the session cookie AND revoke the token so it can't
    be replayed before expiry."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        auth = request.headers.get("authorization", "")
        if auth.startswith("Bearer ") and len(auth) > 7:
            token = auth[7:]
    if token:
        await _revoke_token(token)
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"success": True}


# --- Thread Endpoints ---

class CreateThreadRequest(BaseModel):
    title: str


@app.post("/threads")
async def create_thread_endpoint(req: CreateThreadRequest, user: User = Depends(get_current_user)):
    thread_id = await create_thread(req.title, user.id)
    thread = await get_thread(thread_id)
    return thread


@app.get("/threads")
async def list_threads_endpoint(user: User = Depends(get_current_user)):
    threads = await list_threads(user_id=user.id)
    # Backfill missing titles from the first human message in the
    # LangGraph checkpoint. Titles used to live only in localStorage;
    # threads restored from the DB have title="" until this runs.
    # Persist what we derive so the next request can skip the lookup.
    pool = get_pool()
    for t in threads:
        if t.get("title"):
            continue
        tid = t.get("id")
        if not tid:
            continue
        try:
            title = await _first_message_title(tid)
        except Exception:
            title = ""
        if title:
            t["title"] = title
            try:
                await pool.execute(
                    "UPDATE threads SET title=$1 WHERE id=$2 AND title=''",
                    title, tid,
                )
            except Exception:
                pass
    return threads


async def _first_message_title(thread_id: str) -> str:
    """Return a short display title from the thread's earliest human
    message. Uses the compiled graph's own checkpointer to deserialize
    the `messages` blob, which AsyncPostgresSaver stores as bytea.
    """
    graph = getattr(app.state, "graph", None)
    if graph is None:
        return ""
    try:
        state = await graph.aget_state(
            {"configurable": {"thread_id": thread_id}}
        )
    except Exception:
        return ""
    messages = (state.values or {}).get("messages") or []
    for m in messages:
        mtype = getattr(m, "type", None) or (
            m.get("type") if isinstance(m, dict) else ""
        )
        content = getattr(m, "content", None) if not isinstance(m, dict) else m.get("content", "")
        if mtype in ("human", "user") and isinstance(content, str):
            text = content.strip()
            if text:
                return text[:37] + "..." if len(text) > 40 else text
    return ""


@app.get("/threads/{thread_id}")
async def get_thread_endpoint(thread_id: str, user: User = Depends(get_current_user)):
    return await _verify_thread_access(thread_id, user)


class UpdateContactWebsiteRequest(BaseModel):
    website: str


@app.patch("/contacts/{contact_id}/website")
async def update_contact_website(
    contact_id: str,
    req: UpdateContactWebsiteRequest,
    user: User = Depends(require_capability("contacts:write")),
):
    """Set a human-corrected website override for a contact.

    Stored under contacts.properties.website_override so future
    enrichments prefer it over the email_domain heuristic. Pass an
    empty string to clear the override.
    """
    website = (req.website or "").strip()
    # Normalize: add scheme if the user typed just a domain
    if website and not website.startswith(("http://", "https://")):
        website = f"https://{website}"
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT properties FROM contacts WHERE id=$1", str(contact_id),
    )
    if not row:
        raise HTTPException(status_code=404, detail="Contact not found")
    import json as _json
    props = row["properties"] or {}
    if isinstance(props, str):
        try:
            props = _json.loads(props)
        except Exception:
            props = {}
    if website:
        props["website_override"] = website
    else:
        props.pop("website_override", None)
    # Clear the cached enrichment so the NEXT enrich_contact call
    # re-runs against the new website instead of returning the stale
    # "Already enriched" short-circuit. Without this, correcting the
    # website is a no-op on subsequent re-enriches.
    await pool.execute(
        "UPDATE contacts SET properties=$1, company_context='', enrichment_confidence='' WHERE id=$2",
        _json.dumps(props), str(contact_id),
    )
    return {"contact_id": str(contact_id), "website_override": website}


@app.get("/contacts/{contact_id}/email")
async def get_contact_email(
    contact_id: str,
    user: User = Depends(require_capability("contacts:read_pii")),
):
    """Fetch a contact's email address on-demand from HubSpot.

    PII-on-demand: the email is NOT persisted locally (contacts table
    stores only `has_email` + `email_domain` — see db/contacts.py's
    _strip_pii). This endpoint hits HubSpot directly and returns the
    live value so the SDR can see it while viewing a draft without
    triggering a write to the DB.
    """
    from outreach_pipeline.tools.pii import afetch_contact_pii
    # Reject obviously non-numeric IDs early — HubSpot uses numeric
    # VIDs; anything else is a stub / test fixture and won't return
    # anything useful.
    pii = await afetch_contact_pii(str(contact_id), ["email"])
    return {"contact_id": str(contact_id), "email": pii.get("email", "")}


@app.get("/contacts/{contact_id}/firstname")
async def get_contact_firstname(
    contact_id: str,
    user: User = Depends(require_capability("contacts:read_pii")),
):
    """Fetch a contact's first name on-demand from HubSpot.

    PII-on-demand: the first name is NOT persisted locally. This mirrors
    the approved-email card's email reveal so SDRs can personalize the
    approved draft while keeping the pipeline DB PII-free.
    """
    from outreach_pipeline.tools.pii import afetch_contact_pii

    pii = await afetch_contact_pii(str(contact_id), ["firstname"])
    return {"contact_id": str(contact_id), "firstname": pii.get("firstname", "")}


@app.post("/engagements/{engagement_id}/mark-sent")
async def mark_engagement_sent(
    engagement_id: int,
    user: User = Depends(require_capability("engagements:mark_sent")),
):
    """Record that the SDR sent this email from their own client.

    Accepts two upstream states:
      - engagement.approved                → one hop: approved → sent
      - engagement.pending_approval + draft.approved
                                           → two hops: p_a → approved → sent
        (matches the common post-interrupt state: the Draft Review
        middleware flips draft.status to `approved` but doesn't
        touch the engagement, so pending_approval + approved-draft
        is what the SDR actually sees day-to-day).

    Any other state returns 400 with the specific reason.
    """
    from outreach_pipeline.db.engagements import get_engagement
    from outreach_pipeline.db.drafts import get_drafts as _db_drafts
    from outreach_pipeline.tools.engagements import update_engagement_status

    eng = await get_engagement(engagement_id)
    if not eng:
        raise HTTPException(status_code=404, detail="Engagement not found")
    eng_status = eng.get("status", "")

    if eng_status == "pending_approval":
        # Verify the draft was actually approved before auto-advancing.
        drafts = await _db_drafts(engagement_id=engagement_id, limit=1)
        latest = drafts[0] if drafts else {}
        if (latest.get("status") or "") != "approved":
            raise HTTPException(
                status_code=400,
                detail=(
                    "Draft hasn't been approved yet — approve it on the "
                    "Draft Review card first."
                ),
            )
        step = await update_engagement_status(engagement_id, "approved")
        if not step.get("success"):
            raise HTTPException(status_code=400, detail=step.get("error", ""))

    result = await update_engagement_status(engagement_id, "sent")
    if not result.get("success"):
        raise HTTPException(
            status_code=400,
            detail=result.get("error", "transition failed"),
        )
    eng = result.get("engagement", {})
    return {
        "engagement_id": engagement_id,
        "status": eng.get("status", "sent"),
    }


class UpdateThreadRequest(BaseModel):
    title: str | None = None
    status: str | None = None


@app.patch("/threads/{thread_id}")
async def update_thread_endpoint(
    thread_id: str,
    req: UpdateThreadRequest,
    user: User = Depends(get_current_user),
):
    """Update a thread's title and/or status. Ownership enforced.

    Status is the archive toggle — "active" or "archived". Previously
    archive was a client-only localStorage flag, so a hard reload
    re-hydrated the thread from the server (which had no knowledge of
    the archive) and it reappeared.
    """
    await _verify_thread_access(thread_id, user)
    pool = get_pool()

    sets: list[str] = []
    params: list = []
    idx = 1
    if req.title is not None:
        sets.append(f"title = ${idx}")
        params.append(req.title.strip()[:200])
        idx += 1
    if req.status is not None:
        if req.status not in ("active", "archived"):
            raise HTTPException(status_code=422, detail="status must be 'active' or 'archived'")
        sets.append(f"status = ${idx}")
        params.append(req.status)
        idx += 1
    if not sets:
        return {"id": thread_id}

    sets.append(f"updated_at = ${idx}")
    from datetime import datetime, timezone
    params.append(datetime.now(timezone.utc))
    idx += 1
    params.append(thread_id)

    await pool.execute(
        f"UPDATE threads SET {', '.join(sets)} WHERE id = ${idx}",
        *params,
    )
    return {"id": thread_id, "title": req.title, "status": req.status}


async def _verify_thread_access(thread_id: str, user: User):
    """Verify thread exists and belongs to the user (or user is admin)."""
    thread = await get_thread(thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
    if thread["user_id"] != user.id and user.role != "admin":
        raise HTTPException(status_code=403, detail="Not your thread")
    return thread


@app.get("/threads/{thread_id}/messages")
async def get_messages_endpoint(thread_id: str, user: User = Depends(get_current_user)):
    await _verify_thread_access(thread_id, user)
    messages = await list_messages(thread_id)
    return messages


@app.get("/threads/{thread_id}/history")
async def get_thread_history_endpoint(
    thread_id: str, user: User = Depends(get_current_user),
):
    """Return the thread's checkpointed messages in AG-UI format.

    CopilotKit v2 HttpAgent does NOT auto-hydrate message history on
    mount — agent.messages starts empty. This endpoint lets the client
    seed it (via agent.setMessages) so past conversation is visible
    when a user clicks an existing thread.
    """
    await _verify_thread_access(thread_id, user)
    graph = getattr(app.state, "graph", None)
    if graph is None:
        return []
    try:
        state = await graph.aget_state({"configurable": {"thread_id": thread_id}})
    except Exception:
        return []
    raw = (state.values or {}).get("messages") or []

    def _tool_calls(m):
        tcs = getattr(m, "tool_calls", None)
        if not tcs and isinstance(m, dict):
            tcs = m.get("tool_calls")
        out = []
        for tc in tcs or []:
            if isinstance(tc, dict):
                tid = tc.get("id") or (tc.get("function") or {}).get("id")
                name = tc.get("name") or (tc.get("function") or {}).get("name", "")
                args = tc.get("args") or (tc.get("function") or {}).get("arguments", "")
            else:
                tid = getattr(tc, "id", None)
                name = getattr(tc, "name", "")
                args = getattr(tc, "args", "") or getattr(tc, "arguments", "")
            if not isinstance(args, str):
                try:
                    import json as _json
                    args = _json.dumps(args)
                except Exception:
                    args = str(args)
            if tid:
                out.append({"id": tid, "type": "function",
                            "function": {"name": name, "arguments": args}})
        return out

    result = []
    for i, m in enumerate(raw):
        mtype = getattr(m, "type", None) or (
            m.get("type") if isinstance(m, dict) else None
        ) or (m.get("role") if isinstance(m, dict) else None)
        content = getattr(m, "content", "") if not isinstance(m, dict) else (m.get("content") or "")
        if isinstance(content, list):
            content = "".join(
                p.get("text", "") for p in content if isinstance(p, dict)
            )
        mid = getattr(m, "id", None) or (
            m.get("id") if isinstance(m, dict) else None
        ) or f"hist-{i}"
        if mtype in ("human", "user"):
            result.append({"id": mid, "role": "user", "content": str(content)})
        elif mtype in ("ai", "assistant"):
            tcs = _tool_calls(m)
            entry = {"id": mid, "role": "assistant", "content": str(content)}
            if tcs:
                entry["toolCalls"] = tcs
            result.append(entry)
        elif mtype in ("tool",):
            tcid = getattr(m, "tool_call_id", None) or (
                m.get("tool_call_id") if isinstance(m, dict) else None
            )
            result.append({"id": mid, "role": "tool",
                           "toolCallId": tcid or "",
                           "content": str(content)})
        # Skip system / other types.
    return result


@app.get("/threads/{thread_id}/artifacts")
async def get_artifacts_endpoint(thread_id: str, user: User = Depends(get_current_user)):
    await _verify_thread_access(thread_id, user)
    artifacts = await list_artifacts(thread_id)
    return artifacts


# --- Approval Endpoint ---

class ApprovalRequest(BaseModel):
    artifact_id: int
    action: str  # "approve" or "reject"
    feedback: str = ""


@app.post("/threads/{thread_id}/approve")
async def approve_endpoint(
    thread_id: str,
    req: ApprovalRequest,
    user: User = Depends(require_capability("threads:approve")),
):
    await _verify_thread_access(thread_id, user)
    artifact = await get_artifact(req.artifact_id)
    if not artifact:
        raise HTTPException(status_code=404, detail="Artifact not found")
    if str(artifact.get("thread_id", "")) != thread_id:
        raise HTTPException(status_code=403, detail="Artifact does not belong to this thread")
    if artifact["status"] != "pending_approval":
        raise HTTPException(status_code=400, detail=f"Artifact status is '{artifact['status']}', not 'pending_approval'")

    await record_approval(req.artifact_id, user.id, req.action, feedback=req.feedback or None)
    updated = await get_artifact(req.artifact_id)
    return updated


# --- Connections Endpoint ---

@app.get("/connections")
async def get_connections(user: User = Depends(get_current_user)):
    """Report adapter connection status."""
    connections: dict[str, Any] = {}

    try:
        from outreach_pipeline.config import load_config
        config = load_config()
        connections["hubspot"] = {
            "connected": bool(config.get("hubspot_token")),
            "status": "active" if config.get("hubspot_token") else "not_configured",
        }
    except Exception:
        connections["hubspot"] = {"connected": False, "status": "config_error"}

    try:
        from outreach_pipeline.config import load_config
        config = load_config()
        connections["smtp"] = {
            "connected": bool(config.get("email_sending_enabled")),
            "status": "active" if config.get("email_sending_enabled") else "disabled",
        }
    except Exception:
        connections["smtp"] = {"connected": False, "status": "config_error"}

    rag_path = Path("rag/materials_db")
    connections["rag"] = {
        "connected": rag_path.exists(),
        "status": "indexed" if rag_path.exists() else "not_indexed",
    }

    return connections
