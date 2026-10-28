"""Unit tests for src/crud/webhook.py.

Uses a mocked AsyncSession to drive the CRUD branches without a live DB.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from src import schemas
from src.crud.webhook import (
    delete_webhook_endpoint,
    get_or_create_webhook_endpoint,
    list_webhook_endpoints,
)
from src.exceptions import ResourceNotFoundException
from src.utils.types import GetOrCreateResult


def _endpoint(url="https://hook.example.com/cb", endpoint_id="ep-1", workspace_name="ws"):
    from datetime import UTC, datetime
    return MagicMock(
        url=url,
        id=endpoint_id,
        workspace_name=workspace_name,
        created_at=datetime.now(UTC),
    )


@pytest_asyncio.fixture
async def db():
    m = MagicMock()
    m.execute = AsyncMock(return_value=MagicMock())
    m.commit = AsyncMock()
    m.delete = AsyncMock()
    m.scalar = AsyncMock(return_value=MagicMock())
    return m


@pytest.fixture(autouse=True)
def _patch_get_workspace():
    with patch("src.crud.webhook.get_workspace", new=AsyncMock()) as m:
        yield m


class TestGetOrCreate:
    async def test_workspace_check_called(self, db, _patch_get_workspace):
        with patch("src.crud.webhook.settings") as mock_settings, patch(
            "src.crud.webhook.schemas.WebhookEndpoint.model_validate",
            return_value=MagicMock(),
        ):
            mock_settings.WEBHOOK.MAX_WORKSPACE_LIMIT = 5
            # no existing endpoints
            db.execute.return_value.scalars.return_value.all.return_value = []
            await get_or_create_webhook_endpoint(
                db, "ws", schemas.WebhookEndpointCreate(url="https://hook.example.com/cb")
            )
        _patch_get_workspace.assert_awaited_once_with(db, workspace_name="ws")

    async def test_existing_endpoint_returns_not_created(self, db):
        ep = _endpoint()
        db.execute.return_value.scalars.return_value.all.return_value = [ep]
        result = await get_or_create_webhook_endpoint(
            db, "ws", schemas.WebhookEndpointCreate(url=ep.url)
        )
        assert isinstance(result, GetOrCreateResult)
        assert result.created is False

    async def test_limit_reached_raises_valueerror(self, db):
        with patch("src.crud.webhook.settings") as mock_settings:
            mock_settings.WEBHOOK.MAX_WORKSPACE_LIMIT = 2
            eps = [_endpoint(url="https://a.example.com"), _endpoint(url="https://b.example.com")]
            db.execute.return_value.scalars.return_value.all.return_value = eps
            with pytest.raises(ValueError):
                await get_or_create_webhook_endpoint(
                    db, "ws", schemas.WebhookEndpointCreate(url="https://new.example.com")
                )

    async def test_creates_new_endpoint(self, db):
        with patch("src.crud.webhook.settings") as mock_settings, patch(
            "src.crud.webhook.schemas.WebhookEndpoint.model_validate",
            return_value=MagicMock(),
        ):
            mock_settings.WEBHOOK.MAX_WORKSPACE_LIMIT = 5
            db.execute.return_value.scalars.return_value.all.return_value = []
            result = await get_or_create_webhook_endpoint(
                db, "ws", schemas.WebhookEndpointCreate(url="https://new.example.com")
            )
        assert result.created is True
        db.add.assert_called_once()
        db.commit.assert_awaited_once()

    async def test_workspace_not_found_propagates(self, db, _patch_get_workspace):
        from src.exceptions import ResourceNotFoundException
        _patch_get_workspace.side_effect = ResourceNotFoundException("gone")
        with pytest.raises(ResourceNotFoundException):
            await get_or_create_webhook_endpoint(
                db, "ws", schemas.WebhookEndpointCreate(url="https://x.example.com")
            )


class TestListEndpoints:
    async def test_builds_select(self):
        stmt = await list_webhook_endpoints("ws")
        # returns a Select that filters by workspace
        assert "SELECT" in str(stmt)


class TestDeleteEndpoint:
    async def test_delete_success(self, db):
        ep = _endpoint()
        db.execute.return_value.scalar_one_or_none.return_value = ep
        await delete_webhook_endpoint(db, "ws", "ep-1")
        db.delete.assert_called_once_with(ep)
        db.commit.assert_awaited_once()

    async def test_delete_not_found_raises(self, db):
        db.execute.return_value.scalar_one_or_none.return_value = None
        with pytest.raises(ResourceNotFoundException):
            await delete_webhook_endpoint(db, "ws", "ep-missing")
