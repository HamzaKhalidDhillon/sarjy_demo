"""Thin client for the Cal.com v2 API. No LLM logic here -- pure HTTP + retry.

Auth: Authorization: Bearer <api key>, no OAuth (https://cal.com/docs/api-reference/v2).
Every endpoint also requires a `cal-api-version` header, versioned per-resource by Cal.com;
the exact date strings are read from settings so they can be corrected via .env without a
code change if Cal.com updates them.
"""
import httpx

from backend.core.config import settings
from backend.core.errors import ToolError
from backend.core.http import request_with_retry


class CalComClient:
    def __init__(self):
        self.base_url = settings.calcom_base_url
        self.api_key = settings.calcom_api_key

    def _headers(self, api_version: str | None = None) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if api_version:
            headers["cal-api-version"] = api_version
        return headers

    def _ensure_configured(self) -> None:
        # Fail fast with a clear message instead of sending a malformed "Bearer " header
        # (httpx rejects an empty-token Authorization value outright) or a request that will
        # just 401 -- both would otherwise look like a network bug rather than a config one.
        if not self.api_key:
            raise ToolError("Cal.com is not configured: set CALCOM_API_KEY in .env")

    async def get_event_types(self) -> list[dict]:
        # Verified live: this endpoint 404s if a cal-api-version header is sent at all (unlike
        # slots/bookings, which require one) -- it just returns the authenticated key's own
        # event types, ignoring `username` as a filter.
        self._ensure_configured()
        url = f"{self.base_url}/event-types"
        params = {"username": settings.calcom_username} if settings.calcom_username else {}
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await request_with_retry(
                client, "GET", url, params=params, headers=self._headers(),
            )
        if response.status_code >= 400:
            raise ToolError(f"Cal.com get_event_types failed: {response.status_code} {response.text}")
        # Verified live shape: data.eventTypeGroups[*].eventTypes[*] -- NOT a flat data list.
        groups = response.json().get("data", {}).get("eventTypeGroups", [])
        return [et for group in groups for et in group.get("eventTypes", [])]

    async def get_slots(self, event_type_id: int, start: str, end: str, timezone: str = "UTC") -> dict:
        """start/end are ISO8601 date(time) strings. Returns {"<date>": [{"time" or "start": iso}, ...]}."""
        self._ensure_configured()
        url = f"{self.base_url}/slots"
        params = {"eventTypeId": event_type_id, "start": start, "end": end, "timeZone": timezone}
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await request_with_retry(
                client, "GET", url, params=params,
                headers=self._headers(settings.calcom_api_version_slots),
            )
        if response.status_code >= 400:
            raise ToolError(f"Cal.com get_slots failed: {response.status_code} {response.text}")
        return response.json().get("data", {})

    async def create_booking(
        self, event_type_id: int, start: str, attendee_name: str, attendee_timezone: str,
        attendee_email: str | None = None, guests: list[str] | None = None,
    ) -> dict:
        """Never blindly retried: a retried write could double-book. Callers that need retry-safety
        must use the idempotency-key + BookingAttempt reconciliation flow in agent/orchestrator.py,
        not a second call to this method for the same slot.
        """
        self._ensure_configured()
        url = f"{self.base_url}/bookings"
        attendee: dict = {"name": attendee_name, "timeZone": attendee_timezone}
        if attendee_email:
            attendee["email"] = attendee_email
        payload = {"eventTypeId": event_type_id, "start": start, "attendee": attendee}
        if guests:
            payload["guests"] = guests  # Cal.com emails the invite to these people too
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await request_with_retry(
                client, "POST", url, json=payload, max_attempts=1,
                headers=self._headers(settings.calcom_api_version_bookings),
            )
        if response.status_code == 201:
            return response.json().get("data", {})
        raise ToolError(f"Cal.com create_booking failed: {response.status_code} {response.text}")

    async def cancel_booking(self, uid: str, reason: str = "") -> dict:
        self._ensure_configured()
        url = f"{self.base_url}/bookings/{uid}/cancel"
        payload = {"cancellationReason": reason} if reason else {}
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await request_with_retry(
                client, "POST", url, json=payload,
                headers=self._headers(settings.calcom_api_version_bookings),
            )
        if response.status_code >= 400:
            raise ToolError(f"Cal.com cancel_booking failed: {response.status_code} {response.text}")
        return response.json().get("data", {})
