"""Layer 2: is the sender who they claim to be?

Reads the receiving server's Authentication-Results (SPF / DKIM / DMARC) and checks the
display name against a list of brands and the domains each one really sends from.
A missing or unparseable header means "untrusted", never an error.
"""

import re
from dataclasses import dataclass
from email.utils import parseaddr

# brand keyword in the display name -> domains that brand really sends from
DEFAULT_BRANDS: dict[str, tuple[str, ...]] = {
    "nptel": ("nptel.ac.in", "nptel.iitm.ac.in"),
    "internshala": ("internshala.com",),
    "google": ("google.com", "googlemail.com", "forms.gle"),
    "amazon": ("amazon.com", "amazon.in", "amazonses.com"),
}

FREE_MAIL_DOMAINS = frozenset(
    {"gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "rediffmail.com"}
)

_RESULT_RE = re.compile(r"\b(spf|dkim|dmarc)\s*=\s*([a-z]+)", re.IGNORECASE)


@dataclass(frozen=True)
class AuthVerdict:
    spf: str | None
    dkim: str | None
    dmarc: str | None
    trusted: bool
    impersonated_brand: str | None
    sender_domain: str
    free_mail: bool


def parse_authentication_results(header: str | None) -> dict[str, str | None]:
    """First result per mechanism, except that any passing signature wins (multiple DKIM)."""
    found: dict[str, list[str]] = {"spf": [], "dkim": [], "dmarc": []}
    for name, value in _RESULT_RE.findall(header or ""):
        found[name.lower()].append(value.lower())
    return {k: ("pass" if "pass" in v else (v[0] if v else None)) for k, v in found.items()}


def sender_domain(sender: str | None) -> str:
    address = parseaddr(sender or "")[1].lower()
    return address.rsplit("@", 1)[1] if "@" in address else ""


def _domain_matches(domain: str, allowed: tuple[str, ...]) -> bool:
    return any(domain == d or domain.endswith("." + d) for d in allowed)


def find_impersonated_brand(
    sender: str | None, brands: dict[str, tuple[str, ...]] = DEFAULT_BRANDS
) -> str | None:
    display = parseaddr(sender or "")[0].lower()
    domain = sender_domain(sender)
    for brand, domains in brands.items():
        named = re.search(rf"\b{re.escape(brand.lower())}\b", display)
        if named and not _domain_matches(domain, domains):
            return brand
    return None


def check_authenticity(
    sender: str | None,
    authentication_results: str | None,
    brands: dict[str, tuple[str, ...]] = DEFAULT_BRANDS,
) -> AuthVerdict:
    r = parse_authentication_results(authentication_results)
    trusted = r["dmarc"] == "pass" or (r["spf"] == "pass" and r["dkim"] == "pass")
    domain = sender_domain(sender)
    return AuthVerdict(
        spf=r["spf"],
        dkim=r["dkim"],
        dmarc=r["dmarc"],
        trusted=trusted,
        impersonated_brand=find_impersonated_brand(sender, brands),
        sender_domain=domain,
        free_mail=domain in FREE_MAIL_DOMAINS,
    )
