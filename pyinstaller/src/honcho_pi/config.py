"""Configuration management for Honcho Pi.

Handles loading configuration from environment variables,
.env files, and provides utilities for accessing settings.
"""

import os
from pathlib import Path
from typing import Optional


# Default configuration paths
DEFAULT_CONFIG_DIR = Path.home() / ".config/honcho-pi"
DEFAULT_ENV_FILE = DEFAULT_CONFIG_DIR / ".env"
ALTERNATE_ENV_FILE = Path.home() / ".honcho-pi/.env"

# Default values
DEFAULTS = {
    "HONCHO_BASE_URL": "http://localhost:8000",
    "HONCHO_WORKSPACE": "default",
    "HONCHO_USER": os.environ.get("USER", "pi-user"),
    "API_PORT": "8000",
    "DREAMING_ENABLED": "true",
    "TELEMETRY_ENABLED": "false",
}


def find_env_file() -> Optional[Path]:
    """Find the .env configuration file."""
    # Check primary location
    if DEFAULT_ENV_FILE.exists():
        return DEFAULT_ENV_FILE
    
    # Check alternate location
    if ALTERNATE_ENV_FILE.exists():
        return ALTERNATE_ENV_FILE
    
    # Check for file at base of installation
    install_dir = os.environ.get("INSTALL_DIR_HONCHO_PI", "")
    if install_dir:
        install_env = Path(install_dir) / ".env"
        if install_env.exists():
            return install_env
        # Check parent of install dir (common pyapp pattern)
        parent_env = Path(install_dir).parent / ".env"
        if parent_env.exists():
            return parent_env
    
    return None


def load_env_file():
    """Load environment variables from .env file."""
    env_file = find_env_file()
    
    if not env_file:
        return
    
    # Parse .env file
    try:
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                # Skip comments and empty lines
                if not line or line.startswith('#'):
                    continue
                
                # Parse key=value
                if '=' in line:
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip().strip('"\'')
                    
                    # Only set if not already in environment
                    if key not in os.environ:
                        os.environ[key] = value
    except Exception:
        pass


def get(key: str, default: Optional[str] = None) -> str:
    """Get a configuration value.
    
    Priority:
    1. Environment variable
    2. .env file
    3. Default value
    """
    # First, try environment
    if key in os.environ:
        return os.environ[key]
    
    # Load .env if not loaded yet
    if not getattr(load_env_file, '_loaded', False):
        load_env_file()
        load_env_file._loaded = True
    
    # Try environment again (may have been loaded from .env)
    if key in os.environ:
        return os.environ[key]
    
    # Return default
    if default is not None:
        return default
    
    if key in DEFAULTS:
        return DEFAULTS[key]
    
    raise KeyError(f"Configuration key '{key}' not found")


def get_db_url() -> str:
    """Get database connection URL."""
    # Check standard env var
    url = os.environ.get("DATABASE_URL") or os.environ.get("DB_CONNECTION_URI")
    
    if url:
        return url
    
    # Try loading from .env
    load_env_file()
    url = os.environ.get("DATABASE_URL") or os.environ.get("DB_CONNECTION_URI")
    
    if url:
        return url
    
    # Construct from components
    db_user = get("DB_USER", "postgres")
    db_pass = get("DB_PASSWORD", "password")
    db_host = get("DB_HOST", "localhost")
    db_port = get("DB_PORT", "5432")
    db_name = get("DB_NAME", "honcho")
    
    return f"postgresql+psycopg://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}"


def get_api_url() -> str:
    """Get Honcho API base URL."""
    return get("HONCHO_BASE_URL")


def get_config_dir() -> Path:
    """Get the configuration directory."""
    # Check environment
    config_dir = os.environ.get("HONCHO_PI_CONFIG_DIR")
    if config_dir:
        return Path(config_dir)
    
    # Default location
    return DEFAULT_CONFIG_DIR


def ensure_config_dir():
    """Ensure configuration directory exists."""
    config_dir = get_config_dir()
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


def get_install_dir() -> Optional[Path]:
    """Get PyApp installation directory."""
    install_dir = os.environ.get("INSTALL_DIR_HONCHO_PI", "")
    if install_dir:
        return Path(install_dir)
    
    # Try to find based on pyapp pattern
    import glob
    import re
    
    pattern = str(Path.home() / ".local/share/honcho-pi/*/")
    matches = glob.glob(pattern)
    
    if matches:
        # Sort by version number (best effort)
        def version_key(p):
            match = re.search(r'/(\d+\.\d+\.\d+)/', p)
            return [int(x) for x in match.group(1).split('.')] if match else []
        
        matches.sort(key=version_key, reverse=True)
        return Path(matches[0])
    
    return None


def is_pyapp() -> bool:
    """Check if running as PyApp binary."""
    return os.environ.get("PYAPP") == "1"


def get_pyapp_info() -> dict:
    """Get PyApp environment information."""
    return {
        "is_pyapp": is_pyapp(),
        "command_name": os.environ.get("PYAPP_COMMAND_NAME", None),
        "version": os.environ.get("PYAPP_PROJECT_VERSION", None),
        "install_dir": str(get_install_dir()) if get_install_dir() else None,
    }


class Settings:
    """Settings object with attributes for configuration.
    
    Provides compatibility with bootstrap.py interface.
    """
    
    def __init__(self):
        self.config_dir = get_config_dir()
        
        # Database settings
        self.database_use_docker = False
        self.database_url = get("DATABASE_URL", DEFAULTS["HONCHO_BASE_URL"].replace("http://", "postgresql+psycopg://").replace(":8000", ":5432/honcho"))
        self.database_host = get("DATABASE_HOST", "localhost")
        self.database_port = int(get("DATABASE_PORT", "5432"))
        self.database_name = get("DATABASE_NAME", "honcho")
        self.database_user = get("DATABASE_USER", "postgres")
        self.database_password = get("DATABASE_PASSWORD", "")
        
        # LLM settings
        self.llm_provider = get("LLM_PROVIDER", "anthropic")
        self.llm_model = get("LLM_MODEL", "claude-3-sonnet-20240229")
        self.llm_api_key = get("LLM_API_KEY", "")
        
        # Embedding settings
        self.embedding_use_ollama = get("EMBEDDING_PROVIDER", "ollama") == "ollama"
        self.embedding_provider = get("EMBEDDING_PROVIDER", "ollama")
        self.embedding_ollama_model = get("EMBEDDING_OLLAMA_MODEL", "nomic-embed-text")
        self.embedding_model = get("EMBEDDING_MODEL", "nomic-embed-text")
        self.embedding_api_key = get("EMBEDDING_API_KEY", "")
        
        # Reranker settings
        self.reranker_enabled = get("RERANKER_ENABLED", "false").lower() == "true"
        self.reranker_use_ollama = get("RERANKER_USE_OLLAMA", "true").lower() == "true"
        self.reranker_model = get("RERANKER_MODEL", "qllama/bge-reranker-large:f16")
        
        # API settings
        self.api_host = get("API_HOST", "0.0.0.0")
        self.api_port = int(get("API_PORT", "8000"))
        self.api_url = get_api_url()
        
        # Pi extension settings
        self.pi_extension_enabled = get("PI_EXTENSION_ENABLED", "true").lower() == "true"
        self.pi_extension_hooks = get("PI_EXTENSION_HOOKS", "true").lower() == "true"
        self.pi_extension_git_branch = get("PI_EXTENSION_GIT_BRANCH", "true").lower() == "true"
        
        # Features
        self.dreaming_enabled = get("DREAMING_ENABLED", "true").lower() == "true"
        self.telemetry_enabled = get("TELEMETRY_ENABLED", "false").lower() == "true"
    
    def get_database_url(self) -> str:
        """Get full database connection URL."""
        if self.database_url:
            return self.database_url
        return f"postgresql+psycopg://{self.database_user}:{self.database_password}@{self.database_host}:{self.database_port}/{self.database_name}"
    
    def ensure_directories(self):
        """Ensure all required directories exist."""
        ensure_config_dir()


class ConfigManager:
    """Configuration manager for saving settings."""
    
    def __init__(self):
        self.settings = Settings()
    
    def save_env_file(self, overrides: dict):
        """Save configuration to .env file."""
        env_path = self.settings.config_dir / ".env"
        
        lines = [
            "# Honcho Pi Configuration",
            f"# Generated on: {__import__('datetime').datetime.now().isoformat()}",
            "",
            "# Database",
            f"DATABASE_URL={self.settings.get_database_url()}",
            f"DATABASE_HOST={self.settings.database_host}",
            f"DATABASE_PORT={self.settings.database_port}",
            f"DATABASE_NAME={self.settings.database_name}",
            f"DATABASE_USER={self.settings.database_user}",
            f"DATABASE_PASSWORD={self.settings.database_password}",
            "",
            "# LLM",
            f"LLM_PROVIDER={self.settings.llm_provider}",
            f"LLM_MODEL={self.settings.llm_model}",
            "",
            "# Embeddings",
            f"EMBEDDING_PROVIDER={self.settings.embedding_provider}",
            f"EMBEDDING_MODEL={self.settings.embedding_model}",
            "",
            "# API",
            f"API_HOST={self.settings.api_host}",
            f"API_PORT={self.settings.api_port}",
            "",
            "# Features",
            f"DREAMING_ENABLED={str(self.settings.dreaming_enabled).lower()}",
            f"PI_EXTENSION_ENABLED={str(self.settings.pi_extension_enabled).lower()}",
        ]
        
        # Write API key only if set (don't write empty values to file)
        if self.settings.llm_api_key:
            lines.append(f"LLM_API_KEY={self.settings.llm_api_key}")
        
        # Apply overrides if provided
        for key, value in overrides.items():
            lines.append(f"{key}={value}")
        
        ensure_config_dir()
        with open(env_path, "w") as f:
            f.write("\n".join(lines))


def get_settings() -> Settings:
    """Get current settings."""
    return Settings()
