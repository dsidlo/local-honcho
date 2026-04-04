# Simplified conftest.py - Uses real resources instead of mocks
# Requires: Redis, Ollama (with nomic-embed-text), PostgreSQL with pgvector, OpenAI API key

import logging
from collections.abc import AsyncGenerator
from typing import Any

import jwt
import pytest
import pytest_asyncio
from cashews.picklers import PicklerType
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from nanoid import generate as generate_nanoid
from sqlalchemy import text
from sqlalchemy.engine.url import URL, make_url
from sqlalchemy.exc import OperationalError, ProgrammingError
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
from src.cache.client import cache
from src.config import settings
from src.db import Base
from src.dependencies import get_db
from src.exceptions import HonchoException
from src.main import app
from src.models import Peer, Workspace
from src.security import JWTParams, create_admin_jwt, create_jwt


# Test logging handler
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
    raise ValueError(
        "DB.CONNECTION_URI must be set in configuration or environment."
    )
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
    """Set up test database with extensions."""
    engine = create_async_engine(db_url, echo=False)
    async with engine.connect() as conn:
        # Create pgvector extension
        try:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.commit()
        except ProgrammingError as e:
            raise RuntimeError(f"Failed to create pgvector extension: {e}") from e

        # Create pg_trgm extension for trigram similarity
        try:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
            await conn.commit()
        except ProgrammingError as e:
            raise RuntimeError(f"Failed to create pg_trgm extension: {e}") from e

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
        await conn.execute(text(f"TRUNCATE {', '.join(table_names)} RESTART IDENTITY CASCADE"))


# Session-scoped database engine
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
        drop_database(test_db_url)  # Note: This may fail if connections remain


# Function-scoped database session
@pytest_asyncio.fixture(scope="function")
async def db_session(db_engine: AsyncEngine):
    """Create a database session for each test."""
    Session = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    try:
        async with Session() as session:
            try:
                yield session
            finally:
                await session.rollback()
    finally:
        await _truncate_all_tables(db_engine)


# Real Redis cache fixture
@pytest_asyncio.fixture(scope="session")
async def cache_session():
    """Set up real Redis cache for tests."""
    original_enabled = settings.CACHE.ENABLED
    original_url = settings.CACHE.URL

    try:
        settings.CACHE.ENABLED = True
        redis_url = settings.CACHE.URL or "redis://localhost:6379/0"
        settings.CACHE.URL = redis_url
        cache.setup(redis_url, pickle_type=PicklerType.SQLALCHEMY, enable=True)
        yield None
    finally:
        try:
            await cache.close()
        except Exception:
            pass
        settings.CACHE.ENABLED = original_enabled
        settings.CACHE.URL = original_url


@pytest_asyncio.fixture(scope="function", autouse=True)
async def clear_cache(cache_session):
    """Clear cache between tests."""
    try:
        await cache.delete_match("*")
    except Exception:
        pass
    yield
    try:
        await cache.delete_match("*")
    except Exception:
        pass


# Test client fixture
@pytest.fixture(scope="function")
async def client(
    db_session: AsyncSession,
    cache_session,
) -> AsyncGenerator[TestClient, Any]:
    """Create a FastAPI TestClient."""
    @app.exception_handler(HonchoException)
    async def test_exception_handler(_: Request, exc: HonchoException):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as c:
        if settings.AUTH.USE_AUTH:
            c.headers["Authorization"] = f"Bearer {create_admin_jwt()}"
        yield c


# Authentication fixtures
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
    """Provide client with different auth states."""
    monkeypatch.setattr(settings.AUTH, "USE_AUTH", True)
    monkeypatch.setattr(settings.AUTH, "JWT_SECRET", "test-secret-key-for-jwt-signing-minimum-32-bytes!")

    client.headers.pop("Authorization", None)
    auth_type, token_func = request.param
    client.auth_type = auth_type

    if token_func is not None:
        client.headers["Authorization"] = f"Bearer {token_func()}"

    return client


# Sample data fixture
@pytest_asyncio.fixture(scope="function")
async def sample_data(db_session: AsyncSession) -> AsyncGenerator[tuple[Workspace, Peer], Any]:
    """Create test workspace and peer."""
    test_workspace = models.Workspace(name=str(generate_nanoid()))
    db_session.add(test_workspace)

    test_peer = models.Peer(
        name=str(generate_nanoid()), workspace_name=test_workspace.name
    )
    db_session.add(test_peer)

    await db_session.commit()
    yield test_workspace, test_peer


# Deriver settings fixture
@pytest.fixture(autouse=True)
def enable_deriver_for_tests(request: pytest.FixtureRequest):
    """Enable deriver for tests that need queue processing."""
    original_value = settings.DERIVER.ENABLED
    settings.DERIVER.ENABLED = True
    yield
    settings.DERIVER.ENABLED = original_value