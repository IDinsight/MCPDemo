"""This module contains middlewares for FastAPI."""

# Standard Library
import time

from typing import Awaitable, Callable

# Third Party Library
import secure

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
            token = (
                sanitize_token(token=raw_token)
                if raw_token
                else request.cookies.get("access_token", "")
            )

            try:
                payload = await _verify_caller(  # No scopes needed for audit
                    redis_client=request.app.state.redis, token=token
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


class SecureHeadersMiddleware(BaseHTTPMiddleware):
    """Attach a modern security-header bundle to every HTTP response.

    Headers added
    -------------

    1. Strict-Transport-Security
    2. X-Content-Type-Options
    3. Referrer-Policy
    4. Content-Security-Policy
    5. X-Frame-Options
    6. Permissions-Policy
    7. Server (optional banner)

    Notes
    -----

    1. Policies are built once in ``__init__`` and reused.
    2. Works alongside CORSMiddleware and behind Caddy.
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

        # Set up Content Security Policy (CSP) to restrict resources to the same origin.
        csp = (
            secure.ContentSecurityPolicy()
            .default_src("'self'")
            .script_src("'self'")
            .style_src("'self'")
            .img_src("'self'")
        )

        # For documentation endpoints, use a more relaxed CSP to allow inline scripts
        # and styles, which are often used in Swagger UI and ReDoc.
        csp_docs = (
            secure.ContentSecurityPolicy()
            .default_src("'self'")
            # Swagger UI needs its own JS/CSS from the CDN plus inline/eval
            .script_src("'self' 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net")
            .style_src("'self' 'unsafe-inline' https://cdn.jsdelivr.net")
            # favicon hosted by Tiangolo’s site, logos encoded as data: URIs
            .img_src("'self' data: https://fastapi.tiangolo.com")
        )

        # Configure HSTS (HTTP Strict Transport Security) with a 2-year max age,
        hsts = (
            secure.StrictTransportSecurity()
            .max_age(hsts_seconds)  # 2 years
            .include_subdomains()
            .preload()
        )

        self.secure_headers = secure.Secure(
            csp=csp,
            hsts=hsts,
            permissions=secure.PermissionsPolicy(),
            referrer=secure.ReferrerPolicy().same_origin(),
            server=secure.Server().set("Secure"),
            xcto=secure.XContentTypeOptions().nosniff(),
            xfo=secure.XFrameOptions().deny(),
        )
        self.secure_headers_docs = secure.Secure(
            csp=csp_docs,
            hsts=hsts,
            referrer=secure.ReferrerPolicy().same_origin(),
            xcto=secure.XContentTypeOptions().nosniff(),
            xfo=secure.XFrameOptions().deny(),
        )

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """Intercept request --> response cycle and patch the response with security
        headers.

        This middleware does not modify the request or response body, it only appends
        security headers to the response.

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
            The response from the FastAPI application, with security headers added.
        """

        # Let FastAPI handle the request first.
        response = await call_next(request)

        # Patch the outgoing response in-place.
        if request.url.path.startswith(("/docs", "/redoc", "/openapi.json", "/static")):
            await self.secure_headers_docs.set_headers_async(
                response  # type: ignore[arg-type]
            )
        else:
            await self.secure_headers.set_headers_async(
                response  # type: ignore[arg-type]
            )

        return response
