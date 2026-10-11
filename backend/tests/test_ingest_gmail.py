import json
from unittest.mock import MagicMock

import httplib2
import pytest
from googleapiclient.errors import HttpError
from tenacity import wait_none

from app.ingest.gmail import (
    GoogleGmailApi,
    HistoryExpired,
    MessageGone,
    RateLimited,
    is_rate_limit_error,
)


def http_error(status: int, message: str = "", reason: str = "") -> HttpError:
    body = {"error": {"code": status, "message": message, "errors": [{"reason": reason}]}}
    return HttpError(httplib2.Response({"status": status}), json.dumps(body).encode())


QUOTA_MSG = (
    "Quota exceeded for quota metric 'Total Query Cost' and limit "
    "'Total Query Cost per minute per user' of service 'gmail.googleapis.com'"
)


def make_api(execute_side_effect, **kwargs):
    """GoogleGmailApi over a mocked discovery service whose .execute() follows the script."""
    service = MagicMock()
    execute = MagicMock(side_effect=execute_side_effect)
    request = MagicMock(execute=execute)
    users = service.users.return_value
    users.getProfile.return_value = request
    users.messages.return_value.list.return_value = request
    users.messages.return_value.get.return_value = request
    users.history.return_value.list.return_value = request
    kwargs.setdefault("min_interval", 0)
    return GoogleGmailApi(service, wait=wait_none(), **kwargs), service, execute


def test_profile_history_id_is_returned_as_string():
    api, service, _ = make_api([{"historyId": 12345}])
    assert api.profile_history_id() == "12345"
    service.users.return_value.getProfile.assert_called_once_with(userId="me")


def test_list_messages_passes_query_and_returns_ids_and_next_token():
    api, service, _ = make_api([{"messages": [{"id": "a"}, {"id": "b"}], "nextPageToken": "T2"}])
    assert api.list_messages("q-text", "T1") == (["a", "b"], "T2")
    kwargs = service.users.return_value.messages.return_value.list.call_args.kwargs
    assert kwargs["q"] == "q-text" and kwargs["pageToken"] == "T1"
    assert kwargs["includeSpamTrash"] is False and kwargs["userId"] == "me"


def test_list_messages_handles_empty_mailbox():
    api, _, _ = make_api([{"resultSizeEstimate": 0}])
    assert api.list_messages("q", None) == ([], None)


def test_get_message_requests_full_format():
    api, service, _ = make_api([{"id": "a"}])
    assert api.get_message("a") == {"id": "a"}
    kwargs = service.users.return_value.messages.return_value.get.call_args.kwargs
    assert kwargs == {"userId": "me", "id": "a", "format": "full"}


def test_list_history_requests_message_added_from_start_id():
    page = {"history": [{"id": "1"}], "nextPageToken": "N", "historyId": 999}
    api, service, _ = make_api([page])
    assert api.list_history("500", "P") == ([{"id": "1"}], "N", "999")
    kwargs = service.users.return_value.history.return_value.list.call_args.kwargs
    assert kwargs["startHistoryId"] == "500" and kwargs["pageToken"] == "P"
    assert kwargs["historyTypes"] == ["messageAdded"]


def test_list_history_without_records():
    api, _, _ = make_api([{"historyId": "77"}])
    assert api.list_history("5", None) == ([], None, "77")


# ---------- the two 404s ----------


def test_history_404_raises_history_expired_and_is_not_retried():
    api, _, execute = make_api([http_error(404)])
    with pytest.raises(HistoryExpired):
        api.list_history("old", None)
    assert execute.call_count == 1


def test_message_404_raises_message_gone_and_is_not_retried():
    api, _, execute = make_api([http_error(404)])
    with pytest.raises(MessageGone):
        api.get_message("gone")
    assert execute.call_count == 1


def test_list_messages_404_is_a_plain_http_error():
    api, _, _ = make_api([http_error(404)])
    with pytest.raises(HttpError):
        api.list_messages("q", None)


# ---------- retries ----------


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_transient_http_errors_are_retried_then_succeed(status):
    api, _, execute = make_api([http_error(status), http_error(status), {"id": "a"}])
    assert api.get_message("a") == {"id": "a"}
    assert execute.call_count == 3


def test_network_errors_are_retried():
    api, _, execute = make_api([ConnectionResetError(), TimeoutError(), {"historyId": "1"}])
    assert api.profile_history_id() == "1"
    assert execute.call_count == 3


def test_gives_up_after_max_attempts_and_raises_original_error():
    api, _, execute = make_api([http_error(503)] * 10, max_attempts=3)
    with pytest.raises(HttpError) as info:
        api.get_message("a")
    assert info.value.resp.status == 503
    assert execute.call_count == 3


@pytest.mark.parametrize("status", [400, 401, 403])
def test_client_errors_are_not_retried(status):
    api, _, execute = make_api([http_error(status), {"id": "never"}])
    with pytest.raises(HttpError):
        api.list_messages("q", None)
    assert execute.call_count == 1


# ---------- rate limiting ----------


@pytest.mark.parametrize(
    "error",
    [
        http_error(403, QUOTA_MSG),
        http_error(403, "User-rate limit exceeded", "userRateLimitExceeded"),
        http_error(403, "Rate Limit Exceeded", "rateLimitExceeded"),
        http_error(429, "Too many requests"),
    ],
)
def test_rate_limit_errors_are_retried_with_backoff_then_succeed(error):
    assert is_rate_limit_error(error)
    api, _, execute = make_api([error, error, {"id": "a"}])
    assert api.get_message("a") == {"id": "a"}
    assert execute.call_count == 3


@pytest.mark.parametrize(
    "error",
    [
        http_error(
            403, "Request had insufficient authentication scopes.", "insufficientPermissions"
        ),
        http_error(403, "Gmail API has not been used in project 1 before", "accessNotConfigured"),
        http_error(403, "Daily Limit Exceeded", "dailyLimitExceeded"),
        http_error(400, "bad request"),
        http_error(401, "invalid credentials"),
    ],
)
def test_other_403s_and_client_errors_are_not_treated_as_rate_limits(error):
    assert not is_rate_limit_error(error)
    api, _, execute = make_api([error, {"id": "never"}])
    with pytest.raises(HttpError):
        api.get_message("a")
    assert execute.call_count == 1


@pytest.mark.parametrize("error", [http_error(403, QUOTA_MSG), http_error(429)])
def test_exhausted_rate_limit_raises_rate_limited(error):
    api, _, execute = make_api([error] * 10, max_attempts=3)
    with pytest.raises(RateLimited):
        api.get_message("a")
    assert execute.call_count == 3


def test_exhausted_rate_limit_on_listing_also_raises_rate_limited():
    api, _, _ = make_api([http_error(403, QUOTA_MSG)] * 10, max_attempts=2)
    with pytest.raises(RateLimited):
        api.list_messages("q", None)


def test_calls_are_throttled_to_the_minimum_interval(monkeypatch):
    sleeps = []
    monkeypatch.setattr("app.ingest.gmail.time.sleep", sleeps.append)
    monkeypatch.setattr("app.ingest.gmail.time.monotonic", lambda: 100.0)
    api, _, _ = make_api([{"id": "a"}, {"id": "b"}], min_interval=0.5)
    api.get_message("a")
    api.get_message("b")
    assert sleeps == [pytest.approx(0.5)]  # the 2nd call, made "instantly", had to wait
