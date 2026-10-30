"""Tests for LanceDBVectorStore query projection behavior."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pyarrow as pa
import pytest

from src.exceptions import VectorStoreError
from src.vector_store import VectorRecord
from src.vector_store.lancedb import LanceDBVectorStore


def _build_query_chain(rows: list[dict[str, Any]]) -> MagicMock:
    """Build a chainable mock that mirrors LanceDB's async query builder."""
    chain = MagicMock()
    chain.distance_type.return_value = chain
    chain.limit.return_value = chain
    chain.select.return_value = chain
    chain.where.return_value = chain
    chain.to_list = AsyncMock(return_value=rows)
    return chain


def _patch_table(
    store: LanceDBVectorStore, rows: list[dict[str, Any]]
) -> tuple[MagicMock, MagicMock]:
    """Patch _get_table to return a mock whose vector_search yields the chain."""
    chain = _build_query_chain(rows)
    table = MagicMock()
    table.vector_search = MagicMock(return_value=chain)
    store._get_table = AsyncMock(return_value=table)  # pyright: ignore[reportPrivateUsage]
    return table, chain


@pytest.fixture
def store() -> LanceDBVectorStore:
    return LanceDBVectorStore()


def test_build_where_clause_membership(store: LanceDBVectorStore) -> None:
    """Both the dict `in` form and the bare-list sugar produce an IN clause."""
    assert (
        store._build_where_clause({"session_name": {"in": ["s1", "s2"]}})  # pyright: ignore[reportPrivateUsage]
        == "session_name IN ('s1', 's2')"
    )
    assert (
        store._build_where_clause({"session_name": ["s1", "s2"]})  # pyright: ignore[reportPrivateUsage]
        == "session_name IN ('s1', 's2')"
    )


def test_build_where_clause_empty_membership_fails_closed(
    store: LanceDBVectorStore,
) -> None:
    """An empty membership list must emit an always-false predicate, never an
    omitted condition that would widen scope (fail-open)."""
    assert store._build_where_clause({"session_name": {"in": []}}) == "1 = 0"  # pyright: ignore[reportPrivateUsage]
    assert store._build_where_clause({"session_name": []}) == "1 = 0"  # pyright: ignore[reportPrivateUsage]


@pytest.mark.asyncio
async def test_query_returns_empty_when_table_missing(
    store: LanceDBVectorStore,
) -> None:
    store._get_table = AsyncMock(return_value=None)  # pyright: ignore[reportPrivateUsage]

    results = await store.query("honcho.msg.missing", [0.1, 0.2, 0.3, 0.4])

    assert results == []


@pytest.mark.asyncio
async def test_query_default_does_not_project(store: LanceDBVectorStore) -> None:
    _table, chain = _patch_table(store, rows=[])

    await store.query("honcho.msg.test", [0.1, 0.2, 0.3, 0.4])

    chain.select.assert_not_called()


@pytest.mark.asyncio
async def test_query_with_include_attributes_false_selects_only_id(
    store: LanceDBVectorStore,
) -> None:
    _table, chain = _patch_table(store, rows=[])

    await store.query(
        "honcho.doc.test",
        [0.1, 0.2, 0.3, 0.4],
        include_attributes=False,
    )

    chain.select.assert_called_once_with(["id"])


@pytest.mark.asyncio
async def test_query_with_attribute_list_projects_id_plus_listed(
    store: LanceDBVectorStore,
) -> None:
    _table, chain = _patch_table(store, rows=[])

    await store.query(
        "honcho.msg.test",
        [0.1, 0.2, 0.3, 0.4],
        include_attributes=["message_id"],
    )

    chain.select.assert_called_once_with(["id", "message_id"])


@pytest.mark.asyncio
async def test_query_attribute_list_dedupes_explicit_id(
    store: LanceDBVectorStore,
) -> None:
    _table, chain = _patch_table(store, rows=[])

    await store.query(
        "honcho.msg.test",
        [0.1, 0.2, 0.3, 0.4],
        include_attributes=["id", "message_id"],
    )

    chain.select.assert_called_once_with(["id", "message_id"])


@pytest.mark.asyncio
async def test_query_converts_rows_to_results_with_score_and_metadata(
    store: LanceDBVectorStore,
) -> None:
    rows: list[dict[str, Any]] = [
        {
            "id": "vec_1",
            "_distance": 0.12,
            "vector": [0.0, 0.0, 0.0, 0.0],
            "message_id": "msg_1",
            "session_name": "sess_a",
        },
        {
            "id": "vec_2",
            "_distance": 0.34,
            "message_id": "msg_2",
        },
    ]
    _patch_table(store, rows=rows)

    results = await store.query("honcho.msg.test", [0.1, 0.2, 0.3, 0.4])

    assert [r.id for r in results] == ["vec_1", "vec_2"]
    assert [r.score for r in results] == [0.12, 0.34]
    # id, vector, _distance must not leak into metadata
    assert results[0].metadata == {
        "message_id": "msg_1",
        "session_name": "sess_a",
    }
    assert results[1].metadata == {"message_id": "msg_2"}


@pytest.mark.asyncio
async def test_query_filters_by_max_distance(store: LanceDBVectorStore) -> None:
    rows: list[dict[str, Any]] = [
        {"id": "vec_close", "_distance": 0.05, "message_id": "msg_1"},
        {"id": "vec_far", "_distance": 0.9, "message_id": "msg_2"},
    ]
    _patch_table(store, rows=rows)

    results = await store.query(
        "honcho.msg.test",
        [0.1, 0.2, 0.3, 0.4],
        max_distance=0.5,
    )

    assert [r.id for r in results] == ["vec_close"]


@pytest.mark.asyncio
async def test_query_returns_empty_without_opening_table_on_nonpositive_top_k(
    store: LanceDBVectorStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get_table = AsyncMock(return_value=MagicMock())
    monkeypatch.setattr(store, "_get_table", get_table)

    for top_k in (0, -1):
        assert (
            await store.query(
                "honcho.msg.test",
                [0.1, 0.2, 0.3, 0.4],
                top_k=top_k,
            )
            == []
        )

    get_table.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_table_returns_none_when_table_missing(
    store: LanceDBVectorStore,
) -> None:
    db = MagicMock()
    db.table_names = AsyncMock(return_value=[])
    store._get_db = AsyncMock(return_value=db)  # pyright: ignore[reportPrivateUsage]

    assert await store._get_table("honcho.msg.test") is None  # pyright: ignore[reportPrivateUsage]


@pytest.mark.asyncio
async def test_get_table_opens_existing_table(
    store: LanceDBVectorStore,
) -> None:
    db = MagicMock()
    db.table_names = AsyncMock(return_value=["honcho.msg.test"])
    table = MagicMock()
    db.open_table = AsyncMock(return_value=table)
    store._get_db = AsyncMock(return_value=db)  # pyright: ignore[reportPrivateUsage]

    assert await store._get_table("honcho.msg.test") is table  # pyright: ignore[reportPrivateUsage]
    db.open_table.assert_awaited_once_with("honcho.msg.test")


@pytest.mark.asyncio
async def test_get_or_create_table_returns_existing(
    store: LanceDBVectorStore,
) -> None:
    db = MagicMock()
    db.table_names = AsyncMock(return_value=["honcho.msg.test"])
    table = MagicMock()
    db.open_table = AsyncMock(return_value=table)
    store._get_db = AsyncMock(return_value=db)  # pyright: ignore[reportPrivateUsage]

    assert await store._get_or_create_table("honcho.msg.test") is table  # pyright: ignore[reportPrivateUsage]
    db.create_table.assert_not_called()


@pytest.mark.asyncio
async def test_get_or_create_table_creates_new_with_schema(
    store: LanceDBVectorStore,
) -> None:
    db = MagicMock()
    db.table_names = AsyncMock(return_value=[])
    table = MagicMock()
    db.create_table = AsyncMock(return_value=table)
    store._get_db = AsyncMock(return_value=db)  # pyright: ignore[reportPrivateUsage]

    result = await store._get_or_create_table("honcho.msg.test")  # pyright: ignore[reportPrivateUsage]

    assert result is table
    call = db.create_table.await_args
    assert call.args[0] == "honcho.msg.test"
    schema = call.kwargs["schema"]
    assert [f.name for f in schema] == [
        "id",
        "vector",
        "message_id",
        "session_name",
        "peer_name",
    ]


@pytest.mark.asyncio
async def test_get_or_create_table_opens_table_created_by_another_worker(
    store: LanceDBVectorStore,
) -> None:
    db = MagicMock()
    db.table_names = AsyncMock(return_value=[])
    db.create_table = AsyncMock(side_effect=RuntimeError("already exists"))
    table = MagicMock()
    db.open_table = AsyncMock(return_value=table)
    store._get_db = AsyncMock(return_value=db)  # pyright: ignore[reportPrivateUsage]

    assert await store._get_or_create_table("honcho.doc.test") is table  # pyright: ignore[reportPrivateUsage]


def test_metadata_fields_for_namespace_message(store: LanceDBVectorStore) -> None:
    fields = store._metadata_fields_for_namespace("honcho.msg.abc")  # pyright: ignore[reportPrivateUsage]
    assert [f.name for f in fields] == ["message_id", "session_name", "peer_name"]


def test_metadata_fields_for_namespace_document(store: LanceDBVectorStore) -> None:
    fields = store._metadata_fields_for_namespace("honcho.doc.abc")  # pyright: ignore[reportPrivateUsage]
    assert [f.name for f in fields] == [
        "workspace_name",
        "observer",
        "observed",
        "session_name",
        "level",
    ]


def test_metadata_fields_for_namespace_short_or_unknown(
    store: LanceDBVectorStore,
) -> None:
    assert store._metadata_fields_for_namespace("honcho") == []  # pyright: ignore[reportPrivateUsage]
    assert store._metadata_fields_for_namespace("honcho.other.abc") == []  # pyright: ignore[reportPrivateUsage]


def test_row_to_dict_excludes_reserved_metadata_keys(
    store: LanceDBVectorStore,
) -> None:
    record = VectorRecord(
        id="v1",
        embedding=[0.1, 0.2],
        metadata={"id": "clobber", "vector": [9.9], "_distance": 1.0, "foo": "bar"},
    )
    assert store._row_to_dict(record) == {  # pyright: ignore[reportPrivateUsage]
        "id": "v1",
        "vector": [0.1, 0.2],
        "foo": "bar",
    }


def test_row_to_dict_without_metadata(store: LanceDBVectorStore) -> None:
    record = VectorRecord(id="v1", embedding=[0.1, 0.2])
    assert store._row_to_dict(record) == {  # pyright: ignore[reportPrivateUsage]
        "id": "v1",
        "vector": [0.1, 0.2],
    }


@pytest.mark.asyncio
async def test_upsert_many_short_circuits_on_empty(
    store: LanceDBVectorStore,
) -> None:
    store._get_or_create_table = AsyncMock()  # pyright: ignore[reportPrivateUsage]
    await store.upsert_many("honcho.msg.test", [])
    store._get_or_create_table.assert_not_awaited()  # pyright: ignore[reportPrivateUsage]


@pytest.mark.asyncio
async def test_upsert_many_executes_merge_insert(
    store: LanceDBVectorStore,
) -> None:
    record = VectorRecord(id="v1", embedding=[0.1, 0.2], metadata={"foo": "bar"})
    merger = MagicMock()
    merger.when_matched_update_all.return_value = merger
    merger.when_not_matched_insert_all.return_value = merger
    merger.execute = AsyncMock()
    table = MagicMock()
    table.merge_insert.return_value = merger
    store._get_or_create_table = AsyncMock(return_value=table)  # pyright: ignore[reportPrivateUsage]

    await store.upsert_many("honcho.msg.test", [record])

    table.merge_insert.assert_called_once_with("id")
    merger.execute.assert_awaited_once_with(
        [{"id": "v1", "vector": [0.1, 0.2], "foo": "bar"}]
    )


@pytest.mark.asyncio
async def test_upsert_many_raises_vector_store_error(
    store: LanceDBVectorStore,
) -> None:
    record = VectorRecord(id="v1", embedding=[0.1, 0.2])
    merger = MagicMock()
    merger.when_matched_update_all.return_value = merger
    merger.when_not_matched_insert_all.return_value = merger
    merger.execute = AsyncMock(side_effect=RuntimeError("boom"))
    table = MagicMock()
    table.merge_insert.return_value = merger
    store._get_or_create_table = AsyncMock(return_value=table)  # pyright: ignore[reportPrivateUsage]

    with pytest.raises(VectorStoreError, match="Failed to upsert"):
        await store.upsert_many("honcho.msg.test", [record])


@pytest.mark.asyncio
async def test_query_applies_where_clause_when_filters(
    store: LanceDBVectorStore,
) -> None:
    _table, chain = _patch_table(store, rows=[])

    await store.query(
        "honcho.msg.test",
        [0.1, 0.2, 0.3, 0.4],
        filters={"session_name": "s1"},
    )

    chain.where.assert_called_once_with("session_name = 's1'")


@pytest.mark.asyncio
async def test_query_reraises_generic_exception(
    store: LanceDBVectorStore,
) -> None:
    chain = _build_query_chain(rows=[])
    chain.to_list = AsyncMock(side_effect=RuntimeError("boom"))
    table = MagicMock()
    table.vector_search = MagicMock(return_value=chain)
    store._get_table = AsyncMock(return_value=table)  # pyright: ignore[reportPrivateUsage]

    with pytest.raises(RuntimeError, match="boom"):
        await store.query("honcho.msg.test", [0.1, 0.2, 0.3, 0.4])


def test_build_where_clause_none_for_empty_filters(
    store: LanceDBVectorStore,
) -> None:
    assert store._build_where_clause({}) is None  # pyright: ignore[reportPrivateUsage]
    assert store._build_where_clause(None) is None  # pyright: ignore[reportPrivateUsage]


def test_build_where_clause_rejects_invalid_key(
    store: LanceDBVectorStore,
) -> None:
    with pytest.raises(ValueError, match="Invalid filter key"):
        store._build_where_clause({"bad key": "x"})  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(ValueError, match="Invalid filter key"):
        store._build_where_clause({"1abc": "x"})  # pyright: ignore[reportPrivateUsage]


def test_build_where_clause_escapes_quotes(
    store: LanceDBVectorStore,
) -> None:
    assert (
        store._build_where_clause({"name": "O'Reilly"})  # pyright: ignore[reportPrivateUsage]
        == "name = 'O''Reilly'"
    )
    assert (
        store._build_where_clause({"name": {"in": ["a'b", "plain"]}})  # pyright: ignore[reportPrivateUsage]
        == "name IN ('a''b', 'plain')"
    )


def test_build_where_clause_scalar_types(store: LanceDBVectorStore) -> None:
    assert store._build_where_clause({"active": True}) == "active = true"  # pyright: ignore[reportPrivateUsage]
    assert store._build_where_clause({"deleted": None}) == "deleted IS NULL"  # pyright: ignore[reportPrivateUsage]
    assert store._build_where_clause({"count": 5}) == "count = 5"  # pyright: ignore[reportPrivateUsage]


@pytest.mark.asyncio
async def test_delete_many_short_circuits_on_empty(
    store: LanceDBVectorStore,
) -> None:
    store._get_table = AsyncMock()  # pyright: ignore[reportPrivateUsage]
    await store.delete_many("honcho.msg.test", [])
    store._get_table.assert_not_awaited()  # pyright: ignore[reportPrivateUsage]


@pytest.mark.asyncio
async def test_delete_many_noop_when_table_missing(
    store: LanceDBVectorStore,
) -> None:
    store._get_table = AsyncMock(return_value=None)  # pyright: ignore[reportPrivateUsage]
    await store.delete_many("honcho.msg.test", ["v1"])


@pytest.mark.asyncio
async def test_delete_many_deletes_with_escaped_ids(
    store: LanceDBVectorStore,
) -> None:
    table = MagicMock()
    table.delete = AsyncMock()
    store._get_table = AsyncMock(return_value=table)  # pyright: ignore[reportPrivateUsage]

    await store.delete_many("honcho.msg.test", ["a'1", "b2"])

    table.delete.assert_awaited_once_with("id IN ('a''1', 'b2')")


@pytest.mark.asyncio
async def test_delete_many_reraises_generic_exception(
    store: LanceDBVectorStore,
) -> None:
    table = MagicMock()
    table.delete = AsyncMock(side_effect=RuntimeError("boom"))
    store._get_table = AsyncMock(return_value=table)  # pyright: ignore[reportPrivateUsage]

    with pytest.raises(RuntimeError, match="boom"):
        await store.delete_many("honcho.msg.test", ["v1"])


@pytest.mark.asyncio
async def test_delete_namespace_drops_existing_table(
    store: LanceDBVectorStore,
) -> None:
    db = MagicMock()
    db.table_names = AsyncMock(return_value=["honcho.msg.test"])
    db.drop_table = AsyncMock()
    store._get_db = AsyncMock(return_value=db)  # pyright: ignore[reportPrivateUsage]

    await store.delete_namespace("honcho.msg.test")

    db.drop_table.assert_awaited_once_with("honcho.msg.test")


@pytest.mark.asyncio
async def test_delete_namespace_noop_when_table_missing(
    store: LanceDBVectorStore,
) -> None:
    db = MagicMock()
    db.table_names = AsyncMock(return_value=[])
    db.drop_table = AsyncMock()
    store._get_db = AsyncMock(return_value=db)  # pyright: ignore[reportPrivateUsage]

    await store.delete_namespace("honcho.msg.test")

    db.drop_table.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_namespace_reraises_generic_exception(
    store: LanceDBVectorStore,
) -> None:
    db = MagicMock()
    db.table_names = AsyncMock(side_effect=RuntimeError("boom"))
    store._get_db = AsyncMock(return_value=db)  # pyright: ignore[reportPrivateUsage]

    with pytest.raises(RuntimeError, match="boom"):
        await store.delete_namespace("honcho.msg.test")


@pytest.mark.asyncio
async def test_probe_namespace_dim_raises_when_no_fixed_vector_field(
    store: LanceDBVectorStore,
) -> None:
    db = MagicMock()
    db.table_names = AsyncMock(return_value=["honcho.msg.test"])
    table = MagicMock()
    table.schema = AsyncMock(return_value=pa.schema([pa.field("id", pa.string())]))
    db.open_table = AsyncMock(return_value=table)
    store._get_db = AsyncMock(return_value=db)  # pyright: ignore[reportPrivateUsage]

    with pytest.raises(VectorStoreError, match="no 'vector' field"):
        await store.probe_namespace_dim("honcho.msg.test")
