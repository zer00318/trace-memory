#!/usr/bin/env python3
"""The live cockpit.  Run:  python3 tools/cockpit.py → http://localhost:4242.

The top line is computed only from executable invariants and production
reachability.  The planning tree remains visible for navigation but cannot
move progress.  Retired semantic labels come through the daemon's decrypted,
read-only map projection; this diagnostic process never receives a graph key.
"""
import json
import os
import plistlib
import socket
import sqlite3
import subprocess
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TREE = ROOT / "campaign" / "tree.json"
TREEDIR = ROOT / "campaign" / "tree"
STATUS = ROOT / "campaign" / "status.json"
FRAMES = ROOT / "gold" / "frames"
STORE = ROOT / "data" / "trace_store.sqlite3"
GRAPH = ROOT / "data" / "trace_graph.sqlite3"
EVAL_RUNS = ROOT / "eval_runs"
SOCK = Path.home() / ".trace" / "ask.sock"
APP = Path("/Applications/TRACE.app")
HELPER = APP / "Contents" / "MacOS" / "TRACE"
AGENT = Path.home() / "Library" / "LaunchAgents" / "com.trace.watcher.plist"
PORT = 4242

# A number the founder reads is only worth reading if nobody could have typed
# it. Everything in `live_vitals()` is measured off the real files on every
# refresh; the hand-written `numbers` block in status.json stays separate and
# is labelled as such on the page.
LIVE_UNKNOWN = "—"


def _daemon_request(op, timeout=3.0, **fields):
    """One bounded read from the same private socket the signed face uses."""
    if not SOCK.exists():
        return None
    client = None
    try:
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(timeout)
        client.connect(str(SOCK))
        client.sendall((json.dumps(dict({"op": op}, **fields)) + "\n").encode())
        body = b""
        while b"\n" not in body and len(body) < 512 * 1024:
            part = client.recv(16 * 1024)
            if not part:
                break
            body += part
        if b"\n" not in body:
            return None
        return json.loads(body.split(b"\n", 1)[0].decode("utf-8", "replace"))
    except Exception:                                  # noqa: BLE001
        return None
    finally:
        if client is not None:
            client.close()


def _count(db, sql, args=()):
    """One read-only scalar. Read-only because the daemon owns these files:
    a cockpit that can lock the store is a cockpit that can stop the eye.

    Falls back to a normal connection when `mode=ro` refuses: sqlite cannot
    open a WAL database read-only without its `-shm`, because recovery needs
    to write, and it says so with the unhelpful "unable to open database
    file". The store is WAL, so without this the whole panel reads "—".
    """
    if not db.exists():
        return None
    for dsn, kw in ((f"file:{db}?mode=ro", {"uri": True}), (str(db), {})):
        try:
            con = sqlite3.connect(dsn, timeout=1.0, **kw)
        except Exception:
            continue
        try:
            return con.execute(sql, args).fetchone()[0]
        except Exception:
            continue
        finally:
            con.close()
    return None


def _rows(db, sql, args=()):
    """A bounded read-only row query with the same failure semantics."""
    if not db.exists():
        return None
    for dsn, kw in ((f"file:{db}?mode=ro", {"uri": True}), (str(db), {})):
        try:
            con = sqlite3.connect(dsn, timeout=1.0, **kw)
        except Exception:
            continue
        try:
            return con.execute(sql, args).fetchall()
        except Exception:
            continue
        finally:
            con.close()
    return None


def _table_columns(db, table):
    rows = _rows(db, "pragma table_info(%s)" % table)
    return {r[1] for r in rows} if rows is not None else None


def executable_checks():
    """Napkin-critical facts measured from stores or executable behavior.

    These checks, not campaign percentages, drive the cockpit's top line.
    Unknown is distinct from pass: an absent store proves nothing.
    """
    checks = []

    def add(name, state, detail):
        checks.append({"name": name, "state": state, "detail": detail})

    # The production security claim is about the artifact macOS actually
    # launches, not the entitlements file in git.  Verify the sealed bundle,
    # its non-ad-hoc team identity, the actual embedded entitlements, the
    # scoped-key read, and the installed launch path as one reachability seam.
    signed = False
    team = None
    actual_ents = None
    if HELPER.exists():
        try:
            verified = subprocess.run(
                ["codesign", "--verify", "--deep", "--strict", str(APP)],
                capture_output=True, timeout=10)
            details = subprocess.run(
                ["codesign", "-dv", "--verbose=4", str(HELPER)],
                capture_output=True, text=True, timeout=10).stderr
            signed = (verified.returncode == 0 and "Signature=adhoc" not in details
                      and "Authority=Apple Development:" in details
                      and "flags=0x10000(runtime)" in details)
            for line in details.splitlines():
                if line.startswith("TeamIdentifier="):
                    team = line.partition("=")[2]
            ent = subprocess.run(
                ["codesign", "-d", "--entitlements", ":-", str(HELPER)],
                capture_output=True, timeout=10)
            if ent.returncode == 0:
                actual_ents = plistlib.loads(ent.stdout)
        except Exception:                                   # noqa: BLE001
            pass
    restricted = bool(actual_ents is not None and
                      actual_ents.get("com.apple.security.network.client") is False and
                      # F-LAN (NAPKIN, 2026-10-14): the Mac may listen for its
                      # paired phone on the LAN. It still may not dial out.
                      actual_ents.get("com.apple.security.network.server") is True)
    key_ready = False
    key_detail = "scoped key unavailable"
    if signed:
        try:
            probe = subprocess.run([str(HELPER), "--probe-key"],
                                   capture_output=True, timeout=3)
            key_ready = probe.returncode == 0
            if not key_ready:
                key_detail = ("open TRACE and choose Authorize memory; macOS "
                              "must renew this signed build's Keychain access")
        except subprocess.TimeoutExpired:
            key_detail = "Keychain authorization is awaiting approval"
        except Exception:                                   # noqa: BLE001
            pass
    agent_ready = False
    if AGENT.exists():
        try:
            agent_ready = plistlib.loads(AGENT.read_bytes()).get(
                "ProgramArguments") == [str(HELPER), "--daemon"]
        except Exception:                                   # noqa: BLE001
            pass
    security_ok = signed and restricted and key_ready and agent_ready
    missing = []
    if not signed:
        missing.append("install valid team-signed hardened app")
    if signed and not restricted:
        missing.append("actual network entitlements differ")
    if not key_ready:
        missing.append(key_detail)
    if not agent_ready:
        missing.append("install LaunchAgent through signed helper")
    add("Signed Keychain boundary", "pass" if security_ok else "fail",
        ("team %s; sealed helper owns scoped key and launch path" % team)
        if security_ok else "; ".join(missing))

    # A source file which knows how to listen is not an ear in production.
    # Prove the entire installed seam without opening the microphone from the
    # cockpit itself: the signed native binary contains the bounded capture
    # command, the sealed runtime contains the organ, the daemon enables it,
    # the actual artifact carries audio-input, and a claim made by that exact
    # producer has reached the real append-only store.
    installed_runtime = APP / "Contents/Resources/runtime"
    installed_organ = installed_runtime / "tracemem/app/ear_organ.py"
    installed_entry = installed_runtime / "entry.py"
    native_capture = False
    if HELPER.exists():
        try:
            strings = subprocess.run(
                ["/usr/bin/strings", str(HELPER)], capture_output=True,
                text=True, timeout=3)
            native_capture = (strings.returncode == 0 and
                              "--capture-audio" in strings.stdout)
        except Exception:                                   # noqa: BLE001
            pass
    sealed_organ = installed_organ.is_file()
    daemon_enables_ear = False
    if installed_entry.is_file():
        try:
            daemon_enables_ear = "--ear" in installed_entry.read_text()
        except Exception:                                   # noqa: BLE001
            pass
    audio_entitled = bool(
        actual_ents is not None and
        actual_ents.get("com.apple.security.device.audio-input") is True)
    heard = _count(
        STORE, "select count(*) from claim where modality='mic' "
               "and bidder='ear:ambient'")
    daemon_stats = _daemon_request("stats")
    ear_detail_live = ((daemon_stats or {}).get("organs") or {}).get(
        "ear_detail") or {}
    ear_artifact = (signed and native_capture and sealed_organ and
                    daemon_enables_ear and audio_entitled)
    ear_live = ear_artifact and bool(heard)
    if ear_live:
        ear_detail = "%d ambient ASR claims reached the durable store" % heard
    elif not ear_artifact:
        absent = []
        if not signed:
            absent.append("team-signed app")
        if not native_capture:
            absent.append("native bounded capture command")
        if not sealed_organ:
            absent.append("sealed ear organ")
        if not daemon_enables_ear:
            absent.append("daemon --ear route")
        if not audio_entitled:
            absent.append("actual audio-input entitlement")
        ear_detail = "rebuild/install TRACE; installed artifact lacks: %s" % ", ".join(absent)
    elif heard is None:
        ear_detail = "claim store unavailable; cannot prove a live ambient utterance"
    else:
        last_error = str(ear_detail_live.get("last_error") or "")
        if last_error:
            ear_detail = ("installed ear is reachable but failed after %d "
                          "captured chunk(s): %s" %
                          (int(ear_detail_live.get("chunks_captured", 0)),
                           last_error[:180]))
        elif ear_detail_live:
            ear_detail = ("ear is alive: %d chunk(s) captured, %d transcribed, "
                          "no speech claim yet" %
                          (int(ear_detail_live.get("chunks_captured", 0)),
                           int(ear_detail_live.get("chunks_transcribed", 0))))
        else:
            ear_detail = ("installed ear is reachable but has produced no claims; "
                          "restart daemon for detailed capture/ASR health")
    add("Ambient ear production path", "pass" if ear_live else "fail",
        ear_detail)

    # The macro visual sense used to be three complete source modules with
    # zero production callers. Credit it only when the signed runtime carries
    # all three joins (eye bank, owner-loop pass, local VLM), the live daemon
    # exposes its buffer/pass health, and at least one resulting TEXT claim
    # has actually crossed the append-only boundary. Screenshots themselves
    # are intentionally uncountable here because none may exist on disk.
    sealed_macro = all((installed_runtime / rel).is_file() for rel in (
        "tracemem/extraction/frame_encode.py",
        "tracemem/extraction/macro_buffer.py",
        "tracemem/extraction/vlm_macro.py",
        "tracemem/app/macro_pass.py",
    ))
    daemon_enables_macro = False
    if installed_entry.is_file():
        try:
            entry_text = installed_entry.read_text()
            daemon_enables_macro = "--eye" in entry_text and "--ask" in entry_text
        except Exception:                                  # noqa: BLE001
            pass
    macro_claims = _count(
        STORE, "select count(*) from claim where bidder='vlm_macro'")
    macro_stats_reply = _daemon_request("stats")
    macro_health = ((macro_stats_reply or {}).get("organs") or {}).get("macro")
    macro_reachable = isinstance(macro_health, dict)
    macro_live = bool(signed and sealed_macro and daemon_enables_macro and
                      macro_reachable and macro_claims)
    if macro_live:
        macro_detail = ("%d local scene-understanding claim(s) reached durable "
                        "TEXT; volatile scene bank reports %d held, %d drained" %
                        (macro_claims, int(macro_health.get("held", 0)),
                         int(macro_health.get("drained", 0))))
    else:
        absent = []
        if not signed:
            absent.append("team-signed app")
        if not sealed_macro:
            absent.append("sealed in-memory bank/model/pass")
        if not daemon_enables_macro:
            absent.append("installed eye+brain launch route")
        if not macro_reachable:
            absent.append("restart daemon with macro health")
        if not macro_claims:
            absent.append("one real VLM scene claim")
        macro_detail = "macro visual understanding is not production-proven: " + ", ".join(absent)
    add("Macro visual understanding", "pass" if macro_live else "fail",
        macro_detail)

    installed_helpers = APP / "Contents/Resources/Helpers"
    helper_packages = (list(installed_helpers.glob("*/manifest.json"))
                       if installed_helpers.is_dir() else [])
    helper_artifacts_ok = bool(signed and helper_packages)
    helper_artifact_problem = None
    if helper_artifacts_ok:
        try:
            import hashlib as _hashlib
            for manifest_path in helper_packages:
                manifest = json.loads(manifest_path.read_text())
                executable = manifest_path.parent / manifest["executable"]
                digest = _hashlib.sha256(executable.read_bytes()).hexdigest()
                if (digest != manifest["sha256"] or
                        not executable.is_file() or
                        not os.access(executable, os.X_OK)):
                    raise ValueError("digest or executable mismatch")
        except Exception as exc:                           # noqa: BLE001
            helper_artifacts_ok = False
            helper_artifact_problem = type(exc).__name__
    helper_probe = None
    helper_probe_supported = False
    if HELPER.exists():
        try:
            helper_strings = subprocess.run(
                ["/usr/bin/strings", str(HELPER)], capture_output=True,
                text=True, timeout=3)
            helper_probe_supported = (helper_strings.returncode == 0 and
                                      "--probe-helpers" in helper_strings.stdout)
        except Exception:                                # noqa: BLE001
            pass
    if helper_artifacts_ok and helper_probe_supported:
        try:
            probed = subprocess.run(
                [str(HELPER), "--probe-helpers"], capture_output=True,
                text=True, timeout=15)
            parsed = json.loads(probed.stdout) if probed.stdout else {}
            helper_probe = bool(probed.returncode == 0 and parsed.get("ok"))
        except Exception:                                # noqa: BLE001
            helper_probe = False
    helper_claims = _count(
        STORE, "select count(*) from claim where bidder like 'helper:%'")
    helper_task_cols = _table_columns(STORE, "helper_task")
    helper_pending = (_count(
        STORE, "select count(*) from helper_task where status='pending'")
        if helper_task_cols else None)
    helper_live = (helper_artifacts_ok and bool(helper_claims)
                   and helper_pending == 0)
    if helper_live:
        helper_detail = ("%d sealed helper package(s); %d specialist claims "
                         "reached durable memory; deferred queue empty" %
                         (len(helper_packages), helper_claims))
    elif not helper_artifacts_ok:
        helper_detail = ("rebuild/install TRACE; installed signed app has no "
                         "digest-pinned helper package" +
                         (" (%s)" % helper_artifact_problem
                          if helper_artifact_problem else ""))
    elif helper_claims is None:
        helper_detail = "claim store unavailable; production execution is unproven"
    elif helper_task_cols is None:
        helper_detail = "installed helper path lacks the durable replay ledger"
    elif helper_pending:
        helper_detail = ("%d deferred helper task(s) remain owed; let an idle "
                         "night replay retained text" % helper_pending)
    else:
        helper_detail = (("signed bundle→sealed runtime→macOS sandbox→helper "
                          "probe passes; " if helper_probe else
                          "installed helper sandbox probe fails; ") +
                         "no real helper claim has completed the "
                         "eye→sandbox→store path")
    add("Sandboxed helper production path",
        "pass" if helper_live else "fail", helper_detail)

    # Cold start is credited only when the installed, signed product can ask
    # for a corpus under its own macOS identity, the sealed runtime contains
    # the multi-format reader, and a completed source receipt plus OWNED raw
    # evidence exists.  Source modules in the checkout prove none of those.
    chooser = False
    if HELPER.exists():
        try:
            strings = subprocess.run(
                ["/usr/bin/strings", str(HELPER)], capture_output=True,
                text=True, timeout=3)
            chooser = (strings.returncode == 0 and
                       "--cold-start" in strings.stdout and
                       "Give TRACE a past" in strings.stdout)
        except Exception:                                # noqa: BLE001
            pass
    sealed_formats = (installed_runtime /
                      "tracemem/owned/formats.py").is_file()
    installed_window = APP / "Contents/Resources/ui/window.py"
    installed_face = APP / "Contents/Resources/ui/menubar.py"
    cold_ui = False
    try:
        window_source = installed_window.read_text()
        face_source = installed_face.read_text()
        cold_ui = ("Build memory from a folder" in window_source and
                   "cold_start_from_picker" in window_source and
                   "def cold_start_from_picker" in face_source and
                   "--cold-start" in face_source)
    except Exception:                                   # noqa: BLE001
        pass
    owned_claims = _count(
        STORE, "select count(*) from claim where klass='owned'")
    owned_receipt_cols = _table_columns(STORE, "owned_source_receipt")
    owned_receipts = (_count(
        STORE, "select count(*) from owned_source_receipt "
               "where status='complete'") if owned_receipt_cols else None)
    cold_live = bool(signed and chooser and sealed_formats and cold_ui and
                     owned_claims and owned_receipts)
    if cold_live:
        cold_detail = ("%d exact-content source receipt(s); %d OWNED claims "
                       "reached durable memory before watching" %
                       (owned_receipts, owned_claims))
    else:
        missing = []
        if not signed:
            missing.append("team-signed installed app")
        if not chooser:
            missing.append("signed corpus chooser")
        if not sealed_formats:
            missing.append("sealed mail/PDF/Office reader")
        if not cold_ui:
            missing.append("reachable in-app folder action")
        if owned_receipt_cols is None:
            missing.append("restart receipt ledger")
        elif not owned_receipts:
            missing.append("completed source receipt")
        if not owned_claims:
            missing.append("durable OWNED evidence")
        cold_detail = "cold start is not production-proven: " + ", ".join(missing)
    add("Install-day owned past", "pass" if cold_live else "fail",
        cold_detail)

    sealed_map = (installed_runtime / "tracemem/app/live_map.py").is_file()
    sealed_map_ui = (APP / "Contents/Resources/ui/graph_window.py").is_file()
    stats_reply = daemon_stats
    map_ops = {"map", "belief"} <= set((stats_reply or {}).get("ops") or [])
    map_reply = (_daemon_request("map", query="", include_retired=True,
                                 limit=160) if map_ops else None)
    map_reachable = bool(map_reply and map_reply.get("ok") and
                         isinstance(map_reply.get("nodes"), list) and
                         isinstance(map_reply.get("edges"), list))
    drill_reachable = False
    if map_reachable and map_reply.get("nodes"):
        candidate = map_reply["nodes"][0].get("id")
        drilled = _daemon_request("belief", id=candidate) if candidate else None
        drill_reachable = bool(drilled and drilled.get("ok") and
                               isinstance(drilled.get("evidence"), list) and
                               isinstance(drilled.get("neighbours"), list))
    retired_total = int((map_reply or {}).get("total_retired") or 0)
    retired_visible = (not retired_total or any(
        node.get("retired") for node in (map_reply or {}).get("nodes", [])))
    live_map_ok = bool(signed and sealed_map and sealed_map_ui and map_ops and
                       map_reachable and drill_reachable and retired_visible)
    if live_map_ok:
        map_detail = ("signed native map shows %d of %d current and %d retired; "
                      "node drill resolves relationships and raw evidence" %
                      ((map_reply or {}).get("shown", 0),
                       (map_reply or {}).get("total_live", 0), retired_total))
    else:
        missing = []
        if not signed:
            missing.append("team-signed app")
        if not sealed_map or not sealed_map_ui:
            missing.append("sealed native graph surface")
        if not map_ops:
            missing.append("restart daemon with map/belief operations")
        elif not map_reachable:
            missing.append("live topology reply")
        elif not drill_reachable:
            missing.append("belief evidence drill")
        if not retired_visible:
            missing.append("retired beliefs in bounded view")
        map_detail = "live graph is not production-proven: " + ", ".join(missing)
    add("Browsable live graph", "pass" if live_map_ok else "fail", map_detail)

    cols = _table_columns(STORE, "claim")
    if cols is None:
        add("Raw observation identity", "unknown", "claim store unavailable")
    elif "identity_version" not in cols:
        add("Raw observation identity", "fail", "v1 schema can collapse distinct observations")
    else:
        legacy = _count(STORE, "select count(*) from claim where identity_version < 2")
        current = _count(STORE, "select count(*) from claim where identity_version >= 2")
        audit_tables = _rows(
            STORE, "select name from sqlite_master where type='table' "
            "and name='claim_identity_audit'")
        if not legacy:
            add("Raw observation identity", "pass" if current else "unknown",
                "all %d surviving rows use collision-safe v2" % (current or 0))
        elif not audit_tables:
            add("Raw observation identity", "fail",
                "v2 rows %d; %d legacy rows have no immutable audit mapping" %
                (current or 0, legacy or 0))
        else:
            audited = _count(STORE, "select count(*) from claim_identity_audit") or 0
            unreadable = _count(
                STORE, "select count(*) from claim_identity_audit where status in "
                "('unreadable_foreign_cipher','invalid_wire')") or 0
            invalid = _count(
                STORE, "select count(*) from claim_identity_audit "
                "where status='explicit_unknown_id'") or 0
            collisions = _count(
                STORE, "select coalesce(sum(n),0) from (select count(*) n "
                "from claim_identity_audit where candidate_v2_id is not null "
                "group by candidate_v2_id having count(*)>1)") or 0
            aliases = _count(
                STORE, "select count(*) from claim_identity_audit a join claim c "
                "on c.id=a.candidate_v2_id where c.identity_version>=2") or 0
            remaining = max(0, int(legacy or 0) - int(audited))
            bad = remaining + unreadable + invalid + collisions + aliases
            add("Raw observation identity", "pass" if current and bad == 0 else "fail",
                ("v2 rows %d; legacy mapped %d/%d; unreadable %d; invalid %d; "
                 "candidate conflicts %d. Pre-v2 observations discarded before "
                 "storage remain unknowable, never silently reconstructed") %
                (current or 0, audited, legacy or 0, unreadable, invalid,
                 collisions + aliases))

    graph_tables = _rows(GRAPH, "select name from sqlite_master where type='table'")
    table_names = {r[0] for r in graph_tables or []}

    def graph_meta(key, integer=False):
        table = ("graph_public_meta" if "graph_public_meta" in table_names
                 else "graph_meta")
        cast = "cast(value as integer)" if integer else "value"
        return _count(GRAPH, "select %s from %s where key=?" % (cast, table),
                      (key,))
    durable = {"episode", "episode_claim", "boundary"}
    if graph_tables is None:
        add("Durable binding", "unknown", "graph store unavailable")
    elif durable <= table_names:
        if "boundary_resolution" in table_names:
            pending = _count(
                GRAPH, "select count(*) from boundary b left join "
                "boundary_resolution r on r.t_ms=b.t_ms and r.kind=b.kind "
                "where b.kind='late_scene_pending' and r.t_ms is null")
        else:
            pending = _count(
                GRAPH, "select count(*) from boundary "
                "where kind='late_scene_pending'")
        version = graph_meta("boundary_version", integer=True) or 0
        redirects = (_count(GRAPH, "select count(*) from episode_successor")
                     if "episode_successor" in table_names else 0)
        add("Durable binding", "fail" if pending else "pass",
            "%d late boundaries await versioned reconciliation; stop TRACE "
            "and run ./trace reconcile" % pending
            if pending else
            "episodes, memberships, and boundaries persist; version %d with %d redirects"
            % (version, redirects or 0))
    else:
        add("Durable binding", "fail", "missing tables: %s" %
            ", ".join(sorted(durable - table_names)))

    # BACKLOG MEANS "LEFT BEHIND", NOT "IN FLIGHT" (2026-10-15). Binding runs
    # on a beat behind capture (a 10s flush, the 5s flicker hold, and a night
    # slice that can hold the owner thread), so while the founder is active
    # an instant snapshot almost always catches a few claims mid-flight, and
    # the check read red on a healthy daemon. It now records the store's
    # high-water mark and passes only if everything up to that mark is bound
    # within one beat; a real stall still fails.
    BIND_BEAT_S = 45
    claims = _count(STORE, "select count(*) from claim")
    receipt_table = "binding_receipt" if "binding_receipt" in table_names else None
    bound = (_count(GRAPH, "select count(*) from binding_receipt")
             if receipt_table else None)
    links = _count(STORE, "select count(*) from claim_link")
    max_seq = _count(STORE, "select coalesce(max(seq),0) from claim_link")
    cursor = graph_meta("bind_watermark_seq", integer=True)
    caught_up_s = None
    if (claims is not None and bound is not None and max_seq is not None
            and (bound < claims or int(cursor or 0) < int(max_seq))):
        started = time.time()
        while time.time() - started < BIND_BEAT_S:
            time.sleep(1.0)
            bound_now = _count(GRAPH, "select count(*) from binding_receipt")
            cursor_now = graph_meta("bind_watermark_seq", integer=True)
            if (bound_now is not None and bound_now >= claims
                    and int(cursor_now or 0) >= int(max_seq)):
                caught_up_s = time.time() - started
                break
    in_flight = (max(0, claims - bound)
                 if claims is not None and bound is not None else 0)
    if claims is None or bound is None:
        add("Binding backlog", "fail" if graph_tables is not None else "unknown",
            "per-claim completion receipt table is missing" if graph_tables is not None
            else "claim or graph ledger unavailable")
    elif in_flight == 0 or caught_up_s is not None:
        add("Binding backlog", "pass",
            "0 stored claims left unbound" if in_flight == 0 else
            "%d claims were in flight; all bound within %.0fs (beat %ds)"
            % (in_flight, caught_up_s, BIND_BEAT_S))
    else:
        add("Binding backlog", "fail",
            "%d stored claims still have no bind receipt after %ds"
            % (in_flight, BIND_BEAT_S))

    if claims is None or links is None or graph_tables is None:
        add("Append-order closure", "unknown", "append sequence unavailable")
    else:
        unsequenced = max(0, claims - links)
        cursor = int(cursor or 0)
        closed = unsequenced == 0 and (cursor == int(max_seq or 0)
                                       or caught_up_s is not None)
        add("Append-order closure", "pass" if closed else "fail",
            "%d raw rows lack append sequence; cursor %d of %d%s" %
            (unsequenced, cursor, int(max_seq or 0),
             "" if cursor == int(max_seq or 0) or caught_up_s is None else
             " at first read, closed within %.0fs" % caught_up_s))

    founder_rows = _rows(STORE, "select id from claim where bidder='founder'")
    if founder_rows is None or graph_tables is None:
        add("Founder truth reaches graph", "unknown", "stores unavailable")
    elif not founder_rows:
        add("Founder truth reaches graph", "unknown", "no founder claims to verify")
    else:
        missing = [r[0] for r in founder_rows if not receipt_table or not _count(
            GRAPH, "select count(*) from binding_receipt where claim_id=?", (r[0],))]
        add("Founder truth reaches graph", "pass" if not missing else "fail",
            "%d of %d founder claims lack a binding receipt" %
            (len(missing), len(founder_rows)))

    clock_model = graph_meta("clock_model")
    try:
        import sys as _sys
        src = str(ROOT / "src")
        if src not in _sys.path:
            _sys.path.insert(0, src)
        from tracemem.capture.steady_clock import MODEL_ID, SteadyClock

        state = {"wall": 1_700_000_000.0, "mono": 10.0}
        probe = SteadyClock(wall=lambda: state["wall"],
                            mono=lambda: state["mono"])
        before = probe.now_ms()
        state["wall"] -= 30.0
        after = probe.now_ms()
        jumps = probe.drain_jumps()
        behavior = after >= before and len(jumps) == 1 and jumps[0][1] < 0
        reachable = clock_model == MODEL_ID
        add("Coherent production clock",
            "pass" if behavior and reachable else "fail",
            ("daemon runs %s; backward-step ordering and witness probe pass" % MODEL_ID)
            if behavior and reachable else
            "restart daemon: live graph reports %r; expected %s" %
            (clock_model, MODEL_ID))
    except Exception as exc:
        add("Coherent production clock", "unknown", type(exc).__name__)

    attrs = _rows(GRAPH, "select attributes from node where attributes <> '[]' limit 1")
    if attrs is None:
        add("Semantic graph confidentiality", "unknown", "graph unavailable")
    elif "graph_security" in table_names and _count(
            GRAPH, "select count(*) from graph_security where version=1"):
        sealed_fields = [
            ("node", "attributes", "attributes <> '[]'"),
            ("tombstone", "attributes", "attributes <> '[]'"),
            ("episode", "place_key", "place_key is not null and place_key <> ''"),
            ("pending_mention", "surface", "1"),
            ("pending_mention", "key_body", "1"),
            ("pending_mention", "kind_body", "1"),
            ("ledger", "outcome", "1"),
            ("ledger", "key_body", "1"),
            ("graph_meta", "value", "1"),
            ("graph_meta", "key_body", "1"),
        ]
        unsealed = 0
        for table, column, where in sealed_fields:
            if table not in table_names or column not in (_table_columns(GRAPH, table) or set()):
                unsealed += 1
                continue
            unsealed += (_count(
                GRAPH, "select count(*) from %s where (%s) and %s not like 'g1:%%'"
                % (table, where, column)) or 0)
        add("Semantic graph confidentiality", "pass" if unsealed == 0 else "fail",
            "all semantic payloads and lookup keys are sealed" if unsealed == 0
            else "%d semantic graph fields are unsealed" % unsealed)
    elif not attrs:
        add("Semantic graph confidentiality", "fail",
            "graph has no encryption marker; new semantic rows would be plaintext")
    else:
        try:
            decoded = json.loads(attrs[0][0])
            readable = isinstance(decoded, list)
        except Exception:
            readable = False
        add("Semantic graph confidentiality", "fail" if readable else "pass",
            "semantic attributes are readable plaintext" if readable else
            "semantic attributes are not readable without the graph key")

    # "Living evidence invalidates work", "Confidence accrues" and "Citation
    # closure" used to be built here from in-memory objects, so production
    # state could never fail them. An independent review (2026-10-15) called
    # them what they were: unit tests on the scoreboard. They live in
    # tests/unit/test_living_node_behaviour.py now.

    # The Mac eye's three easy-to-regress seams, exercised without capturing
    # the cockpit user's screen: stillness must eventually earn a read, blur
    # must be judged before OCR, and the text claim must cite the window that
    # owns its pixels rather than whichever app owns the keyboard.
    try:
        from tracemem.capture.window_stack import WindowInfo, window_at
        from tracemem.extraction.blur_gate import is_readable
        from tracemem.extraction.l1_claims import runs_to_claims
        from tracemem.extraction.l1_read import ScreenRun
        from tracemem.extraction.peripheral import Peripheral

        still = [[128] * 16 for _ in range(10)]
        peripheral = Peripheral(dwell_ms=5)
        peripheral.observe(still, 0)
        dwell = peripheral.observe(still, 10)
        sharp = [[0 if (x + y) % 2 else 255 for x in range(5)]
                 for y in range(5)]
        windows = (WindowInfo(
            window_id=73, owner="pixel-owner", pid=7, title="proof",
            layer=0, rect=(0, 0, 100, 100), z=0),)
        owner = window_at((50, 50), windows)
        claims_for_pixel = runs_to_claims(
            [ScreenRun("cockpit visible text", 25, 25, 50, 20, .9)],
            frame_w=100, frame_h=100, t_ms=10, frame_id="cockpit-eye",
            device_id="cockpit", app="keyboard-owner", place_key="dev:cockpit",
            bidder="peripheral:change", bid_id="cockpit-eye:10",
            bid_predicted_value=1.0, actual_cost=0.0,
            attribution=lambda _run: (
                owner.owner, "window:%s" % owner.window_id) if owner else None)
        claim = claims_for_pixel[0]
        behavior = (any(e.kind == "dwell" for e in dwell)
                    and is_readable(sharp)
                    and claim.coord.capture.app == "pixel-owner"
                    and claim.coord.capture.window == "window:73")
        live_organs = (daemon_stats or {}).get("organs") or {}
        eye_state = live_organs.get("eye")
        eye_detail = live_organs.get("eye_detail") or {}
        frames = int(eye_detail.get("frames", 0) or 0)
        production_eye = frames > 0 and eye_state in ("alive", "parked")
        if behavior and production_eye:
            eye_state_out = "pass"
            eye_message = ("signed eye has delivered %d frame(s); still-screen "
                           "dwell, blur gate, and pixel-owner citation execute"
                           % frames)
        elif not behavior:
            eye_state_out = "fail"
            eye_message = ("dwell, readability, or per-window attribution "
                           "invariant failed")
        elif daemon_stats is None:
            eye_state_out = "fail"
            eye_message = ("source invariants execute, but the signed daemon "
                           "is unreachable")
        else:
            why = str(eye_detail.get("last_error") or "")[:180]
            eye_state_out = "fail"
            eye_message = ("source invariants execute, but the signed eye is "
                           "%s with %d frames%s" %
                           (eye_state or "unknown", frames,
                            (": " + why) if why else ""))
        add("Mac eye cadence and attribution", eye_state_out, eye_message)
    except Exception as exc:
        add("Mac eye cadence and attribution", "unknown", type(exc).__name__)

    try:
        from tracemem.nodes.marination.mining import GRAMMAR_VERSION

        live_grammar = graph_meta("night_hunger_grammar")
        pending = graph_meta("night_hunger_pending", integer=True)
        unresolved = graph_meta("night_hunger_unresolved", integer=True)
        examined = graph_meta("night_hunger_examined", integer=True)
        if graph_tables is None:
            state = "unknown"
            detail = "graph unavailable"
        elif live_grammar != GRAMMAR_VERSION:
            state = "fail"
            detail = ("night has not re-read retained text with grammar %s; "
                      "let the signed daemon complete an idle night" %
                      GRAMMAR_VERSION)
            live_night = (daemon_stats or {}).get("night") or {}
            skipped = str(live_night.get("skipped_because") or "")
            if skipped:
                detail += "; live scheduler is waiting because %s" % skipped
                if skipped == "on battery":
                    detail += " — connect power and leave TRACE idle"
        elif int(pending or 0) != 0:
            state = "fail"
            detail = "%d durable hunger tasks remain after an interrupted night" % pending
        else:
            state = "pass"
            detail = ("grammar %s examined %d tasks; %d node hungers remain open"
                      % (GRAMMAR_VERSION, int(examined or 0),
                         int(unresolved or 0)))
        add("Night hunger re-reads durable text", state, detail)
    except Exception as exc:
        add("Night hunger re-reads durable text", "unknown", type(exc).__name__)

    # SENSES ARE MEASURED BY WHAT THEY DID RECENTLY, NOT EVER (2026-10-15).
    # The old check was `select distinct modality`: a sense that produced one
    # claim in its life stayed green for ever. Meanwhile window titles were
    # dark for 50 days and the ear for 8, and nothing turned red. Every window
    # below is UNCALIBRATED: a shape chosen for how each sense is used, not a
    # measurement.
    HOUR = 3600 * 1000
    latest = {}
    for bidder_like, key in (("ax:%", "ax"), ("peripheral:%", "screen_ocr")):
        latest[key] = _count(STORE, "select max(t_ms) from claim where bidder like ?",
                             (bidder_like,))
    for modality in ("screen", "mic", "camera"):
        latest[modality] = _count(STORE, "select max(t_ms) from claim where modality=?",
                                  (modality,))
    now_ms = int(time.time() * 1000)

    def ago(t):
        if not t:
            return "never"
        hours = (now_ms - int(t)) / HOUR
        return ("%.0f min ago" % (hours * 60) if hours < 1 else
                "%.1f h ago" % hours if hours < 48 else "%.0f days ago" % (hours / 24))

    screen_t = latest.get("screen") or 0
    # Activity is the screen: a sense is late only relative to a moment the
    # founder was demonstrably at the Mac.
    stale = {}
    if latest.get("ax") is None or screen_t - int(latest["ax"] or 0) > HOUR:
        stale["window titles (Accessibility)"] = ago(latest.get("ax"))
    if latest.get("mic") is None or screen_t - int(latest["mic"] or 0) > 24 * HOUR:
        stale["ear"] = ago(latest.get("mic"))
    if latest.get("camera") is None or now_ms - int(latest["camera"] or 0) > 7 * 24 * HOUR:
        stale["phone camera"] = ago(latest.get("camera"))
    if not screen_t:
        stale = dict({"screen": ago(None)}, **stale)
    if _count(STORE, "select count(*) from claim") is None:
        add("Eyes and ears in production", "unknown", "claim store unavailable")
    else:
        add("Eyes and ears in production", "fail" if stale else "pass",
            ("silent: " + "; ".join("%s last %s" % kv for kv in stale.items()))
            if stale else
            "screen %s, titles %s, ear %s, phone %s" % (
                ago(screen_t), ago(latest.get("ax")), ago(latest.get("mic")),
                ago(latest.get("camera"))))
    ax_late = "window titles (Accessibility)" in stale
    add("Window titles flowing", "fail" if ax_late else "pass",
        ("last window title %s while the screen was read %s; grant "
         "Accessibility to /Applications/TRACE.app" % (ago(latest.get("ax")), ago(screen_t)))
        if ax_late else "last window title %s" % ago(latest.get("ax")))

    # NOTHING READABLE AT REST (2026-10-15). Encryption was proven on the live
    # files only; the review found two backups under data/backup/ holding
    # plaintext claims and beliefs. Every SQLite file under data/ is checked:
    # three spread samples of each claim/node/tombstone table.
    leaks = []
    data_dir = ROOT / "data"
    for path in sorted(data_dir.rglob("*")) if data_dir.is_dir() else []:
        try:
            if not path.is_file() or path.stat().st_size < 4096:
                continue
            with open(path, "rb") as fh:
                if fh.read(16) != b"SQLite format 3\x00":
                    continue
        except OSError:
            continue
        # immutable=1: a scan must not create -wal/-shm beside a backup, and
        # must never fall back to a writable open the way _count does.
        try:
            con = sqlite3.connect("file:%s?mode=ro&immutable=1" % path, uri=True)
        except Exception:                                   # noqa: BLE001
            continue
        try:
            for table, column in (("claim", "ciphertext"), ("node", "attributes"),
                                  ("tombstone", "attributes")):
                try:
                    top = con.execute("select max(rowid) from %s" % table).fetchone()[0]
                except sqlite3.Error:
                    continue
                if not top:
                    continue
                found = 0
                for start in (0, int(top) // 2, max(0, int(top) - 300)):
                    found += con.execute((
                        "select count(*) from (select %s v from %s where rowid > ? "
                        "order by rowid limit 300) where json_valid(cast(v as text))")
                        % (column, table), (start,)).fetchone()[0] or 0
                if found:
                    leaks.append("%s:%s" % (path.relative_to(ROOT), table))
        finally:
            con.close()
    add("No plaintext memory at rest", "fail" if leaks else "pass",
        ("readable rows in " + ", ".join(leaks)) if leaks else
        "sampled claim/node/tombstone rows in every SQLite file under data/ are sealed")

    return checks


def _retired_beliefs(limit=20):
    projected = _daemon_request("map", query="", include_retired=True,
                                limit=160)
    if projected and projected.get("ok"):
        retired = [node for node in projected.get("nodes", [])
                   if node.get("retired")]
        retired.sort(key=lambda node: -int(node.get("retired_t_ms") or 0))
        return [{"id": node.get("id", ""),
                 "kind": node.get("kind", "belief"),
                 "label": node.get("label") or str(node.get("id", ""))[:8],
                 "retired_t_ms": node.get("retired_t_ms"),
                 "source_episode_ids": []}
                for node in retired[:limit]]
    rows = _rows(
        GRAPH,
        "select id, kind, retired_t_ms, attributes from tombstone "
        "order by retired_t_ms desc limit ?", (limit,))
    if rows is None:
        return []
    out = []
    for node_id, kind, retired_t_ms, raw in rows:
        label = node_id[:8]
        sources = []
        try:
            attrs = json.loads(raw)
            if attrs:
                best = max(attrs, key=lambda a: (a.get("grade", 0), a.get("t_ms", 0)))
                label = str(best.get("value") or label)
                sources = list(best.get("source_episode_ids") or [])
        except Exception:
            pass
        out.append({"id": node_id, "kind": kind, "label": label,
                    "retired_t_ms": retired_t_ms, "source_episode_ids": sources})
    return out


# WHAT A SLOW MEASUREMENT COSTS. The panel refreshes every 3 seconds; a
# subprocess or a git read per refresh would make the cockpit the busiest
# thing on the machine. Anything that is not a scalar SQL read is computed at
# most this often and served from the cache in between.
_SLOW_CACHE = {"at": 0.0, "value": {}}
_CHECK_CACHE = {"at": 0.0, "value": []}
SLOW_EVERY_S = 30.0


def _cached_executable_checks():
    now = time.time()
    if now - _CHECK_CACHE["at"] > SLOW_EVERY_S:
        _CHECK_CACHE["value"] = executable_checks()
        _CHECK_CACHE["at"] = now
    return _CHECK_CACHE["value"]


def _sh(*args) -> str:
    import subprocess
    try:
        return subprocess.run(args, capture_output=True, text=True,
                              timeout=3).stdout.strip()
    except Exception:                                        # noqa: BLE001
        return ""


def _daemon_pid():
    """PID of the installed sealed runtime, without matching this probe."""
    result = _sh("ps", "-axo", "pid=,command=")
    markers = (
        "/Applications/TRACE.app/Contents/Resources/runtime/entry.py --daemon",
        "/Applications/TRACE.app/Contents/MacOS/TRACE --daemon",
    )
    for line in result.splitlines():
        pid, _, command = line.strip().partition(" ")
        if pid.isdigit() and any(marker in command for marker in markers):
            return int(pid)
    return None


def _python_tree_digest(root: Path) -> str:
    """One digest of executable Python source, stable across mtimes."""
    import hashlib
    h = hashlib.sha256()
    if not root.is_dir():
        return ""
    for path in sorted(root.rglob("*.py")):
        h.update(str(path.relative_to(root)).encode())
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def _slow_vitals() -> dict:
    """The measurements that need a subprocess or the filesystem.

    THE DAEMON'S AGE AGAINST THE CODE'S. Added 2026-08-28 after finding the
    founder's running daemon four days and forty-five commits behind the
    tree — including the guard that stops an uncited answer being served as
    memory, which was fixed two days earlier and was still fabricating on the
    machine it was fixed for. `./trace status` has said "OLDER THAN THE CODE"
    for a while; nothing that anyone looks at every day did.

    WHETHER THE EXPERIMENT HAS EVER RUN. `evalx/` can return SHED_SUFFICES —
    the verdict that says stop building the node tier — and had been run once,
    on synthetic fixtures, with three gate-eligible questions. A cockpit whose
    top line is a completion percentage, next to an untested central bet, is
    measuring the wrong thing confidently.
    """
    out = {}

    pid = _daemon_pid()
    if pid:
        try:
            installed = (APP / "Contents/Resources/runtime/tracemem")
            out["code it is missing"] = (
                "current tree" if _python_tree_digest(installed) ==
                _python_tree_digest(ROOT / "src/tracemem")
                else "installed runtime differs from working tree")
        except Exception:                                    # noqa: BLE001
            out["code it is missing"] = LIVE_UNKNOWN
    else:
        out["code it is missing"] = "no daemon"

    runs = (sorted(EVAL_RUNS.glob("falsify_*/report.json"))
            if EVAL_RUNS.exists() else [])
    if runs:
        try:
            r = json.loads(runs[-1].read_text())
            out["falsification"] = "%s (n=%s)" % (
                r.get("verdict", "?"), r.get("n_gate_eligible", "?"))
        except Exception:                                    # noqa: BLE001
            out["falsification"] = LIVE_UNKNOWN
    else:
        out["falsification"] = "NEVER RUN"
    return out


def _attributes_are_opaque() -> bool:
    """Is the graph's `attributes` column ciphertext rather than JSON?

    The confidential graph store writes each value with a `g1:` marker, and
    the panel holds no key by design — it reads plaintext columns so it can
    never become a second way to read someone's memory. Detected rather than
    configured: a flag would be one more thing that can be wrong while the
    numbers keep printing.
    """
    row = _count(GRAPH, "select attributes from node "
                        "where attributes is not null limit 1")
    return isinstance(row, str) and row.startswith("g1:")


def _belief_vitals() -> dict:
    """The DERIVED tier, described by what it actually says.

    `nodes/health.sayable_belief_rate` was the first number about belief
    QUALITY rather than shape, and it earns its place — but it scores
    PHRASING: a belief is sayable if it carries no raw schema key. Measured
    2026-08-28 on the real graph, 100 of 190 beliefs were co-occurrence
    triples ("A and B: both repeatedly present with C") which contain no
    schema keys and therefore already score as sayable. A metric that can be
    satisfied by rewording will be satisfied by rewording, so the composition
    is shown beside it: how many beliefs are counting, how many are
    co-occurrence, and how many are something else.
    """
    v = {}
    beliefs = _count(GRAPH, "select count(*) from node where derivation_depth>=1")
    if beliefs is None:
        return v
    v["beliefs held"] = beliefs
    if not beliefs:
        return v

    deep = _count(GRAPH, "select max(derivation_depth) from node")
    if deep is not None:
        # 1 means nothing has ever been derived FROM a derived thing — the
        # cell has combined, but never with a cell it made.
        v["deepest thought"] = deep

    # THE SHAPE OF THE BELIEF TIER, from the columns that are still readable.
    # `kind` and `derivation_depth` are plaintext by design; a hypothesis is a
    # composition or a cause, a pattern is a count. It is a coarser reading
    # than the text gives, and it is one nobody has to hold a key for.
    hyp = _count(GRAPH, "select count(*) from node where kind='hypothesis'")
    pat = _count(GRAPH, "select count(*) from node where kind='pattern'")
    if hyp is not None and pat is not None and (hyp + pat):
        v["counting vs composing"] = "%d / %d" % (pat, hyp)

    if _attributes_are_opaque():
        # A MEASUREMENT THAT CANNOT READ ITS INPUT SAYS SO (2026-08-29).
        #
        # The graph was migrated to confidential storage, so `attributes` is
        # now `g1:<ciphertext>`. These two vitals are SQL LIKE against that
        # column — and a LIKE that matches nothing does not fail, it returns
        # zero. The panel therefore reported `sayable beliefs 100%` and
        # `merely co-occurrence 0%` on a graph it could no longer read: a
        # perfect score, produced by blindness, in a product whose whole
        # discipline is that silence must never look like health. It is the
        # same shape as the cumulative claim count that kept rising through a
        # sixteen-day blackout, and it was in the panel built to catch that.
        #
        # Unknown is the honest reading. The composition is worth restoring
        # properly — the night holds the key and could publish these counts as
        # plain integers when it runs — but a wrong number is worse than none.
        v["sayable beliefs"] = LIVE_UNKNOWN
        v["merely co-occurrence"] = LIVE_UNKNOWN
        return v

    machine = _count(GRAPH,
                     "select count(*) from node where derivation_depth>=1 and ("
                     "attributes like '%recurring:%' or attributes like '%page=%'"
                     " or attributes like '%site=%' or attributes like '%app=%')")
    cooc = _count(GRAPH, "select count(*) from node where derivation_depth>=1 "
                         "and attributes like '%repeatedly present with%'")
    if machine is not None:
        v["sayable beliefs"] = "%d%%" % round(100 * (beliefs - machine) / beliefs)
    if cooc is not None:
        # 100% co-occurrence means the night is composing pairs and deriving
        # nothing.
        v["merely co-occurrence"] = "%d%%" % round(100 * cooc / beliefs)
    return v


def live_vitals():
    """What is TRUE on disk right now — never hand-typed, never stale."""
    v = {}
    claims = _count(STORE, "select count(*) from claim")
    nodes = _count(GRAPH, "select count(*) from node")
    edges = _count(GRAPH, "select count(*) from edge")
    tombs = _count(GRAPH, "select count(*) from tombstone")

    v["claims stored"] = f"{claims:,}" if claims is not None else LIVE_UNKNOWN

    # ARE WE ACTUALLY MOVING? The founder asked, and the panel could not
    # answer — "claims stored" kept climbing while the eye was blind, because
    # the daemon was diligently recording its OWN failure. 51 claims in an
    # hour, every one a witness to not seeing anything. A number that rises
    # when nothing is being perceived is a number that reads as health, and
    # this product does not get to have one of those.
    #
    # `bidder` is a plaintext column, so this works on an encrypted store with
    # no key: 'system:witness' is the daemon writing about itself, anything
    # else is a real perception.
    hour_ago = int((time.time() - 3600) * 1000)
    seen = _count(STORE,
                  "select count(*) from claim where t_ms > ? "
                  "and bidder <> 'system:witness'", (hour_ago,))
    v["seen last hour"] = seen if seen is not None else LIVE_UNKNOWN
    # WATCHING means frames are landing, not that a process is up. A daemon
    # that is running and blind is the failure this line exists to expose.
    #
    # AND FOR HOW LONG (2026-08-28). "BLIND" alone was true on the day the eye
    # stopped and equally true 49 hours later, which is the difference between
    # a hiccup and the second multi-day blackout this product has had. The eye
    # was dark for sixteen days once and the panel said the same word
    # throughout.
    last_seen = _count(STORE, "select max(t_ms) from claim "
                              "where bidder <> 'system:witness'")
    if seen is None:
        v["the eye"] = LIVE_UNKNOWN
    elif seen > 0:
        v["the eye"] = "WATCHING"
    elif last_seen:
        mins = int((time.time() - last_seen / 1000) // 60)
        v["the eye"] = ("BLIND %dm" % mins if mins < 120 else
                        "BLIND %.1fd" % (mins / 1440.0))
    else:
        v["the eye"] = "BLIND"

    # HOW MANY SENSES ARE ACTUALLY FEEDING IT. The napkin draws Eyes AND Ears,
    # and the amendments add a phone pillar with a camera. `modality` is a
    # plaintext column, so this is a count of what the store has really been
    # given — not a count of what has been written.
    con_kinds = _count(STORE, "select count(distinct modality) from claim")
    if con_kinds is not None:
        v["senses feeding it"] = "%d of 4" % con_kinds

    v["nodes in graph"] = f"{nodes:,}" if nodes is not None else LIVE_UNKNOWN
    v["edges in graph"] = f"{edges:,}" if edges is not None else LIVE_UNKNOWN
    # The collapse number. 2026-07-29: a night on real data produced 340
    # edges/node — 87% of a complete graph, which ranks nothing. This is the
    # one figure that says whether the memory tier is healthy or drowning.
    if nodes:
        v["edges per node"] = round(edges / nodes, 1) if edges is not None else LIVE_UNKNOWN
    else:
        v["edges per node"] = LIVE_UNKNOWN
    v["changed its mind"] = tombs if tombs is not None else LIVE_UNKNOWN

    v.update(_belief_vitals())

    # YOUR OWN ANSWERS, COUNTED. F-GRADE makes a founder answer the highest
    # evidence in the store and the ONLY instrument that can measure the
    # memory tier ("a single-frame ceiling structurally cannot"). The whole
    # path is built — gradeable digest lines, `./trace grade`, perceiver
    # `founder` at VERBATIM — and on 2026-08-28 the store held zero of them.
    # A capability nobody has used once is not a capability yet.
    grades = _count(STORE, "select count(*) from claim where bidder='founder'")
    v["your grades"] = grades if grades is not None else LIVE_UNKNOWN

    # WHAT KEEPING RAW FOR EVER ACTUALLY COSTS, measured rather than quoted.
    # F-RAW justifies departing from biology with "~1.6 MB/day, 0.57 GB/year".
    # That was measured on 2026-07-29 over 4,987 claims — about 830 a day,
    # before the eye rebuild. On days the eye is really open this store takes
    # ~45,000, so the real figure is over an order of magnitude larger. The
    # decision may well still be right; the number it rests on has to be true.
    days = _count(STORE,
                  "select count(*) from (select date(t_ms/1000,'unixepoch',"
                  "'localtime') d from claim where bidder <> 'system:witness' "
                  "group by d having count(*) > 1000)")
    if days and claims and STORE.exists():
        per_day = claims / float(days)
        bytes_per_claim = STORE.stat().st_size / float(claims)
        v["claims per open day"] = f"{int(per_day):,}"
        v["a year of watching"] = "%.1f GB" % (
            per_day * bytes_per_claim * 365 / 1e9)

    # STORE MTIME IS NOT PROCESS LIVENESS. A healthy ambient ear hearing
    # silence may append nothing for hours, while a stale socket can outlive
    # a kill. Inspect the exact signed helper/runtime command instead; sight
    # remains a separate vital immediately above.
    if _daemon_pid():
        v["daemon"] = "up"
    elif SOCK.exists():
        v["daemon"] = "STALE SOCKET"
    else:
        v["daemon"] = "DARK"

    now = time.time()
    if now - _SLOW_CACHE["at"] > SLOW_EVERY_S:
        _SLOW_CACHE["value"] = _slow_vitals()
        _SLOW_CACHE["at"] = now
    v.update(_SLOW_CACHE["value"])
    return v


HTML = r"""<!doctype html>
<html><head><meta charset="utf-8"><title>TRACE cockpit</title>
<style>
 body{background:#0d1117;color:#e6edf3;font:15px -apple-system,sans-serif;
      margin:0;padding:26px;max-width:1060px;margin:auto}
 h1{font-size:22px;margin:0 0 2px} .sub{color:#8b949e;font-size:12px}
 .bigwrap{margin:18px 0 6px}
 .bigbar{height:24px;background:#21262d;border-radius:12px;overflow:hidden}
 .bigfill{height:100%;background:linear-gradient(90deg,#1f6feb,#2ea043);transition:width .6s}
 .bignum{font-size:32px;font-weight:700;margin-right:8px}
 .nums{display:flex;gap:26px;margin:14px 0 18px;flex-wrap:wrap}
 .num b{font-size:24px;display:block}
 .num span{color:#8b949e;font-size:11px}
 .vitals{background:#0b1a12;border:1px solid #1c3d29;border-radius:8px;
         padding:12px 14px;margin:16px 0 6px}
 .vitals .cap{color:#3fb950;font-size:10px;letter-spacing:.08em;
              text-transform:uppercase;margin-bottom:8px}
 .vitals .row{display:flex;gap:26px;flex-wrap:wrap}
 .vital b{font-size:22px;display:block;color:#3fb950}
 .vital span{color:#6e8b78;font-size:11px}
 .vital b.dark{color:#f85149}
 .vital b.warn{color:#d29922}
 .checks{margin:12px 0 20px;display:grid;grid-template-columns:1fr 1fr;gap:8px}
 .check{background:#161b22;border:1px solid #30363d;border-radius:7px;padding:9px 11px}
 .check.pass{border-color:#238636}.check.fail{border-color:#da3633}.check.unknown{border-color:#9e6a03}
 .check b{display:block;font-size:13px}.check span{color:#8b949e;font-size:11px}
 .retired{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:12px 14px;margin:14px 0}
 .retired .belief{padding:5px 0;border-bottom:1px solid #21262d}
 .retired .belief:last-child{border-bottom:0}.retired small{color:#8b949e}
 .handtyped{color:#8b949e;font-size:10px;letter-spacing:.08em;
            text-transform:uppercase;margin:14px 0 -6px}
 .now{background:#161b22;border:1px solid #21262d;border-radius:8px;padding:10px 14px;margin:0 0 18px}
 .now div{margin:2px 0;font-size:13px}
 .now div::before{content:"\25B8 ";color:#1f6feb}
 .node{margin:2px 0}
 .hdr{display:flex;align-items:center;gap:8px;cursor:pointer;padding:5px 6px;border-radius:6px}
 .hdr:hover{background:#161b22}
 .hdr .car{width:12px;color:#8b949e;font-size:11px;flex-shrink:0}
 .hdr .nm{flex:0 0 auto;max-width:46%}
 .hdr .plain{color:#6e7681;font-size:12px;flex:1;overflow:hidden;
             text-overflow:ellipsis;white-space:nowrap}
 .hdr .st{font-size:11px;flex-shrink:0}
 .st.real{color:#3fb950}.st.fake{color:#4493f8}.st.design{color:#d29922}
 .st.open{color:#f0883e}.st.not{color:#8b949e}.st.wait{color:#f85149}
 .hdr .pc{width:38px;text-align:right;font-size:12px;color:#8b949e;flex-shrink:0}
 .hdr .bar{width:150px;height:8px;background:#21262d;border-radius:4px;
           overflow:hidden;flex-shrink:0}
 .hdr .fill{height:100%;background:#1f6feb;transition:width .6s}
 .fill.hi{background:#2ea043}.fill.lo{background:#6e40c9}
 .kids{margin-left:22px;border-left:1px solid #21262d;padding-left:8px}
 .leafmeta{margin:1px 0 6px 40px;font-size:12px}
 .files{color:#58606a;font-family:ui-monospace,monospace;font-size:11px}
 .openq{color:#f0883e;margin-top:2px}
 .openq::before{content:"OPEN \2014 ";font-weight:600}
 .do{color:#4493f8;margin-top:2px}
 .do::before{content:"DO \2014 ";font-weight:600}
 .proof{color:#3fb950;margin-top:2px}
 .proof::before{content:"PROOF \2014 ";font-weight:600}
 .after{color:#8b949e;margin-top:2px}
 .after::before{content:"AFTER \2014 ";font-weight:600}
 .lvl0>.hdr .nm{font-weight:700;font-size:16px}
 footer{color:#484f58;font-size:11px;margin-top:24px}
</style></head><body>
<h1>TRACE</h1><div class="sub" id="upd"></div>
<div class="bigwrap"><span class="bignum" id="pct"></span>of executable napkin invariants passing
 <div class="bigbar"><div class="bigfill" id="pctbar"></div></div></div>
<div class="checks" id="checks"></div>
<div class="vitals"><div class="cap">Live — read off the real files every 3 seconds</div>
 <div class="row" id="vitals"></div></div>
<div class="retired"><div class="cap">Retired beliefs — the graph's changed mind</div>
 <div id="retired"></div></div>
<div class="handtyped">Planning map — shown for navigation, never used as progress</div>
<div id="tree"></div>
<footer>The top line is computed only from executable checks. Planning bars
and campaign files cannot increase it. OPEN = an unresolved design question.</footer>
<script>
const openSet=new Set(["r0","r1"]);  // top and the eye start open
function pct(n){
 if(n.pct!==undefined)return n.pct;
 const ks=(n.kids||[]).map(pct);
 return ks.length?Math.round(ks.reduce((a,b)=>a+b,0)/ks.length):0;
}
function leaves(n){return n.kids?n.kids.reduce((a,k)=>a+leaves(k),0):1;}
function stcls(s){s=(s||"").toLowerCase();
 if(s.includes("real")||s.includes("alive"))return"real";
 if(s.includes("open"))return"open";
 if(s.includes("design")||s.includes("building")||s.includes("stub"))return"design";
 if(s.includes("wait")||s.includes("unproven"))return"wait";
 if(s.includes("not started")||s.includes("after"))return"not";
 return"fake";}
function esc(t){return (t||"").replace(/&/g,"&amp;").replace(/</g,"&lt;");}
function render(n,id,depth){
 const p=pct(n), open=openSet.has(id), kids=n.kids||[];
 const openCount=countOpen(n);
 let h='<div class="node lvl'+depth+'"><div class="hdr" onclick="tog(\''+id+'\')">';
 h+='<span class="car">'+(kids.length?(open?'▼':'▶'):(n.open&&n.open.length?'○':'·'))+'</span>';
 h+='<span class="nm">'+esc(n.name)+'</span>';
 h+='<span class="plain">'+esc(n.plain||'')+(openCount&&kids.length?' · '+openCount+' open':'')+'</span>';
 h+='<span class="st '+stcls(n.state)+'">'+esc(n.state||'')+'</span>';
 h+='<span class="pc">'+p+'%</span>';
 h+='<div class="bar"><div class="fill'+(p>=70?' hi':(p<15?' lo':''))+'" style="width:'+p+'%"></div></div>';
 h+='</div>';
 if(open){
  if(!kids.length&&(n.files||n.open||n.brief||n.oracle||n.needs)){
   h+='<div style="margin:4px 0"><a href="/dispatch?atom='+encodeURIComponent(n.name)+'" target="_blank" style="color:#58a6ff;font-size:11px">\u2192 dispatch this atom</a></div>';
   h+='<div class="leafmeta">';
   if(n.files)h+='<div class="files">'+n.files.map(esc).join(' · ')+'</div>';
   if(n.brief)h+='<div class="do">'+esc(n.brief)+'</div>';
   if(n.oracle)h+='<div class="proof">'+esc(n.oracle)+'</div>';
   if(n.needs)h+='<div class="after">'+n.needs.map(esc).join(' · ')+'</div>';
   (n.open||[]).forEach(q=>{h+='<div class="openq">'+esc(q)+'</div>';});
   h+='</div>';
  }
  if(kids.length){h+='<div class="kids">'+
   kids.map((k,i)=>render(k,id+'.'+i,depth+1)).join('')+'</div>';}
 }
 return h+'</div>';
}
function countOpen(n){
 let c=(n.open||[]).length;(n.kids||[]).forEach(k=>c+=countOpen(k));return c;}
let TREE=null;
function tog(id){openSet.has(id)?openSet.delete(id):openSet.add(id);draw();}
function draw(){
 if(!TREE)return;
 document.getElementById('tree').innerHTML=
  TREE.kids.map((k,i)=>render(k,'r'+i,0)).join('');
}
// A vital is only worth reading if the bad value LOOKS bad. These are the
// exact readings that were true while the panel looked calm: an eye dark for
// days, a daemon behind the code that fixed it, an experiment never run, and
// a grading loop nobody has used once.
function alarm(k,v){
 const s=(''+v);
 if(s==='DARK'||s.startsWith('BLIND')||s==='NEVER RUN')return ' class="dark"';
 if(k==='your grades'&&s==='0')return ' class="dark"';
 if(k==='code it is missing'&&/commit/.test(s))return ' class="dark"';
 if(k==='deepest thought'&&s==='1')return ' class="warn"';
 if(k==='senses feeding it'&&s.startsWith('1 '))return ' class="warn"';
 if(k==='merely co-occurrence'&&parseInt(s)>=50)return ' class="warn"';
 if(s.startsWith('quiet'))return ' class="warn"';
 return '';
}
async function tick(){
 try{
  const d=await(await fetch('/data')).json();
  TREE=d.tree;
  document.getElementById('upd').textContent='updated '+d.updated+
   ' · '+d.checks.length+' executable invariants · '+countOpen(d.tree)+' open questions';
  const decided=d.checks.filter(c=>c.state!=='unknown');
  const passed=decided.filter(c=>c.state==='pass').length;
  const total=decided.length?Math.round(100*passed/decided.length):0;
  document.getElementById('pct').textContent=total+'%';
  document.getElementById('pctbar').style.width=total+'%';
  document.getElementById('checks').innerHTML=d.checks.map(c=>
   '<div class="check '+c.state+'"><b>'+esc(c.state.toUpperCase()+' · '+c.name)+
   '</b><span>'+esc(c.detail)+'</span></div>').join('');
  document.getElementById('vitals').innerHTML=Object.entries(d.live).map(
   ([k,v])=>'<div class="vital"><b'+alarm(k,v)+'>'+esc(''+v)+
   '</b><span>'+esc(k)+'</span></div>').join('');
  document.getElementById('retired').innerHTML=d.retired.length?d.retired.map(r=>
   '<div class="belief"><b>'+esc(r.label)+'</b> <small>'+esc(r.kind)+' · '+
   new Date(r.retired_t_ms).toLocaleString()+' · '+esc(r.id.slice(0,8))+'</small></div>'
   ).join(''):'<small>No retired belief rows are readable.</small>';
  draw();
 }catch(e){}
}
tick(); setInterval(tick,3000);
</script></body></html>"""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/":
            self._send(200, HTML.encode(), "text/html")
        elif self.path == "/data":
            try:
                if TREEDIR.is_dir():
                    # One file per napkin box; workers deepen their own section.
                    tree = {"kids": [
                        json.loads(f.read_text())
                        for f in sorted(TREEDIR.glob("*.json"))]}
                else:
                    tree = json.loads(TREE.read_text())
            except Exception:
                tree = {"kids": []}
            d = {
                "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
                "live": live_vitals(),
                "checks": _cached_executable_checks(),
                "retired": _retired_beliefs(),
                "tree": tree,
            }
            self._send(200, json.dumps(d).encode(), "application/json")
        elif self.path.startswith("/dispatch"):
            # Dispatch button (ruling aa): hand any atom's full boot prompt
            # to a fresh zero-context session. Plain text so it is one
            # select-all away from a paste.
            from urllib.parse import parse_qs, urlparse
            q = parse_qs(urlparse(self.path).query)
            name = (q.get("atom") or [""])[0]
            try:
                # `python tools/cockpit.py` puts tools/ on sys.path, not the
                # repo root — so import the sibling module directly. (The
                # suite passed on `tools.dispatch_prompt` because conftest.py
                # puts the root on the path; the live server did not.)
                import sys as _sys
                if str(ROOT) not in _sys.path:
                    _sys.path.insert(0, str(ROOT))
                from dispatch_prompt import build_prompt
                body = build_prompt(name).encode()
                self._send(200, body, "text/plain; charset=utf-8")
            except ImportError:
                # The dispatch tool lives in the maintainer's local workspace,
                # not in the published product.
                self._send(404, b"dispatch is not part of this install",
                           "text/plain; charset=utf-8")
            except KeyError as exc:
                # AmbiguousAtom is a KeyError subclass: its message lists the
                # candidates with their paths, which is what the human needs.
                self._send(404, str(exc).encode(), "text/plain; charset=utf-8")
            except Exception as exc:
                self._send(500, repr(exc).encode(), "text/plain")
        else:
            self._send(404, b"", "text/plain")


def main():
    print(f"Cockpit: http://localhost:{PORT}")
    try:
        HTTPServer(("127.0.0.1", PORT), H).serve_forever()
    except OSError:
        print(f"Port {PORT} already in use — cockpit is probably already running.")


if __name__ == "__main__":
    main()
