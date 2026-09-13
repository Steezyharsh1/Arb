#!/usr/bin/env python3
"""IBKR Client Portal Gateway session bootstrap & health check.

WHAT THIS IS
    Interactive Brokers' Client Portal Web API is served by a small Java
    gateway you run on your own machine. It listens on
    https://localhost:5000 with a SELF-SIGNED certificate, and its session
    expires (roughly daily), so anything that talks to it — including the
    future ForecastEx / CME event-contract adapter (Phase 3) — needs a
    reliable way to (a) confirm the session is authenticated and (b) nudge
    it back to life when it is not.

    This script does exactly that, and nothing else. It places no orders and
    reads no account data beyond the list of account IDs.

HOW TO USE IT (beginner walkthrough: the 'Human action checklist' (H3) in docs/ROADMAP.md)
    1. Start the gateway in its own terminal and log in via the browser page
       it opens (username + 2FA):
           cd <gateway folder> && bin/run.sh root/conf.yaml
    2. In a second terminal, from the repository root:
           python scripts/ibkr_gateway_session.py --allow-self-signed
    3. Read the verdict it prints. Exit code 0 = authenticated.

TLS HANDLING (deliberate, and stricter than the script this replaced)
    The gateway's certificate is self-signed, so verification cannot succeed
    against a normal trust store. This script will NOT silently disable
    verification. You must either:
      * pass --ca-bundle <path> with a certificate you trust (best), or
      * pass --allow-self-signed, which disables verification ONLY when the
        target host is localhost / 127.0.0.1 / [::1].
    Pointing it at any other host with --allow-self-signed is refused.
    Never disable certificate verification for a remote host.

EXIT CODES
    0 authenticated   1 reachable but not authenticated   2 unreachable
    3 refused (bad arguments / unsafe TLS request)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from urllib.parse import urlparse

import httpx

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}
DEFAULT_BASE = "https://localhost:5000/v1/api"


def _is_local(base: str) -> bool:
    return (urlparse(base).hostname or "").lower() in LOCAL_HOSTS


def _mask(account_id: str) -> str:
    """Show only the last 4 characters of an account id."""
    account_id = str(account_id or "")
    return f"…{account_id[-4:]}" if len(account_id) > 4 else "…"


def build_client(base: str, ca_bundle: str | None,
                 allow_self_signed: bool, timeout: float) -> httpx.Client:
    if ca_bundle:
        return httpx.Client(verify=ca_bundle, timeout=timeout)
    if allow_self_signed:
        if not _is_local(base):
            raise SystemExit(
                f"REFUSED: --allow-self-signed may only be used with a local "
                f"gateway (got host {urlparse(base).hostname!r}). Disabling "
                f"certificate verification for a remote host is not "
                f"permitted. Use --ca-bundle instead.")
        print("WARNING: TLS verification DISABLED for the local gateway "
              "(self-signed certificate). Localhost only.", file=sys.stderr)
        return httpx.Client(verify=False, timeout=timeout)
    raise SystemExit(
        "REFUSED: the Client Portal Gateway uses a self-signed certificate, "
        "so you must choose one of:\n"
        "  --ca-bundle <path>       verify against a certificate you trust\n"
        "  --allow-self-signed      skip verification (localhost only)\n"
        "See this file's docstring for why this is not optional.")


def auth_status(client: httpx.Client, base: str) -> dict:
    r = client.get(f"{base}/iserver/auth/status")
    r.raise_for_status()
    payload = r.json()
    # The gateway returns either an object or a list of objects.
    items = payload if isinstance(payload, list) else [payload]
    merged: dict = {}
    for item in items:
        if isinstance(item, dict):
            merged.update(item)
    return merged


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Check / bootstrap an IBKR Client Portal Gateway session.")
    ap.add_argument("--base", default=DEFAULT_BASE,
                    help=f"gateway API base (default: {DEFAULT_BASE})")
    ap.add_argument("--ca-bundle", default=None,
                    help="path to a CA/certificate to verify the gateway with")
    ap.add_argument("--allow-self-signed", action="store_true",
                    help="skip TLS verification for a LOCAL gateway only")
    ap.add_argument("--init", action="store_true",
                    help="attempt a brokerage-session (re)initialisation "
                         "before polling status")
    ap.add_argument("--polls", type=int, default=6,
                    help="how many status checks to make (default: 6)")
    ap.add_argument("--interval", type=float, default=5.0,
                    help="seconds between status checks (default: 5)")
    ap.add_argument("--timeout", type=float, default=30.0,
                    help="per-request timeout in seconds (default: 30)")
    args = ap.parse_args(argv)

    base = args.base.rstrip("/")
    try:
        client = build_client(base, args.ca_bundle, args.allow_self_signed,
                              args.timeout)
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return 3

    with client:
        try:
            if args.init:
                print("1. Initialising brokerage session "
                      "(POST /iserver/auth/ssodh/init)…")
                r = client.post(f"{base}/iserver/auth/ssodh/init",
                                json={"publish": True, "compete": True})
                print(f"   HTTP {r.status_code}: {r.text[:200]}")
            else:
                print("1. Skipping session init (pass --init to attempt it).")

            for attempt in range(1, max(1, args.polls) + 1):
                print(f"2. Status check {attempt}/{args.polls} "
                      f"(GET /iserver/auth/status)…")
                status = auth_status(client, base)
                authenticated = bool(status.get("authenticated"))
                print(f"   authenticated={authenticated} "
                      f"connected={status.get('connected')} "
                      f"competing={status.get('competing')}")
                if authenticated:
                    accounts = client.get(f"{base}/iserver/accounts").json()
                    masked = [_mask(a) for a in (accounts or [])]
                    print(f"3. Accounts visible to the session: "
                          f"{masked or 'NONE'} (ids masked on purpose)")
                    if not masked:
                        print("   Authenticated but no accounts are visible — "
                              "check trading permissions and that the gateway "
                              "is logged into the account you expect.")
                    print("\nSUCCESS — the gateway session is live.")
                    return 0
                if attempt < args.polls:
                    time.sleep(args.interval)

            print("\nNOT AUTHENTICATED. Open the gateway's browser page "
                  "(https://localhost:5000), log in with your IBKR credentials "
                  "and complete the 2FA prompt, then re-run this script. "
                  "If the login page keeps redirecting, restart the gateway "
                  "process and try again.", file=sys.stderr)
            return 1
        except httpx.TransportError as exc:
            print(f"\nUNREACHABLE: {type(exc).__name__}: {exc}\n"
                  f"Is the gateway running? Start it with "
                  f"`bin/run.sh root/conf.yaml` in the gateway folder and "
                  f"confirm https://localhost:5000 loads in a browser.",
                  file=sys.stderr)
            return 2
        except httpx.HTTPStatusError as exc:
            print(f"\nHTTP ERROR {exc.response.status_code}: "
                  f"{exc.response.text[:300]}", file=sys.stderr)
            return 2
        except json.JSONDecodeError as exc:
            print(f"\nUnexpected (non-JSON) response from the gateway: {exc}. "
                  f"It usually means you hit the login page instead of the "
                  f"API — re-authenticate in the browser.", file=sys.stderr)
            return 2


if __name__ == "__main__":
    raise SystemExit(main())
