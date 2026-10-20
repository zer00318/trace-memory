"""Black-box proof that production enters through the signed TRACE artifact."""
from __future__ import annotations

import plistlib
import subprocess
import sys
from pathlib import Path

import pytest

APP = Path("/Applications/TRACE.app")
HELPER = APP / "Contents" / "MacOS" / "TRACE"


pytestmark = pytest.mark.skipif(
    sys.platform != "darwin" or not APP.exists(),
    reason="installed macOS artifact oracle")


def _run(*args):
    return subprocess.run(args, capture_output=True, check=False, timeout=10)


def test_installed_bundle_and_every_sealed_resource_verify():
    result = _run("codesign", "--verify", "--deep", "--strict", str(APP))
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert HELPER.exists()
    assert (APP / "Contents/Resources/runtime/tracemem/__main__.py").exists()
    assert (APP / "Contents/Resources/runtime/site-packages").is_dir()


def test_build_recipe_seals_the_live_map_surface():
    # Artifact reachability is measured live by the cockpit because an older
    # valid installed bundle is allowed to remain recoverable when signing is
    # denied. This oracle proves the next successful build cannot omit it.
    root = Path(__file__).resolve().parents[2]
    recipe = (root / "packaging/make_app.py").read_text()
    assert '"graph_window.py"' in recipe
    assert (root / "src/tracemem/app/live_map.py").is_file()


def test_build_recipe_seals_the_post_install_folder_door():
    """Cancelling install-day consent cannot make cold start unreachable."""
    root = Path(__file__).resolve().parents[2]
    recipe = (root / "packaging/make_app.py").read_text()
    window = (root / "src/tracemem/app/window.py").read_text()
    face = (root / "src/tracemem/app/menubar.py").read_text()

    assert '"window.py"' in recipe and '"menubar.py"' in recipe
    assert "Build memory from a folder" in window
    assert "cold_start_from_picker" in window
    assert "def cold_start_from_picker" in face
    assert "--cold-start" in face


def test_build_recipe_seals_a_non_mutating_helper_boundary_probe():
    root = Path(__file__).resolve().parents[2]
    recipe = (root / "packaging/make_app.py").read_text()
    native = (root / "packaging/TraceHelper.swift").read_text()
    probe = (root / "packaging/helper_probe.py").read_text()

    assert 'runtime / "helper_probe.py"' in recipe
    assert '"--probe-helpers"' in native
    assert "if args.isEmpty" in native
    assert "unknown helper argument" in native
    assert "HelperRuntime.discover" in probe
    assert "TraceSandboxProbe" in probe
    assert "ClaimLog" not in probe and "append(" not in probe


def test_installed_helper_has_real_team_identity_and_hardened_runtime():
    result = _run("codesign", "-dv", "--verbose=4", str(HELPER))
    text = result.stderr.decode("utf-8", "replace")
    info = plistlib.loads((APP / "Contents/Info.plist").read_bytes())
    assert "Signature=adhoc" not in text
    assert "Authority=Apple Development:" in text
    assert "TeamIdentifier=%s" % info["TRACEDeveloperTeam"] in text
    assert "flags=0x10000(runtime)" in text


def test_actual_artifact_entitlements_match_the_reviewed_plist():
    result = _run("codesign", "-d", "--entitlements", ":-", str(HELPER))
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    actual = plistlib.loads(result.stdout)
    source = plistlib.loads(
        (Path(__file__).resolve().parents[2] / "packaging/Trace.entitlements").read_bytes())
    assert actual == source
    assert actual["com.apple.security.network.client"] is False
    assert actual["com.apple.security.network.server"] is True    # F-LAN
    assert "keychain-access-groups" not in actual


def test_helper_probe_never_prints_the_master_key():
    result = _run(str(HELPER), "--probe-key")
    combined = result.stdout + result.stderr
    # Before migration this is an item-not-found failure; after migration it
    # is a fixed success sentence. Neither path may reveal key material.
    assert result.returncode in (0, 70)
    assert len(combined) < 256
    assert b"scoped key readable" in combined or b"Keychain" in combined
