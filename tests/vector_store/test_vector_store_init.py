"""Unit tests for the vector store abstraction layer (src/vector_store/__init__.py)."""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, patch

import pytest

from src.config import settings
from src.vector_store import (
    VectorStore,
    _create_store_by_type,
    _hash_namespace_components,
    close_external_vector_store,
    get_external_vector_store,
)
from src.vector_store.lancedb import LanceDBVectorStore
from src.vector_store.qdrant import QdrantVectorStore
from src.vector_store.turbopuffer import TurbopufferVectorStore


def test_hash_namespace_components_is_deterministic_urlsafe() -> None:
    """SHA-256 -> base64url, fixed 43 chars, deterministic, distinct per input."""
    first = _hash_namespace_components("workspace", "observer", "observed")
    second = _hash_namespace_components("workspace", "observer", "observed")
    assert first == second
    assert len(first) == 43
    assert set(first) <= set(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    )
    assert first != _hash_namespace_components("workspace", "observer", "someone-else")


@pytest.fixture
def base_store() -> VectorStore:
    """An instantiable VectorStore so the abstract method bodies can run."""
    with patch.object(VectorStore, "__abstractmethods__", set()):
        yield VectorStore()


def test_get_vector_namespace_document(base_store: VectorStore) -> None:
    ns = base_store.get_vector_namespace(
        "document", "workspace", observer="observer", observed="observed"
    )
    suffix = _hash_namespace_components("workspace", "observer", "observed")
    assert ns == f"{settings.VECTOR_STORE.NAMESPACE}.doc.{suffix}"


def test_get_vector_namespace_document_requires_both_peers(
    base_store: VectorStore,
) -> None:
    with pytest.raises(ValueError, match="observer and observed"):
        base_store.get_vector_namespace(
            "document", "workspace", observer=None, observed="observed"
        )
    with pytest.raises(ValueError, match="observer and observed"):
        base_store.get_vector_namespace(
            "document", "workspace", observer="observer", observed=None
        )


def test_get_vector_namespace_message(base_store: VectorStore) -> None:
    ns = base_store.get_vector_namespace("message", "workspace")
    suffix = _hash_namespace_components("workspace")
    assert ns == f"{settings.VECTOR_STORE.NAMESPACE}.msg.{suffix}"


@pytest.mark.asyncio
async def test_abstract_operation_bodies_are_executable_noops(
    base_store: VectorStore,
) -> None:
    """Abstract bodies (`...`) are executable and return None when invoked."""
    assert await base_store.upsert_many("ns", []) is None
    assert await base_store.query("ns", [0.1, 0.2]) is None
    assert await base_store.delete_many("ns", []) is None
    assert await base_store.delete_namespace("ns") is None
    assert await base_store.close() is None
    assert await base_store.probe_namespace_dim("ns") is None


def test_create_store_by_type_turbopuffer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.VECTOR_STORE, "TURBOPUFFER_API_KEY", "test-key")
    assert isinstance(_create_store_by_type("turbopuffer"), TurbopufferVectorStore)


def test_create_store_by_type_lancedb() -> None:
    assert isinstance(_create_store_by_type("lancedb"), LanceDBVectorStore)


def test_create_store_by_type_qdrant() -> None:
    assert isinstance(_create_store_by_type("qdrant"), QdrantVectorStore)


def test_create_store_by_type_unknown_raises_value_error() -> None:
    with pytest.raises(ValueError, match="Unknown vector store type"):
        _create_store_by_type("nope")


def test_create_store_by_type_lancedb_importerror_raises_runtime_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "src.vector_store.lancedb", None)
    with pytest.raises(RuntimeError, match="lancedb"):
        _create_store_by_type("lancedb")


def test_create_store_by_type_qdrant_importerror_raises_runtime_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "src.vector_store.qdrant", None)
    with pytest.raises(RuntimeError, match="qdrant"):
        _create_store_by_type("qdrant")


def test_get_external_vector_store_returns_none_for_pgvector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get_external_vector_store.cache_clear()
    monkeypatch.setattr(settings.VECTOR_STORE, "TYPE", "pgvector")
    try:
        assert get_external_vector_store() is None
        assert get_external_vector_store() is None
    finally:
        get_external_vector_store.cache_clear()


def test_get_external_vector_store_creates_and_caches_singleton(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get_external_vector_store.cache_clear()
    monkeypatch.setattr(settings.VECTOR_STORE, "TYPE", "turbopuffer")
    monkeypatch.setattr(settings.VECTOR_STORE, "TURBOPUFFER_API_KEY", "test-key")
    try:
        first = get_external_vector_store()
        second = get_external_vector_store()
        assert isinstance(first, TurbopufferVectorStore)
        assert first is second
    finally:
        get_external_vector_store.cache_clear()


@pytest.mark.asyncio
async def test_close_external_vector_store_closes_singleton(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get_external_vector_store.cache_clear()
    monkeypatch.setattr(settings.VECTOR_STORE, "TYPE", "turbopuffer")
    monkeypatch.setattr(settings.VECTOR_STORE, "TURBOPUFFER_API_KEY", "test-key")
    try:
        store = get_external_vector_store()
        store.close = AsyncMock()
        await close_external_vector_store()
        store.close.assert_awaited_once()
        assert get_external_vector_store.cache_info().misses == 0
    finally:
        get_external_vector_store.cache_clear()


@pytest.mark.asyncio
async def test_close_external_vector_store_with_none_store_still_clears_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get_external_vector_store.cache_clear()
    monkeypatch.setattr(settings.VECTOR_STORE, "TYPE", "pgvector")
    try:
        get_external_vector_store()
        await close_external_vector_store()
        get_external_vector_store()
        # A fresh miss (hits == 0) proves the cache was cleared even though
        # the cached store is None.
        assert get_external_vector_store.cache_info().hits == 0
        assert get_external_vector_store.cache_info().misses == 1
    finally:
        get_external_vector_store.cache_clear()
