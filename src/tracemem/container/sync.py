"""OWNS: HAVE/WANT convergence between devices (spec 06 §4, ruling A6, K4).

The design finding of spec 06: there is NO conflict resolution — claims are
content-addressed and immutable, append is idempotent, projection is
order-independent, so convergence is set union. Identity is key possession:
both sides prove HKDF('transport') via challenge-response HMAC; no PKI, no
accounts. This module is the convergence logic over an in-process peer;
the wire transport (Bonjour discovery, relay auth) is the flagged R4 pass.
"""
from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass
from typing import List, Set


class SyncAuthError(Exception):
    pass


@dataclass(frozen=True)
class SyncReport:
    pulled: int
    pushed: int


class SyncPeer:
    def __init__(self, keyring, claim_log) -> None:
        self._transport_key = keyring.derive("transport")
        self.claim_log = claim_log

    # -- identity: possession of the master key IS device membership (A6) --

    def prove(self, nonce: bytes) -> bytes:
        return hmac.new(self._transport_key, nonce, hashlib.sha256).digest()

    def _verify(self, peer: "SyncPeer") -> None:
        nonce = os.urandom(16)
        if not hmac.compare_digest(peer.prove(nonce), self.prove(nonce)):
            raise SyncAuthError("peer failed transport-key challenge — not this "
                                "user's device")

    # -- inventory --

    def have(self) -> Set[str]:
        return {c.id for c in self.claim_log.all_claims()}

    def fetch(self, ids) -> List:
        wanted = set(ids)
        return [c for c in self.claim_log.all_claims() if c.id in wanted]

    # -- convergence: set union, both directions, idempotent by construction --

    def exchange(self, peer: "SyncPeer") -> SyncReport:
        self._verify(peer)
        peer._verify(self)
        mine, theirs = self.have(), peer.have()
        pulled = pushed = 0
        for claim in peer.fetch(theirs - mine):
            self.claim_log.append(claim)             # INSERT OR IGNORE: replay-safe
            pulled += 1
        for claim in self.fetch(mine - theirs):
            peer.claim_log.append(claim)
            pushed += 1
        return SyncReport(pulled=pulled, pushed=pushed)
