"""OWNS: the composition root (spec 08 §7): config dict -> live object graph.

The ONLY module allowed to know concrete adapter names. Ports resolve here:
absent config keys get the in-memory/test implementation, so the full graph
constructs on a laptop with zero hardware — that is the oracle.
"""
from __future__ import annotations

from tracemem.app.budgets import InterruptionLedger, TokenLedger
from tracemem.app.hub import Hub, Wiring
from tracemem.app.scheduler import Spotlight
from tracemem.capture.steady_clock import SteadyClock
from tracemem.nodes.graph_store import MemoryGraphStore
from tracemem.nodes.sqlite_graph_store import SqliteGraphStore
from tracemem.prediction.model import RoutineModel
from tracemem.prediction.surprise import score as surprise_score
from tracemem.supplier.vector_index import VectorIndex
from tracemem.text.claim_log import ClaimLog


def _crypto_for(config: dict, store_path: str):
    """THE DEFAULT IS ENCRYPTION, and it is decided here or nowhere.

    Before 2026-07-23 this was `config.get("crypto")` — None unless a caller
    passed one, and no production caller ever did. That is how every live
    capture run in this project's history wrote a person's screen contents
    to disk in the clear. The fix is not a flag; it is which way the default
    points, because a default that must be remembered is a default that gets
    forgotten exactly once and then never noticed.

    An in-memory store keeps NullCrypto: there is no disk to protect, and the
    tests that build the whole graph on a laptop with no Keychain stay honest
    (store_meta still records `cipher = none`, so nothing pretends).

    THE KEYCHAIN IS NEVER REACHED FOR IMPLICITLY. A first draft of this
    function fell back to MacKeychain() whenever the store was on disk,
    which made the TEST SUITE read the founder's real login keychain —
    the exact side effect keychain_mac.py's own docstring says the suite
    must never have ("writing to the founder's real login keychain as a
    side effect of running tests is unacceptable"). Production passes a
    keychain down from the CLI; anything that does not pass one gets
    NullCrypto and says so out loud. Plaintext stays possible and stays
    LOUD; it never again becomes the quiet default.
    """
    if "crypto" in config:
        return config["crypto"]                      # explicit wins, incl. None
    if store_path == ":memory:":
        return None
    keychain = config.get("keychain")
    if keychain is None:
        return None                                  # caller announces it

    # AN EXISTING STORE IS OPENED THE WAY IT WAS WRITTEN (2026-07-28).
    #
    # The default points at encryption, and it should. But applying that
    # default to a store that ALREADY EXISTS in the clear does not encrypt
    # it — it makes it unreadable. Witnessed: the founder's real store holds
    # 604 plaintext claims, and `--ask` opened it with SodiumCrypto, printed
    # "STORE IS MIXED ... older claims CANNOT be read", and then crashed on
    # the first one. The reader now skips foreign rows instead of dying, but
    # skipping is not the answer here: the claims are RIGHT THERE and the
    # store itself records that plaintext wrote them.
    #
    # So a pre-existing store's own record decides. This cannot silently
    # downgrade a NEW store — `created_with` only exists once something has
    # been written — and it can never make an ENCRYPTED store open in the
    # clear, because that record would say SodiumCrypto. It only stops the
    # default from locking a user out of memory they already have.
    #
    # Turning such a store into an encrypted one is a MIGRATION (re-writing
    # every claim under a key), which is a deliberate act with its own
    # ruling, not something a default should do by surprise on next boot.
    existing = _created_with(store_path)
    if existing == "none":
        return None

    from tracemem.container.crypto import SodiumCrypto
    from tracemem.container.keyring import Keyring
    return SodiumCrypto(Keyring(keychain).derive("claim-log"))


def _created_with(store_path: str):
    """What cipher wrote this store, read WITHOUT any key. None if the file
    does not exist yet or has never been written to."""
    import os
    import sqlite3
    if not os.path.exists(store_path):
        return None
    try:
        db = sqlite3.connect(store_path)
        try:
            row = db.execute(
                "SELECT value FROM store_meta WHERE key='cipher'").fetchone()
        finally:
            db.close()
        return row[0] if row else None
    except sqlite3.Error:
        return None                      # not a store we can read; leave the
                                         # default alone rather than guess


def _graph_key_for(config: dict, graph_path: str):
    """Purpose key for a graph that was born encrypted or migrated explicitly.

    A populated legacy graph stays readable in plaintext until the explicit,
    backup-first migration runs. Merely upgrading code must never turn the
    founder's graph into ciphertext in place or lock the daemon out of it.
    """
    if "graph_key" in config:
        return config["graph_key"]
    if graph_path == ":memory:":
        return None
    keychain = config.get("keychain")
    if keychain is None:
        return None
    import os
    import sqlite3
    if os.path.exists(graph_path):
        try:
            db = sqlite3.connect(graph_path)
            try:
                protected = db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name='graph_security'").fetchone()
                if protected:
                    row = db.execute(
                        "SELECT COUNT(*) FROM graph_security").fetchone()
                    if row and row[0]:
                        from tracemem.container.keyring import Keyring
                        return Keyring(keychain).derive("graph-store")
                semantic = 0
                for table in ("node", "tombstone", "episode", "graph_meta"):
                    exists = db.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' "
                        "AND name=?", (table,)).fetchone()
                    if exists:
                        semantic += db.execute(
                            "SELECT COUNT(*) FROM %s" % table).fetchone()[0]
                if semantic:
                    return None
            finally:
                db.close()
        except sqlite3.Error:
            return None
    from tracemem.container.keyring import Keyring
    return Keyring(keychain).derive("graph-store")


def build(config: dict) -> Hub:
    # Even non-daemon composition (cold-start ingestion and black-box builds)
    # gets the same nondecreasing civil-time law. There is no second raw-wall
    # fallback hiding behind the production wiring seam.
    clock = config.get("clock") or SteadyClock()
    store_path = config.get("store_path", ":memory:")
    claim_log = config.get("claim_log") or ClaimLog(
        store_path, crypto=_crypto_for(config, store_path))
    # THE GRAPH IS DURABLE WHEN IT HAS A HOME (P1, 2026-07-29).
    #
    # This line was `config.get("graph_store") or MemoryGraphStore()`, and
    # `__main__` passed no override — so every belief the system derived died
    # at process exit. Nothing downstream noticed, because the entity tier is
    # rebuilt from raw by `Brain.backfill` on every boot; what did NOT come
    # back were the derived meta nodes, the tombstone graveyard ("you used to
    # think X"), and the processed-work ledgers. A memory that forgets what it
    # concluded, every time it restarts, cannot be graded the next morning —
    # which is the whole point of the digest.
    #
    # In-memory stays the default so the full graph still constructs on a
    # laptop with zero configuration (that is the wiring oracle). A path opts
    # into durability, and production always passes one.
    graph_store = config.get("graph_store")
    if graph_store is None:
        graph_path = config.get("graph_store_path")
        graph_store = (SqliteGraphStore(
            graph_path, key=_graph_key_for(config, graph_path)) if graph_path
                       else MemoryGraphStore())
    embedder = config.get("embedder")
    if embedder is None:
        raise ValueError("wiring needs an embedder port (sentence-transformers "
                         "in anger, HashEmbedder in tests) — there is no default "
                         "because a silent fallback would poison retrieval")
    reasoner = config.get("reasoner")
    if reasoner is None:
        raise ValueError("wiring needs a reasoner port — the heuristic "
                         "reasoner refuses everything by design")
    spotlight = config.get("spotlight") or Spotlight(
        swap_table=config.get("swap_table", {"local-ollama": 8.0}), clock=clock)
    # prediction wired LIVE (P5.4): the routine model the night refits is the
    # same one scoring surprise at ingest — one model, two consumers. Before
    # the first fit it returns mid-surprise (0.5): ignorance, never fake salience.
    routine_model = config.get("routine_model") or RoutineModel()
    surprise_fn = config.get("surprise_fn") or (
        lambda claim: surprise_score(claim.coord.world.place_key,
                                     claim.coord.t_ms, routine_model))
    wiring = Wiring(
        claim_log=claim_log,
        graph_store=graph_store,
        vector_index=config.get("vector_index") or VectorIndex(embedder),
        reasoner=reasoner,
        spotlight=spotlight,
        clock=clock,
        embedder=embedder,
        token_ledger=config.get("token_ledger") or TokenLedger(
            config.get("token_ledger_path", ":memory:"),
            cap=config.get("night_token_cap", 60_000)),
        interruptions=config.get("interruptions") or InterruptionLedger(
            config.get("interruption_ledger_path", ":memory:"),
            cap_per_day=config.get("interruption_cap", 3)),
        surprise_fn=surprise_fn,
        routine_model=routine_model,
        answer_model=config.get("answer_model", "local-ollama"),
        answer_budget_tokens=config.get("answer_budget_tokens", 8000),
        verify_answers=config.get("verify_answers", True),
    )
    return Hub(wiring)
