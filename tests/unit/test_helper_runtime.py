"""Black-box contract for discovered, sandboxed helper executables."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tracemem.extraction.helper_runtime import (
    HelperManifest,
    HelperReading,
    HelperRuntime,
    InvalidManifest,
    UntrustedHelper,
)

ROOT = Path(__file__).resolve().parents[2]


# 3s, not the shipped 750ms: these tests run a binary compiled seconds ago,
# and its FIRST launch includes macOS assessing a brand-new executable. Under
# a loaded suite that alone exceeded 750ms and failed behaviour tests at
# random (2026-10-15). Latency has its own test (timeout_ms=50 below).
def build_swift_package(tmp_path, source, *, helper_id="com.test.helper",
                        resource="cpu", timeout_ms=3000, priority=1.0,
                        apps=(), contains=()):
    package = tmp_path / helper_id
    package.mkdir()
    src = package / "Helper.swift"
    src.write_text(source)
    exe = package / "helper"
    subprocess.run(["xcrun", "swiftc", str(src), "-o", str(exe),
                    "-framework", "Foundation"], check=True,
                   capture_output=True)
    manifest = {
        "api": 1, "id": helper_id, "version": "1.0.0",
        "executable": "helper", "trust": "bundle", "team_id": None,
        "resource": resource, "timeout_ms": timeout_ms,
        "priority": priority,
        "triggers": {"apps": list(apps), "contains": list(contains)},
        "permissions": [],
        "sha256": hashlib.sha256(exe.read_bytes()).hexdigest(),
    }
    (package / "manifest.json").write_text(json.dumps(manifest))
    return package


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS sandbox oracle")
def test_reference_helper_is_discovered_sandboxed_and_returns_bounded_schema(tmp_path):
    source = (ROOT / "plugins/reference_code/Helper.swift").read_text()
    build_swift_package(tmp_path, source,
                        helper_id="com.trace.reference.code-symbols",
                        contains=("class ", "def "))
    runtime = HelperRuntime.discover(tmp_path, allow_unsealed=True)

    report = runtime.run("class MemoryCell def bind", "editor", {"t_ms": 1})

    assert report.failures == () and report.deferred == ()
    assert len(report.readings) == 1
    reading = report.readings[0]
    assert reading.helper_id == "com.trace.reference.code-symbols"
    assert reading.version == "1.0.0"
    assert reading.text == "Code symbols: MemoryCell, bind"
    assert reading.structured["symbols"] == ["MemoryCell", "bind"]


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS sandbox oracle")
@pytest.mark.parametrize("where", ["checkout", "system-temp", "private-tmp"])
def test_sandbox_denies_a_helper_access_to_the_users_files(tmp_path, where):
    """FOUND 2026-10-15 by running the suite from a fresh clone under
    /private/tmp: the profile denied /Users and /Volumes and allowed every
    other read, so a third-party helper could read the user's temp folders
    (/private/var/folders/.../T, /private/tmp) and hand the contents back as
    claims. The probe used to live only in the checkout, so the test proved
    the property only when the repo happened to sit under /Users."""
    import tempfile
    base = {"checkout": ROOT, "system-temp": Path(tempfile.gettempdir()),
            "private-tmp": Path("/private/tmp")}[where]
    secret = base / (".helper-sandbox-secret-%d" % os.getpid())
    # The probe path is a test artifact, not user data; cleanup is explicit.
    secret.write_text("MUST_NOT_ESCAPE")
    try:
        source = r'''
import Foundation
let d = FileHandle.standardInput.readDataToEndOfFile()
let o = try! JSONSerialization.jsonObject(with: d) as! [String: Any]
let c = o["context"] as! [String: Any]
let p = c["probe"] as! String
let stolen = (try? String(contentsOfFile: p))
let claims: [[String: Any]] = stolen == nil ? [] : [["text": stolen!, "confidence": 1.0]]
let out: [String: Any] = ["schema": "trace.helper.claims.v1", "claims": claims]
FileHandle.standardOutput.write(try! JSONSerialization.data(withJSONObject: out))
'''
        build_swift_package(tmp_path, source)
        runtime = HelperRuntime.discover(tmp_path, allow_unsealed=True)

        report = runtime.run("anything", "anything", {"probe": str(secret)})

        assert report.readings == ()
        assert "MUST_NOT_ESCAPE" not in repr(report)
    finally:
        secret.unlink(missing_ok=True)


def test_manifest_digest_tampering_is_rejected_before_execution(tmp_path):
    package = tmp_path / "com.test.helper"
    package.mkdir()
    executable = package / "helper"
    executable.write_bytes(b"not the bytes named by the digest")
    executable.chmod(0o755)
    raw = {
        "api": 1, "id": "com.test.helper", "version": "1.0.0",
        "executable": "helper", "trust": "bundle", "team_id": None,
        "resource": "cpu", "timeout_ms": 100, "priority": 1,
        "triggers": {"apps": [], "contains": []}, "permissions": [],
        "sha256": "0" * 64,
    }
    (package / "manifest.json").write_text(json.dumps(raw))

    with pytest.raises(UntrustedHelper, match="digest"):
        HelperRuntime.discover(tmp_path, allow_unsealed=True)


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS sandbox oracle")
def test_timeout_cancels_a_stuck_helper_and_names_the_failure(tmp_path):
    source = r'''
import Foundation
Thread.sleep(forTimeInterval: 2.0)
let out: [String: Any] = ["schema": "trace.helper.claims.v1", "claims": []]
FileHandle.standardOutput.write(try! JSONSerialization.data(withJSONObject: out))
'''
    build_swift_package(tmp_path, source, timeout_ms=50)
    runtime = HelperRuntime.discover(tmp_path, allow_unsealed=True)
    started = time.monotonic()

    report = runtime.run("x", "app", {})

    assert time.monotonic() - started < 1.0
    assert report.readings == ()
    assert len(report.failures) == 1
    assert "timed out after 50ms" in report.failures[0]


def manifest(name, resource="cpu", priority=1.0):
    return HelperManifest(
        id=name, version="1.0.0", executable=Path("/unused"), trust="bundle",
        team_id=None, resource=resource, timeout_ms=500, priority=priority,
        apps=(), contains=(), sha256="0" * 64)


def test_all_cheap_helpers_execute_in_parallel():
    def runner(m, wire):
        time.sleep(.12)
        return [HelperReading(m.id, m.version, m.id, 1.0, {}, .12,
                              m.priority)]

    runtime = HelperRuntime([manifest("com.test.one"),
                             manifest("com.test.two")], runner=runner)
    started = time.monotonic()
    report = runtime.run("x", "app", {})
    elapsed = time.monotonic() - started

    assert {r.helper_id for r in report.readings} == {
        "com.test.one", "com.test.two"}
    assert elapsed < .21


def test_only_highest_priority_heavy_helper_runs_and_other_work_is_named():
    def runner(m, wire):
        return [HelperReading(m.id, m.version, m.id, 1.0, {}, 0.0,
                              m.priority)]

    runtime = HelperRuntime([
        manifest("com.test.low", "gpu", .5),
        manifest("com.test.high", "gpu", 2.0),
    ], runner=runner)
    report = runtime.run("x", "app", {})

    assert [r.helper_id for r in report.readings] == ["com.test.high"]
    assert report.deferred == ("com.test.low@1.0.0",)


def test_manifest_contract_is_closed_and_grants_no_permissions(tmp_path):
    package = tmp_path / "com.test.helper"
    package.mkdir()
    raw = {
        "api": 1, "id": "com.test.helper", "version": "1.0.0",
        "executable": "helper", "trust": "bundle", "team_id": None,
        "resource": "cpu", "timeout_ms": 100, "priority": 1,
        "triggers": {"apps": [], "contains": []},
        "permissions": ["network"], "sha256": "0" * 64,
    }
    (package / "manifest.json").write_text(json.dumps(raw))
    with pytest.raises(InvalidManifest, match="grants no"):
        HelperRuntime.discover(tmp_path, allow_unsealed=True)
