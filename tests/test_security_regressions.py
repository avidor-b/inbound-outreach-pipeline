"""Security regression tests for review findings.

These tests pin the intended secure posture for the app and infra. Some are
static checks because the vulnerable surfaces are registered inside the
LangGraph/FastAPI lifespan and are expensive to instantiate in unit tests.
"""
from __future__ import annotations

import inspect
import json
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).parent.parent


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _function_body(src: str, route: str) -> str:
    """Return the source slice for a FastAPI route handler."""
    route_idx = src.index(route)
    line_start = src.rfind("\n", 0, route_idx) + 1
    indent = src[line_start:route_idx]
    next_route_match = re.search(
        rf"\n{re.escape(indent)}@app\.",
        src[route_idx + len(route):],
    )
    next_route = (
        route_idx + len(route) + next_route_match.start()
        if next_route_match else -1
    )
    if next_route == -1:
        next_yield_match = re.search(
            rf"\n{re.escape(indent)}yield\b",
            src[route_idx + len(route):],
        )
        next_route = (
            route_idx + len(route) + next_yield_match.start()
            if next_yield_match else -1
        )
    return src[route_idx: next_route if next_route != -1 else len(src)]


def _terraform_block(src: str, marker: str) -> str:
    """Return the source slice for one top-level Terraform block."""
    start = src.index(marker)
    next_block_match = re.search(r"\n(?:resource|data|variable|output|locals)\s+\"", src[start + len(marker):])
    end = (
        start + len(marker) + next_block_match.start()
        if next_block_match else len(src)
    )
    return src[start:end]


class TestAgentEndpointAuth:
    def test_copilotkit_health_requires_authenticated_user(self):
        """Agent-specific health is an app fingerprint and should not be public."""
        src = _read("src/outreach_pipeline/server.py")
        body = _function_body(src, '@app.get("/copilotkit/health")')

        assert "get_current_user" in body, (
            "/copilotkit/health must require authenticated identity"
        )

    def test_copilotkit_endpoint_requires_real_authenticated_user(self):
        """The public agent execution endpoint must not fail open to user_id=default."""
        src = _read("src/outreach_pipeline/server.py")
        body = _function_body(src, '@app.post("/copilotkit")')

        assert "get_current_user" in body or "_decode_and_check_revocation" in body, (
            "/copilotkit must require authenticated, revocation-checked identity"
        )
        assert "_extract_user_id" not in body, (
            "/copilotkit must not use the fail-open helper that returns 'default'"
        )

    def test_suggester_endpoint_requires_authenticated_user(self):
        """The suggester agent is still an agent endpoint and must be auth-gated."""
        src = _read("src/outreach_pipeline/server.py")
        body = _function_body(src, '@app.post("/copilotkit-suggester")')

        assert "get_current_user" in body or "_decode_and_check_revocation" in body, (
            "/copilotkit-suggester must reject unauthenticated requests"
        )

    def test_agent_endpoints_authenticate_before_body_validation(self):
        """Unauthenticated malformed/empty agent calls must return 401, not validation 500s."""
        src = _read("src/outreach_pipeline/server.py")
        for route in ('@app.post("/copilotkit")', '@app.post("/copilotkit-suggester")'):
            body = _function_body(src, route)
            auth_pos = body.index("get_current_user")
            assert auth_pos < body.index("request.json()"), (
                f"{route} must authenticate before parsing request JSON"
            )
            assert auth_pos < body.index("RunAgentInput"), (
                f"{route} must authenticate before AG-UI input validation"
            )


class TestRoleBoundaries:
    def test_sensitive_routes_declare_role_or_capability_dependencies(self):
        """Mutating/PII routes should advertise authorization beyond login."""
        src = _read("src/outreach_pipeline/server.py")
        sensitive_routes = [
            '@app.patch("/contacts/{contact_id}/website")',
            '@app.get("/contacts/{contact_id}/email")',
            '@app.get("/contacts/{contact_id}/firstname")',
            '@app.post("/engagements/{engagement_id}/mark-sent")',
        ]

        for route in sensitive_routes:
            body = _function_body(src, route)
            assert re.search(r"require_(?:role|capability)|user\.role", body), (
                f"{route} must enforce role/capability authorization"
            )


class TestSsrfGuards:
    def test_url_fetch_and_scrape_paths_use_private_network_blocking(self):
        """Outbound URL tools must reject localhost, private, and link-local targets."""
        combined = "\n".join(
            _read(path)
            for path in [
                "src/outreach_pipeline/tools/enrich_company.py",
                "src/outreach_pipeline/tools/rag.py",
                "src/outreach_pipeline/scrape_site.py",
            ]
        )

        assert "ipaddress" in combined, "URL safety checks should inspect resolved IPs"
        for attr in ("is_private", "is_loopback", "is_link_local"):
            assert attr in combined, f"URL safety checks must reject {attr} addresses"
        assert re.search(r"socket\.(?:getaddrinfo|gethostbyname)", combined), (
            "URL safety checks must resolve hostnames before fetching"
        )


class TestDataLayerArchitecture:
    def test_fetch_mqls_save_to_db_is_async_postgres_aware(self):
        """Ingestion must not call async Postgres functions through legacy SQLite sync APIs."""
        from outreach_pipeline.fetch_mqls import save_to_db

        assert inspect.iscoroutinefunction(save_to_db), (
            "save_to_db should be async and await asyncpg-backed db functions"
        )

    def test_legacy_sqlite_module_is_not_reexported_as_primary_db_api(self):
        """The primary outreach_pipeline.db import path should not expose SQLite helpers."""
        import outreach_pipeline.db as db

        assert "connection.py" not in inspect.getsourcefile(db.get_db), (
            "legacy SQLite get_db should not be re-exported from outreach_pipeline.db"
        )


class TestSupplyChainAndState:
    def test_nextjs_version_is_above_current_security_floor(self):
        """Next 16.0-16.2.2 has public advisories; keep the direct dep patched."""
        package = json.loads(_read("frontend/package.json"))
        next_version = package["dependencies"]["next"].lstrip("^~")
        major, minor, patch = (int(part) for part in next_version.split(".")[:3])

        assert (major, minor, patch) >= (16, 2, 3), (
            "Next.js must stay on a patched 16.x release; npm audit reported "
            "16.1.6 as vulnerable and 16.2.4 as the available non-major fix"
        )

    def test_frontend_package_lock_is_committable_and_tracked(self):
        """Docker builds use npm ci, so the frontend lockfile must be versioned."""
        lockfile = ROOT / "frontend" / "package-lock.json"
        assert lockfile.exists(), "frontend/package-lock.json must exist"

        ignored = subprocess.run(
            ["git", "check-ignore", "-q", "frontend/package-lock.json"],
            cwd=ROOT,
        )
        assert ignored.returncode != 0, "frontend/package-lock.json must not be ignored"

        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "frontend/package-lock.json"],
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        assert tracked.returncode == 0, "frontend/package-lock.json must be tracked"

    def test_terraform_backend_is_not_hardcoded_but_has_secure_template(self):
        versions = _read("terraform/versions.tf")
        backend_example = _read("terraform/backend.example.hcl")
        terraform_gitignore = _read("terraform/.gitignore")

        backend = re.search(r'backend\s+"s3"\s*\{([^}]*)\}', versions, re.DOTALL)
        assert backend, "Terraform must declare the configurable S3 backend"
        assert not re.search(
            r"\b(bucket|key|region|dynamodb_table|use_lockfile)\s*=", backend.group(1)
        ), "The S3 backend declaration must not hardcode one deployment's settings"
        assert re.search(r"encrypt\s*=\s*true", backend_example), (
            "Remote-state template must enable encryption"
        )
        assert (
            "dynamodb_table" in backend_example or "use_lockfile" in backend_example
        ), "Remote-state template must include locking"
        assert "outreach-terraform-state" not in backend_example, (
            "Backend template must not point at this production account"
        )
        assert "backend.hcl" in terraform_gitignore, (
            "Per-environment backend config must stay uncommitted"
        )

    def test_alb_only_routes_canonical_app_host(self):
        """The raw ALB DNS should not serve app/API traffic; only the app FQDN should."""
        alb = _read("terraform/alb.tf")

        listener = _terraform_block(alb, 'resource "aws_lb_listener" "https"')
        assert 'type = "fixed-response"' in listener, (
            "ALB listener default action must deny raw/default hosts"
        )
        assert re.search(r"status_code\s*=\s*\"404\"", listener), (
            "ALB listener default deny should return 404"
        )
        assert 'resource "aws_lb_listener_certificate" "alb_self_signed"' not in alb, (
            "legacy raw-ALB listener certificate should not be kept as an access path"
        )

        for resource in (
            'resource "aws_lb_listener_rule" "frontend_app"',
            'resource "aws_lb_listener_rule" "backend_api_core"',
            'resource "aws_lb_listener_rule" "backend_api_engagements"',
            'resource "aws_lb_listener_rule" "backend_api_pipeline"',
        ):
            body = _terraform_block(alb, resource)
            assert "host_header" in body and "local.app_fqdn" in body, (
                f"{resource} must be scoped to the canonical app hostname"
            )

    def test_public_alb_listener_does_not_route_liveness_probe(self):
        """ALB can use /health internally, but public clients should not fingerprint it."""
        alb = _read("terraform/alb.tf")

        target_group = _terraform_block(alb, 'resource "aws_lb_target_group" "server"')
        assert re.search(r'path\s*=\s*"/health"', target_group), (
            "server target group should keep using the internal liveness path"
        )

        for resource in (
            'resource "aws_lb_listener_rule" "backend_api_core"',
            'resource "aws_lb_listener_rule" "backend_api_engagements"',
            'resource "aws_lb_listener_rule" "backend_api_pipeline"',
        ):
            body = _terraform_block(alb, resource)
            assert '"/health"' not in body, (
                f"{resource} must not publicly route /health"
            )

    def test_deploy_smokes_do_not_require_public_health_endpoint(self):
        """Post-deploy checks should verify app/API health without exposing /health publicly."""
        remote_deploy = _read("terraform/remote_deploy.sh")
        auth_smoke = _read("scripts/prod_auth_smoke.sh")

        assert "$APP_URL/health" not in remote_deploy
        assert re.search(r'\$APP_URL/"\)', remote_deploy), (
            "deploy script should check the canonical frontend root"
        )
        assert "$APP_URL/auth/me" in remote_deploy, (
            "deploy script should check the backend through an authenticated route challenge"
        )
        assert not re.search(r"probe\s+GET\s+/health\s+200", auth_smoke), (
            "unauth route smoke should not require public /health"
        )

    def test_frontend_sets_baseline_security_headers(self):
        """Next frontend should enforce browser-side defense-in-depth headers."""
        next_config = _read("frontend/next.config.ts")

        for expected in (
            "poweredByHeader: false",
            "X-Content-Type-Options",
            "X-Frame-Options",
            "Referrer-Policy",
            "Permissions-Policy",
            "Content-Security-Policy",
            "frame-ancestors 'none'",
        ):
            assert expected in next_config
