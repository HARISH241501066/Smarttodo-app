"""Check that no secret from .env ended up in the built frontend.

    python scripts/scan_bundle.py [../frontend/.next]

Reads the secret-looking values from the repo's .env, then looks for each one (and for a few
well-known key shapes) in every file of the build. Prints only key NAMES, never values. Exit 1 if
anything is found. VAPID_PUBLIC_KEY is meant to reach the browser and is skipped.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SECRETISH = re.compile(r"(SECRET|PASSWORD|TOKEN|PRIVATE|FERNET|API_KEY|_KEY)$", re.IGNORECASE)
PUBLIC_OK = {"VAPID_PUBLIC_KEY", "NEXT_PUBLIC_VAPID_PUBLIC_KEY"}
SHAPES = {
    "Google OAuth client secret (GOCSPX-)": rb"GOCSPX-[A-Za-z0-9_-]{10,}",
    "private key block": rb"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    "Google API key (AIza)": rb"AIza[0-9A-Za-z_-]{30,}",
}


skipped: list[str] = []


def env_secrets() -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    env = ROOT / ".env"
    if not env.exists():
        return out
    for line in env.read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip("\"'")
        if key in PUBLIC_OK or not SECRETISH.search(key):
            continue
        for part in value.split(","):  # rotation lists hold several keys
            part = part.strip()
            if len(part) >= 8 and part not in str(ROOT):
                out[key] = part.encode()
            elif len(part) >= 8:
                skipped.append(key)  # appears in the repo's own path, so every build contains it
    return out


def main() -> int:
    build = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "frontend" / ".next"
    if not build.exists():
        print(f"no build at {build}; run `npm run build` first")
        return 2
    secrets = env_secrets()
    found: set[str] = set()
    files = 0
    for path in build.rglob("*"):
        if not path.is_file() or path.stat().st_size > 50_000_000:
            continue
        files += 1
        data = path.read_bytes()
        for name, value in secrets.items():
            if value in data:
                found.add(f"value of {name} in {path.relative_to(build)}")
        for name, pattern in SHAPES.items():
            if re.search(pattern, data):
                found.add(f"{name} in {path.relative_to(build)}")
    if skipped:
        print("not checkable (value is part of the repo path):", ", ".join(sorted(set(skipped))))
    print(f"scanned {files} files in {build.name} against {len(secrets)} secret values from .env")
    for item in sorted(found):
        print("FOUND:", item)
    print("0 findings" if not found else f"{len(found)} findings")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
