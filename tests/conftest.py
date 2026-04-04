"""
Test configuration for Honcho tests.
Uses real resources: PostgreSQL with pgvector, Ollama embeddings, real LLM calls.
No mocks, no Redis (cache disabled for tests).
"""

import logging
from collections.abc import AsyncGenerator
from typing import Any

import jwt
import pytest
import pytest_asyncio
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from nanoid import generate as generate_nanoid
from sqlalchemy import text
from sqlalchemy.engine.url import URL, make_url
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy_utils import (
    create_database,
    database_exists,
    drop_database,
)

from src import models
from src.config import settings
from src.db import Base
from src.dependencies import get_db, set_test_db_session
from src.exceptions import HonchoException
from src.main import app
from src.models import Peer, Workspace
from src.security import JWTParams, create_admin_jwt, create_jwt


# Logging configuration
class TestHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord):
        self.records.append(record)


test_handler = TestHandler()
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[test_handler],
)
logger = logging.getLogger(__name__)
logging.getLogger("sqlalchemy.engine.Engine").disabled = True

# Test database URL
DB_URI = settings.DB.CONNECTION_URI
if not DB_URI:
    raise ValueError("DB.CONNECTION_URI must be set in configuration or environment.")
CONNECTION_URI = make_url(DB_URI)


def _get_test_db_url(worker_id: str) -> URL:
    """Get a worker-specific test database URL."""
    db_name = "test_db" if worker_id == "master" else f"test_db_{worker_id}"
    return CONNECTION_URI.set(database=db_name)


def create_test_database(db_url: URL):
    """Create test database if it doesn't exist."""
    try:
        if not database_exists(db_url):
            logger.info(f"Creating test database: {db_url.database}")
            create_database(db_url)
        else:
            logger.info(f"Database already exists: {db_url.database}")
    except Exception as e:
        logger.error(f"Error creating database: {e}")
        raise


async def setup_test_database(db_url: URL):
    """Set up test database with required extensions."""
    engine = create_async_engine(db_url, echo=False)

    async with engine.begin() as conn:
        # Create pgvector extension
        try:
            logger.info("Creating pgvector extension...")
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            logger.info("pgvector extension created.")
        except ProgrammingError as e:
            raise RuntimeError(f"Failed to create pgvector extension: {e}") from e

        # Create pg_trgm extension for trigram similarity
        try:
            logger.info("Creating pg_trgm extension...")
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
            logger.info("pg_trgm extension created.")
        except ProgrammingError as e:
            raise RuntimeError(f"Failed to create pg_trgm extension: {e}") from e

        # Create all tables - must be in the same transaction as extensions
        # because pgvector types are needed for table creation
        logger.info("Creating all tables...")
        await conn.run_sync(Base.metadata.create_all)
        logger.info("Tables created.")
        
        # Verify content_tsv column exists by trying to select from it
        # We do this in the same transaction to ensure consistency
        logger.info("Verifying content_tsv column...")
        try:
            await conn.execute(text("SELECT content_tsv FROM documents LIMIT 0"))
            logger.info("content_tsv column verified.")
        except Exception as e:
            logger.info(f"content_tsv column not found: {e}")
            raise

    return engine


async def _truncate_all_tables(engine: AsyncEngine) -> None:
    """Truncate all tables between tests."""
    table_names: list[str] = []
    for table in Base.metadata.sorted_tables:
        if table.schema:
            table_names.append(f'"{table.schema}"."{table.name}"')
        else:
            table_names.append(f'"{table.name}"')

    if not table_names:
        return

    async with engine.begin() as conn:
        await conn.execute(
            text(f"TRUNCATE {', '.join(table_names)} RESTART IDENTITY CASCADE")
        )


# =============================================================================
# Session-scoped fixtures
# =============================================================================


@pytest_asyncio.fixture(scope="session")
async def db_engine(worker_id: str):
    """Create database engine once per test session."""
    test_db_url = _get_test_db_url(worker_id)
    create_test_database(test_db_url)
    engine = await setup_test_database(test_db_url)

    # Force schema to 'public' for tests
    original_schema = Base.metadata.schema
    Base.metadata.schema = "public"
    for table in Base.metadata.tables.values():
        table.schema = "public"

    try:
        yield engine
    finally:
        await engine.dispose()
        Base.metadata.schema = original_schema
        for table in Base.metadata.tables.values():
            table.schema = original_schema
        drop_database(test_db_url)


@pytest_asyncio.fixture(scope="function")
async def db_session(db_engine: AsyncEngine):
    """Create a database session for each test."""
    Session = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with Session() as session:
        # Set the test session for tracked_db to use
        token = set_test_db_session(session)
        try:
            yield session
        finally:
            # Reset the test session context
            from src.dependencies import _test_db_session
            _test_db_session.reset(token)
            await session.rollback()
    # Truncate tables after test
    await _truncate_all_tables(db_engine)


# =============================================================================
# Cache - DISABLED for tests (no Redis dependency)
# =============================================================================


@pytest_asyncio.fixture(scope="session", autouse=True)
async def setup_cache():
    """Set up cache (disabled) for tests.
    
    Even when cache is disabled, cashews requires setup() to be called
    to avoid NotConfiguredError. We set up a no-op cache.
    """
    from cashews.picklers import PicklerType
    
    original_enabled = settings.CACHE.ENABLED
    original_url = settings.CACHE.URL
    
    # Disable cache in settings
    settings.CACHE.ENABLED = False
    
    # Set up cache with a dummy URL - it won't be used since ENABLED=False
    # but setup() must be called to avoid NotConfiguredError
    from src.cache.client import cache
    cache.setup(
        "redis://localhost:6379/15",  # Dummy URL, won't be used
        pickle_type=PicklerType.SQLALCHEMY,
        enable=False,  # Disabled
    )
    
    yield
    
    # Restore original settings
    settings.CACHE.ENABLED = original_enabled
    settings.CACHE.URL = original_url
    
    # Close cache
    try:
        await cache.close()
    except Exception:
        pass


@pytest_asyncio.fixture(scope="function", autouse=True)
async def cleanup_global_singletons():
    """Clean up global singletons after each test to prevent state leakage.
    
    This ensures that singleton clients (embedding, reranker) are reset
    between tests, preventing event loop and connection pool issues.
    
    IMPORTANT: Monkeypatch may set attributes on the proxy objects,
    which bypasses __getattr__. We need to clear these attributes
    to ensure fresh client access on next use.
    """
    yield
    
    # Reset embedding client singleton after each test
    # We don't close the client to avoid event loop issues; instead we just
    # reset the singleton so a fresh client will be created on next access.
    # This works because _EmbeddingClientProxy always calls get_embedding_client()
    # which creates a new instance if _embedding_client_instance is None.
    import src.embedding_client as embedding_module
    import src.reranker_client as reranker_module
    
    # Clear any monkeypatched attributes from the proxy
    # These bypass __getattr__ and may reference old clients
    if hasattr(embedding_module.embedding_client, '__dict__'):
        embedding_module.embedding_client.__dict__.clear()
    
    # Reset embedding client - just set to None, don't try to close
    # The httpx client will be garbage collected when the instance is dropped
    embedding_module._embedding_client_instance = None
    
    # Reset reranker client
    reranker_module._reranker_instance = None


# =============================================================================
# Test client
# =============================================================================


@pytest.fixture(scope="function")
async def client(db_session: AsyncSession) -> AsyncGenerator[TestClient, Any]:
    """Create a FastAPI TestClient for each test."""
    
    # Register exception handlers
    @app.exception_handler(HonchoException)
    async def test_exception_handler(_: Request, exc: HonchoException):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    async def override_get_db():
        yield db_session

    # Override get_db dependency
    app.dependency_overrides[get_db] = override_get_db

    # Set the test database session for tracked_db to use
    # This ensures tracked_db uses the test database instead of production
    test_session_token = set_test_db_session(db_session)

    try:
        with TestClient(app) as c:
            if settings.AUTH.USE_AUTH:
                c.headers["Authorization"] = f"Bearer {create_admin_jwt()}"
            yield c
    finally:
        # Reset the test session context
        from src.dependencies import _test_db_session
        _test_db_session.reset(test_session_token)


# =============================================================================
# Authentication clients
# =============================================================================


def create_invalid_jwt() -> str:
    return jwt.encode({"ad": "invalid"}, "this is not the secret", algorithm="HS256")


class AuthClient(TestClient):
    auth_type: str | None = None


@pytest.fixture(
    params=[
        ("none", None),
        ("invalid", create_invalid_jwt),
        ("empty", lambda: create_jwt(JWTParams())),
        ("admin", create_admin_jwt),
    ]
)
def auth_client(
    client: AuthClient,
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
):
    """Provide client with different authentication states."""
    monkeypatch.setattr(settings.AUTH, "USE_AUTH", True)
    monkeypatch.setattr(
        settings.AUTH, "JWT_SECRET", "test-secret-key-for-jwt-signing-minimum-32-bytes!"
    )

    client.headers.pop("Authorization", None)
    auth_type, token_func = request.param
    client.auth_type = auth_type

    if token_func is not None:
        client.headers["Authorization"] = f"Bearer {token_func()}"

    return client


# =============================================================================
# Test data fixtures
# =============================================================================


@pytest_asyncio.fixture(scope="function")
async def sample_data(db_session: AsyncSession) -> AsyncGenerator[tuple[Workspace, Peer], Any]:
    """Create sample workspace and peer for tests."""
    test_workspace = models.Workspace(name=str(generate_nanoid()))
    db_session.add(test_workspace)

    test_peer = models.Peer(
        name=str(generate_nanoid()), workspace_name=test_workspace.name
    )
    db_session.add(test_peer)

    await db_session.commit()
    yield test_workspace, test_peer


# =============================================================================
# Settings fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def enable_deriver_for_tests():
    """Enable deriver for tests that need queue processing."""
    original_value = settings.DERIVER.ENABLED
    settings.DERIVER.ENABLED = True
    yield
    settings.DERIVER.ENABLED = original_value


@pytest.fixture(autouse=True)
def disable_telemetry():
    """Disable telemetry for tests."""
    original_value = settings.TELEMETRY.ENABLED
    settings.TELEMETRY.ENABLED = False
    yield
    settings.TELEMETRY.ENABLED = original_value


# =============================================================================
# Mock fixtures for tests that cannot use real resources
# =============================================================================
# These mocks are ONLY for tests that verify internal behavior (call counts,
# argument inspection) which cannot be tested with real resources.


@pytest.fixture
def mock_vector_store():
    """Mock vector store for unit tests that verify internal behavior.
    
    Use this ONLY when you need to assert on mock calls (call_count, await_args).
    Integration tests should use real pgvector instead.
    """
    from unittest.mock import AsyncMock, MagicMock
    
    from src.vector_store import VectorQueryResult, VectorRecord, VectorUpsertResult
    
    mock = MagicMock()
    mock.upsert_many = AsyncMock(return_value=VectorUpsertResult(ok=True))
    mock.query = AsyncMock(return_value=[])
    mock.delete_many = AsyncMock(return_value=None)
    mock.delete_namespace = AsyncMock(return_value=None)
    mock.get_vector_namespace = MagicMock(return_value="test_namespace")
    
    return mock