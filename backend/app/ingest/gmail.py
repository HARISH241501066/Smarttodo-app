"""Thin wrapper over the Gmail API: retries, and the two 404 cases that matter."""

import logging
import socket
import time
from typing import Any, Protocol

import httplib2
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from tenacity import (
    Retrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
LIST_PAGE_SIZE = 100
HISTORY_PAGE_SIZE = 500


class HistoryExpired(Exception):
    """The stored history_id is too old (Gmail returned 404); a backfill is required."""


class MessageGone(Exception):
    """The message was deleted between being listed and being fetched."""


class RateLimited(Exception):
    """Gmail kept rejecting calls for quota/rate reasons even after backing off."""


class GmailApi(Protocol):
    def profile_history_id(self) -> str: ...

    def list_messages(self, query: str, page_token: str | None) -> tuple[list[str], str | None]: ...

    def get_message(self, message_id: str) -> dict[str, Any]: ...

    def get_attachment(self, message_id: str, attachment_id: str) -> str:
        """Base64url data of one attachment/large body part."""
        ...

    def list_history(
        self, start_history_id: str, page_token: str | None
    ) -> tuple[list[dict[str, Any]], str | None, str | None]:
        """Returns (history records, next page token, mailbox history id)."""
        ...


_RATE_LIMIT_MARKERS = ("ratelimitexceeded", "quota exceeded", "rate limit exceeded", "per minute")


def is_rate_limit_error(exc: BaseException) -> bool:
    """429s, and the 403s Gmail uses for per-user/per-minute quota ("Total Query Cost")."""
    if not isinstance(exc, HttpError):
        return False
    if exc.resp.status == 429:
        return True
    return exc.resp.status == 403 and any(m in str(exc).lower() for m in _RATE_LIMIT_MARKERS)


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, HttpError):
        return exc.resp.status in RETRY_STATUSES or is_rate_limit_error(exc)
    return isinstance(exc, socket.timeout | TimeoutError | ConnectionError | httplib2.HttpLib2Error)


class GoogleGmailApi:
    def __init__(self, service, *, max_attempts: int = 6, wait=None, min_interval: float = 0.1):
        self._service = service
        self._max_attempts = max_attempts
        self._wait = wait or wait_exponential(multiplier=1, max=30)
        self._min_interval = min_interval  # light client-side throttle between calls
        self._last_call = 0.0

    @classmethod
    def from_credentials(cls, credentials) -> "GoogleGmailApi":
        return cls(build("gmail", "v1", credentials=credentials, cache_discovery=False))

    def _execute(self, request) -> dict[str, Any]:
        retrying = Retrying(
            retry=retry_if_exception(_is_transient),
            stop=stop_after_attempt(self._max_attempts),
            wait=self._wait,
            reraise=True,
            before_sleep=lambda rs: logger.warning(
                "gmail call failed (%s); retrying, attempt %d",
                type(rs.outcome.exception()).__name__,
                rs.attempt_number,
            ),
        )
        self._throttle()
        try:
            return retrying(request.execute)
        except HttpError as exc:
            if is_rate_limit_error(exc):
                raise RateLimited(f"gmail rate limit (status {exc.resp.status})") from exc
            raise

    def _throttle(self) -> None:
        wait = self._min_interval - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def profile_history_id(self) -> str:
        profile = self._execute(self._service.users().getProfile(userId="me"))
        return str(profile["historyId"])

    def list_messages(self, query: str, page_token: str | None) -> tuple[list[str], str | None]:
        resp = self._execute(
            self._service.users()
            .messages()
            .list(
                userId="me",
                q=query,
                pageToken=page_token,
                maxResults=LIST_PAGE_SIZE,
                includeSpamTrash=False,
            )
        )
        return [m["id"] for m in resp.get("messages", [])], resp.get("nextPageToken")

    def get_message(self, message_id: str) -> dict[str, Any]:
        try:
            return self._execute(
                self._service.users().messages().get(userId="me", id=message_id, format="full")
            )
        except HttpError as exc:
            if exc.resp.status == 404:
                raise MessageGone(message_id) from exc
            raise

    def get_attachment(self, message_id: str, attachment_id: str) -> str:
        try:
            resp = self._execute(
                self._service.users()
                .messages()
                .attachments()
                .get(userId="me", messageId=message_id, id=attachment_id)
            )
        except HttpError as exc:
            if exc.resp.status == 404:
                raise MessageGone(message_id) from exc
            raise
        return resp.get("data", "")

    def list_history(
        self, start_history_id: str, page_token: str | None
    ) -> tuple[list[dict[str, Any]], str | None, str | None]:
        try:
            resp = self._execute(
                self._service.users()
                .history()
                .list(
                    userId="me",
                    startHistoryId=start_history_id,
                    historyTypes=["messageAdded"],
                    pageToken=page_token,
                    maxResults=HISTORY_PAGE_SIZE,
                )
            )
        except HttpError as exc:
            if exc.resp.status == 404:
                raise HistoryExpired(start_history_id) from exc
            raise
        history_id = resp.get("historyId")
        return (
            resp.get("history", []),
            resp.get("nextPageToken"),
            str(history_id) if history_id else None,
        )
