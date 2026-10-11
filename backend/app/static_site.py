"""Serves the built dashboard (a static export of the Next.js app) from FastAPI.

One service, one origin, one cookie domain: no CORS and no second server. Every exported page is
a file (`/settings` -> settings.html); anything else that looks like an app route falls back to
index.html. The API lives under /api, so it is never hidden by this.

The pages' inline scripts are fixed text in a static export, so each page's Content-Security-Policy
lists their SHA-256 hashes instead of allowing inline scripts in general.
"""

import base64
import hashlib
import re
from pathlib import Path

from fastapi.responses import FileResponse, JSONResponse, Response

# Never answered with index.html, even when they match no route: they are API or server paths.
RESERVED = ("api", "auth", "action", "health", "dev", "docs", "redoc", "openapi.json")
_INLINE_SCRIPT = re.compile(
    r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.DOTALL | re.IGNORECASE
)
_NOT_JS = re.compile(r'type="(?:application/json|application/ld\+json)"', re.IGNORECASE)

PAGE_CSP = (
    "default-src 'self'; script-src 'self'{hashes}; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; worker-src 'self'; "
    "manifest-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; "
    "frame-ancestors 'none'"
)
PAGE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


def script_hashes(html: str) -> list[str]:
    out = []
    for match in _INLINE_SCRIPT.finditer(html):
        tag = match.group(0)[: match.group(0).index(">") + 1]
        if _NOT_JS.search(tag):
            continue  # data blocks are never executed
        digest = hashlib.sha256(match.group(1).encode()).digest()
        out.append("'sha256-" + base64.b64encode(digest).decode() + "'")
    return sorted(set(out))


class StaticSite:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self._csp: dict[Path, str] = {}

    @property
    def ready(self) -> bool:
        return (self.root / "index.html").is_file()

    def _find(self, path: str) -> Path | None:
        rel = path.strip("/")
        for candidate in (rel, f"{rel}.html", f"{rel}/index.html") if rel else ("index.html",):
            file = (self.root / candidate).resolve()
            if file.is_file() and file.is_relative_to(self.root):
                return file
        return None

    def _csp_for(self, file: Path) -> str:
        if file not in self._csp:
            hashes = script_hashes(file.read_text(encoding="utf-8", errors="replace"))
            self._csp[file] = PAGE_CSP.format(hashes="".join(" " + h for h in hashes))
        return self._csp[file]

    def respond(self, path: str, *, head: bool = False) -> Response:
        top = path.strip("/").split("/", 1)[0]
        if top in RESERVED:
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        file = self._find(path)
        if file is None:
            if "." in path.rsplit("/", 1)[-1]:  # a missing asset, not an app route
                return JSONResponse({"detail": "Not Found"}, status_code=404)
            file = self.root / "index.html"  # an app route: let the client router handle it
        headers = dict(PAGE_HEADERS)
        # Every file carries a policy that suits it: a service worker runs under the policy of the
        # response that delivered it, so the API's "default-src 'none'" would stop it fetching.
        headers["Content-Security-Policy"] = PAGE_CSP.format(hashes="")
        if file.suffix == ".html":
            headers["Content-Security-Policy"] = self._csp_for(file)
            headers["Cache-Control"] = "no-cache"
        elif "/_next/static/" in file.as_posix():
            headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:  # sw.js, the manifest, icons: must never be stale
            headers["Cache-Control"] = "no-cache"
            if file.name == "sw.js":
                headers["Service-Worker-Allowed"] = "/"
        return FileResponse(file, headers=headers)
