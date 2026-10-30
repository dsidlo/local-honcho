"""Deep unit tests for src/utils/search.py."""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from src.config import settings
from src.exceptions import ValidationException
from src.utils import search as mod


# ---------------------------------------------------------------------------
# reciprocal_rank_fusion
# ---------------------------------------------------------------------------

def test_rrf_empty():
    assert mod.reciprocal_rank_fusion(limit=5) == []


def test_rrf_basic_fusion():
    a = ["x", "y", "z"]
    b = ["y", "x", "w"]
    fused = mod.reciprocal_rank_fusion(a, b, k=60, limit=10)
    # x appears at rank1 in both -> highest score; y second.
    assert fused[0] == "x"
    assert fused[1] == "y"


def test_rrf_limit_truncates():
    a = ["a", "b", "c", "d"]
    assert mod.reciprocal_rank_fusion(a, k=60, limit=2) == ["a", "b"]


# ---------------------------------------------------------------------------
# _uses_pgvector_message_search
# ---------------------------------------------------------------------------

def test_uses_pgvector_when_type_pgvector(monkeypatch):
    monkeypatch.setattr(settings.VECTOR_STORE, "TYPE", "pgvector")
    monkeypatch.setattr(settings.VECTOR_STORE, "MIGRATED", False)
    assert mod._uses_pgvector_message_search() is True


def test_uses_pgvector_when_not_migrated(monkeypatch):
    monkeypatch.setattr(settings.VECTOR_STORE, "TYPE", "turbopuffer")
    monkeypatch.setattr(settings.VECTOR_STORE, "MIGRATED", False)
    assert mod._uses_pgvector_message_search() is True


def test_not_pgvector_when_external_migrated(monkeypatch):
    monkeypatch.setattr(settings.VECTOR_STORE, "TYPE", "turbopuffer")
    monkeypatch.setattr(settings.VECTOR_STORE, "MIGRATED", True)
    assert mod._uses_pgvector_message_search() is False


# ---------------------------------------------------------------------------
# query_external_vector_message_ids
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_query_external_zero_limit():
    assert await mod.query_external_vector_message_ids("ws", [0.1], 0) == []


@pytest.mark.asyncio
async def test_query_external_no_store(monkeypatch):
    monkeypatch.setattr(mod, "get_external_vector_store", lambda: None)
    assert await mod.query_external_vector_message_ids("ws", [0.1], 5) == []


def _fake_store(query_results=None, namespace="ns"):
    return SimpleNamespace(
        get_vector_namespace=lambda kind, ws: namespace,
        query=AsyncLambda(query_results or []),
    )


def AsyncLambda(value):
    async def _f(*a, **k):
        return value

    return _f


@pytest.mark.asyncio
async def test_query_external_empty_results(monkeypatch):
    store = _fake_store(query_results=[])
    monkeypatch.setattr(mod, "get_external_vector_store", lambda: store)
    assert await mod.query_external_vector_message_ids("ws", [0.1], 5) == []


@pytest.mark.asyncio
async def test_query_external_dedupes_message_ids(monkeypatch):
    results = [
        SimpleNamespace(metadata={"message_id": "m1"}),
        SimpleNamespace(metadata={"message_id": "m1"}),
        SimpleNamespace(metadata={"message_id": "m2"}),
        SimpleNamespace(metadata={}),  # missing message_id -> skipped
    ]
    store = _fake_store(query_results=results)
    monkeypatch.setattr(mod, "get_external_vector_store", lambda: store)
    ids = await mod.query_external_vector_message_ids(
        "ws", [0.1], 5,
        filters={"session_id": "s1", "peer_id": "p1"},
    )
    assert ids == ["m1", "m2"]


# ---------------------------------------------------------------------------
# fetch_messages_by_ids
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_messages_empty_ids():
    db = SimpleNamespace()
    assert await mod.fetch_messages_by_ids(db, []) == []


class _Msg:
    def __init__(self, public_id):
        self.public_id = public_id


@pytest.mark.asyncio
async def test_fetch_messages_preserves_order():
    found = [_Msg("m1"), _Msg("m2"), _Msg("m3")]
    scalars = SimpleNamespace(all=lambda: found)
    result = SimpleNamespace(scalars=lambda: scalars)
    db = SimpleNamespace(execute=AsyncLambda(result))
    out = await mod.fetch_messages_by_ids(db, ["m3", "m1", "missing"])
    assert [m.public_id for m in out] == ["m3", "m1"]


# ---------------------------------------------------------------------------
# _semantic_search_pgvector
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_semantic_search_dedupes():
    dup = _Msg("m1")
    messages = [dup, _Msg("m1"), _Msg("m2"), _Msg("m3")]
    scalars = SimpleNamespace(all=lambda: messages)
    result = SimpleNamespace(scalars=lambda: scalars)
    db = SimpleNamespace(execute=AsyncLambda(result))
    out = await mod._semantic_search_pgvector(db, "ws", [0.1], limit=2)
    assert [m.public_id for m in out] == ["m1", "m2"]


# ---------------------------------------------------------------------------
# _filter_by_peer_perspective
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_filter_peer_empty_messages():
    assert await mod._filter_by_peer_perspective(SimpleNamespace(), [], "ws", "p") == []


@pytest.mark.asyncio
async def test_filter_peer_no_memberships():
    db = SimpleNamespace(execute=AsyncLambda(_Result([])))
    msg = SimpleNamespace(session_name="s1", created_at=SimpleNamespace())
    out = await mod._filter_by_peer_perspective(db, [msg], "ws", "p")
    assert out == []


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class _Membership:
    def __init__(self, session_name, joined_at, left_at):
        self.session_name = session_name
        self.joined_at = joined_at
        self.left_at = left_at


class _When:
    def __init__(self, t):
        self.t = t

    def __ge__(self, other):
        return self.t >= other.t

    def __le__(self, other):
        return self.t <= other.t


@pytest.mark.asyncio
async def test_filter_peer_matching_window():
    joined = _When(0)
    left = _When(100)
    created = _When(50)
    memberships = [_Membership("s1", joined, left)]
    db = SimpleNamespace(execute=AsyncLambda(_Result(memberships)))
    msg = SimpleNamespace(session_name="s1", created_at=created)
    out = await mod._filter_by_peer_perspective(db, [msg], "ws", "p")
    assert out == [msg]


@pytest.mark.asyncio
async def test_filter_peer_outside_window():
    joined = _When(0)
    left = _When(100)
    created = _When(200)
    memberships = [_Membership("s1", joined, left)]
    db = SimpleNamespace(execute=AsyncLambda(_Result(memberships)))
    msg = SimpleNamespace(session_name="s1", created_at=created)
    out = await mod._filter_by_peer_perspective(db, [msg], "ws", "p")
    assert out == []


# ---------------------------------------------------------------------------
# _fulltext_search
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fulltext_special_chars():
    from sqlalchemy import select
    from src import models

    found = [_Msg("m1")]
    scalars = SimpleNamespace(all=lambda: found)
    result = SimpleNamespace(scalars=lambda: scalars)
    db = SimpleNamespace(execute=AsyncLambda(result))
    stmt = select(models.Message)
    out = await mod._fulltext_search(db, "a+b!", stmt, 10)
    assert out == found


@pytest.mark.asyncio
async def test_fulltext_fts_path():
    from sqlalchemy import select
    from src import models

    found = [_Msg("m2")]
    scalars = SimpleNamespace(all=lambda: found)
    result = SimpleNamespace(scalars=lambda: scalars)
    db = SimpleNamespace(execute=AsyncLambda(result))
    stmt = select(models.Message)
    out = await mod._fulltext_search(db, "hello world", stmt, 10)
    assert out == found


# ---------------------------------------------------------------------------
# search()
# ---------------------------------------------------------------------------

def _tracked_db_factory(managed):
    @asynccontextmanager
    async def _tracked(name, **kw):
        yield managed

    return _tracked


@pytest.mark.asyncio
async def test_search_empty_query():
    assert await mod.search("   ") == []


@pytest.mark.asyncio
async def test_search_peer_perspective_requires_workspace():
    with pytest.raises(ValidationException, match="workspace"):
        await mod.search("hello", filters={"peer_perspective": "p"})


@pytest.mark.asyncio
async def test_search_embedding_token_limit_raises(monkeypatch):
    from src.embedding_client import EmbeddingTokenLimitError

    monkeypatch.setattr(settings, "EMBED_MESSAGES", True)
    monkeypatch.setattr(settings.VECTOR_STORE, "TYPE", "pgvector")

    async def _embed(_q):
        raise EmbeddingTokenLimitError("too long")

    monkeypatch.setattr(mod.embedding_client, "embed", _embed)

    with pytest.raises(ValidationException, match="token limit"):
        await mod.search("a long query", filters={"workspace_id": "ws"})


@pytest.mark.asyncio
async def test_search_pgvector_rrf_path(monkeypatch):
    monkeypatch.setattr(settings, "EMBED_MESSAGES", True)
    monkeypatch.setattr(settings.VECTOR_STORE, "TYPE", "pgvector")
    monkeypatch.setattr(settings.VECTOR_STORE, "MIGRATED", False)

    async def _embed(_q):
        return [0.1, 0.2]

    monkeypatch.setattr(mod.embedding_client, "embed", _embed)

    managed = SimpleNamespace(expunge=lambda m: None)
    monkeypatch.setattr(mod, "tracked_db", _tracked_db_factory(managed))

    sem_msg = _Msg("sem1")
    fts_msg = _Msg("fts1")

    async def _semantic(*a, **k):
        return [sem_msg]

    async def _fulltext(*a, **k):
        return [fts_msg]

    monkeypatch.setattr(mod, "_semantic_search_pgvector", _semantic)
    monkeypatch.setattr(mod, "_fulltext_search", _fulltext)

    out = await mod.search("hi", filters={"workspace_id": "ws"}, limit=10)
    # Both semantic + fulltext -> RRF fusion returns items in fused order.
    assert set(m.public_id for m in out) == {"sem1", "fts1"}


@pytest.mark.asyncio
async def test_semantic_search_filter_branch(monkeypatch):
    messages = [_Msg("m1"), _Msg("m2")]
    scalars = SimpleNamespace(all=lambda: messages)
    result = SimpleNamespace(scalars=lambda: scalars)
    db = SimpleNamespace(execute=AsyncLambda(result))

    seen = {}
    def fake_apply_filter(stmt, model, filters):
        seen["filters"] = filters
        return stmt

    monkeypatch.setattr(mod, "apply_filter", fake_apply_filter)
    out = await mod._semantic_search_pgvector(
        db, "ws", [0.1], 2, filters={"level": "explicit"}
    )
    assert [m.public_id for m in out] == ["m1", "m2"]
    assert seen["filters"]["workspace_id"] == "ws"


@pytest.mark.asyncio
async def test_search_peer_perspective_join_embed_off(monkeypatch):
    monkeypatch.setattr(settings, "EMBED_MESSAGES", False)
    managed = SimpleNamespace(expunge=lambda m: None)
    monkeypatch.setattr(mod, "tracked_db", _tracked_db_factory(managed))

    fts_msg = _Msg("fts1")

    async def _fulltext(*a, **k):
        return [fts_msg]

    monkeypatch.setattr(mod, "_fulltext_search", _fulltext)
    out = await mod.search(
        "hi",
        filters={"peer_perspective": "p", "workspace_id": "ws"},
        limit=10,
    )
    assert [m.public_id for m in out] == ["fts1"]


@pytest.mark.asyncio
async def test_search_external_branch_with_peer_filter(monkeypatch):
    monkeypatch.setattr(settings, "EMBED_MESSAGES", True)
    monkeypatch.setattr(settings.VECTOR_STORE, "TYPE", "turbopuffer")
    monkeypatch.setattr(settings.VECTOR_STORE, "MIGRATED", True)

    async def _embed(_q):
        return [0.1, 0.2]

    monkeypatch.setattr(mod.embedding_client, "embed", _embed)

    class FakeStore:
        def get_vector_namespace(self, kind, ws):
            return f"{kind}:{ws}"

        async def query(self, namespace, q, **k):
            return [SimpleNamespace(metadata={"message_id": "em1"})]

    monkeypatch.setattr(mod, "get_external_vector_store", lambda: FakeStore())

    sem_msg = _Msg("em1")

    async def _fetch(*a, **k):
        return [sem_msg]

    async def _filter_peer(*a, **k):
        return a[1]  # passthrough

    async def _fulltext(*a, **k):
        return [_Msg("fts1")]

    monkeypatch.setattr(mod, "fetch_messages_by_ids", _fetch)
    monkeypatch.setattr(mod, "_filter_by_peer_perspective", _filter_peer)
    monkeypatch.setattr(mod, "_fulltext_search", _fulltext)

    managed = SimpleNamespace(expunge=lambda m: None)
    monkeypatch.setattr(mod, "tracked_db", _tracked_db_factory(managed))

    out = await mod.search(
        "hi",
        filters={"peer_perspective": "p", "workspace_id": "ws"},
        limit=10,
    )
    assert set(m.public_id for m in out) == {"em1", "fts1"}
