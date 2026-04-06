import logging  # noqa: I001
import sys
from logging.config import fileConfig
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from alembic import context
from sqlalchemy import engine_from_config, pool, text

# Override environment for migration
import os
os.environ['HONCHO_DB_CONNECTION_URI'] = 'postgresql+psycopg://dsidlo:rexrabbit@127.0.0.1:5433/honcho'
os.environ['LLM__EMBEDDING_PROVIDER'] = 'ollama'

import os

# Set env vars for migration to avoid pydantic errors
os.environ['HONCHO_DB_CONNECTION_URI'] = 'postgresql+psycopg2://dsidlo:rexrabbit@127.0.0.1:5433/honcho_dev'
os.environ['LLM__EMBEDDING_PROVIDER'] = 'ollama'

# Mock settings to avoid full load
class MockSettings:
    class DB:
        CONNECTION_URI = os.getenv('HONCHO_DB_CONNECTION_URI')
    DB = DB()

settings = MockSettings()

# Import your models
from src.db import Base

# Import all models so they register with Base.metadata
# Skip full models load to avoid config
# import src.models  # noqa: F401

from src.models import Base  # Only Base for metadata


# Set up logging more verbosely
logging.basicConfig()
logging.getLogger("sqlalchemy.engine").setLevel(logging.INFO)
logging.getLogger("alembic").setLevel(logging.DEBUG)

# Add project root to Python path
sys.path.append(str(Path(__file__).parents[1]))

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def get_url() -> str:
    url = settings.DB.CONNECTION_URI
    if url is None:
        raise ValueError("DB_CONNECTION_URI not set")
    return url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = get_url()

    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
