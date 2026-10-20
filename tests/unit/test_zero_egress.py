"""Nothing leaves this machine, proved by grep rather than by promise.

`Yours alone / Egress audit / The zero-egress test`. The product's central
claim is that pixels and text stay on this Mac. That claim is worth exactly
as much as it is checkable, and until now it was checkable only by watching a
firewall — which nobody does, and which cannot be run in CI.

WHAT THIS ACTUALLY PROVES, stated honestly so nobody mistakes it for more: it
proves that no SHIPPING module names a network destination that is not
localhost. It cannot prove a running process made no connection — that needs
a firewall log — and it cannot see through a hostname built at runtime from
pieces. What it CAN do is catch the realistic way this promise breaks: a
convenient library, a telemetry snippet, an API endpoint pasted in during a
debugging session and forgotten.

The three refusals in the map — no keylogging, no telemetry, no cloud
processing — are LAW RECORDED and have never had a test. This is one.
"""
from __future__ import annotations

import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]

# Where the product actually runs. Tools are included: `./trace export` and
# the wipe are as much a part of the promise as the daemon.
SHIPPING = [ROOT / "src", ROOT / "tools"]

# The only hosts a local-first memory may name.
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "0.0.0.0", "::1"}

# Modules whose mere import would mean something reaches outward. urllib is
# permitted because ollama is local — the HOST check below is what makes that
# safe, not the import.
FORBIDDEN_IMPORTS = {
    "requests", "httpx", "aiohttp", "boto3", "google", "openai",
    "anthropic", "sentry_sdk", "posthog", "segment", "mixpanel",
    "analytics", "datadog", "newrelic", "smtplib", "ftplib", "telnetlib",
}

# Files that talk about the outside world in order to REFUSE it, or that
# measure a ceiling deliberately and are never shipped in the daemon path.
EXEMPT = {"frontier_spend.py", "frontier.py"}


def _py_files():
    for root in SHIPPING:
        for f in root.rglob("*.py"):
            if f.name not in EXEMPT:
                yield f


def _string_constants(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.value


def test_no_shipping_module_imports_a_way_out():
    """A telemetry SDK cannot be added by accident; it has to be imported."""
    offenders = []
    for f in _py_files():
        try:
            tree = ast.parse(f.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module.split(".")[0]]
            for n in names:
                if n in FORBIDDEN_IMPORTS:
                    offenders.append("%s imports %s"
                                     % (f.relative_to(ROOT), n))
    assert offenders == [], "a way off this machine appeared: %r" % offenders


def test_every_url_in_the_product_points_at_this_machine():
    """The realistic way this promise breaks is not malice — it is an API
    endpoint pasted in during a debugging session and forgotten."""
    import re

    url_re = re.compile(r"\b(?:https?|ws|wss|ftp)://([^/\s'\"]+)")
    offenders = []
    for f in _py_files():
        try:
            tree = ast.parse(f.read_text())
        except SyntaxError:
            continue
        for s in _string_constants(tree):
            for host in url_re.findall(s):
                bare = host.split(":")[0].strip("[]")
                if bare in ALLOWED_HOSTS:
                    continue
                # Documentation links in docstrings are prose, not egress.
                if bare.endswith("apple.com") or bare.endswith("python.org"):
                    continue
                # RFC 2606 RESERVED TLDs. `.example`, `.invalid` and `.test`
                # exist so that an example CANNOT resolve to anything — a
                # fixture host in a drill script is the intended use, and
                # exempting them is the standard doing its job rather than a
                # hole being cut. The scan found exactly one: tools/drills.py
                # naming `bank.example`.
                if bare.split(".")[-1] in ("example", "invalid", "test",
                                           "localhost"):
                    continue
                offenders.append("%s -> %s" % (f.relative_to(ROOT), host))
    assert offenders == [], "these point off the machine: %r" % offenders


def test_the_ollama_endpoint_is_local():
    """The one thing that DOES speak over a socket. If this ever became a
    hosted endpoint, every claim about local-only processing would be false
    while every other test kept passing."""
    from tracemem.llm.reasoner_ollama import OllamaReasoner

    r = OllamaReasoner("gemma3:12b-it-qat")
    assert r.url.startswith("http://127.0.0.1:")


def test_the_macro_eye_endpoint_is_local():
    from tracemem.extraction.vlm_macro import DEFAULT_URL

    assert DEFAULT_URL.startswith("http://127.0.0.1:")


def test_the_ask_socket_is_a_unix_socket_not_a_port():
    """A TCP port is reachable from the network; a unix socket in the user's
    home is not. This is the difference between a memory anyone on the café
    wifi can interrogate and one only its owner can."""
    import inspect

    from tracemem.app import ask_socket

    src = inspect.getsource(ask_socket)
    assert "AF_UNIX" in src
    assert "AF_INET" not in src


def test_this_test_says_what_it_cannot_prove():
    """It proves no shipping module NAMES a remote destination. It cannot
    prove a running process made no connection — that needs a firewall log —
    and a hostname assembled at runtime would slip past it. Recorded here so
    nobody upgrades this into a guarantee it does not make."""
    assert __doc__ and "cannot prove" in __doc__
