"""Is a URL a Google Form? (Source categories now come from the user's sources.)"""

from urllib.parse import urlparse


def is_gform_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return host == "forms.gle" or (host == "docs.google.com" and parsed.path.startswith("/forms"))
