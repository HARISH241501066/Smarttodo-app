"""Undo email-provider link wrapping, by string decoding only.

Newsletter and "campaign" tools rewrite every link to go through their own tracking host, e.g.
    https://url1234.mail.example.com/ls/click?upn=...&url=https%3A%2F%2Fonlinecourses.nptel.ac.in%2Fquiz
The page that finishes the task is the encoded destination, so that is what gets scored and
stored. Nothing is ever requested: this is parsing, not following redirects. Wrappers that hide
the destination in an opaque token cannot be unwrapped and are left as they are.
"""

from urllib.parse import parse_qs, unquote, urlparse

# Query parameters that commonly carry the real destination.
REDIRECT_PARAMS = (
    "url",
    "u",
    "uri",
    "redirect",
    "redirect_url",
    "redirect_uri",
    "target",
    "dest",
    "destination",
    "link",
)
MAX_LAYERS = 3  # wrappers are sometimes nested


def _http_url(value: str) -> str | None:
    value = value.strip()
    if value.lower().startswith(("http://", "https://")) and urlparse(value).hostname:
        return value
    return None


def _path_wrapped(url: str) -> str | None:
    """Amazon SES click tracking puts the destination in the path: https://x.awstrack.me/L0/<url>/1/..."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if (host == "awstrack.me" or host.endswith(".awstrack.me")) and parsed.path.startswith("/L0/"):
        return _http_url(unquote(parsed.path[4:].split("/", 1)[0]))
    return None


def unwrap_tracking_url(url: str) -> str:
    current = url
    for _ in range(MAX_LAYERS):
        in_path = _path_wrapped(current)
        if in_path and in_path != current:
            current = in_path
            continue
        query = urlparse(current).query
        if not query:
            break
        params = parse_qs(query)  # decodes one layer of %-encoding
        found = None
        for key in REDIRECT_PARAMS:
            for value in params.get(key, []):
                found = _http_url(value) or _http_url(unquote(value))  # tolerate double encoding
                if found:
                    break
            if found:
                break
        if not found or found == current:
            break
        current = found
    return current
