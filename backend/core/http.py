"""Shared retry/backoff helper for all outbound HTTP calls (OpenAI, Cal.com).

Kept dependency-light: httpx + stdlib only, no tenacity/backoff package.
"""
import asyncio
import random

import httpx

DEFAULT_RETRY_STATUSES = (429, 500, 502, 503, 504)


async def request_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    max_attempts: int = 3,
    retry_statuses=DEFAULT_RETRY_STATUSES,
    base_delay: float = 0.5,
    **kwargs,
) -> httpx.Response:
    """Retry on retryable status codes and transient connect/read errors.

    Never retries once a request has definitely reached the server and the response is simply
    an error we don't consider retryable (e.g. 4xx other than 429) -- callers that need
    "don't retry if the write may have already happened" semantics (e.g. booking creation)
    should set max_attempts=1 and handle ambiguity themselves.
    """
    last_response: httpx.Response | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            response = await client.request(method, url, **kwargs)
        except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError):
            if attempt == max_attempts:
                raise
            await asyncio.sleep(base_delay * (2 ** (attempt - 1)) + random.uniform(0, 0.25))
            continue

        if response.status_code not in retry_statuses or attempt == max_attempts:
            return response

        last_response = response
        retry_after = response.headers.get("Retry-After")
        delay = float(retry_after) if retry_after else base_delay * (2 ** (attempt - 1))
        await asyncio.sleep(delay + random.uniform(0, 0.25))

    assert last_response is not None  # pragma: no cover - loop always returns/raises above
    return last_response
