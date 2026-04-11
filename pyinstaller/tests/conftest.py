"""
Shared fixtures for honcho-pi integration tests.

Provides Docker Compose management, HTTP client, tmux driver,
and honcho context helpers for all test phases.
"""

import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Optional

import pytest
import requests

# =============================================================================
# Constants
# =============================================================================

DOCKER_DIR = Path(__file__).parent / "docker"
COMPOSE_FILE = DOCKER_DIR / "docker-compose.yml"
PROJECT_NAME = "honcho-pi-test"
TEST_RESULTS_DIR = Path(__file__).parent / "test-results"

DEFAULT_API_URL = "http://localhost:8000"
DEFAULT_DB_URL = "postgresql+psycopg://postgres:honcho_test_password@localhost:5432/honcho"
DEFAULT_OLLAMA_URL = "http://localhost:11434"

HEALTH_RETRY_TIMEOUT = 120  # seconds
HEALTH_RETRY_INTERVAL = 3   # seconds
DERIVER_PROCESS_TIMEOUT = 60  # seconds

# =============================================================================
# Docker Compose Manager
# =============================================================================


class DockerComposeManager:
    """Manages the Docker Compose stack for integration tests."""

    def __init__(self, compose_file: Path = COMPOSE_FILE, project_name: str = PROJECT_NAME):
        self.compose_file = compose_file
        self.project_name = project_name

    def _run(self, *args, **kwargs) -> subprocess.CompletedProcess:
        cmd = [
            "docker", "compose",
            "-f", str(self.compose_file),
            "-p", self.project_name,
        ] + list(args)
        return subprocess.run(cmd, capture_output=True, text=True, timeout=300, **kwargs)

    def up(self, detach: bool = True) -> subprocess.CompletedProcess:
        args = ["up"]
        if detach:
            args.append("-d")
        args.append("--build")
        return self._run(*args)

    def down(self, remove_volumes: bool = True) -> subprocess.CompletedProcess:
        args = ["down"]
        if remove_volumes:
            args.extend(["-v", "--rmi", "local"])
        return self._run(*args)

    def logs(self, service: Optional[str] = None, tail: int = 100) -> str:
        args = ["logs", "--tail", str(tail)]
        if service:
            args.append(service)
        result = self._run(*args)
        return result.stdout

    def exec(self, service: str, command: str, user: Optional[str] = None) -> subprocess.CompletedProcess:
        args = ["exec", "-T"]
        if user:
            args.extend(["-u", user])
        args.extend([service, "bash", "-c", command])
        return self._run(*args)

    def copy_into(self, service: str, src: str, dst: str) -> subprocess.CompletedProcess:
        container_id = self._get_container_id(service)
        cmd = ["docker", "cp", src, f"{container_id}:{dst}"]
        return subprocess.run(cmd, capture_output=True, text=True)

    def _get_container_id(self, service: str) -> str:
        result = self._run("ps", "-q", service)
        return result.stdout.strip().split("\n")[0]

    def wait_healthy(self, service: str, timeout: int = HEALTH_RETRY_TIMEOUT) -> bool:
        """Wait for a Docker Compose service to become healthy."""
        start = time.time()
        while time.time() - start < timeout:
            result = self._run("ps", "--format", "json", service)
            # Parse health status from docker compose ps
            if "healthy" in result.stdout.lower():
                return True
            time.sleep(HEALTH_RETRY_INTERVAL)
        return False


# =============================================================================
# HTTP Client
# =============================================================================


class HttpClient:
    """HTTP client for the Honcho API."""

    def __init__(self, base_url: str = DEFAULT_API_URL, timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()

    def get(self, path: str, **kwargs) -> requests.Response:
        url = f"{self.base_url}{path}"
        return self.session.get(url, timeout=self.timeout, **kwargs)

    def post(self, path: str, json: Optional[dict] = None, **kwargs) -> requests.Response:
        url = f"{self.base_url}{path}"
        return self.session.post(url, json=json, timeout=self.timeout, **kwargs)

    def delete(self, path: str, **kwargs) -> requests.Response:
        url = f"{self.base_url}{path}"
        return self.session.delete(url, timeout=self.timeout, **kwargs)

    def wait_for_health(self, timeout: int = HEALTH_RETRY_TIMEOUT) -> bool:
        """Poll /health until the API is responding."""
        start = time.time()
        while time.time() - start < timeout:
            try:
                resp = self.get("/health")
                if resp.status_code == 200:
                    return True
            except requests.ConnectionError:
                pass
            time.sleep(HEALTH_RETRY_INTERVAL)
        return False

    def create_workspace(self, workspace_id: str) -> dict:
        resp = self.post(f"/v1/workspaces", json={"id": workspace_id})
        resp.raise_for_status()
        return resp.json()

    def create_peer(self, workspace_id: str, peer_id: str, metadata: Optional[dict] = None) -> dict:
        resp = self.post(
            f"/v1/workspaces/{workspace_id}/peers",
            json={"id": peer_id, "metadata": metadata or {}},
        )
        resp.raise_for_status()
        return resp.json()

    def create_session(
        self,
        workspace_id: str,
        session_id: str,
        peers: dict,
        metadata: Optional[dict] = None,
    ) -> dict:
        resp = self.post(
            f"/v1/workspaces/{workspace_id}/sessions",
            json={
                "id": session_id,
                "peers": peers,
                "metadata": metadata or {},
            },
        )
        resp.raise_for_status()
        return resp.json()

    def send_messages(
        self,
        workspace_id: str,
        session_id: str,
        messages: list[dict],
    ) -> dict:
        resp = self.post(
            f"/v1/workspaces/{workspace_id}/sessions/{session_id}/messages",
            json={"messages": messages},
        )
        resp.raise_for_status()
        return resp.json()

    def chat(
        self,
        workspace_id: str,
        peer_id: str,
        query: str,
        reasoning_level: str = "low",
        session_id: Optional[str] = None,
    ) -> dict:
        body = {
            "query": query,
            "reasoning_level": reasoning_level,
            "stream": False,
        }
        if session_id:
            body["session_id"] = session_id
        resp = self.post(
            f"/v1/workspaces/{workspace_id}/peers/{peer_id}/chat",
            json=body,
        )
        resp.raise_for_status()
        return resp.json()


# =============================================================================
# Tmux Driver
# =============================================================================


class TmuxDriver:
    """Drive interactive commands inside a tmux session within a Docker container."""

    def __init__(self, compose: DockerComposeManager, service: str = "test-target",
                 session_name: str = "honcho-test"):
        self.compose = compose
        self.service = service
        self.session = session_name

    def start_tmux(self) -> None:
        """Create a tmux session inside the container."""
        self.compose.exec(self.service, f"tmux new-session -d -s {self.session}")

    def send_keys(self, keys: str, enter: bool = True) -> None:
        """Send keystrokes to the tmux session."""
        escaped_keys = keys.replace("'", "'\\''")
        suffix = " Enter" if enter else ""
        self.compose.exec(
            self.service,
            f"tmux send-keys -t {self.session} '{escaped_keys}'{suffix}",
        )

    def send_line(self, line: str) -> None:
        """Send a line of input (equivalent to typing + Enter)."""
        self.send_keys(line, enter=True)

    def capture_pane(self) -> str:
        """Capture current tmux pane output."""
        result = self.compose.exec(
            self.service,
            f"tmux capture-pane -t {self.session} -p",
        )
        return result.stdout

    def wait_for(self, pattern: str, timeout: int = 30, interval: int = 2) -> bool:
        """Wait for a pattern to appear in the tmux pane output."""
        start = time.time()
        while time.time() - start < timeout:
            output = self.capture_pane()
            if re.search(pattern, output, re.IGNORECASE):
                return True
            time.sleep(interval)
        return False

    def run_command(self, command: str, timeout: int = 60) -> str:
        """Run a command and wait for the shell prompt to return."""
        self.send_keys(command, enter=True)
        # Wait for command to complete (look for common prompts)
        self.wait_for(r"[\$#>]\s*$", timeout=timeout)
        return self.capture_pane()

    def kill_session(self) -> None:
        """Kill the tmux session."""
        self.compose.exec(self.service, f"tmux kill-session -t {self.session} 2>/dev/null || true")


# =============================================================================
# Diagnostic Collector
# =============================================================================


class DiagnosticCollector:
    """Collect diagnostics when a test fails."""

    def __init__(self, compose: DockerComposeManager, output_dir: Path):
        self.compose = compose
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def collect_all(self) -> None:
        """Collect all diagnostics."""
        self._collect_docker_logs()
        self._collect_journalctl()
        self._collect_env_file()
        self._collect_systemctl_status()
        self._collect_pi_extension()
        self._collect_api_health()

    def _write(self, filename: str, content: str) -> None:
        path = self.output_dir / filename
        path.write_text(content)

    def _collect_docker_logs(self) -> None:
        for service in ["postgres", "redis", "ollama", "test-target"]:
            logs = self.compose.logs(service, tail=200)
            self._write(f"docker-logs-{service}.txt", logs)

    def _collect_journalctl(self) -> None:
        for svc in ["honcho-api", "honcho-deriver"]:
            result = self.compose.exec(
                "test-target",
                f"journalctl --user -u {svc} --no-pager -n 100",
            )
            self._write(f"journalctl-{svc}.txt", result.stdout + result.stderr)

    def _collect_env_file(self) -> None:
        result = self.compose.exec(
            "test-target",
            "cat ~/.config/honcho-pi/.env 2>/dev/null || echo 'No .env file found'",
        )
        # Redact any real API keys
        content = result.stdout
        content = re.sub(r'(sk-[a-zA-Z0-9]{10,})', 'sk-REDACTED', content)
        content = re.sub(r'(AIzaSy[a-zA-Z0-9]{10,})', 'AIzaSyREDACTED', content)
        content = re.sub(r'(PASSWORD=.*)', 'PASSWORD=REDACTED', content)
        self._write("honcho-pi-env.txt", content)

    def _collect_systemctl_status(self) -> None:
        for svc in ["honcho-api", "honcho-deriver"]:
            result = self.compose.exec(
                "test-target",
                f"systemctl --user status {svc} 2>/dev/null || echo 'Service not found'",
            )
            self._write(f"systemctl-{svc}.txt", result.stdout + result.stderr)

    def _collect_pi_extension(self) -> None:
        # Check extension file
        result = self.compose.exec(
            "test-target",
            "ls -la ~/.pi/agent/extensions/honcho.ts 2>/dev/null || echo 'No extension found'",
        )
        self._write("pi-extension-ls.txt", result.stdout)

        # Check settings
        result = self.compose.exec(
            "test-target",
            "cat ~/.pi/agent/settings.json 2>/dev/null || echo 'No settings found'",
        )
        self._write("pi-settings.json", result.stdout)

    def _collect_api_health(self) -> None:
        result = self.compose.exec(
            "test-target",
            "curl -s http://localhost:8000/health 2>/dev/null || echo 'API not reachable'",
        )
        self._write("api-health.txt", result.stdout)


# =============================================================================
# Honcho Test Context
# =============================================================================


class HonchoTestContext:
    """Aggregates all test helpers into a single context object."""

    def __init__(self):
        self.docker: Optional[DockerComposeManager] = None
        self.http: Optional[HttpClient] = None
        self.tmux: Optional[TmuxDriver] = None
        self.diagnostics: Optional[DiagnosticCollector] = None
        self.workspace_id: str = "test-ws"
        self.peer_id: str = "test-user"
        self.session_id: str = "test-session"


# =============================================================================
# Pytest Fixtures
# =============================================================================


@pytest.fixture(scope="session")
def docker_compose() -> DockerComposeManager:
    """Provide a Docker Compose manager that starts/stops the test stack."""
    manager = DockerComposeManager()

    skip = os.environ.get("SKIP_DOCKER_UP")
    if not skip:
        result = manager.up(detach=True)
        if result.returncode != 0:
            pytest.fail(f"docker compose up failed:\n{result.stderr}")

        # Wait for services to be healthy
        if not manager.wait_healthy("postgres", timeout=120):
            logs = manager.logs("postgres")
            pytest.fail(f"PostgreSQL did not become healthy:\n{logs}")

    yield manager

    if not skip:
        # Collect diagnostics before tearing down
        collector = DiagnosticCollector(manager, TEST_RESULTS_DIR / "session-end")
        collector.collect_all()

        manager.down(remove_volumes=True)


@pytest.fixture(scope="session")
def http_client(docker_compose) -> HttpClient:
    """Provide an HTTP client for the Honcho API."""
    client = HttpClient()
    if not client.wait_for_health(timeout=180):
        pytest.fail("Honcho API did not become healthy within 180 seconds")
    return client


@pytest.fixture(scope="session")
def tmux_driver(docker_compose) -> TmuxDriver:
    """Provide a tmux driver for interactive testing."""
    driver = TmuxDriver(docker_compose)
    driver.start_tmux()
    yield driver
    driver.kill_session()


@pytest.fixture(scope="session")
def honcho_context(docker_compose, http_client) -> HonchoTestContext:
    """Provide a fully wired test context."""
    ctx = HonchoTestContext()
    ctx.docker = docker_compose
    ctx.http = http_client
    ctx.diagnostics = DiagnosticCollector(
        docker_compose,
        TEST_RESULTS_DIR / "failure",
    )
    yield ctx


@pytest.fixture(autouse=True)
def collect_diagnostics_on_failure(honcho_context, request):
    """Collect diagnostics if a test fails."""
    yield
    if request.node.rep_call.failed:
        honcho_context.diagnostics.collect_all()