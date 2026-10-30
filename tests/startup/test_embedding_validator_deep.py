"""Deep unit tests for src/startup/embedding_validator.py external-sampling paths."""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from src.startup.embedding_validator import (
    StartupValidationError,
    _introspect_pgvector_dims_once,
    _probe_namespace_dim,
    _sample_collection_keys,
    _sample_external_namespaces,
    _sample_workspace_names,
)


class FakeConn:
    def __init__(self, rows):
        self.rows = rows

    async def execute(self, stmt, params=None):
        return _IterableResult(self.rows)


class _IterableResult:
    def __init__(self, rows):
        self.rows = rows

    def __iter__(self):
        return iter(self.rows)


def _fake_engine(rows):
    class Engine:
        def connect(self):
            @asynccontextmanager
            async def _cm():
                yield FakeConn(rows)

            return _cm()

    return Engine()


async def _probe(store, ns):
    return await store.probe_namespace_dim(ns)


@pytest.mark.asyncio
async def test_introspect_pgvector_dims_once():
    rows = [
        SimpleNamespace(table_name="documents", typmod=1536),
        SimpleNamespace(table_name="message_embeddings", typmod=1536),
    ]
    engine = _fake_engine(rows)
    dims = await _introspect_pgvector_dims_once(engine, "public")
    assert dims == {"documents": 1536, "message_embeddings": 1536}


@pytest.mark.asyncio
async def test_sample_workspace_names():
    engine = _fake_engine([("ws_a",), ("ws_b",)])
    names = await _sample_workspace_names(engine, 10)
    assert names == ["ws_a", "ws_b"]


@pytest.mark.asyncio
async def test_sample_collection_keys():
    engine = _fake_engine([("ws", "obs", "seen")])
    keys = await _sample_collection_keys(engine, 10)
    assert keys == [("ws", "obs", "seen")]


@pytest.mark.asyncio
async def test_sample_external_skips_when_no_namespaces(monkeypatch):
    monkeypatch.setattr(
        "src.startup.embedding_validator._sample_workspace_names",
        async_lambda([]),
    )
    monkeypatch.setattr(
        "src.startup.embedding_validator._sample_collection_keys",
        async_lambda([]),
    )
    called = {"n": 0}
    engine = _fake_engine([])

    def get_external(*a, **k):
        called["n"] += 1
        raise AssertionError("should not be called")

    monkeypatch.setattr(
        "src.vector_store.get_external_vector_store", get_external
    )
    await _sample_external_namespaces(engine, target_dim=1536)
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_sample_external_returns_when_store_none(monkeypatch):
    monkeypatch.setattr(
        "src.startup.embedding_validator._sample_workspace_names",
        async_lambda(["ws1"]),
    )
    monkeypatch.setattr(
        "src.startup.embedding_validator._sample_collection_keys",
        async_lambda([]),
    )
    monkeypatch.setattr(
        "src.vector_store.get_external_vector_store", lambda: None
    )
    await _sample_external_namespaces(_fake_engine([]), target_dim=1536)


@pytest.mark.asyncio
async def test_sample_external_raises_on_mismatch(monkeypatch):
    monkeypatch.setattr(
        "src.startup.embedding_validator._sample_workspace_names",
        async_lambda(["ws1"]),
    )
    monkeypatch.setattr(
        "src.startup.embedding_validator._sample_collection_keys",
        async_lambda([]),
    )

    class FakeStore:
        def get_vector_namespace(self, kind, workspace_name, **kw):
            return f"{kind}:{workspace_name}"

        async def probe_namespace_dim(self, namespace):
            return 768  # mismatch

    monkeypatch.setattr(
        "src.vector_store.get_external_vector_store", lambda: FakeStore()
    )
    with pytest.raises(StartupValidationError, match="dim != 1536"):
        await _sample_external_namespaces(_fake_engine([]), target_dim=1536)


@pytest.mark.asyncio
async def test_sample_external_passes_when_dims_match(monkeypatch):
    monkeypatch.setattr(
        "src.startup.embedding_validator._sample_workspace_names",
        async_lambda(["ws1"]),
    )
    monkeypatch.setattr(
        "src.startup.embedding_validator._sample_collection_keys",
        async_lambda([("ws1", "obs", "seen")]),
    )

    class FakeStore:
        def get_vector_namespace(self, kind, workspace_name, **kw):
            return f"{kind}:{workspace_name}"

        async def probe_namespace_dim(self, namespace):
            return 1536 if namespace.startswith("message") else None

    monkeypatch.setattr(
        "src.vector_store.get_external_vector_store", lambda: FakeStore()
    )
    await _sample_external_namespaces(_fake_engine([]), target_dim=1536)


def async_lambda(value):
    async def _f(*a, **k):
        return value

    return _f


@pytest.mark.asyncio
async def test_probe_namespace_dim_delegates():
    class FakeStore:
        async def probe_namespace_dim(self, namespace):
            return 1536

    assert await _probe(FakeStore(), "ns") == 1536


@pytest.mark.asyncio
async def test_validate_external_branch_invokes_sample(monkeypatch):
    from src.startup.embedding_validator import validate_embedding_schema

    async def fake_introspect(_engine, schema):
        return {"documents": 1536, "message_embeddings": 1536}

    async def fake_sample(_engine, **kw):
        called["sample"] = kw["target_dim"]

    monkeypatch.setattr(
        "src.startup.embedding_validator._introspect_pgvector_dims_with_retry",
        fake_introspect,
    )
    monkeypatch.setattr(
        "src.startup.embedding_validator._sample_external_namespaces",
        fake_sample,
    )

    app_settings = SimpleNamespace(
        EMBEDDING=SimpleNamespace(VECTOR_DIMENSIONS=1536),
        DB=SimpleNamespace(SCHEMA="public"),
        VECTOR_STORE=SimpleNamespace(TYPE="turbopuffer"),
    )
    called = {}
    await validate_embedding_schema(_fake_engine([]), app_settings=app_settings)
    assert called.get("sample") == 1536
