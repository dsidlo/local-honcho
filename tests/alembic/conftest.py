"""Pytest configuration for Alembic-focused tests."""

from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import URL
from sqlalchemy_utils import (
    create_database,  # pyright: ignore
    database_exists,  # pyright: ignore
    drop_database,  # pyright: ignore
)

from src.config import settings
from tests.conftest import CONNECTION_URI


def _render_for_configparser(url: URL) -> str:
    """Render a URL that is safe for configparser interpolation.

    Unix-socket hosts (paths like /var/run/postgresql) get percent-encoded by
    render_as_string(), and configparser then rejects the '%' escapes. For
    socket connections we omit the network host entirely and pass the socket
    directory via libpq's 'host' query parameter, which contains no '%'.
    """
    pairs: list[str] = []
    for k, v in url.query.items():
        pairs.append(f"{k}={v}")

    host = str(url.host) if url.host else None
    if host and host.startswith("/"):
        # Socket connection: express via query param, not as netloc host
        if not any(p.startswith("host=") for p in pairs):
            pairs.insert(0, f"host={host}")
    else:
        pairs = [f"host={host}", *(p for p in pairs if not p.startswith("host="))] if host else pairs
        if url.port:
            pairs.append(f"port={url.port}")

    return f"postgresql+psycopg:///{url.database or ''}" + ("?" + "&".join(pairs) if pairs else "")

ALEMBIC_CONFIG_PATH = Path(__file__).resolve().parents[2] / "alembic.ini"
ALEMBIC_TEST_DB_URL: URL = CONNECTION_URI.set(database="alembic_migration_tests")
ALEMBIC_TEST_DB_STR: str = _render_for_configparser(ALEMBIC_TEST_DB_URL)


@pytest.fixture(scope="session", autouse=True)
def configure_alembic_settings(alembic_database: str) -> Generator[None, None, None]:
    """Point application settings at the Alembic test database."""

    previous_uri = settings.DB.CONNECTION_URI
    os.environ["DB_CONNECTION_URI"] = ALEMBIC_TEST_DB_STR
    settings.DB.CONNECTION_URI = ALEMBIC_TEST_DB_STR

    try:
        yield
    finally:
        settings.DB.CONNECTION_URI = previous_uri
        if previous_uri:
            os.environ["DB_CONNECTION_URI"] = previous_uri
        else:
            os.environ.pop("DB_CONNECTION_URI", None)


@pytest.fixture
def alembic_cfg(alembic_database: str) -> Config:
    """Provide an Alembic Config bound to the alembic test database."""
    cfg = Config(str(ALEMBIC_CONFIG_PATH))
    cfg.set_main_option("script_location", str(ALEMBIC_CONFIG_PATH.parent / "migrations"))
    cfg.set_main_option("sqlalchemy.url", ALEMBIC_TEST_DB_STR)
    return cfg


@pytest.fixture(scope="session")
def alembic_database() -> Generator[str, None, None]:
    """Provision a dedicated DB for Alembic verification tests."""

    assert ALEMBIC_TEST_DB_URL.database == "alembic_migration_tests", (
        "Can't set up Alembic test database fixture. "
        + "ALEMBIC_TEST_DB_URL.database is {ALEMBIC_TEST_DB_URL.database}, "
        + "expected 'alembic_migration_tests'. "
    )

    if database_exists(ALEMBIC_TEST_DB_STR):
        drop_database(ALEMBIC_TEST_DB_STR)  # start fresh
    create_database(ALEMBIC_TEST_DB_STR)

    engine = create_engine(ALEMBIC_TEST_DB_STR)
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        yield ALEMBIC_TEST_DB_STR
    finally:
        engine.dispose()
        if database_exists(ALEMBIC_TEST_DB_STR):
            drop_database(ALEMBIC_TEST_DB_STR)


@pytest.fixture
def alembic_engine(alembic_database: str) -> Generator[Engine, None, None]:
    """Yield an engine bound to the Alembic test database."""

    engine = create_engine(alembic_database, pool_pre_ping=True)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(autouse=True)
def reset_schema_between_tests(
    alembic_engine: Engine,
) -> Generator[None, None, None]:
    """Drop and recreate the schema for a clean slate each test (fast reset)."""

    schema = settings.DB.SCHEMA

    def _reset_schema() -> None:
        with alembic_engine.begin() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            # pgvector's type lives in the public schema; installing it there means
            # dropping public CASCADE removes the type. Recreate it after reset.
            conn.execute(text('CREATE EXTENSION IF NOT EXISTS vector'))
            if schema != "public":
                conn.execute(text('ALTER EXTENSION vector SET SCHEMA public'))

    _reset_schema()
    yield
    _reset_schema()
