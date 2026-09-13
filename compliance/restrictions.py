"""Operational restrictions ledger (§13, §35).

Central place recording what the system must never do, so code reviews
and tests can assert against it. These are enforced structurally
elsewhere (no scraping code exists, adapters use official endpoints,
etc.); this module documents and exposes them programmatically."""
from __future__ import annotations

PROHIBITED_ACTIONS: tuple[str, ...] = (
    "scrape websites when an official API exists",
    "reverse-engineer private endpoints",
    "bypass authentication",
    "bypass geographic restrictions",
    "circumvent API rate limits",
    "circumvent account restrictions",
    "use VPNs/proxies to bypass eligibility",
    "automate a platform in violation of its terms",
)


def assert_endpoint_is_official(url: str) -> None:
    """Raise if a URL is not one of the documented official API hosts."""
    official_hosts = (
        "api.elections.kalshi.com", "api.kalshi.com", "demo-api.kalshi.co",
        "api.webull.com", "localhost", "127.0.0.1",  # IBKR CP gateway is local
    )
    from urllib.parse import urlparse
    host = urlparse(url).hostname or ""
    if host not in official_hosts:
        raise PermissionError(
            f"Endpoint {host!r} is not a documented official API host. "
            f"Scraping/private endpoints are prohibited (§13).")
