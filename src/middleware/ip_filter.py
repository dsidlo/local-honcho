"""IP restriction middleware for FastAPI."""

from __future__ import annotations

import ipaddress
import logging
from typing import TYPE_CHECKING

from fastapi import HTTPException, Request, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from starlette.types import ASGIApp

logger = logging.getLogger(__name__)


class IPRestrictionMiddleware(BaseHTTPMiddleware):
    """Middleware to restrict access based on client IP address.

    Allows only connections from specified IP networks (CIDR ranges).
    By default, allows localhost (127.0.0.0/8).
    """

    def __init__(
        self,
        app: ASGIApp,
        allowed_networks: list[str] | None = None,
        excluded_paths: list[str] | None = None,
    ) -> None:
        """Initialize the IP restriction middleware.

        Args:
            app: The FastAPI application.
            allowed_networks: List of CIDR notation networks to allow.
                             Defaults to ["127.0.0.0/8"] (localhost).
            excluded_paths: List of path prefixes that bypass IP restriction.
                          Useful for health checks or public endpoints.
        """
        super().__init__(app)
        self.allowed_networks = [
            ipaddress.ip_network(net) for net in (allowed_networks or ["127.0.0.0/8"])
        ]
        self.excluded_paths = excluded_paths or []

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Process the request and check IP restrictions.

        Args:
            request: The incoming request.
            call_next: The next handler in the chain.

        Returns:
            The response from the next handler.

        Raises:
            HTTPException: If the client IP is not in the allowed networks.
        """
        # Skip IP check for excluded paths
        path = request.url.path
        for excluded_path in self.excluded_paths:
            if path.startswith(excluded_path):
                return await call_next(request)

        # Get client IP
        client_host = request.client.host if request.client else None

        # FastAPI TestClient sends requests with the pseudo-host "testclient",
        # which is not a valid IP address. Treat it as loopback for tests.
        if client_host == "testclient":
            client_host = "127.0.0.1"

        if not client_host:
            logger.warning("No client host found in request")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Could not determine client IP",
            )

        # Handle IPv6 localhost
        if client_host in ("::1", "::ffff:127.0.0.1"):
            client_host = "127.0.0.1"

        try:
            client_ip = ipaddress.ip_address(client_host)
        except ValueError:
            logger.warning(f"Invalid client IP address: {client_host}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid client IP address",
            ) from None

        # Check if client IP is in any allowed network
        allowed = any(client_ip in network for network in self.allowed_networks)

        if not allowed:
            logger.warning(
                f"Access denied for IP {client_host} - not in allowed networks: "
                f"{[str(n) for n in self.allowed_networks]}"
            )
            # Returning the response directly (rather than raising HTTPException)
            # so BaseHTTPMiddleware does not convert it into a 500.
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"detail": "Access denied: IP not in allowed range"},
            )

        return await call_next(request)