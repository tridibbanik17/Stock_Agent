"""FastAPI dependencies for IP rate limits and light request abuse checks."""

from __future__ import annotations

import logging
from typing import Annotated, Callable

from fastapi import Depends, HTTPException, Request, status

from app.config import get_settings
from app.services.rate_limit import get_limiter

logger = logging.getLogger("stock_agent.abuse")


def client_ip(request: Request) -> str:
    """
    Resolve caller IP. Only honor X-Forwarded-For when TRUST_PROXY is enabled
    (otherwise clients could spoof the rate-limit key).
    """
    settings = get_settings()
    if settings.get("trust_proxy"):
        forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
        if forwarded:
            return forwarded[:64]
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def enforce_json_content_type(request: Request) -> None:
    """POST bodies for subscribe/snapshot must be JSON."""
    content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if content_type not in {"application/json", "text/json"}:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Content-Type must be application/json",
        )


def enforce_body_size(request: Request) -> None:
    """Reject oversized payloads early (before JSON parse burns CPU)."""
    settings = get_settings()
    max_bytes = int(settings.get("max_request_body_bytes") or 65_536)
    raw_len = request.headers.get("content-length")
    if raw_len is None:
        return
    try:
        length = int(raw_len)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Content-Length",
        ) from None
    if length > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Request body exceeds {max_bytes} bytes",
        )


def rate_limit(bucket: str) -> Callable:
    """
    Build a dependency that rate-limits by (bucket, client IP).

    Limits come from settings:
      subscribe → rate_limit_subscribe_per_min
      snapshot  → rate_limit_snapshot_per_min
      unsubscribe → rate_limit_unsubscribe_per_min
    """

    async def _dependency(request: Request) -> None:
        settings = get_settings()
        limits = {
            "subscribe": int(settings.get("rate_limit_subscribe_per_min") or 10),
            "snapshot": int(settings.get("rate_limit_snapshot_per_min") or 30),
            "unsubscribe": int(settings.get("rate_limit_unsubscribe_per_min") or 30),
        }
        limit = limits.get(bucket, 30)
        window = float(settings.get("rate_limit_window_seconds") or 60)
        ip = client_ip(request)
        key = f"{bucket}:{ip}"
        allowed, remaining, retry_after = get_limiter().check(
            key, limit=limit, window_seconds=window
        )
        request.state.rate_limit_limit = limit
        request.state.rate_limit_remaining = remaining
        request.state.rate_limit_bucket = bucket

        if not allowed:
            logger.warning(
                "Rate limit exceeded bucket=%s ip=%s limit=%s/%ss",
                bucket,
                ip,
                limit,
                int(window),
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded for {bucket}. Try again in {retry_after}s.",
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(limit),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Bucket": bucket,
                },
            )

    return _dependency


async def protect_subscribe(request: Request) -> None:
    enforce_body_size(request)
    enforce_json_content_type(request)
    await rate_limit("subscribe")(request)


async def protect_snapshot(request: Request) -> None:
    enforce_body_size(request)
    enforce_json_content_type(request)
    await rate_limit("snapshot")(request)


async def protect_unsubscribe(request: Request) -> None:
    if request.method in {"POST", "PUT", "PATCH"}:
        enforce_body_size(request)
    await rate_limit("unsubscribe")(request)


ProtectSubscribe = Annotated[None, Depends(protect_subscribe)]
ProtectSnapshot = Annotated[None, Depends(protect_snapshot)]
ProtectUnsubscribe = Annotated[None, Depends(protect_unsubscribe)]


def rate_limit_headers(request: Request) -> dict[str, str]:
    limit = getattr(request.state, "rate_limit_limit", None)
    remaining = getattr(request.state, "rate_limit_remaining", None)
    bucket = getattr(request.state, "rate_limit_bucket", None)
    headers: dict[str, str] = {}
    if limit is not None:
        headers["X-RateLimit-Limit"] = str(limit)
    if remaining is not None:
        headers["X-RateLimit-Remaining"] = str(remaining)
    if bucket:
        headers["X-RateLimit-Bucket"] = str(bucket)
    return headers
