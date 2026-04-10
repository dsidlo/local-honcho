"""Configuration management for Honcho Pi."""

import os
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field


class ConfigError(Exception):
    """Configuration error."""
    pass


@dataclass
class Settings:
    """Honcho Pi settings."""
    
    # Paths
    config_dir: Path = field(default_factory=lambda: Path.home() / ".config" / "honcho-pi")
    data_dir: Path = field(default_factory=lambda: Path.home() / ".local" / "share" / "honcho-pi")
    log_dir: Path = field(default_factory=lambda: Path.home() / ".local" / "state" / "honcho-pi" / "logs")
    
    # Honcho Core
    honcho_source_dir: Optional[Path] = None
    honcho_install_dir: Path = field(default_factory=lambda: Path.home() / ".local" / "lib" / "honcho-pi")
    
    # Pi Integration
    pi_agent_dir: Path = field(default_factory=lambda: Path.home() / ".pi" / "agent")
    pi_extensions_dir: Path = field(default_factory=lambda: Path.home() / ".pi" / "agent" / "extensions")
    
    # Environment
    env_file: Optional[Path] = None
    
    # Service Configuration
    api_port: int = 8333
    api_host: str = "127.0.0.1"
    
    # Database
    database_url: Optional[str] = None
    use_docker_db: bool = False
    
    # LLM Configuration
    llm_provider: str = "anthropic"  # anthropic, openai, groq, gemini
    llm_api_key: Optional[str] = None
    
    # Embedding
    embedding_provider: str = "openai"  # openai, ollama
    embedding_model: str = "text-embedding-3-small"
    ollama_url: Optional[str] = None
    
    # Reranker
    reranker_enabled: bool = False
    reranker_model: str = "qllama/bge-reranker-large:f16"
    
    # Dreamer
    dreaming_enabled: bool = True
    
    def __post_init__(self):
        """Post-initialization setup."""
        if self.env_file is None:
            self.env_file = self.config_dir / ".env"
        if self.honcho_source_dir is None:
            self.honcho_source_dir = self.honcho_install_dir / "honcho"
    
    @classmethod
    def from_env(cls) -> "Settings":
        """Load settings from environment and env file."""
        # PyApp sets these at runtime
        settings = cls()
        
        # Override from env vars
        if env_file := os.getenv("HONCHO_PI_ENV_FILE"):
            settings.env_file = Path(env_file)
        
        # Load from env file if exists
        if settings.env_file and settings.env_file.exists():
            settings.load_env_file(settings.env_file)
        
        # Override with direct env vars
        settings.config_dir = _env_path("HONCHO_PI_CONFIG_DIR", settings.config_dir)
        settings.data_dir = _env_path("HONCHO_PI_DATA_DIR", settings.data_dir)
        settings.honcho_install_dir = _env_path("HONCHO_PI_INSTALL_DIR", settings.honcho_install_dir)
        
        settings.api_port = int(os.getenv("HONCHO_PI_PORT", settings.api_port))
        settings.api_host = os.getenv("HONCHO_PI_HOST", settings.api_host)
        settings.database_url = os.getenv("HONCHO_PI_DATABASE_URL") or settings.database_url
        settings.use_docker_db = _env_bool("HONCHO_PI_USE_DOCKER_DB", settings.use_docker_db)
        
        settings.llm_provider = os.getenv("LLM_PROVIDER", settings.llm_provider)
        settings.llm_api_key = os.getenv(f"LLM_{settings.llm_provider.upper()}_API_KEY") or settings.llm_api_key
        
        settings.embedding_provider = os.getenv("EMBEDDING_PROVIDER", settings.embedding_provider)
        settings.embedding_model = os.getenv("EMBEDDING_MODEL", settings.embedding_model)
        settings.ollama_url = os.getenv("LLM_OLLAMA_BASE_URL", settings.ollama_url)
        
        settings.reranker_enabled = _env_bool("RERANKER_ENABLED", settings.reranker_enabled)
        settings.reranker_model = os.getenv("RERANKER_MODEL", settings.reranker_model)
        
        settings.dreaming_enabled = _env_bool("DREAMING_ENABLED", settings.dreaming_enabled)
        
        return settings
    
    def load_env_file(self, path: Path):
        """Load settings from .env file."""
        if not path.exists():
            return
        
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                os.environ.setdefault(key, value)
    
    def save_env_file(self, path: Optional[Path] = None):
        """Save current settings to .env file."""
        path = path or self.env_file
        if not path:
            return
        
        path.parent.mkdir(parents=True, exist_ok=True)
        
        env_vars = [
            "# Honcho Pi Configuration",
            f"HONCHO_PI_VERSION=1.0.0",
            f"HONCHO_BASE_URL=http://{self.api_host}:{self.api_port}",
            f"HONCHO_WORKSPACE={os.getenv('HONCHO_WORKSPACE', 'default')}",
            f"HONCHO_USER={os.getenv('USER', 'default')}",
            f"HONCHO_AGENT_ID={os.getenv('HONCHO_AGENT_ID', 'agent-honcho-pi')}",
            "",
            "# Database",
            f"DATABASE_URL={self.database_url or 'postgresql+psycopg://postgres:postgres@localhost:5432/honcho'}",
            "",
            "# API Settings",
            f"API_PORT={self.api_port}",
            f"API_HOST={self.api_host}",
            "",
            "# LLM Configuration",
            f"LLM_PROVIDER={self.llm_provider}",
        ]
        
        # Add API key comment (don't write actual key)
        if self.llm_api_key:
            env_vars.append(f"# LLM_{self.llm_provider.upper()}_API_KEY=***")
        
        env_vars.extend([
            "",
            "# Embedding",
            f"EMBEDDING_PROVIDER={self.embedding_provider}",
            f"EMBEDDING_MODEL={self.embedding_model}",
        ])
        
        if self.ollama_url:
            env_vars.append(f"LLM_OLLAMA_BASE_URL={self.ollama_url}")
        
        env_vars.extend([
            "",
            "# Reranker",
            f"RERANKER_ENABLED={str(self.reranker_enabled).lower()}",
            f"RERANKER_MODEL={self.reranker_model}",
            "",
            "# Dreamer",
            f"DREAMING_ENABLED={str(self.dreaming_enabled).lower()}",
        ])
        
        path.write_text("\n".join(env_vars) + "\n")
        path.chmod(0o600)  # Restrict permissions
    
    def ensure_directories(self):
        """Ensure all required directories exist."""
        dirs_to_create = [
            self.config_dir,
            self.data_dir,
            self.log_dir,
            self.honcho_install_dir,
            self.pi_extensions_dir,
        ]
        for d in dirs_to_create:
            d.mkdir(parents=True, exist_ok=True)
    
    def validate(self) -> list[str]:
        """Validate settings and return list of errors."""
        errors = []
        
        if not self.database_url and not self.use_docker_db:
            errors.append("Database URL or Docker DB must be configured")
        
        if self.llm_provider in ("anthropic", "openai", "groq") and not self.llm_api_key:
            errors.append(f"{self.llm_provider} API key is required")
        
        if not self.honcho_install_dir.exists():
            errors.append(f"Honcho install directory does not exist: {self.honcho_install_dir}")
        
        # Check port availability
        import socket
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind((self.api_host, self.api_port))
            sock.close()
        except OSError:
            errors.append(f"Port {self.api_port} is already in use")
        
        return errors


def _env_path(key: str, default: Path) -> Path:
    """Get path from environment or default."""
    if value := os.getenv(key):
        return Path(value)
    return default


def _env_bool(key: str, default: bool) -> bool:
    """Get boolean from environment or default."""
    if value := os.getenv(key):
        return value.lower() in ("true", "1", "yes", "on")
    return default


# Global settings instance
settings = Settings.from_env()