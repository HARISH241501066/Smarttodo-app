import json
from pathlib import Path
from typing import Any

from app.ingest.gmail import MessageGone

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "emails"


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text())


def fixtures_by_number(*numbers: int) -> dict[str, dict[str, Any]]:
    """Fixtures keyed by Gmail message id (msgNNN), selected by their number prefix."""
    out = {}
    for n in numbers:
        path = next(FIXTURE_DIR.glob(f"{n:02d}_*.json"))
        raw = json.loads(path.read_text())
        out[raw["id"]] = raw
    return out


def history_page(message_ids, history_id: str, labels=("INBOX",)) -> dict[str, Any]:
    """A history.list page; each id may be a str or an (id, labels) pair."""
    records = []
    for i, entry in enumerate(message_ids):
        mid, lbls = entry if isinstance(entry, tuple) else (entry, labels)
        records.append(
            {
                "id": str(i),
                "messagesAdded": [
                    {"message": {"id": mid, "threadId": f"t-{mid}", "labelIds": list(lbls)}}
                ],
            }
        )
    return {"history": records, "historyId": history_id}


class FakeGmailApi:
    """In-memory stand-in for GmailApi that records every call."""

    def __init__(self, messages=None, listing=None, page_size=100, history_id="1000"):
        self.messages: dict[str, dict] = dict(messages or {})
        self.listing: list[str] = list(self.messages if listing is None else listing)
        self.page_size = page_size
        self.profile = history_id
        self.history_now = history_id  # historyId reported when there are no pages
        self.history_pages: list[dict] = []
        self.history_error: Exception | None = None
        self.list_error: Exception | None = None
        self.get_errors: dict[str, Exception] = {}
        self.calls: list[tuple] = []

    def profile_history_id(self) -> str:
        self.calls.append(("profile",))
        return self.profile

    def list_messages(self, query, page_token):
        self.calls.append(("list", query, page_token))
        if self.list_error:
            raise self.list_error
        start = int(page_token or 0)
        page = self.listing[start : start + self.page_size]
        more = start + self.page_size < len(self.listing)
        return page, (str(start + self.page_size) if more else None)

    def get_message(self, message_id):
        self.calls.append(("get", message_id))
        if message_id in self.get_errors:
            raise self.get_errors[message_id]
        if message_id not in self.messages:
            raise MessageGone(message_id)
        return self.messages[message_id]

    def list_history(self, start_history_id, page_token):
        self.calls.append(("history", start_history_id, page_token))
        if self.history_error:
            raise self.history_error
        if not self.history_pages:
            return [], None, self.history_now
        index = int(page_token or 0)
        page = self.history_pages[index]
        more = index + 1 < len(self.history_pages)
        return page.get("history", []), (str(index + 1) if more else None), page.get("historyId")

    def gets(self) -> list[str]:
        return [c[1] for c in self.calls if c[0] == "get"]
