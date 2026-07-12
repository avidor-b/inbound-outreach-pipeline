"""Cloud-config hygiene tests — production requires explicit secrets and
config, local dev keeps working without them.

These lock in the Wave 1 security-posture change so future refactors can't
accidentally reintroduce silent localhost fallbacks or insecure defaults.
"""
from __future__ import annotations

import importlib
import os

import pytest


@pytest.fixture
def no_production_env(monkeypatch):
    """Ensure ENVIRONMENT is not set — acts like local dev."""
    monkeypatch.delenv("ENVIRONMENT", raising=False)


@pytest.fixture
def production_env(monkeypatch):
    """Set ENVIRONMENT=production for this test."""
    monkeypatch.setenv("ENVIRONMENT", "production")


class TestDatabaseUrl:
    def test_dev_falls_back_to_localhost_when_everything_missing(
        self, monkeypatch, no_production_env, tmp_path,
    ):
        # No DATABASE_URL, no config.yaml → localhost default in dev
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.chdir(tmp_path)  # no config.yaml in cwd

        from outreach_pipeline.db.pool import _get_database_url

        url = _get_database_url()
        assert url.startswith("postgresql://pipeline:pipeline@localhost")

    def test_production_raises_when_database_url_missing(
        self, monkeypatch, production_env, tmp_path,
    ):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.chdir(tmp_path)  # no config.yaml either

        from outreach_pipeline.db.pool import _get_database_url

        with pytest.raises(RuntimeError, match="DATABASE_URL is required"):
            _get_database_url()

    def test_env_var_wins_over_config_and_default(self, monkeypatch, no_production_env):
        monkeypatch.setenv("DATABASE_URL", "postgresql://prod@db/app")

        from outreach_pipeline.db.pool import _get_database_url

        assert _get_database_url() == "postgresql://prod@db/app"


class TestJwtSecret:
    """server.py reads JWT_SECRET at module import time. We re-import the
    module under different env conditions to cover both branches."""

    def _reload_server_module(self):
        import outreach_pipeline.server as srv
        return importlib.reload(srv)

    def test_dev_uses_insecure_default_with_warning(
        self, monkeypatch, no_production_env,
    ):
        monkeypatch.delenv("JWT_SECRET", raising=False)
        with pytest.warns(UserWarning, match="JWT_SECRET not set"):
            srv = self._reload_server_module()
        assert srv.JWT_SECRET == "dev-only-insecure-secret"

    def test_production_raises_when_jwt_secret_missing(
        self, monkeypatch, production_env,
    ):
        monkeypatch.delenv("JWT_SECRET", raising=False)
        with pytest.raises(RuntimeError, match="JWT_SECRET is required"):
            self._reload_server_module()


class TestCorsOrigins:
    def test_dev_appends_localhost_automatically(
        self, monkeypatch, no_production_env,
    ):
        monkeypatch.setenv("FRONTEND_URL", "http://localhost:3000")
        monkeypatch.setenv("JWT_SECRET", "x")  # avoid reload path blowing up
        srv = importlib.reload(
            __import__("outreach_pipeline.server", fromlist=["server"])
        )
        origins = srv._allowed_cors_origins()
        assert "http://localhost:3000" in origins

    def test_production_does_not_auto_append_localhost(
        self, monkeypatch, production_env,
    ):
        monkeypatch.setenv("FRONTEND_URL", "https://app.example.com")
        monkeypatch.setenv("JWT_SECRET", "prod-secret-not-insecure-default")
        srv = importlib.reload(
            __import__("outreach_pipeline.server", fromlist=["server"])
        )
        origins = srv._allowed_cors_origins()
        assert origins == ["https://app.example.com"]
        assert "http://localhost:3000" not in origins

    def test_cors_extra_origins_parsed(
        self, monkeypatch, production_env,
    ):
        monkeypatch.setenv("FRONTEND_URL", "https://app.example.com")
        monkeypatch.setenv(
            "CORS_EXTRA_ORIGINS",
            "https://preview.example.com, https://staging.example.com",
        )
        monkeypatch.setenv("JWT_SECRET", "prod-secret")
        srv = importlib.reload(
            __import__("outreach_pipeline.server", fromlist=["server"])
        )
        origins = srv._allowed_cors_origins()
        assert origins == [
            "https://app.example.com",
            "https://preview.example.com",
            "https://staging.example.com",
        ]


class TestConfigPath:
    def test_explicit_arg_wins(self, tmp_path, monkeypatch):
        """An explicit config_path argument is used as-is."""
        yaml_file = tmp_path / "explicit.yaml"
        yaml_file.write_text(
            "reports_path: /tmp\nreport_timezone: UTC\ndatabase_url: explicit://\n"
        )
        monkeypatch.setenv("CONFIG_PATH", str(tmp_path / "ignored.yaml"))
        from outreach_pipeline.config import load_config
        cfg = load_config(str(yaml_file))
        assert cfg["database_url"] == "explicit://"

    def test_config_path_env_var_used_when_no_arg(self, tmp_path, monkeypatch):
        """CONFIG_PATH env var wins when no explicit argument given."""
        yaml_file = tmp_path / "from-env.yaml"
        yaml_file.write_text(
            "reports_path: /tmp\nreport_timezone: UTC\ndatabase_url: from-env://\n"
        )
        monkeypatch.setenv("CONFIG_PATH", str(yaml_file))
        monkeypatch.chdir(tmp_path.parent)  # so relative 'config.yaml' can't shadow
        from outreach_pipeline.config import load_config
        cfg = load_config()
        assert cfg["database_url"] == "from-env://"

    def test_falls_back_to_cwd_config_yaml(self, tmp_path, monkeypatch):
        """No CONFIG_PATH, no arg → 'config.yaml' relative to cwd."""
        monkeypatch.delenv("CONFIG_PATH", raising=False)
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text(
            "reports_path: /tmp\nreport_timezone: UTC\ndatabase_url: cwd://\n"
        )
        monkeypatch.chdir(tmp_path)
        from outreach_pipeline.config import load_config
        cfg = load_config()
        assert cfg["database_url"] == "cwd://"


class TestHealthEndpoints:
    """The ALB target-group check hits /health; the orchestrator's readiness
    gate hits /ready. Both must be registered at module scope so they
    respond even if the agent graph fails to init."""

    @pytest.mark.asyncio
    async def test_health_always_returns_ok(self):
        from fastapi.testclient import TestClient
        from outreach_pipeline.server import app
        # TestClient doesn't run the lifespan — perfect for proving /health
        # works even without DB pool initialization.
        client = TestClient(app)
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}

    @pytest.mark.asyncio
    async def test_ready_returns_503_when_pool_not_initialized(self, monkeypatch):
        # Force get_pool() to raise by closing the shared pool for the
        # duration of this test, then restore.
        from fastapi.testclient import TestClient
        import outreach_pipeline.db.pool as pool_mod
        from outreach_pipeline.server import app

        original_pool = pool_mod._pool
        pool_mod._pool = None
        try:
            client = TestClient(app)
            r = client.get("/ready")
            assert r.status_code == 503
            assert r.json()["status"] == "not_ready"
        finally:
            pool_mod._pool = original_pool

    @pytest.mark.asyncio
    async def test_ready_returns_200_when_pool_healthy(self, _pool):
        # _pool fixture ensures the pool is initialized against
        # outreach_test. Use httpx.AsyncClient so the request stays on the
        # same event loop as the pool — TestClient spawns a new loop per
        # request and can't reuse the session pool.
        from httpx import ASGITransport, AsyncClient
        from outreach_pipeline.server import app
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            r = await client.get("/ready")
        assert r.status_code == 200
        assert r.json()["status"] == "ready"


@pytest.fixture(autouse=True, scope="module")
def _restore_server_module():
    """After these tests reload the server module under weird env states,
    put it back to normal so downstream tests see a clean module."""
    yield
    # Restore dev-sane env then reload so the process-wide module state is
    # what the rest of the suite expects.
    os.environ.pop("ENVIRONMENT", None)
    os.environ["JWT_SECRET"] = "test-secret-ok"
    import outreach_pipeline.server as srv
    importlib.reload(srv)
    os.environ.pop("JWT_SECRET", None)
