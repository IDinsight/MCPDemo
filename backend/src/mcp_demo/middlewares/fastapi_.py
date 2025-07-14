"""This module contains middlewares for FastAPI."""

# Standard Library
import time

from typing import Awaitable, Callable

# Third Party Library
from fastapi import Request, Response
from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.types import ASGIApp

# Package Library
from mcp_demo.auth.utils import _verify_caller
from mcp_demo.utils.general import sanitize_token


class AuditMiddleware(BaseHTTPMiddleware):
    """Structured-audit middleware.

    Every inbound HTTP request is timed and enriched with contextual extra fields
    (`ip`, `ua`, `sub`, `latency_ms`, `path`, `status`) before being sent to Loguru.

    The JWT subject (`sub`) is extracted from either the `Authorization` header or the
    `access_token` cookie; any token that fails verification is ignored and the request
    is logged as coming from "anonymous".

    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Intercept request --> response cycle and emit an audit log.

        Example logging output: {
          "time": "2025-07-14T20:10:44.268000+00:00",
          "level": "INFO",
          "message": "http_request",
          "extra": {
            "ip": "127.0.0.1",
            "latency_ms": 37.19,
            "path": "/auth/token",
            "status": 200
            "sub": "user_123",
            "ua": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) ...",
          }
        }

        Parameters
        ----------
        request
            The incoming FastAPI request.
        call_next
            The next ASGI app in the middleware stack, which processes the request and
            returns a response.

        Returns
        -------
        Response
            The unmodified response from `call_next` after timing and logging have
            completed.
        """

        start = time.perf_counter()
        response: Response = await call_next(request)
        duration_ms = (time.perf_counter() - start) * 1000.0

        # Pull basic context (client metadata) from the request.
        assert request.client is not None, f"Request client is None: {request}"
        ip = request.client.host  # Starlette gives remote address
        path = request.url.path
        ua = request.headers.get("user-agent", "-")

        # Try to extract `sub` from header or secure cookie.
        if hasattr(request.state, "audit_sub"):
            sub = request.state.audit_sub
        else:
            raw_token = request.headers.get("authorization", None)
            if raw_token:
                token = sanitize_token(token=raw_token)
            else:
                token = request.cookies.get("access_token", "")

            try:
                payload = await _verify_caller(
                    redis_client=request.app.state.redis,
                    required_scopes=set(),  # No scopes needed for audit
                    token=token,
                )
                sub = payload["sub"]
            except Exception:  # pylint: disable=W0718
                sub = "anonymous"

        # Every field is attached via bind, so it lands in the `extra` part of the JSON
        # log for easy querying.
        logger.bind(
            ip=ip,
            latency_ms=round(duration_ms, 2),
            path=path,
            status=response.status_code,
            sub=sub,
            ua=ua,
        ).info("http_request")

        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Middleware that appends standard security headers to all HTTP responses.

    Headers added:
    - Strict-Transport-Security: Forces use of HTTPS and prevents downgrade attacks.
    - X-Content-Type-Options: Prevents MIME-type sniffing by browsers.
    - Referrer-Policy: Restricts how much referrer info is sent with cross-origin
        requests.

    This middleware helps secure your API by:

    1. Enforcing secure connections via HSTS:
       - `Strict-Transport-Security: max-age={hsts_seconds}; includeSubDomains; preload`
         instructs browsers to use HTTPS for all future requests to your domain,
         including subdomains, for the specified duration.
    2. Preventing MIME-type sniffing:
       - `X-Content-Type-Options: nosniff` ensures browsers respect the declared
         `Content-Type` header instead of inferring types, reducing XSS risk.
    3. Limiting referrer leakage:
       - `Referrer-Policy: same-origin` ensures that full referrer URLs, possibly
         containing sensitive info, are only sent on same-origin requests.
    """

    def __init__(self, app: ASGIApp, hsts_seconds: int = 63_072_000):
        """Initialize the middleware with the FastAPI app and HSTS duration.

        Parameters
        ----------
        app
            The FastAPI application instance.
        hsts_seconds
            The duration in seconds for which the browser should remember to use
            HTTPS. Default is 63,072,000 seconds (2 years).
        """

        super().__init__(app)

        self.hsts_value = f"max-age={hsts_seconds}; includeSubDomains; preload"

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """Process the incoming request and append security headers to the response.

        Parameters
        ----------
        request
            The incoming FastAPI request.
        call_next
            The next ASGI app in the middleware stack.

        Returns
        -------
        Response
            The modified response with security headers.
        """

        response: Response = await call_next(request)

        # Enforce HTTPS.
        response.headers.setdefault("Strict-Transport-Security", self.hsts_value)

        # Disable MIME sniffing.
        response.headers.setdefault("X-Content-Type-Options", "nosniff")

        # Referrer trimming.
        response.headers.setdefault("Referrer-Policy", "same-origin")

        return response
