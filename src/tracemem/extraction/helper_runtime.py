"""Sandboxed third-party helper discovery and execution.

Helpers are executables, never imported Python.  A helper receives one JSON
document on stdin and may return only the bounded claims-v1 JSON shape on
stdout.  The parent owns coordinates, provenance, grades, and persistence;
the child can suggest text and structured detail but cannot mint a Claim.

Trust has two explicit forms:
* ``bundle``: a digest-pinned executable inside TRACE's sealed app bundle;
* ``developer-id``: a digest-pinned, validly signed external executable whose
  TeamIdentifier matches the manifest.

On macOS every invocation also crosses sandbox-exec.  System libraries remain
readable so a native process can start, while user homes, mounted volumes,
network access, child processes, and file writes are denied.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional, Tuple

API_VERSION = 1
OUTPUT_SCHEMA = "trace.helper.claims.v1"
MAX_INPUT_BYTES = 64 * 1024
MAX_OUTPUT_BYTES = 256 * 1024
MAX_CLAIMS = 16
ID_RE = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)+$")
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


class HelperError(RuntimeError):
    pass


class InvalidManifest(HelperError):
    pass


class UntrustedHelper(HelperError):
    pass


@dataclass(frozen=True)
class HelperManifest:
    id: str
    version: str
    executable: Path
    trust: str
    team_id: Optional[str]
    resource: str
    timeout_ms: int
    priority: float
    apps: Tuple[str, ...]
    contains: Tuple[str, ...]
    sha256: str

    @property
    def producer(self) -> str:
        return "%s@%s" % (self.id, self.version)

    def matches(self, text: str, app: str) -> bool:
        app = (app or "").lower()
        body = (text or "").lower()
        return (not self.apps and not self.contains) or any(
            needle in app for needle in self.apps) or any(
            needle in body for needle in self.contains)


@dataclass(frozen=True)
class HelperReading:
    helper_id: str
    version: str
    text: str
    confidence: float
    structured: dict
    duration_s: float
    priority: float


@dataclass(frozen=True)
class HelperRun:
    readings: Tuple[HelperReading, ...] = ()
    failures: Tuple[str, ...] = ()
    deferred: Tuple[str, ...] = ()


def _clean_strings(value, field):
    if not isinstance(value, list) or not all(
            isinstance(item, str) and item.strip() for item in value):
        raise InvalidManifest("%s must be a list of non-empty strings" % field)
    return tuple(item.lower() for item in value)


def load_manifest(path: Path) -> HelperManifest:
    try:
        raw = json.loads(path.read_text())
    except Exception as exc:
        raise InvalidManifest("cannot parse %s: %s" % (path, exc)) from exc
    required = {"api", "id", "version", "executable", "trust",
                "resource", "timeout_ms", "priority", "triggers",
                "permissions", "sha256", "team_id"}
    if set(raw) != required:
        raise InvalidManifest("manifest fields differ: missing=%s extra=%s" % (
            sorted(required - set(raw)), sorted(set(raw) - required)))
    if raw["api"] != API_VERSION:
        raise InvalidManifest("unsupported helper API %r" % raw["api"])
    if not isinstance(raw["id"], str) or not ID_RE.fullmatch(raw["id"]):
        raise InvalidManifest("invalid reverse-DNS helper id")
    if not isinstance(raw["version"], str) or not VERSION_RE.fullmatch(raw["version"]):
        raise InvalidManifest("helper version must be MAJOR.MINOR.PATCH")
    if raw["resource"] not in ("cpu", "gpu"):
        raise InvalidManifest("resource must be cpu or gpu")
    if raw["trust"] not in ("bundle", "developer-id"):
        raise InvalidManifest("trust must be bundle or developer-id")
    permissions = raw["permissions"]
    if permissions != []:
        raise InvalidManifest(
            "helper API v1 grants no filesystem, network, or device permissions")
    timeout = raw["timeout_ms"]
    if not isinstance(timeout, int) or not 50 <= timeout <= 5_000:
        raise InvalidManifest("timeout_ms must be within 50–5000")
    if not isinstance(raw["priority"], (int, float)):
        raise InvalidManifest("priority must be numeric")
    triggers = raw["triggers"]
    if not isinstance(triggers, dict) or set(triggers) != {"apps", "contains"}:
        raise InvalidManifest("triggers must contain exactly apps and contains")
    executable_name = raw["executable"]
    if (not isinstance(executable_name, str) or not executable_name or
            Path(executable_name).name != executable_name):
        raise InvalidManifest("executable must be one filename")
    digest = raw["sha256"]
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise InvalidManifest("sha256 must be a lowercase 64-character digest")
    trust = raw["trust"]
    team_id = raw["team_id"]
    if trust == "bundle" and team_id is not None:
        raise InvalidManifest("bundled helper team_id must be null")
    if trust == "developer-id" and (not isinstance(team_id, str) or
                                     not re.fullmatch(r"[A-Z0-9]{10}", team_id)):
        raise InvalidManifest("developer-id helper needs a 10-character team_id")
    return HelperManifest(
        id=raw["id"], version=raw["version"],
        executable=(path.parent / executable_name).resolve(), trust=trust,
        team_id=team_id, resource=raw["resource"], timeout_ms=timeout,
        priority=float(raw["priority"]),
        apps=_clean_strings(triggers["apps"], "triggers.apps"),
        contains=_clean_strings(triggers["contains"], "triggers.contains"),
        sha256=digest)


def _codesign_details(path: Path):
    verified = subprocess.run(
        ["/usr/bin/codesign", "--verify", "--strict", str(path)],
        capture_output=True, timeout=10)
    detail = subprocess.run(
        ["/usr/bin/codesign", "-dv", "--verbose=4", str(path)],
        capture_output=True, text=True, timeout=10).stderr
    return verified.returncode == 0, detail


def verify_manifest(manifest: HelperManifest, root: Path,
                    allow_unsealed: bool = False) -> None:
    if not manifest.executable.is_file() or not os.access(manifest.executable, os.X_OK):
        raise UntrustedHelper("helper executable is missing or not executable")
    try:
        manifest.executable.relative_to(root.resolve())
    except ValueError as exc:
        raise UntrustedHelper("helper executable escapes its package root") from exc
    actual = hashlib.sha256(manifest.executable.read_bytes()).hexdigest()
    if actual != manifest.sha256:
        raise UntrustedHelper("helper executable digest differs from manifest")
    if allow_unsealed:
        return
    if manifest.trust == "bundle":
        parts = manifest.executable.parts
        try:
            app_at = next(i for i, part in enumerate(parts) if part.endswith(".app"))
        except StopIteration as exc:
            raise UntrustedHelper("bundled helper is not inside an app") from exc
        app = Path(*parts[:app_at + 1])
        ok, _detail = _codesign_details(app)
        if not ok:
            raise UntrustedHelper("containing app seal is invalid")
    else:
        ok, detail = _codesign_details(manifest.executable)
        if not ok or not manifest.team_id:
            raise UntrustedHelper("external helper lacks a valid declared identity")
        if "TeamIdentifier=%s" % manifest.team_id not in detail:
            raise UntrustedHelper("external helper TeamIdentifier mismatch")


# Where a user's files live outside their home folder. /Users and /Volumes
# were the original list; the temp roots were added 2026-10-15 after a fresh
# clone under /private/tmp showed a helper reading a planted secret there.
# /private/var/folders holds every app's per-user temp and cache files.
_USER_DATA_ROOTS = ("/Users", "/Volumes", "/private/var/folders",
                    "/private/tmp", "/private/var/tmp")


def _sandbox_profile(executable: Path) -> str:
    resolved = executable.resolve()
    target = str(resolved).replace('"', '\\"')
    package = str(resolved.parent).replace('"', '\\"')
    return " ".join([
        "(version 1)", "(deny default)",
        '(allow process-exec (literal "%s"))' % target,
        "(allow sysctl-read)", "(allow file-read*)",
        "(allow file-map-executable)",
    ] + ['(deny file-read* (subpath "%s"))' % root for root in _USER_DATA_ROOTS] + [
        # The helper may read its own package (its binary and bundled data,
        # e.g. an engine's weights) wherever it is installed. Later rules
        # win in SBPL, so this re-opens exactly that one folder.
        '(allow file-read* (subpath "%s"))' % package,
        "(deny file-write*)", "(deny network*)",
    ])


def _command(manifest: HelperManifest):
    if sys.platform != "darwin":
        raise HelperError("helpers require the enforced macOS sandbox")
    return ["/usr/bin/sandbox-exec", "-p",
            _sandbox_profile(manifest.executable), str(manifest.executable)]


class HelperRuntime:
    _gpu = threading.Lock()

    def __init__(self, manifests: Iterable[HelperManifest], *,
                 runner: Optional[Callable] = None) -> None:
        self.manifests = tuple(manifests)
        self._runner = runner or self._invoke
        self.calls = 0
        self.failures = 0
        self.deferred = 0

    @classmethod
    def discover(cls, root, *, allow_unsealed: bool = False, runner=None):
        root = Path(root).resolve()
        manifests = []
        if not root.is_dir():
            return cls((), runner=runner)
        ids = set()
        for path in sorted(root.glob("*/manifest.json")):
            manifest = load_manifest(path)
            if manifest.id in ids:
                raise InvalidManifest("duplicate helper id %s" % manifest.id)
            verify_manifest(manifest, path.parent,
                            allow_unsealed=allow_unsealed)
            manifests.append(manifest)
            ids.add(manifest.id)
        return cls(manifests, runner=runner)

    def run(self, text: str, app: str, context: dict,
            only: Optional[Iterable[str]] = None) -> HelperRun:
        wire = json.dumps({"api": API_VERSION, "text": text, "app": app,
                           "context": context}, sort_keys=True).encode()
        if len(wire) > MAX_INPUT_BYTES:
            return HelperRun(failures=("helper input exceeds %d bytes" %
                                       MAX_INPUT_BYTES,))
        wanted = set(only) if only is not None else None
        matched = [m for m in self.manifests if m.matches(text, app)
                   and (wanted is None or m.producer in wanted)]
        cpu = [m for m in matched if m.resource == "cpu"]
        gpu = sorted((m for m in matched if m.resource == "gpu"),
                     key=lambda m: (-m.priority, m.id))[:1]
        deferred = tuple(m.producer for m in matched
                         if m.resource == "gpu" and m not in gpu)
        acquired = False
        if gpu:
            acquired = self._gpu.acquire(blocking=False)
            if not acquired:
                deferred += tuple(m.producer for m in gpu)
                gpu = []
        readings = []
        failures = []
        selected = cpu + gpu
        try:
            if selected:
                with ThreadPoolExecutor(max_workers=len(selected),
                                        thread_name_prefix="trace-helper") as pool:
                    futures = {pool.submit(self._runner, m, wire): m
                               for m in selected}
                    for future in as_completed(futures):
                        manifest = futures[future]
                        try:
                            readings.extend(future.result())
                        except Exception as exc:              # noqa: BLE001
                            failures.append("%s: %s" %
                                            (manifest.producer, exc))
        finally:
            if acquired:
                self._gpu.release()
        self.calls += len(selected)
        self.failures += len(failures)
        self.deferred += len(deferred)
        return HelperRun(tuple(readings), tuple(failures), tuple(deferred))

    @staticmethod
    def _invoke(manifest: HelperManifest, wire: bytes):
        started = time.monotonic()
        try:
            result = subprocess.run(
                _command(manifest), input=wire, capture_output=True,
                timeout=manifest.timeout_ms / 1000.0,
                env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent",
                     "TMPDIR": "/nonexistent", "LC_CTYPE": "UTF-8"})
        except subprocess.TimeoutExpired as exc:
            raise HelperError("timed out after %dms" % manifest.timeout_ms) from exc
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", "replace").strip()[:240]
            raise HelperError(detail or "sandboxed helper exited %d" %
                              result.returncode)
        if len(result.stdout) > MAX_OUTPUT_BYTES:
            raise HelperError("output exceeds %d bytes" % MAX_OUTPUT_BYTES)
        try:
            out = json.loads(result.stdout)
        except Exception as exc:
            raise HelperError("output is not one JSON document") from exc
        if not isinstance(out, dict) or set(out) != {"schema", "claims"}:
            raise HelperError("output must contain exactly schema and claims")
        if out["schema"] != OUTPUT_SCHEMA or not isinstance(out["claims"], list):
            raise HelperError("unsupported helper output schema")
        if len(out["claims"]) > MAX_CLAIMS:
            raise HelperError("helper emitted more than %d claims" % MAX_CLAIMS)
        elapsed = max(0.0, time.monotonic() - started)
        readings = []
        for item in out["claims"]:
            if not isinstance(item, dict) or not {"text", "confidence"} <= set(item):
                raise HelperError("each helper claim needs text and confidence")
            if set(item) - {"text", "confidence", "structured"}:
                raise HelperError("helper claim contains undeclared fields")
            text = item["text"]
            confidence = item["confidence"]
            structured = item.get("structured", {})
            if (not isinstance(text, str) or not text.strip() or
                    not isinstance(confidence, (int, float)) or
                    not 0 <= confidence <= 1 or not isinstance(structured, dict)):
                raise HelperError("invalid helper claim value")
            readings.append(HelperReading(
                manifest.id, manifest.version, text.strip(),
                float(confidence), structured, elapsed, manifest.priority))
        return readings

    def health(self):
        return {"discovered": len(self.manifests), "calls": self.calls,
                "failures": self.failures, "deferred": self.deferred}
