"""Tests for auth: signup, login, JWT, thread isolation, email validation."""
import pytest

from outreach_pipeline.db.threads import create_user, create_thread, add_message, get_thread
from outreach_pipeline.db.threads import create_artifact, update_artifact_status
from outreach_pipeline.db.contacts import upsert_contact
from outreach_pipeline.db.pool import get_pool
from outreach_pipeline.server import app, _create_token, _decode_token, _validate_email

from fastapi import HTTPException
from httpx import AsyncClient, ASGITransport


@pytest.fixture
async def client(db):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", cookies={}) as c:
        yield c


def _make_client():
    """Create a fresh client with no shared cookie jar (for isolation tests)."""
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test", cookies={})


async def _signup(client, email="a@example.com", name="Alice", password="testpass1234"):
    r = await client.post("/auth/signup", json={"email": email, "name": name, "password": password})
    return r


async def _login(client, email="a@example.com", password="testpass1234"):
    r = await client.post("/auth/login", json={"email": email, "password": password})
    return r


async def _auth_header(client, email="a@example.com", name="Alice"):
    r = await _signup(client, email, name)
    token = r.json()["token"]
    return {"Authorization": f"Bearer {token}"}


# ===== Signup =====

class TestSignup:
    @pytest.mark.asyncio
    async def test_signup_success(self, client):
        r = await _signup(client)
        assert r.status_code == 200
        data = r.json()
        assert data["token"]
        assert data["user"]["email"] == "a@example.com"
        assert data["user"]["name"] == "Alice"

    @pytest.mark.asyncio
    async def test_signup_duplicate_email(self, client):
        await _signup(client, "dup@example.com", "First")
        r = await _signup(client, "dup@example.com", "Second")
        assert r.status_code == 409

    @pytest.mark.asyncio
    async def test_signup_email_case_normalized(self, client):
        r = await _signup(client, "User@EXAMPLE.COM", "Bob")
        assert r.json()["user"]["email"] == "user@example.com"
        # Login with different case should work
        r2 = await _login(client, "USER@example.com")
        assert r2.status_code == 200

    @pytest.mark.asyncio
    async def test_signup_empty_email_rejected(self, client):
        r = await _signup(client, "", "Bob")
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_signup_invalid_email_rejected(self, client):
        r = await _signup(client, "not-an-email", "Bob")
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_signup_whitespace_email_rejected(self, client):
        r = await _signup(client, "   ", "Bob")
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_signup_empty_name_rejected(self, client):
        r = await _signup(client, "a@example.com", "")
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_signup_whitespace_name_rejected(self, client):
        r = await _signup(client, "a@example.com", "   ")
        assert r.status_code == 422


# ===== Login =====

class TestLogin:
    @pytest.mark.asyncio
    async def test_login_success(self, client):
        await _signup(client)
        r = await _login(client)
        assert r.status_code == 200
        assert r.json()["token"]

    @pytest.mark.asyncio
    async def test_login_unknown_email(self, client):
        r = await _login(client, "nobody@example.com")
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_login_invalid_email(self, client):
        r = await _login(client, "bad")
        assert r.status_code == 422


# ===== JWT =====

class TestJWT:
    @pytest.mark.asyncio
    async def test_valid_token_returns_user(self, client):
        h = await _auth_header(client)
        r = await client.get("/auth/me", headers=h)
        assert r.status_code == 200
        assert r.json()["email"] == "a@example.com"

    @pytest.mark.asyncio
    async def test_missing_header_returns_401(self, client):
        r = await client.get("/auth/me")
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_malformed_bearer_returns_401(self, client):
        r = await client.get("/auth/me", headers={"Authorization": "Bearer"})
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_invalid_token_returns_401(self, client):
        r = await client.get("/auth/me", headers={"Authorization": "Bearer garbage.token.here"})
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_expired_token_rejected(self, client):
        from datetime import datetime, timedelta, timezone
        import jwt
        payload = {
            "sub": "u1",
            "email": "a@example.com",
            "role": "user",
            "exp": datetime.now(timezone.utc) - timedelta(hours=1),
        }
        expired = jwt.encode(payload, "change-me-in-production", algorithm="HS256")
        r = await client.get("/auth/me", headers={"Authorization": f"Bearer {expired}"})
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_deleted_user_token_rejected(self, client):
        h = await _auth_header(client, "del@example.com", "Delete Me")
        # Verify works first
        r = await client.get("/auth/me", headers=h)
        assert r.status_code == 200
        # Delete user from DB
        pool = get_pool()
        await pool.execute("DELETE FROM users WHERE email='del@example.com'")
        # Token should now fail
        r = await client.get("/auth/me", headers=h)
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_logout_revokes_token(self, client):
        """After logout, the same bearer token must be rejected — even
        though its `exp` hasn't passed."""
        h = await _auth_header(client, "logout@example.com", "Logger")
        r = await client.get("/auth/me", headers=h)
        assert r.status_code == 200
        r = await client.post("/auth/logout", headers=h)
        assert r.status_code == 200
        r = await client.get("/auth/me", headers=h)
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_tokens_have_jti_claim(self, client):
        """Every issued token carries a jti so it can be revoked."""
        import jwt
        r = await _signup(client, "jti@example.com", "Jti")
        token = r.json()["token"]
        payload = jwt.decode(token, options={"verify_signature": False})
        assert payload.get("jti"), "issued tokens must carry a jti claim"


# ===== Rate limiting =====

class TestRateLimiting:
    @pytest.mark.asyncio
    async def test_login_rate_limited_after_burst(self, db):
        """6th login attempt in a minute from the same IP returns 429."""
        async with _make_client() as c:
            for _ in range(5):
                r = await c.post("/auth/login", json={"email": "nobody@example.com", "password": "wrong"})
                # Any non-429 is fine here — we're testing the 6th.
                assert r.status_code != 429
            r = await c.post("/auth/login", json={"email": "nobody@example.com", "password": "wrong"})
            assert r.status_code == 429


# ===== Security headers =====

class TestSecurityHeaders:
    @pytest.mark.asyncio
    async def test_hsts_and_nosniff_set(self, client):
        r = await client.get("/health")
        assert r.headers.get("strict-transport-security", "").startswith("max-age=")
        assert r.headers.get("x-content-type-options") == "nosniff"
        assert r.headers.get("x-frame-options") == "DENY"
        assert "default-src" in r.headers.get("content-security-policy", "")


# ===== Thread Isolation =====

class TestThreadIsolation:
    """Each user gets a separate httpx client to isolate cookies."""

    @pytest.mark.asyncio
    async def test_user_cannot_see_other_users_threads(self, db):
        async with _make_client() as alice, _make_client() as bob:
            await alice.post("/auth/signup", json={"email": "a@example.com", "name": "Alice", "password": "testpass1234"})
            await bob.post("/auth/signup", json={"email": "b@example.com", "name": "Bob", "password": "testpass1234"})
            r = await alice.post("/threads", json={"title": "Alice's"})
            tid = r.json()["id"]
            r = await bob.get(f"/threads/{tid}")
            assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_user_cannot_read_other_users_messages(self, db):
        async with _make_client() as alice, _make_client() as bob:
            await alice.post("/auth/signup", json={"email": "a@example.com", "name": "Alice", "password": "testpass1234"})
            await bob.post("/auth/signup", json={"email": "b@example.com", "name": "Bob", "password": "testpass1234"})
            r = await alice.post("/threads", json={"title": "Alice's"})
            tid = r.json()["id"]
            r = await bob.get(f"/threads/{tid}/messages")
            assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_user_cannot_read_other_users_artifacts(self, db):
        async with _make_client() as alice, _make_client() as bob:
            await alice.post("/auth/signup", json={"email": "a@example.com", "name": "Alice", "password": "testpass1234"})
            await bob.post("/auth/signup", json={"email": "b@example.com", "name": "Bob", "password": "testpass1234"})
            r = await alice.post("/threads", json={"title": "Alice's"})
            tid = r.json()["id"]
            r = await bob.get(f"/threads/{tid}/artifacts")
            assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_user_cannot_approve_other_users_artifacts(self, db):
        async with _make_client() as alice, _make_client() as bob:
            await alice.post("/auth/signup", json={"email": "a@example.com", "name": "Alice", "password": "testpass1234"})
            await bob.post("/auth/signup", json={"email": "b@example.com", "name": "Bob", "password": "testpass1234"})
            r = await alice.post("/threads", json={"title": "Alice's"})
            tid = r.json()["id"]
            await upsert_contact("c1", {"company": "Acme"})
            aid = await create_artifact(tid, "email_draft", "c1", {"body": "Hi"})
            await update_artifact_status(aid, "pending_approval")
            r = await bob.post(
                f"/threads/{tid}/approve",
                json={"artifact_id": aid, "action": "approve"},
            )
            assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_admin_can_access_any_thread(self, db):
        async with _make_client() as alice, _make_client() as admin_c:
            await alice.post("/auth/signup", json={"email": "a@example.com", "name": "Alice", "password": "testpass1234"})
            await create_user("admin1", "admin@example.com", "Admin", role="admin")
            admin_token = _create_token("admin1", "admin@example.com", "admin")
            r = await alice.post("/threads", json={"title": "Alice's"})
            tid = r.json()["id"]
            r = await admin_c.get(f"/threads/{tid}", headers={"Authorization": f"Bearer {admin_token}"})
            assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_list_threads_only_shows_own(self, db):
        async with _make_client() as alice, _make_client() as bob:
            await alice.post("/auth/signup", json={"email": "a@example.com", "name": "Alice", "password": "testpass1234"})
            await bob.post("/auth/signup", json={"email": "b@example.com", "name": "Bob", "password": "testpass1234"})
            await alice.post("/threads", json={"title": "Alice's"})
            await bob.post("/threads", json={"title": "Bob's"})
            r_a = await alice.get("/threads")
            r_b = await bob.get("/threads")
            assert len(r_a.json()) == 1
            assert len(r_b.json()) == 1
            assert r_a.json()[0]["title"] == "Alice's"
            assert r_b.json()[0]["title"] == "Bob's"

    @pytest.mark.asyncio
    async def test_nonexistent_thread_returns_404(self, client):
        h = await _auth_header(client)
        r = await client.get("/threads/nonexistent-id", headers=h)
        assert r.status_code == 404


# ===== Email Validation =====

class TestEmailValidation:
    def test_valid_email(self):
        assert _validate_email("user@example.com") == "user@example.com"

    def test_email_lowercased(self):
        assert _validate_email("User@EXAMPLE.COM") == "user@example.com"

    def test_email_stripped(self):
        assert _validate_email("  user@example.com  ") == "user@example.com"

    def test_empty_email_raises(self):
        with pytest.raises(HTTPException) as exc:
            _validate_email("")
        assert exc.value.status_code == 422

    def test_whitespace_email_raises(self):
        with pytest.raises(HTTPException):
            _validate_email("   ")

    def test_no_at_sign_raises(self):
        with pytest.raises(HTTPException):
            _validate_email("notanemail")

    def test_no_domain_raises(self):
        with pytest.raises(HTTPException):
            _validate_email("user@")

    def test_no_tld_raises(self):
        with pytest.raises(HTTPException):
            _validate_email("user@domain")


# ===== Cookie Auth =====

class TestCookieAuth:
    @pytest.mark.asyncio
    async def test_signup_sets_cookie(self, client):
        r = await _signup(client)
        assert r.status_code == 200
        assert "outreach_session" in r.cookies

    @pytest.mark.asyncio
    async def test_login_sets_cookie(self, client):
        await _signup(client)
        r = await _login(client)
        assert r.status_code == 200
        assert "outreach_session" in r.cookies

    @pytest.mark.asyncio
    async def test_cookie_auth_works_for_me(self, client):
        """After signup, /auth/me should work via cookie (no header needed)."""
        await _signup(client)
        r = await client.get("/auth/me")
        assert r.status_code == 200
        assert r.json()["email"] == "a@example.com"

    @pytest.mark.asyncio
    async def test_logout_clears_cookie(self, client):
        await _signup(client)
        r = await client.post("/auth/logout")
        assert r.status_code == 200
        # Cookie should be cleared — next /auth/me should fail
        # (httpx respects Set-Cookie deletions)
        r = await client.get("/auth/me")
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_cookie_httponly(self, client):
        """Cookie should be httpOnly (not accessible via JS)."""
        r = await _signup(client)
        # Check Set-Cookie header for httponly flag
        set_cookie = r.headers.get("set-cookie", "")
        assert "httponly" in set_cookie.lower()
