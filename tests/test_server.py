"""Unit tests for server.py -- FastAPI endpoints + auth (async Postgres)."""
import os

import pytest
from httpx import AsyncClient, ASGITransport

os.environ["JWT_SECRET"] = "test-secret-key-for-testing"

from outreach_pipeline.server import app  # noqa: E402
from outreach_pipeline.db.contacts import get_contact, upsert_contact  # noqa: E402
from outreach_pipeline.db.threads import (  # noqa: E402
    create_user, create_thread,
    create_artifact, update_artifact_status,
)


class TestAuth:
    @pytest.mark.asyncio
    async def test_login_creates_token(self, db):
        await create_user("u1", "alice@example.com", "Alice", "admin", password_hash="$2b$12$jNA37BRlb5.xuk8emW1OpeukXl8jpKizNl1OYg2abxLFZfZf1T/z2")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/auth/login", json={"email": "alice@example.com", "password": "testpass1234"})
        assert resp.status_code == 200
        data = resp.json()
        assert "token" in data
        assert data["user"]["email"] == "alice@example.com"

    @pytest.mark.asyncio
    async def test_login_unknown_user(self, db):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/auth/login", json={"email": "nobody@example.com", "password": "testpass1234"})
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_me_with_valid_token(self, db):
        await create_user("u1", "alice@example.com", "Alice", "admin", password_hash="$2b$12$jNA37BRlb5.xuk8emW1OpeukXl8jpKizNl1OYg2abxLFZfZf1T/z2")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            login = await client.post("/auth/login", json={"email": "alice@example.com", "password": "testpass1234"})
            token = login.json()["token"]
            resp = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json()["email"] == "alice@example.com"

    @pytest.mark.asyncio
    async def test_me_without_token(self, db):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/auth/me")
        assert resp.status_code == 401


class TestThreads:
    @pytest.mark.asyncio
    async def test_create_thread(self, db):
        await create_user("u1", "alice@example.com", "Alice", "drafter", password_hash="$2b$12$jNA37BRlb5.xuk8emW1OpeukXl8jpKizNl1OYg2abxLFZfZf1T/z2")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            login = await client.post("/auth/login", json={"email": "alice@example.com", "password": "testpass1234"})
            headers = {"Authorization": f"Bearer {login.json()['token']}"}
            resp = await client.post("/threads", json={"title": "Test batch"}, headers=headers)
        assert resp.status_code == 200
        assert "id" in resp.json()
        assert resp.json()["title"] == "Test batch"

    @pytest.mark.asyncio
    async def test_list_threads(self, db):
        await create_user("u1", "alice@example.com", "Alice", "drafter", password_hash="$2b$12$jNA37BRlb5.xuk8emW1OpeukXl8jpKizNl1OYg2abxLFZfZf1T/z2")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            login = await client.post("/auth/login", json={"email": "alice@example.com", "password": "testpass1234"})
            headers = {"Authorization": f"Bearer {login.json()['token']}"}
            await client.post("/threads", json={"title": "T1"}, headers=headers)
            await client.post("/threads", json={"title": "T2"}, headers=headers)
            resp = await client.get("/threads", headers=headers)
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    @pytest.mark.asyncio
    async def test_list_threads_scoped_to_user(self, db):
        await create_user("u1", "alice@example.com", "Alice", "drafter", password_hash="$2b$12$jNA37BRlb5.xuk8emW1OpeukXl8jpKizNl1OYg2abxLFZfZf1T/z2")
        await create_user("u2", "bob@example.com", "Bob", "viewer", password_hash="$2b$12$jNA37BRlb5.xuk8emW1OpeukXl8jpKizNl1OYg2abxLFZfZf1T/z2")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as alice, \
                     AsyncClient(transport=transport, base_url="http://test") as bob:
            await alice.post("/auth/login", json={"email": "alice@example.com", "password": "testpass1234"})
            await bob.post("/auth/login", json={"email": "bob@example.com", "password": "testpass1234"})
            await alice.post("/threads", json={"title": "Alice's"})
            await bob.post("/threads", json={"title": "Bob's"})
            alice_threads = (await alice.get("/threads")).json()
            bob_threads = (await bob.get("/threads")).json()
        assert len(alice_threads) == 1
        assert len(bob_threads) == 1


class TestApprovalEndpoint:
    @pytest.mark.asyncio
    async def test_approve_artifact(self, db):
        await create_user("u1", "alice@example.com", "Alice", "sender", password_hash="$2b$12$jNA37BRlb5.xuk8emW1OpeukXl8jpKizNl1OYg2abxLFZfZf1T/z2")
        tid = await create_thread("Test", "u1")
        aid = await create_artifact(tid, "draft", "c1", '{"subject":"Hi","body":"Hello"}')
        await update_artifact_status(aid, "pending_approval")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            login = await client.post("/auth/login", json={"email": "alice@example.com", "password": "testpass1234"})
            headers = {"Authorization": f"Bearer {login.json()['token']}"}
            resp = await client.post(
                f"/threads/{tid}/approve",
                json={"artifact_id": aid, "action": "approve"},
                headers=headers,
            )
        assert resp.status_code == 200
        assert resp.json()["status"] == "approved"

    @pytest.mark.asyncio
    async def test_reject_artifact_with_feedback(self, db):
        await create_user("u1", "alice@example.com", "Alice", "sender", password_hash="$2b$12$jNA37BRlb5.xuk8emW1OpeukXl8jpKizNl1OYg2abxLFZfZf1T/z2")
        tid = await create_thread("Test", "u1")
        aid = await create_artifact(tid, "draft", "c1", '{"subject":"Hi","body":"Hello"}')
        await update_artifact_status(aid, "pending_approval")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            login = await client.post("/auth/login", json={"email": "alice@example.com", "password": "testpass1234"})
            headers = {"Authorization": f"Bearer {login.json()['token']}"}
            resp = await client.post(
                f"/threads/{tid}/approve",
                json={"artifact_id": aid, "action": "reject", "feedback": "Too long"},
                headers=headers,
            )
        assert resp.status_code == 200
        assert resp.json()["status"] == "rejected"


class TestConnections:
    @pytest.mark.asyncio
    async def test_get_connections(self, db):
        await create_user("u1", "alice@example.com", "Alice", "admin", password_hash="$2b$12$jNA37BRlb5.xuk8emW1OpeukXl8jpKizNl1OYg2abxLFZfZf1T/z2")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            login = await client.post("/auth/login", json={"email": "alice@example.com", "password": "testpass1234"})
            headers = {"Authorization": f"Bearer {login.json()['token']}"}
            resp = await client.get("/connections", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "hubspot" in data
        assert "smtp" in data
        assert "rag" in data


class TestContactPiiEndpoints:
    @pytest.mark.asyncio
    async def test_firstname_fetch_is_transient_and_not_saved(self, db, monkeypatch):
        await create_user(
            "u1",
            "alice@example.com",
            "Alice",
            "sender",
            password_hash="$2b$12$jNA37BRlb5.xuk8emW1OpeukXl8jpKizNl1OYg2abxLFZfZf1T/z2",
        )
        await upsert_contact("12345", {
            "company": "Acme",
            "email": "buyer@example.com",
            "firstname": "StoredShouldBeStripped",
        })

        async def fake_fetch(contact_id, fields=None):
            assert contact_id == "12345"
            assert fields == ["firstname"]
            return {"firstname": "Maria"}

        monkeypatch.setattr("outreach_pipeline.tools.pii.afetch_contact_pii", fake_fetch)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            login = await client.post("/auth/login", json={"email": "alice@example.com", "password": "testpass1234"})
            headers = {"Authorization": f"Bearer {login.json()['token']}"}
            resp = await client.get("/contacts/12345/firstname", headers=headers)

        assert resp.status_code == 200
        assert resp.json() == {"contact_id": "12345", "firstname": "Maria"}

        stored = await get_contact("12345")
        assert stored["properties"].get("firstname") is None
        assert stored["properties"].get("email") is None
