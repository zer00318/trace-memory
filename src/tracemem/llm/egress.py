"""OWNS: the one rule every outbound request in the shipped app obeys.

The app is not App-Sandboxed (packaging/entitlements.md says why), so
`network.client = false` is a promise macOS does not enforce for it. This
module is the enforcement instead: every request the app makes passes
through `require_loopback`, and tests/unit/test_egress.py fails if any
shipped module imports a network client outside the allowlist or names a
non-loopback URL. "No cloud processing" (the refusals) is therefore a
mechanism, not a convention (independent review, 2026-10-15).
"""
from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit


class EgressRefused(RuntimeError):
    pass


def require_loopback(url: str) -> str:
    """Return `url` unchanged if it names this machine; raise otherwise."""
    host = urlsplit(url).hostname or ""
    if host == "localhost":
        return url
    try:
        if ipaddress.ip_address(host).is_loopback:
            return url
    except ValueError:
        pass
    raise EgressRefused("refusing a request to %r: TRACE talks only to this "
                        "machine (refusals: no cloud processing)" % host)
