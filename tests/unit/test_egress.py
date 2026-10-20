"""No cloud processing, mechanically (refusals; review 2026-10-15).

The app is not sandboxed, so macOS does not enforce network.client=false
for it. These tests are the enforcement: only allowlisted modules may hold a
network client, every URL literal in shipped code names this machine, and
every request is gated by require_loopback.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from tracemem.llm.egress import EgressRefused, require_loopback

SRC = Path(__file__).resolve().parents[2] / "src" / "tracemem"
NETWORK_MODULES = {"urllib.request", "http.client", "requests", "httpx",
                   "aiohttp", "urllib3"}
# The local model server (loopback) is the only HTTP peer the app has.
ALLOWED = {"llm/reasoner_ollama.py", "llm/ollama_keeper.py",
           "extraction/vlm_macro.py"}


def _imports(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                yield a.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module


def test_only_the_local_model_clients_hold_a_network_client():
    offenders = []
    for path in SRC.rglob("*.py"):
        rel = path.relative_to(SRC).as_posix()
        mods = set(_imports(ast.parse(path.read_text())))
        if mods & NETWORK_MODULES and rel not in ALLOWED:
            offenders.append(rel)
    assert offenders == []


def test_every_url_in_shipped_code_names_this_machine():
    remote = []
    for path in SRC.rglob("*.py"):
        for url in re.findall(r"https?://[^\s\"')]+", path.read_text()):
            host = re.sub(r"^https?://", "", url).split("/")[0].split(":")[0]
            if host not in ("127.0.0.1", "localhost", "[::1]", "::1"):
                remote.append((path.relative_to(SRC).as_posix(), url))
    assert remote == []


def test_every_allowed_client_passes_its_requests_through_the_gate():
    for rel in ALLOWED:
        text = (SRC / rel).read_text()
        assert "require_loopback(" in text, rel
        for line in text.splitlines():
            if "urlopen(" in line or "Request(" in line:
                window = text[text.index(line):text.index(line) + 200]
                assert "require_loopback(" in window or "req," in window, (rel, line)


@pytest.mark.parametrize("url", ["https://api.openai.com/v1/chat",
                                 "http://192.168.1.10:11434/api/generate",
                                 "http://example.com", "http://localhost.evil.com/x"])
def test_a_remote_url_is_refused(url):
    with pytest.raises(EgressRefused):
        require_loopback(url)


@pytest.mark.parametrize("url", ["http://127.0.0.1:11434/api/generate",
                                 "http://localhost:11434/api/version",
                                 "http://[::1]:11434/x"])
def test_this_machine_is_allowed(url):
    assert require_loopback(url) == url


def test_a_reasoner_pointed_elsewhere_never_opens_a_socket(monkeypatch):
    import tracemem.llm.reasoner_ollama as ro
    opened = []
    monkeypatch.setattr(ro.urllib.request, "urlopen",
                        lambda *a, **k: opened.append(a) or None)
    with pytest.raises(EgressRefused):
        ro._default_transport("https://api.example.com/generate", {}, 1.0)
    assert opened == []
