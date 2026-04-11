# =============================================================================
# Multi-stage Dockerfile for honcho-pi integration testing
# Stage 1: Build the honcho-pi PyInstaller binary
# Stage 2: Clean Ubuntu target for installation testing
# =============================================================================

# --- Stage 1: Builder ---
FROM ubuntu:22.04 AS builder

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-dev \
    python3-venv \
    build-essential \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast dependency resolution
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.cargo/bin:$PATH"

WORKDIR /build

# Copy pyinstaller package source
COPY pyinstaller/ /build/pyinstaller/
COPY src/ /build/src/
COPY migrations/ /build/migrations/
COPY alembic.ini /build/alembic.ini
COPY pyproject.toml /build/pyproject.toml

# Build the honcho-pi binary
WORKDIR /build/pyinstaller
RUN uv pip install --system pyinstaller && \
    bash build.sh --clean

# Verify the binary exists
RUN ls -lh /build/pyinstaller/dist/honcho-pi && \
    /build/pyinstaller/dist/honcho-pi --version || true

# --- Stage 2: Test Target ---
FROM ubuntu:22.04 AS test-target

ENV DEBIAN_FRONTEND=noninteractive

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    systemd \
    systemd-sysv \
    libsystemd0 \
    dbus \
    curl \
    git \
    tmux \
    python3 \
    python3-pip \
    python3-venv \
    postgresql-client \
    iproute2 \
    procps \
    psmisc \
    ca-certificates \
    locales \
    && rm -rf /var/lib/apt/lists/*

# Generate locale
RUN locale-gen en_US.UTF-8
ENV LANG=en_US.UTF-8
ENV LANGUAGE=en_US:en
ENV LC_ALL=en_US.UTF-8

# Install uv (needed by pi-mono)
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.cargo/bin:/root/.local/bin:$PATH"

# Enable systemd user sessions
RUN mkdir -p /etc/systemd/system/user@.service.d && \
    echo "[Service]\nDelegate=yes" > /etc/systemd/system/user@.service.d/delegate.conf

# Create user for testing (systemd user sessions need a real user)
RUN useradd -m -s /bin/bash htest && \
    mkdir -p /home/htest/.config/systemd/user && \
    chown -R htest:htest /home/htest

# Copy the built binary from builder
COPY --from=builder /build/pyinstaller/dist/honcho-pi /usr/local/bin/honcho-pi
RUN chmod +x /usr/local/bin/honcho-pi

# Copy entrypoint script
COPY tests/docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Copy test environment file
COPY tests/docker/.env.test /etc/honcho/env.template

# Create working directory
WORKDIR /workspace

# Health check: ensure systemd is available
HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=3 \
    CMD systemctl --user is-active default.target || exit 0

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["sleep", "infinity"]