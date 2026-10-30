"""Deep CRUD session tests for uncovered branches."""

import pytest
from nanoid import generate as generate_nanoid
from sqlalchemy.ext.asyncio import AsyncSession

from src import crud, models, schemas
from src.crud import session as session_crud
from src.exceptions import ResourceNotFoundException, ValidationException


def test_session_cache_key_fmt():
    key = session_crud.session_cache_key("my_ws", "my_sess")
    assert "my_ws" in key and "my_sess" in key
    assert key.startswith(session_crud.cache_key_namespace())


def test_count_observers_in_config():
    cfg = schemas.SessionPeerConfig(observe_others=True, observe_me=False)
    bystander = schemas.SessionPeerConfig(observe_others=False, observe_me=True)
    assert session_crud.count_observers_in_config({"a": cfg, "b": bystander}) == 1
    assert session_crud.count_observers_in_config({}) == 0


def test_reject_resolved_scope_peers_passes_for_normal(monkeypatch):
    monkeypatch.setattr(session_crud, "is_scope_peer", lambda name, meta: False)
    peer = models.Peer(name="p", workspace_name="ws", internal_metadata={})
    session_crud._reject_resolved_scope_peers([peer])  # no raise


def test_reject_resolved_scope_peers_raises(monkeypatch):
    monkeypatch.setattr(session_crud, "is_scope_peer", lambda name, meta: True)
    peer = models.Peer(name="scope.x", workspace_name="ws", internal_metadata={})
    with pytest.raises(ValidationException, match="are scopes"):
        session_crud._reject_resolved_scope_peers([peer])


@pytest.mark.asyncio
async def test_get_sessions_reverse_branches(db_session, sample_data):
    from datetime import UTC, datetime

    test_workspace, _ = sample_data
    ws = test_workspace.name
    db_session.add_all(
        [
            models.Session(
                name="s_asc1", workspace_name=ws,
                created_at=datetime(2020, 1, 1, tzinfo=UTC),
            ),
            models.Session(
                name="s_asc2", workspace_name=ws,
                created_at=datetime(2021, 1, 1, tzinfo=UTC),
            ),
        ]
    )
    await db_session.flush()

    asc = await session_crud.get_sessions(ws)
    rows_asc = (await db_session.execute(asc)).scalars().all()
    assert [r.name for r in rows_asc] == ["s_asc1", "s_asc2"]

    desc = await session_crud.get_sessions(ws, reverse=True)
    rows_desc = (await db_session.execute(desc)).scalars().all()
    assert [r.name for r in rows_desc] == ["s_asc2", "s_asc1"]


@pytest.mark.asyncio
async def test_is_peer_in_session_false_when_no_membership(db_session, sample_data):
    test_workspace, _ = sample_data
    ws = test_workspace.name
    name = "s_ispeer"
    db_session.add(models.Session(name=name, workspace_name=ws))
    await db_session.flush()
    ok = await session_crud.is_peer_in_session(db_session, ws, name, "nobody")
    assert ok is False


@pytest.mark.asyncio
async def test_remove_peers_from_session(db_session, sample_data):
    test_workspace, test_peer = sample_data
    session_name = str(generate_nanoid())
    await crud.get_or_create_session(
        db_session,
        schemas.SessionCreate(
            name=session_name, peers={test_peer.name: schemas.SessionPeerConfig()}
        ),
        test_workspace.name,
    )
    removed = await session_crud.remove_peers_from_session(
        db_session, test_workspace.name, session_name, {test_peer.name}
    )
    assert removed is True
    active = await session_crud.is_peer_in_session(
        db_session, test_workspace.name, session_name, test_peer.name
    )
    assert active is False


@pytest.mark.asyncio
async def test_upsert_session_peers_empty_fetch_skipped(db_session, sample_data):
    test_workspace, _ = sample_data
    session_name = str(generate_nanoid())
    db_session.add(models.Session(name=session_name, workspace_name=test_workspace.name))
    await db_session.flush()
    result = await session_crud.upsert_session_peers(
        db_session,
        test_workspace.name,
        session_name,
        {},
        fetch_after_upsert=False,
    )
    assert result == []


@pytest.mark.asyncio
async def test_delete_session_cascade(db_session, sample_data):
    test_workspace, test_peer = sample_data
    session_name = str(generate_nanoid())
    db_session.add(models.Session(name=session_name, workspace_name=test_workspace.name))
    await db_session.flush()

    msg = models.Message(
        workspace_name=test_workspace.name,
        session_name=session_name,
        peer_name=test_peer.name,
        content="hello",
        seq_in_session=0,
    )
    db_session.add(msg)
    await db_session.flush()

    result = await crud.delete_session(db_session, test_workspace.name, session_name)
    assert result.messages_deleted >= 1
    with pytest.raises(ResourceNotFoundException):
        await crud.get_session(
            db_session, session_name, test_workspace.name, include_inactive=True
        )


@pytest.mark.asyncio
async def test_delete_session_external_vector_store(monkeypatch, db_session, sample_data):
    test_workspace, test_peer = sample_data
    session_name = str(generate_nanoid())
    db_session.add(models.Session(name=session_name, workspace_name=test_workspace.name))
    await db_session.flush()

    msg = models.Message(
        workspace_name=test_workspace.name,
        session_name=session_name,
        peer_name=test_peer.name,
        content="ext",
        seq_in_session=0,
    )
    db_session.add(msg)
    await db_session.flush()
    msg_id = msg.public_id

    embedding = models.MessageEmbedding(
        workspace_name=test_workspace.name,
        session_name=session_name,
        message_id=msg_id,
        peer_name=test_peer.name,
        content="ext",
        embedding=[0.1] * 1536,
    )
    db_session.add(embedding)
    await db_session.flush()

    deleted = []

    class FakeStore:
        def get_vector_namespace(self, kind, workspace_name, *a, **k):
            return f"{kind}:{workspace_name}"

        async def delete_many(self, namespace, ids):
            deleted.extend(ids)

    monkeypatch.setattr(session_crud, "get_external_vector_store", lambda: FakeStore())
    result = await crud.delete_session(db_session, test_workspace.name, session_name)
    assert result.messages_deleted >= 1
    assert deleted  # external delete was invoked


@pytest.mark.asyncio
async def test_get_peer_config_missing_session_peer(db_session, sample_data):
    test_workspace, test_peer = sample_data
    session_name = str(generate_nanoid())
    db_session.add(models.Session(name=session_name, workspace_name=test_workspace.name))
    await db_session.flush()
    with pytest.raises(ResourceNotFoundException):
        await session_crud.get_peer_config(
            db_session, test_workspace.name, session_name, test_peer.name
        )


@pytest.mark.asyncio
async def test_set_peer_config_creates_new(db_session, sample_data):
    test_workspace, test_peer = sample_data
    session_name = str(generate_nanoid())
    await crud.get_or_create_session(
        db_session,
        schemas.SessionCreate(name=session_name),
        test_workspace.name,
    )
    await session_crud.set_peer_config(
        db_session,
        test_workspace.name,
        session_name,
        test_peer.name,
        schemas.SessionPeerConfig(observe_others=True, observe_me=False),
    )
    cfg = await session_crud.get_peer_config(
        db_session, test_workspace.name, session_name, test_peer.name
    )
    assert cfg.observe_others is True


@pytest.mark.asyncio
async def test_set_peer_config_updates_existing(db_session, sample_data):
    test_workspace, test_peer = sample_data
    session_name = str(generate_nanoid())
    await crud.get_or_create_session(
        db_session,
        schemas.SessionCreate(
            name=session_name,
            peers={test_peer.name: schemas.SessionPeerConfig(observe_others=True)},
        ),
        test_workspace.name,
    )
    await session_crud.set_peer_config(
        db_session,
        test_workspace.name,
        session_name,
        test_peer.name,
        schemas.SessionPeerConfig(observe_others=False, observe_me=True),
    )
    cfg = await session_crud.get_peer_config(
        db_session, test_workspace.name, session_name, test_peer.name
    )
    assert cfg.observe_others is False
    assert cfg.observe_me is True
