import logging  # noqa: I001
import os
import sys
from logging.config import fileConfig
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from alembic import context
from sqlalchemy import engine_from_config, pool, text

# Ensure a connection URI is always present for the migration process.
# Alembic runs with only the base environment; src.config requires a URI.
# If none is configured, fall back to the local Unix-socket postgres.
os.environ.setdefault(
    'HONCHO_DB_CONNECTION_URI',
    'postgresql+psycopg://dsidlo@localhost:5433/honcho_dev',
)
os.environ.setdefault('LLM__EMBEDDING_PROVIDER', 'ollama')

from src.config import settings

from src.models import Base  # noqa: F401  (registers all models on Base.metadata)


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
    # Prefer the URL the caller pinned into the Alembic config (tests point
    # this at a dedicated migration-test database). Fall back to app settings.
    url = config.get_main_option("sqlalchemy.url")
    if not url or url == "driver://user:pass@localhost/dbname":
        url = settings.DB.CONNECTION_URI
    if not url:
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
    # Tests may inject a connection into the config; reuse it if present.
    connection = config.attributes.get("connection")

    if connection is None:
        connectable = engine_from_config(
            config.get_section(config.config_ini_section),
            prefix="sqlalchemy.",
            poolclass=pool.NullPool,
            url=get_url(),
        )
        with connectable.connect() as connection:
            context.configure(
                connection=connection, target_metadata=target_metadata
            )

            with context.begin_transaction():
                context.run_migrations()
    else:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
