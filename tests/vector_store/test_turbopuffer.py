"""Tests for TurbopufferVectorStore error handling on 5xx responses."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from turbopuffer import InternalServerError, NotFoundError

from src.config import settings
from src.exceptions import VectorStoreError
from src.vector_store import VectorRecord
from src.vector_store.turbopuffer import TurbopufferVectorStore


def _internal_server_error(status_code: int = 503) -> InternalServerError:
    request = httpx.Request(
        "POST", "https://api.turbopuffer.com/v2/namespaces/ns/write"
    )
    response = httpx.Response(status_code, request=request)
    return InternalServerError("turbopuffer unavailable", response=response, body=None)


def _not_found_error() -> NotFoundError:
    request = httpx.Request("GET", "https://api.turbopuffer.com/v2/namespaces/ns")
    response = httpx.Response(404, request=request)
    return NotFoundError("namespace not found", response=response, body=None)


class _FakeRow:
    """Minimal stand-in for a Turbopuffer query response row."""

    def __init__(
        self,
        id_: str,
        dist: float | None = None,
        model_extra: dict[str, Any] | None = None,
    ) -> None:
        self.id = id_
        self._dist = dist
        self.model_extra = model_extra

    def __contains__(self, key: object) -> bool:
        return key == "$dist" and self._dist is not None

    def __getitem__(self, key: str) -> object:
        if key == "$dist":
            if self._dist is None:
                raise KeyError(key)
            return self._dist
        raise KeyError(key)


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> TurbopufferVectorStore:
    monkeypatch.setattr(settings.VECTOR_STORE, "TURBOPUFFER_API_KEY", "test-key")
    monkeypatch.setattr(settings.VECTOR_STORE, "TURBOPUFFER_REGION", "gcp-us-east4")
    return TurbopufferVectorStore()


@pytest.fixture
def record() -> VectorRecord:
    return VectorRecord(
        id="doc_1", embedding=[0.1, 0.2, 0.3, 0.4], metadata={"foo": "bar"}
    )


@pytest.mark.asyncio
async def test_upsert_many_raises_vector_store_error_on_5xx(
    store: TurbopufferVectorStore,
    record: VectorRecord,
) -> None:
    namespace_mock = MagicMock()
    namespace_mock.write = AsyncMock(side_effect=_internal_server_error(503))
    store._get_namespace = MagicMock(return_value=namespace_mock)  # pyright: ignore[reportPrivateUsage]

    with pytest.raises(VectorStoreError) as excinfo:
        await store.upsert_many("honcho.doc.test", [record])

    assert "honcho.doc.test" in str(excinfo.value)
    assert isinstance(excinfo.value.__cause__, InternalServerError)
    namespace_mock.write.assert_awaited_once()


def test_build_filters_membership(store: TurbopufferVectorStore) -> None:
    """Both the dict `in` form and the bare-list sugar produce an In filter."""
    assert store._build_filters({"session_name": {"in": ["s1", "s2"]}}) == (  # pyright: ignore[reportPrivateUsage]
        "session_name",
        "In",
        ["s1", "s2"],
    )
    assert store._build_filters({"session_name": ["s1", "s2"]}) == (  # pyright: ignore[reportPrivateUsage]
        "session_name",
        "In",
        ["s1", "s2"],
    )


def test_build_filters_empty_membership_fails_closed(
    store: TurbopufferVectorStore,
) -> None:
    """An empty membership list must produce an always-false filter, never an
    omitted/empty In that could widen scope (fail-open)."""
    never = ("And", [("session_name", "Eq", ""), ("session_name", "NotEq", "")])
    assert store._build_filters({"session_name": {"in": []}}) == never  # pyright: ignore[reportPrivateUsage]
    assert store._build_filters({"session_name": []}) == never  # pyright: ignore[reportPrivateUsage]


@pytest.mark.asyncio
async def test_upsert_many_short_circuits_on_empty(
    store: TurbopufferVectorStore,
) -> None:
    namespace_mock = MagicMock()
    namespace_mock.write = AsyncMock()
    store._get_namespace = MagicMock(return_value=namespace_mock)  # pyright: ignore[reportPrivateUsage]

    await store.upsert_many("honcho.doc.test", [])

    namespace_mock.write.assert_not_awaited()


@pytest.mark.asyncio
async def test_upsert_many_succeeds_without_raising(
    store: TurbopufferVectorStore,
    record: VectorRecord,
) -> None:
    namespace_mock = MagicMock()
    namespace_mock.write = AsyncMock()
    store._get_namespace = MagicMock(return_value=namespace_mock)  # pyright: ignore[reportPrivateUsage]

    result = await store.upsert_many("honcho.doc.test", [record])

    assert result is None
    namespace_mock.write.assert_awaited_once()


@pytest.mark.asyncio
async def test_query_passes_requested_include_attributes(
    store: TurbopufferVectorStore,
) -> None:
    namespace_mock = MagicMock()
    namespace_mock.query = AsyncMock(return_value=MagicMock(rows=[]))
    store._get_namespace = MagicMock(return_value=namespace_mock)  # pyright: ignore[reportPrivateUsage]

    await store.query(
        "honcho.msg.test",
        [0.1, 0.2, 0.3, 0.4],
        include_attributes=["message_id"],
    )

    namespace_mock.query.assert_awaited_once()
    assert namespace_mock.query.await_args.kwargs["include_attributes"] == [
        "message_id"
    ]


@pytest.mark.asyncio
async def test_query_can_skip_attributes(
    store: TurbopufferVectorStore,
) -> None:
    namespace_mock = MagicMock()
    namespace_mock.query = AsyncMock(return_value=MagicMock(rows=[]))
    store._get_namespace = MagicMock(return_value=namespace_mock)  # pyright: ignore[reportPrivateUsage]

    await store.query(
        "honcho.doc.test",
        [0.1, 0.2, 0.3, 0.4],
        include_attributes=False,
    )

    namespace_mock.query.assert_awaited_once()
    assert namespace_mock.query.await_args.kwargs["include_attributes"] is False


@pytest.mark.asyncio
async def test_query_returns_empty_without_calling_api_on_nonpositive_top_k(
    store: TurbopufferVectorStore,
) -> None:
    """Turbopuffer rejects top_k < 1; never hit the network with a bad value."""
    namespace_mock = MagicMock()
    namespace_mock.query = AsyncMock()
    store._get_namespace = MagicMock(return_value=namespace_mock)  # pyright: ignore[reportPrivateUsage]

    for top_k in (0, -1):
        assert (
            await store.query(
                "honcho.msg.test",
                [0.1, 0.2, 0.3, 0.4],
                top_k=top_k,
            )
            == []
        )

    store._get_namespace.assert_not_called()  # pyright: ignore[reportPrivateUsage]
    namespace_mock.query.assert_not_awaited()


def test_init_raises_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.VECTOR_STORE, "TURBOPUFFER_API_KEY", None)
    with pytest.raises(ValueError, match="TURBOPUFFER_API_KEY"):
        TurbopufferVectorStore()


def test_get_namespace_delegates_to_client(
    store: TurbopufferVectorStore,
) -> None:
    store.tpuf = MagicMock()
    store.tpuf.namespace.return_value = "ns-object"
    assert store._get_namespace("my-ns") == "ns-object"  # pyright: ignore[reportPrivateUsage]
    store.tpuf.namespace.assert_called_once_with("my-ns")


@pytest.mark.asyncio
async def test_upsert_many_reraises_generic_exception(
    store: TurbopufferVectorStore,
    record: VectorRecord,
) -> None:
    namespace_mock = MagicMock()
    namespace_mock.write = AsyncMock(side_effect=RuntimeError("boom"))
    store._get_namespace = MagicMock(return_value=namespace_mock)  # pyright: ignore[reportPrivateUsage]

    with pytest.raises(RuntimeError, match="boom"):
        await store.upsert_many("honcho.doc.test", [record])


@pytest.mark.asyncio
async def test_query_passes_filters_to_rank_query(
    store: TurbopufferVectorStore,
) -> None:
    namespace_mock = MagicMock()
    namespace_mock.query = AsyncMock(return_value=MagicMock(rows=[]))
    store._get_namespace = MagicMock(return_value=namespace_mock)  # pyright: ignore[reportPrivateUsage]

    await store.query(
        "honcho.msg.test",
        [0.1, 0.2, 0.3, 0.4],
        filters={"session_name": "s1"},
    )

    kwargs = namespace_mock.query.await_args.kwargs
    assert kwargs["filters"] == ("session_name", "Eq", "s1")


@pytest.mark.asyncio
async def test_query_parses_rows_metadata_and_distance(
    store: TurbopufferVectorStore,
) -> None:
    rows = [
        _FakeRow("vec_1", dist=0.1, model_extra={"foo": "bar", "$internal": "skip"}),
        _FakeRow("vec_2", dist=0.5),
        _FakeRow("vec_3", model_extra={"baz": 1}),
    ]
    namespace_mock = MagicMock()
    namespace_mock.query = AsyncMock(return_value=MagicMock(rows=rows))
    store._get_namespace = MagicMock(return_value=namespace_mock)  # pyright: ignore[reportPrivateUsage]

    results = await store.query("honcho.msg.test", [0.1, 0.2, 0.3, 0.4])

    assert [r.id for r in results] == ["vec_1", "vec_2", "vec_3"]
    assert [r.score for r in results] == [0.1, 0.5, 0.0]
    assert results[0].metadata == {"foo": "bar"}
    assert results[1].metadata == {}
    assert results[2].metadata == {"baz": 1}


@pytest.mark.asyncio
async def test_query_filters_by_max_distance(
    store: TurbopufferVectorStore,
) -> None:
    rows = [_FakeRow("near", dist=0.05), _FakeRow("far", dist=0.9)]
    namespace_mock = MagicMock()
    namespace_mock.query = AsyncMock(return_value=MagicMock(rows=rows))
    store._get_namespace = MagicMock(return_value=namespace_mock)  # pyright: ignore[reportPrivateUsage]

    results = await store.query(
        "honcho.msg.test",
        [0.1, 0.2, 0.3, 0.4],
        max_distance=0.5,
    )

    assert [r.id for r in results] == ["near"]


@pytest.mark.asyncio
async def test_query_returns_empty_on_not_found(
    store: TurbopufferVectorStore,
) -> None:
    namespace_mock = MagicMock()
    namespace_mock.query = AsyncMock(side_effect=_not_found_error())
    store._get_namespace = MagicMock(return_value=namespace_mock)  # pyright: ignore[reportPrivateUsage]

    assert await store.query("honcho.msg.test", [0.1, 0.2, 0.3, 0.4]) == []


@pytest.mark.asyncio
async def test_query_returns_empty_on_internal_server_error(
    store: TurbopufferVectorStore,
) -> None:
    namespace_mock = MagicMock()
    namespace_mock.query = AsyncMock(side_effect=_internal_server_error(503))
    store._get_namespace = MagicMock(return_value=namespace_mock)  # pyright: ignore[reportPrivateUsage]

    assert await store.query("honcho.msg.test", [0.1, 0.2, 0.3, 0.4]) == []


@pytest.mark.asyncio
async def test_query_reraises_generic_exception(
    store: TurbopufferVectorStore,
) -> None:
    namespace_mock = MagicMock()
    namespace_mock.query = AsyncMock(side_effect=RuntimeError("boom"))
    store._get_namespace = MagicMock(return_value=namespace_mock)  # pyright: ignore[reportPrivateUsage]

    with pytest.raises(RuntimeError, match="boom"):
        await store.query("honcho.msg.test", [0.1, 0.2, 0.3, 0.4])


def test_build_filters_none_or_empty_returns_none(
    store: TurbopufferVectorStore,
) -> None:
    assert store._build_filters(None) is None  # pyright: ignore[reportPrivateUsage]
    assert store._build_filters({}) is None  # pyright: ignore[reportPrivateUsage]


def test_build_filters_equality(store: TurbopufferVectorStore) -> None:
    assert store._build_filters({"key": "value"}) == (  # pyright: ignore[reportPrivateUsage]
        "key",
        "Eq",
        "value",
    )


def test_build_filters_combines_multiple_with_and(
    store: TurbopufferVectorStore,
) -> None:
    assert store._build_filters({"a": 1, "b": 2}) == (  # pyright: ignore[reportPrivateUsage]
        "And",
        [("a", "Eq", 1), ("b", "Eq", 2)],
    )


def test_build_filters_defensive_empty_items_guard(
    store: TurbopufferVectorStore,
) -> None:
    """A truthy filter container that yields no items returns None (defensive)."""
    weird = MagicMock()
    weird.items.return_value = []
    assert store._build_filters(weird) is None  # pyright: ignore[reportPrivateUsage]


@pytest.mark.asyncio
async def test_delete_many_short_circuits_on_empty(
    store: TurbopufferVectorStore,
) -> None:
    store._get_namespace = MagicMock()  # pyright: ignore[reportPrivateUsage]
    await store.delete_many("honcho.doc.test", [])
    store._get_namespace.assert_not_called()  # pyright: ignore[reportPrivateUsage]


@pytest.mark.asyncio
async def test_delete_many_succeeds(
    store: TurbopufferVectorStore,
) -> None:
    namespace_mock = MagicMock()
    namespace_mock.write = AsyncMock()
    store._get_namespace = MagicMock(return_value=namespace_mock)  # pyright: ignore[reportPrivateUsage]

    await store.delete_many("honcho.doc.test", ["id1", "id2"])

    namespace_mock.write.assert_awaited_once_with(deletes=["id1", "id2"])


@pytest.mark.asyncio
async def test_delete_many_not_found_is_swallowed(
    store: TurbopufferVectorStore,
) -> None:
    namespace_mock = MagicMock()
    namespace_mock.write = AsyncMock(side_effect=_not_found_error())
    store._get_namespace = MagicMock(return_value=namespace_mock)  # pyright: ignore[reportPrivateUsage]

    await store.delete_many("honcho.doc.test", ["id1"])


@pytest.mark.asyncio
async def test_delete_many_internal_server_error_raises_vector_store_error(
    store: TurbopufferVectorStore,
) -> None:
    namespace_mock = MagicMock()
    namespace_mock.write = AsyncMock(side_effect=_internal_server_error(503))
    store._get_namespace = MagicMock(return_value=namespace_mock)  # pyright: ignore[reportPrivateUsage]

    with pytest.raises(VectorStoreError, match="unavailable"):
        await store.delete_many("honcho.doc.test", ["id1"])


@pytest.mark.asyncio
async def test_delete_many_reraises_generic_exception(
    store: TurbopufferVectorStore,
) -> None:
    namespace_mock = MagicMock()
    namespace_mock.write = AsyncMock(side_effect=RuntimeError("boom"))
    store._get_namespace = MagicMock(return_value=namespace_mock)  # pyright: ignore[reportPrivateUsage]

    with pytest.raises(RuntimeError, match="boom"):
        await store.delete_many("honcho.doc.test", ["id1"])


@pytest.mark.asyncio
async def test_delete_namespace_succeeds(
    store: TurbopufferVectorStore,
) -> None:
    namespace_mock = MagicMock()
    namespace_mock.delete_all = AsyncMock()
    store._get_namespace = MagicMock(return_value=namespace_mock)  # pyright: ignore[reportPrivateUsage]

    await store.delete_namespace("honcho.doc.test")

    namespace_mock.delete_all.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_namespace_not_found_is_swallowed(
    store: TurbopufferVectorStore,
) -> None:
    namespace_mock = MagicMock()
    namespace_mock.delete_all = AsyncMock(side_effect=_not_found_error())
    store._get_namespace = MagicMock(return_value=namespace_mock)  # pyright: ignore[reportPrivateUsage]

    await store.delete_namespace("honcho.doc.test")


@pytest.mark.asyncio
async def test_delete_namespace_reraises_generic_exception(
    store: TurbopufferVectorStore,
) -> None:
    namespace_mock = MagicMock()
    namespace_mock.delete_all = AsyncMock(side_effect=RuntimeError("boom"))
    store._get_namespace = MagicMock(return_value=namespace_mock)  # pyright: ignore[reportPrivateUsage]

    with pytest.raises(RuntimeError, match="boom"):
        await store.delete_namespace("honcho.doc.test")


@pytest.mark.asyncio
async def test_close_closes_client(store: TurbopufferVectorStore) -> None:
    store.tpuf = MagicMock()
    store.tpuf.close = AsyncMock()

    await store.close()

    store.tpuf.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_probe_namespace_dim_returns_none_when_not_exists(
    store: TurbopufferVectorStore,
) -> None:
    namespace_mock = MagicMock()
    namespace_mock.exists = AsyncMock(return_value=False)
    store._get_namespace = MagicMock(return_value=namespace_mock)  # pyright: ignore[reportPrivateUsage]

    assert await store.probe_namespace_dim("honcho.doc.test") is None


@pytest.mark.asyncio
async def test_probe_namespace_dim_exists_not_found_returns_none(
    store: TurbopufferVectorStore,
) -> None:
    namespace_mock = MagicMock()
    namespace_mock.exists = AsyncMock(side_effect=_not_found_error())
    store._get_namespace = MagicMock(return_value=namespace_mock)  # pyright: ignore[reportPrivateUsage]

    assert await store.probe_namespace_dim("honcho.doc.test") is None


@pytest.mark.asyncio
async def test_probe_namespace_dim_schema_not_found_returns_none(
    store: TurbopufferVectorStore,
) -> None:
    namespace_mock = MagicMock()
    namespace_mock.exists = AsyncMock(return_value=True)
    namespace_mock.schema = AsyncMock(side_effect=_not_found_error())
    store._get_namespace = MagicMock(return_value=namespace_mock)  # pyright: ignore[reportPrivateUsage]

    assert await store.probe_namespace_dim("honcho.doc.test") is None


@pytest.mark.asyncio
async def test_probe_namespace_dim_missing_vector_attribute_raises(
    store: TurbopufferVectorStore,
) -> None:
    namespace_mock = MagicMock()
    namespace_mock.exists = AsyncMock(return_value=True)
    namespace_mock.schema = AsyncMock(return_value={})
    store._get_namespace = MagicMock(return_value=namespace_mock)  # pyright: ignore[reportPrivateUsage]

    with pytest.raises(VectorStoreError, match="no 'vector' attribute"):
        await store.probe_namespace_dim("honcho.doc.test")


@pytest.mark.asyncio
async def test_probe_namespace_dim_unparseable_type_raises(
    store: TurbopufferVectorStore,
) -> None:
    namespace_mock = MagicMock()
    namespace_mock.exists = AsyncMock(return_value=True)
    namespace_mock.schema = AsyncMock(
        return_value={"vector": SimpleNamespace(type="string")}
    )
    store._get_namespace = MagicMock(return_value=namespace_mock)  # pyright: ignore[reportPrivateUsage]

    with pytest.raises(VectorStoreError, match="unparseable"):
        await store.probe_namespace_dim("honcho.doc.test")


@pytest.mark.asyncio
async def test_probe_namespace_dim_extracts_dim(
    store: TurbopufferVectorStore,
) -> None:
    namespace_mock = MagicMock()
    namespace_mock.exists = AsyncMock(return_value=True)
    namespace_mock.schema = AsyncMock(
        return_value={"vector": SimpleNamespace(type="[768]f32")}
    )
    store._get_namespace = MagicMock(return_value=namespace_mock)  # pyright: ignore[reportPrivateUsage]

    assert await store.probe_namespace_dim("honcho.doc.test") == 768
