"""Tests for the ChromaDB client mode switch.

Mode is picked from env:
  CHROMA_HOST unset → PersistentClient against a local filesystem path.
  CHROMA_HOST set   → HttpClient against chroma:CHROMA_PORT.

These tests pin the switch so a future refactor can't silently break
one mode.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from outreach_pipeline.adapters import chroma_client


class TestIsHttpMode:
    def test_unset_is_false(self, monkeypatch):
        monkeypatch.delenv("CHROMA_HOST", raising=False)
        assert chroma_client.is_http_mode() is False

    def test_set_is_true(self, monkeypatch):
        monkeypatch.setenv("CHROMA_HOST", "chroma")
        assert chroma_client.is_http_mode() is True

    def test_empty_string_treated_as_unset(self, monkeypatch):
        # Compose passes CHROMA_HOST=${CHROMA_HOST:-} in dev — empty
        # string means 'not configured', not 'use an empty hostname'.
        monkeypatch.setenv("CHROMA_HOST", "")
        assert chroma_client.is_http_mode() is False


class TestGetChromaClient:
    def test_persistent_by_default(self, monkeypatch, tmp_path):
        monkeypatch.delenv("CHROMA_HOST", raising=False)
        db_path = tmp_path / "db"
        with patch("chromadb.PersistentClient") as mock_persist, \
             patch("chromadb.HttpClient") as mock_http:
            chroma_client.get_chroma_client(str(db_path))
            mock_persist.assert_called_once_with(path=str(db_path))
            mock_http.assert_not_called()
        # The helper eagerly creates the directory so first-time use
        # doesn't race on MKDIR errors inside chromadb.
        assert db_path.exists()

    def test_http_when_env_set(self, monkeypatch):
        monkeypatch.setenv("CHROMA_HOST", "chroma")
        monkeypatch.setenv("CHROMA_PORT", "8000")
        with patch("chromadb.HttpClient") as mock_http, \
             patch("chromadb.PersistentClient") as mock_persist:
            chroma_client.get_chroma_client("ignored")
            mock_http.assert_called_once_with(host="chroma", port=8000)
            mock_persist.assert_not_called()

    def test_http_default_port(self, monkeypatch):
        monkeypatch.setenv("CHROMA_HOST", "chroma")
        monkeypatch.delenv("CHROMA_PORT", raising=False)
        with patch("chromadb.HttpClient") as mock_http:
            chroma_client.get_chroma_client()
            _, kwargs = mock_http.call_args
            assert kwargs["host"] == "chroma"
            assert kwargs["port"] == 8000


class TestCollectionPathExists:
    def test_http_mode_always_returns_true(self, monkeypatch):
        # Can't cheaply probe a remote server — optimistically return
        # True and let the query surface the actual miss.
        monkeypatch.setenv("CHROMA_HOST", "chroma")
        assert chroma_client.collection_path_exists("does/not/matter") is True

    def test_local_mode_checks_filesystem(self, monkeypatch, tmp_path):
        monkeypatch.delenv("CHROMA_HOST", raising=False)
        # Missing path
        assert chroma_client.collection_path_exists(str(tmp_path / "missing")) is False
        # Empty dir is still 'not populated'
        empty = tmp_path / "empty"
        empty.mkdir()
        assert chroma_client.collection_path_exists(str(empty)) is False
        # Dir with any child counts as populated
        (empty / "chroma.sqlite").write_text("x")
        assert chroma_client.collection_path_exists(str(empty)) is True


class TestAdapterRoutesThroughHelper:
    """The ChromaDBAdapter read paths must go through get_chroma_client
    so the HTTP switch stays consistent with the standalone CLI paths."""

    def test_collection_count_uses_helper_in_http_mode(self, monkeypatch):
        monkeypatch.setenv("CHROMA_HOST", "chroma")
        fake_coll = MagicMock()
        fake_coll.count.return_value = 42
        fake_client = MagicMock()
        fake_client.get_collection.return_value = fake_coll

        from outreach_pipeline.adapters.chromadb_adapter import ChromaDBAdapter

        with patch(
            "outreach_pipeline.adapters.chroma_client.get_chroma_client",
            return_value=fake_client,
        ) as mock_get:
            adapter = ChromaDBAdapter(db_path="/unused/in/http/mode")
            n = adapter.collection_count()
            assert n == 42
            mock_get.assert_called_once()

    def test_get_all_chunks_returns_empty_on_connect_error(self, monkeypatch):
        monkeypatch.setenv("CHROMA_HOST", "chroma")
        from outreach_pipeline.adapters.chromadb_adapter import ChromaDBAdapter

        with patch(
            "outreach_pipeline.adapters.chroma_client.get_chroma_client",
            side_effect=ConnectionError("chroma down"),
        ):
            adapter = ChromaDBAdapter(db_path="/unused")
            # Must not raise — graceful degradation on transient Chroma
            # outages keeps the agent responsive (empty context > crash).
            assert adapter.get_all_chunks() == []
