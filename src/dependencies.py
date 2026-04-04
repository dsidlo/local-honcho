import uuid
from contextlib import asynccontextmanager
from contextvars import ContextVar

from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.db import SessionLocal, request_context

# Context variable to store test database session
# When set, tracked_db will use this instead of creating a new SessionLocal
_test_db_session: ContextVar[AsyncSession | None] = ContextVar("_test_db_session", default=None)


async def get_db():
    """FastAPI Dependency Generator for Database"""

    context = request_context.get() or "unknown"

    db: AsyncSession = SessionLocal()
    try:
        if settings.DB.TRACING:
            await db.execute(
                text("SELECT set_config('application_name', :name, false)"),
                {"name": context},
            )
        yield db
    except Exception:
        await db.rollback()
        raise
    finally:
        if db.in_transaction():
            await db.rollback()
        await db.close()


@asynccontextmanager
async def tracked_db(operation_name: str | None = None):
    """Context manager for tracked database sessions.
    
    In tests, this will use the test database session if one is set via
    set_test_db_session(). Otherwise, it creates a new SessionLocal connection.
    """
    # Check if we're in a test context with a test session
    test_session = _test_db_session.get()
    if test_session is not None:
        # Use the test session directly - no need to close it
        yield test_session
        return
    
    # Normal production flow: create a new session
    context = request_context.get()
    token = None

    if not context and operation_name:
        context = f"task:{operation_name}:{str(uuid.uuid4())[:8]}"
        token = request_context.set(context)

    # Create session with tracking info
    db = SessionLocal()

    try:
        if settings.DB.TRACING:
            await db.execute(
                text("SELECT set_config('application_name', :name, false)"),
                {"name": context or f"task:{operation_name}"},
            )

        yield db
    except Exception:
        await db.rollback()
        raise
    finally:
        if db.in_transaction():
            await db.rollback()
        await db.close()
        if token:  # Only reset if we set it
            request_context.reset(token)


def set_test_db_session(session: AsyncSession | None) -> object:
    """Set the test database session for tracked_db to use.
    
    This should only be used in test fixtures to inject the test session.
    Returns a token that must be used to reset the context.
    """
    return _test_db_session.set(session)


db: AsyncSession = Depends(get_db)
