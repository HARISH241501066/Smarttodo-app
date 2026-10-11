import pytest

from app.ingest.query import (
    HARD_EXCLUDED_LABELS,
    build_query,
    labels_allowed,
    normalize_entries,
    normalize_keywords,
    sender_matches,
)
from app.sources import DEFAULT_SOURCES, Source, find_source, load_sources, matching_keyword

NPTEL = Source("NPTEL", "nptel", ("nptel.ac.in",))
DIGII = Source("Digii", "digii", ("digii.example",))


def test_query_is_exactly_sources_and_keywords():
    sources = load_sources(DEFAULT_SOURCES)
    assert build_query(sources, ["hackathon", "ideathon", "datathon"]) == (
        "newer_than:30d -in:spam -in:trash (from:nptel.ac.in OR hackathon OR ideathon OR datathon)"
    )


def test_every_source_sender_gets_a_from_term_and_no_category_exclusions():
    sources = [
        Source("A", "custom", ("a.test", "x@y.test")),
        Source("B", "custom", ("b.test",)),
    ]
    query = build_query(sources, ["hackathon"])
    assert query == (
        "newer_than:30d -in:spam -in:trash "
        "(from:a.test OR from:x@y.test OR from:b.test OR hackathon)"
    )
    assert "category" not in query and "-from" not in query


def test_window_days_follow_the_argument():
    assert build_query([NPTEL], [], days=3).startswith("newer_than:3d ")


def test_nothing_to_search_for_gives_no_query():
    assert build_query([], []) is None
    assert build_query([Source("Digii", "digii", ())], None) is None


def test_duplicate_senders_and_keywords_are_collapsed():
    query = build_query(
        [NPTEL, Source("Again", "custom", ("NPTEL.ac.in",))], ["Hackathon", "hackathon"]
    )
    assert query.count("nptel.ac.in") == 1 and query.count("hackathon") == 1


def test_keyword_phrases_are_quoted():
    assert build_query([], ["smart india hackathon"]).endswith('("smart india hackathon")')


@pytest.mark.parametrize(
    "bad",
    ["x OR in:inbox", "-in:anywhere", "a b", "(boss@work.test)", "", "  ", 7, None, "from:x.test"],
)
def test_entries_that_could_inject_search_operators_are_dropped(bad):
    assert normalize_entries([bad]) == []
    assert build_query([Source("S", "custom", ("ok.test",))], []) == (
        "newer_than:30d -in:spam -in:trash (from:ok.test)"
    )


@pytest.mark.parametrize("bad", ["a) OR (from:boss@work.test", "in:inbox", "", 3, "x" * 80])
def test_keywords_that_could_inject_search_operators_are_dropped(bad):
    assert normalize_keywords([bad]) == []


def test_entries_are_lowercased_and_at_signs_stripped():
    assert normalize_entries(["@NPTEL.ac.in", "Me@Example.COM"]) == [
        "nptel.ac.in",
        "me@example.com",
    ]


# ---------- strict sender matching ----------


@pytest.mark.parametrize(
    ("sender", "matches"),
    [
        ("NPTEL <noreply@nptel.ac.in>", True),
        ("x@onlinecourses.nptel.ac.in", True),
        ("x@a.b.nptel.ac.in", True),
        ("x@fakenptel.ac.in", False),
        ("x@nptel.ac.in.evil.test", False),
        ("x@mynptel.ac.in", False),
        ("NPTEL Official <x@evil.test>", False),  # the display name counts for nothing
        ("not an address", False),
        ("", False),
        (None, False),
    ],
)
def test_domain_entry_matches_exact_domain_or_true_subdomain_only(sender, matches):
    assert sender_matches(sender, ["nptel.ac.in"]) is matches


def test_address_entry_matches_only_that_exact_address():
    entries = ["alerts@jobs.test"]
    assert sender_matches("Jobs <alerts@jobs.test>", entries)
    assert sender_matches("Jobs <ALERTS@jobs.test>", entries)
    assert not sender_matches("other@jobs.test", entries)
    assert not sender_matches("alerts@sub.jobs.test", entries)
    assert not sender_matches("xalerts@jobs.test", entries)


def test_find_source_returns_the_first_matching_source():
    sources = [NPTEL, DIGII]
    assert find_source("a@onlinecourses.nptel.ac.in", sources) is NPTEL
    assert find_source("a@digii.example", sources) is DIGII
    assert find_source("a@fakenptel.ac.in", sources) is None


def test_load_sources_skips_malformed_entries():
    raw = [
        {"name": "Good", "type": "custom", "senders": ["A.test", "bad sender"]},
        {"name": "", "type": "custom", "senders": []},
        {"name": "NoType", "senders": []},
        {"name": "WrongType", "type": "banana", "senders": []},
        "junk",
        None,
    ]
    assert load_sources(raw) == [Source("Good", "custom", ("a.test",))]
    assert load_sources(None) == []


def test_keywords_match_whole_words_only():
    assert matching_keyword("Join the Hackathon today", ["hackathon"]) == "hackathon"
    assert matching_keyword("HACKATHON!", ["hackathon"]) == "hackathon"
    assert matching_keyword("A hackathons list", ["hackathon"]) is None
    assert matching_keyword("smart india hackathon 2026", ["smart india"]) == "smart india"
    assert matching_keyword("nothing here", ["hackathon"]) is None


def test_spam_and_trash_labels_are_the_only_hard_exclusions():
    assert HARD_EXCLUDED_LABELS == {"SPAM", "TRASH"}
    assert labels_allowed(["INBOX", "CATEGORY_PROMOTIONS"])  # promotions are fine now
    assert not labels_allowed(["INBOX", "SPAM"])
    assert not labels_allowed(["TRASH"])
    assert labels_allowed(None)
