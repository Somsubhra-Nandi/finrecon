"""A very small JSON-over-HTTPS client, plus the HTTP-status classification.

Deliberately ``urllib`` from the standard library rather than ``httpx``,
``requests`` or three vendor SDKs. The project adds no runtime dependency
for this: a reviewer clones the repo, installs pydantic, and the provider
layer works. The cost is a few dozen lines of plumbing; the benefit is that
the dependency surface of a financial-reconciliation controller does not
grow three transitive trees for one POST.

The status classification lives here, once, so all three adapters agree on
which HTTP outcomes are infrastructure failures (and therefore may fall
back) and which are configuration failures (and therefore may not). See
:mod:`finrecon.agent.providers.base` for why that line is drawn where it is.

Nothing here logs, stores, or returns an API key. Headers are built inside
:func:`post_json` from the key argument and are never echoed back to the
caller, never attached to an exception, and never written to a trajectory.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from typing import Any

from finrecon.agent.providers.base import (
    ProviderConfigurationError,
    ProviderInfrastructureError,
)

DEFAULT_TIMEOUT_SECONDS = 60.0

_REDACTED = "<redacted>"


def classify_http_status(provider: str, status: int, body: str) -> Exception:
    """Map an HTTP status onto the taxonomy. Never includes request headers."""
    detail = _truncate(body)
    if status == 429:
        return ProviderInfrastructureError(
            provider, ProviderInfrastructureError.RATE_LIMITED, f"HTTP 429: {detail}"
        )
    if status == 402:
        return ProviderInfrastructureError(
            provider, ProviderInfrastructureError.QUOTA_EXHAUSTED, f"HTTP 402: {detail}"
        )
    if status in (401, 403):
        return ProviderConfigurationError(
            provider, ProviderConfigurationError.UNAUTHORIZED, f"HTTP {status}: {detail}"
        )
    if 500 <= status < 600:
        return ProviderInfrastructureError(
            provider,
            ProviderInfrastructureError.SERVER_ERROR,
            f"HTTP {status}: {detail}",
        )
    if 400 <= status < 500:
        # A 4xx that is neither auth nor rate limiting is a malformed
        # request: our bug, not the provider's availability. Falling back
        # would send the same malformed request to a second provider.
        return ProviderConfigurationError(
            provider, ProviderConfigurationError.BAD_REQUEST, f"HTTP {status}: {detail}"
        )
    return ProviderInfrastructureError(
        provider,
        ProviderInfrastructureError.PROTOCOL_ERROR,
        f"unexpected HTTP {status}: {detail}",
    )


def _truncate(body: str, limit: int = 400) -> str:
    body = body.strip().replace("\n", " ")
    return body if len(body) <= limit else body[:limit] + "..."


def post_json(
    *,
    provider: str,
    url: str,
    payload: dict[str, Any],
    api_key: str,
    auth_header: str = "Authorization",
    auth_prefix: str = "Bearer ",
    extra_headers: dict[str, str] | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """POST ``payload`` as JSON and decode the JSON response.

    Raises :class:`ProviderInfrastructureError` for anything that prevented a
    decodable answer, and :class:`ProviderConfigurationError` for credential
    or request-shape problems. A decodable answer with unusable *content* is
    not this layer's concern -- adapters raise that as a protocol error only
    when the provider envelope itself is missing.
    """
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    headers[auth_header] = f"{auth_prefix}{api_key}"
    if extra_headers:
        headers.update(extra_headers)

    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:  # noqa: PERF203 - distinct handling per type
        try:
            error_body = exc.read().decode("utf-8", errors="replace")
        except Exception:  # pragma: no cover - defensive
            error_body = ""
        raise classify_http_status(provider, exc.code, error_body) from None
    except socket.timeout:
        raise ProviderInfrastructureError(
            provider,
            ProviderInfrastructureError.TIMEOUT,
            f"no response within {timeout_seconds}s",
        ) from None
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, socket.timeout):
            raise ProviderInfrastructureError(
                provider,
                ProviderInfrastructureError.TIMEOUT,
                f"no response within {timeout_seconds}s",
            ) from None
        raise ProviderInfrastructureError(
            provider, ProviderInfrastructureError.NETWORK_ERROR, str(reason)
        ) from None
    except TimeoutError:
        raise ProviderInfrastructureError(
            provider,
            ProviderInfrastructureError.TIMEOUT,
            f"no response within {timeout_seconds}s",
        ) from None
    except OSError as exc:
        raise ProviderInfrastructureError(
            provider, ProviderInfrastructureError.NETWORK_ERROR, str(exc)
        ) from None

    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProviderInfrastructureError(
            provider,
            ProviderInfrastructureError.PROTOCOL_ERROR,
            f"response body is not JSON ({exc.msg})",
        ) from None

    if not isinstance(decoded, dict):
        raise ProviderInfrastructureError(
            provider,
            ProviderInfrastructureError.PROTOCOL_ERROR,
            f"response body is {type(decoded).__name__}, not a JSON object",
        )
    return decoded


def redact(value: str | None) -> str:
    """What a secret looks like anywhere it might otherwise be recorded."""
    return _REDACTED if value else ""


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "classify_http_status",
    "post_json",
    "redact",
]
