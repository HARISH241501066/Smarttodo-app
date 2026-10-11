import pytest

from app.ingest.query import (
    BASE_QUERY,
    DEFAULT_DENYLIST,
    build_query,
    labels_allowed,
    normalize_denylist,
    sender_matches_denylist,
)

SPEC_BASE = (
    "newer_than:30d -category:promotions -category:social -category:forums -in:spam -in:trash"
)


def test_base_query_matches_spec():
    assert BASE_QUERY == SPEC_BASE


def test_default_denylist_is_linkedin_and_naukri():
    assert DEFAULT_DENYLIST == ("linkedin.com", "naukri.com")
    assert build_query(DEFAULT_DENYLIST) == f"{SPEC_BASE} -from:linkedin.com -from:naukri.com"


def test_empty_denylist_adds_no_from_clauses():
    assert build_query([]) == SPEC_BASE
    assert build_query(None) == SPEC_BASE


def test_custom_denylist_entries_become_from_clauses():
    assert build_query(["foo.com", "bar@baz.org"]) == (
        f"{SPEC_BASE} -from:foo.com -from:bar@baz.org"
    )


def test_denylist_is_normalized_and_deduplicated():
    assert normalize_denylist([" Foo.COM ", "foo.com", "@bar.com"]) == ["foo.com", "bar.com"]


@pytest.mark.parametrize(
    "bad", ["x OR in:inbox", "a b", "(", "", "-from:x", "a:b", "foo.com)", "{evil}", "\n", 5, None]
)
def test_unsafe_entries_cannot_inject_search_operators(bad):
    query = build_query(["ok.com", bad])
    assert query == f"{SPEC_BASE} -from:ok.com"


@pytest.mark.parametrize(
    ("sender", "expected"),
    [
        ("LinkedIn <notifications@linkedin.com>", True),
        ("jobs@mail.linkedin.com", True),  # subdomain
        ("Naukri <info@NAUKRI.com>", True),  # case-insensitive
        ("x@evil-linkedin.com", False),  # look-alike, not a subdomain
        ("x@linkedin.com.evil.test", False),
        ("friend@gmail.com", False),
        ("not an address", False),
        ("", False),
        (None, False),
    ],
)
def test_sender_matches_denylist(sender, expected):
    assert sender_matches_denylist(sender, ["linkedin.com", "naukri.com"]) is expected


def test_full_address_entry_matches_only_that_address():
    assert sender_matches_denylist("Bob <bob@x.com>", ["bob@x.com"])
    assert not sender_matches_denylist("Al <al@x.com>", ["bob@x.com"])


@pytest.mark.parametrize(
    ("labels", "allowed"),
    [
        (["INBOX", "UNREAD"], True),
        ([], True),
        (None, True),
        (["INBOX", "CATEGORY_PROMOTIONS"], False),
        (["CATEGORY_SOCIAL"], False),
        (["CATEGORY_FORUMS"], False),
        (["SPAM"], False),
        (["TRASH", "INBOX"], False),
        (["CATEGORY_UPDATES"], True),  # not excluded by the spec'd query
    ],
)
def test_labels_allowed(labels, allowed):
    assert labels_allowed(labels) is allowed
