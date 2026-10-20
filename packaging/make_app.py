#!/usr/bin/env python3
"""Build TRACE.app — a real double-clickable macOS application.

WHY A BUNDLE IS NOT COSMETIC, and why this is the fix for the bug that has
cost the founder a day: **macOS attributes Screen Recording to the RESPONSIBLE
PROCESS.** Until now TRACE had no application identity at all — just a Python
binary that a terminal, or launchd, happened to run. So the grant belonged to
whatever started it:

    started from Terminal  -> the grant is Terminal's, capture works
    started by launchd     -> different responsible process, TCC -3801,
                              the eye is blind and everything else reads green

That is not a permission the founder can "grant once". It moves. A bundle
with a stable identifier and a code signature is a thing macOS can hold a
grant AGAINST, and a daemon launched by that app inherits it as the
responsible parent — so granting TRACE once actually stays granted.

WHAT THIS BUILDS

    TRACE.app/
      Contents/Info.plist        identity, name, icon, usage strings
      Contents/MacOS/TRACE       native signed Keychain/launch helper
      Contents/Resources/runtime sealed application code and dependencies
      Contents/Resources/ui      sealed native UI bridge

The build refuses to fall back to an ad-hoc signature.  The helper is signed
with the founder's Apple Development identity and hardened runtime, verifies
the entire sealed bundle before reading the application-ACL Keychain item,
and gives the bundled runtime the master only over an inherited pipe.  It is
a production identity on this Mac, not yet a notarized distribution build.

    python3 packaging/make_app.py [--install]

`--install` also copies it to /Applications so it appears in the Screen
Recording list like any other app.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_NAME = "TRACE"
BUNDLE_ID = "com.trace.watcher"
DIST = ROOT / "dist"
APP = DIST / f"{APP_NAME}.app"

# The UI's own modules, copied in flat. window.py imports `menubar` either
# way on purpose (see its header).
UI_MODULES = ("menubar.py", "window.py", "settings.py", "graph_window.py")

# Just enough of PyObjC to draw a window.  The native helper invokes the
# build-time interpreter but imports only sealed bundle resources (-E/-s).
PYOBJC = ("objc", "AppKit", "Foundation", "CoreFoundation", "PyObjCTools",
          "Quartz")

def _identity_and_team(requested=None):
    out = subprocess.run(
        ["security", "find-identity", "-v", "-p", "codesigning"],
        capture_output=True, text=True, check=True).stdout
    identities = re.findall(r'"(Apple Development:[^"]+)"', out)
    identity = requested or (identities[0] if identities else None)
    if not identity:
        raise SystemExit(
            "no Apple Development signing identity. A production TRACE build "
            "will not fall back to ad-hoc signing.")
    cert = subprocess.run(
        ["security", "find-certificate", "-c", identity, "-p"],
        capture_output=True, check=True).stdout
    subject = subprocess.run(
        ["openssl", "x509", "-noout", "-subject"], input=cert,
        capture_output=True, check=True).stdout.decode("utf-8", "replace")
    match = re.search(r"(?:^|[/, ])OU\s*=\s*([A-Z0-9]+)", subject)
    if not match:
        raise SystemExit("could not derive TeamIdentifier from signing certificate")
    return identity, match.group(1)


def _python_executable() -> str:
    return subprocess.run(
        [str(ROOT / ".venv" / "bin" / "python"), "-c",
         "import sys; print(sys.executable)"], capture_output=True,
        text=True, check=True).stdout.strip()


def _icon(dest: Path) -> str:
    """Borrow a system icon rather than shipping a fake one.

    An app with a generic icon is honest; an app wearing a logo it does not
    have is not. When a designed icon exists this is the one line to change.
    """
    src = Path("/System/Library/CoreServices/CoreTypes.bundle/Contents/"
               "Resources/GenericApplicationIcon.icns")
    if src.exists():
        shutil.copy(src, dest / "trace.icns")
        return "trace.icns"
    return ""


def build(identity=None) -> Path:
    identity, team_id = _identity_and_team(identity)
    # Build beside the last verified artifact.  Code signing may legitimately
    # pause for Keychain authorization; an interruption must not turn the last
    # working dist bundle into a half-signed one.
    staging = DIST / (APP.name + ".building")
    if staging.exists():
        shutil.rmtree(staging)
    macos = staging / "Contents" / "MacOS"
    res = staging / "Contents" / "Resources"
    macos.mkdir(parents=True)
    res.mkdir(parents=True)

    icon = _icon(res)
    info = {
        "CFBundleName": APP_NAME,
        "CFBundleDisplayName": APP_NAME,
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleExecutable": APP_NAME,
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "0.1",
        "CFBundleVersion": "1",
        "LSMinimumSystemVersion": "13.0",
        # A window app, not a background item: the founder asked for something
        # he can open and look at.
        "LSUIElement": False,
        "NSHighResolutionCapable": True,
        # macOS shows these sentences in the permission prompt. They are the
        # only place most people ever read what a program intends, so they say
        # the true thing rather than the reassuring one.
        "NSCameraUsageDescription":
            "TRACE does not use the camera.",
        "NSMicrophoneUsageDescription":
            "TRACE listens continuously while remembering is on, transcribes "
            "on this Mac, and destroys each audio chunk immediately.",
        "NSAppleEventsUsageDescription":
            "TRACE opens System Settings for you when a permission is missing.",
        "TRACEDeveloperTeam": team_id,
    }
    if icon:
        info["CFBundleIconFile"] = icon
    (staging / "Contents" / "Info.plist").write_bytes(plistlib.dumps(info))

    # The UI, flat, with no dependency on the repo being readable.
    ui = res / "ui"
    ui.mkdir(parents=True, exist_ok=True)
    for name in UI_MODULES:
        shutil.copy(ROOT / "src" / "tracemem" / "app" / name, ui / name)
    # A REAL SCRIPT FILE, not `python3 -c`. PyObjC bootstraps a GUI process by
    # re-execing the interpreter through its own Python.app, and with `-c` the
    # window ended up created, correctly sized, and never displayed. Running a
    # file is the path that works.
    (ui / "main.py").write_text(
        "import os, sys\n"
        "HERE = os.path.dirname(os.path.abspath(__file__))\n"
        "sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'pylib'))\n"
        "sys.path.insert(0, HERE)\n"
        "import window\n"
        "sys.exit(window.main())\n")

    pylib = res / "pylib"
    pylib.mkdir(parents=True, exist_ok=True)
    site = ROOT / ".venv" / "lib" / "python3.9" / "site-packages"
    if not site.is_dir():
        # The bundle runs on the system's Python 3.9 (Xcode command-line
        # tools), so its sealed site-packages must be built for 3.9.
        sys.exit("TRACE.app needs a Python 3.9 .venv: "
                 "/usr/bin/python3 -m venv .venv (see README, Set up)")
    for mod in PYOBJC:
        src_mod = site / mod
        if src_mod.exists():
            dst = pylib / mod
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src_mod, dst, symlinks=True)

    # The daemon code and every imported dependency are sealed resources.
    # The native helper verifies this bundle signature before it ever reads
    # the Keychain item, then launches only this copy with -E/-s.
    runtime = res / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / "src" / "tracemem", runtime / "tracemem")
    shutil.copytree(site, runtime / "site-packages", symlinks=True)
    (runtime / "entry.py").write_text(
        "import os, sys\n"
        "HERE = os.path.dirname(os.path.abspath(__file__))\n"
        "sys.path[:] = [HERE, os.path.join(HERE, 'site-packages')] + "
        "[p for p in sys.path if p and 'site-packages' not in p]\n"
        "from tracemem.__main__ import main\n"
        "ROOT = %r\n" % str(ROOT) +
        "args = sys.argv[1:]\n"
        # THE EAR IS OPT-IN (founder, 2026-10-07): opening the microphone
        # every 15s flicked the mic indicator and pushed Bluetooth headphones
        # into call mode, changing media volume. "ear": true in
        # ~/.trace/settings.json turns it back on at the next start.
        "def _ear_on():\n"
        "    import json\n"
        "    try:\n"
        "        with open(os.path.expanduser('~/.trace/settings.json')) as fh:\n"
        "            return json.load(fh).get('ear') is True\n"
        "    except Exception:\n"
        "        return False\n"
        # THE PHONE'S DOOR IS ON BY DEFAULT (founder ruling F-LAN,
        # 2026-10-14): "phone": false in ~/.trace/settings.json closes it.
        "def _phone_on():\n"
        "    import json\n"
        "    try:\n"
        "        with open(os.path.expanduser('~/.trace/settings.json')) as fh:\n"
        "            return json.load(fh).get('phone') is not False\n"
        "    except Exception:\n"
        "        return True\n"
        "if args == ['--daemon']:\n"
        "    args = ['--store', os.path.join(ROOT,'data','trace_store.sqlite3'),"
        " '--graph', os.path.join(ROOT,'data','trace_graph.sqlite3'),"
        " '--lock', os.path.join(ROOT,'data','trace.lock'), '--eye', '--ask']"
        " + (['--ear'] if _ear_on() else [])"
        " + (['--phone'] if _phone_on() else [])\n"
        "else:\n"
        "    args = ['--store', os.path.join(ROOT,'data','trace_store.sqlite3'),"
        " '--graph', os.path.join(ROOT,'data','trace_graph.sqlite3'),"
        " '--lock', os.path.join(ROOT,'data','trace.lock')] + args\n"
        "raise SystemExit(main(args))\n")
    (runtime / "verify.py").write_text(
        "import json, os, sys\n"
        "HERE = os.path.dirname(os.path.abspath(__file__))\n"
        "sys.path[:] = [HERE, os.path.join(HERE, 'site-packages')] + "
        "[p for p in sys.path if p and 'site-packages' not in p]\n"
        "from tracemem.app.wiring import _crypto_for\n"
        "from tracemem.container.inherited_keychain import InheritedKeychain\n"
        "from tracemem.container.keyring import Keyring\n"
        "from tracemem.nodes.sqlite_graph_store import SqliteGraphStore\n"
        "from tracemem.text.claim_log import ClaimLog\n"
        "ROOT = %r\n" % str(ROOT) +
        "kc = InheritedKeychain(int(os.environ['TRACE_MASTER_KEY_FD']))\n"
        "raw = ClaimLog(os.path.join(ROOT,'data','trace_store.sqlite3'), "
        "crypto=_crypto_for({'keychain':kc}, os.path.join(ROOT,'data','trace_store.sqlite3')))\n"
        "graph = SqliteGraphStore(os.path.join(ROOT,'data','trace_graph.sqlite3'), "
        "key=Keyring(kc).derive('graph-store'))\n"
        "print(json.dumps({'claims':raw.count(),'nodes':len(graph.all_nodes()),"
        "'receipts':len(graph.binding_seen())},sort_keys=True))\n")
    shutil.copy(ROOT / "packaging" / "helper_probe.py",
                runtime / "helper_probe.py")

    # Sandboxed helper PACKAGES are sealed resources, not nested macOS
    # bundles.  Putting `com.trace.*` directories directly under
    # Contents/Helpers makes codesign interpret each directory as a nested
    # bundle and reject it as "bundle format unrecognized".  Their native
    # executables are still signed individually before the containing seal;
    # Resources/Helpers is the data-driven package root the runtime receives.
    # The checked-in manifest's zero digest is a build placeholder; the
    # installed manifest pins the exact executable bytes macOS will launch.
    helpers = res / "Helpers"
    helper_sources = ROOT / "plugins"
    if helper_sources.is_dir():
        for manifest_path in sorted(helper_sources.glob("*/manifest.json")):
            manifest = json.loads(manifest_path.read_text())
            source_dir = manifest_path.parent
            dest = helpers / manifest["id"]
            dest.mkdir(parents=True, exist_ok=True)
            executable = dest / manifest["executable"]
            subprocess.run([
                "xcrun", "swiftc", str(source_dir / "Helper.swift"),
                "-o", str(executable), "-framework", "Foundation",
            ], check=True)
            subprocess.run([
                "codesign", "--force", "--options", "runtime",
                "--timestamp=none", "--sign", identity, str(executable),
            ], check=True)
            manifest["sha256"] = hashlib.sha256(executable.read_bytes()).hexdigest()
            (dest / "manifest.json").write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    generated = DIST / "TraceHelper.generated.swift"
    helper = (ROOT / "packaging" / "TraceHelper.swift").read_text()
    helper = helper.replace("__PYTHON_PATH__", _python_executable())
    generated.write_text(helper)
    exe = macos / APP_NAME
    subprocess.run([
        "xcrun", "swiftc", str(generated), "-o", str(exe),
        "-framework", "Foundation", "-framework", "Security",
        "-framework", "LocalAuthentication", "-framework", "AVFoundation",
        "-framework", "AppKit",
    ], check=True)

    entitlements = plistlib.loads(
        (ROOT / "packaging" / "Trace.entitlements").read_bytes())
    # This development Mac has no provisioning profile. Claiming the
    # restricted access-group entitlement makes macOS kill the helper. The
    # item is instead constrained by a Keychain application ACL bound to the
    # signed helper's designated requirement (TraceHelper.swift).
    entitlements.pop("keychain-access-groups", None)
    ent_path = DIST / "Trace.expanded.entitlements"
    ent_path.write_bytes(plistlib.dumps(entitlements))
    subprocess.run([
        # Nested helper executables were already signed above. Re-signing
        # them with --deep here could change bytes after their manifests
        # pinned a digest. Sign the outer bundle and seal the nested code.
        "codesign", "--force", "--strict", "--options", "runtime",
        "--timestamp=none", "--entitlements", str(ent_path),
        "--sign", identity, str(staging),
    ], check=True)
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(staging)],
                   check=True)
    if APP.exists():
        shutil.rmtree(APP)
    staging.rename(APP)
    print("signed: %s (team %s)" % (identity, team_id))
    return APP


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--install", action="store_true",
                    help="also copy into /Applications so it shows up in the "
                         "Screen Recording list like any other app")
    ap.add_argument("--identity", default=None,
                    help="Apple Development signing identity; auto-detected")
    args = ap.parse_args(argv)

    app = build(args.identity)
    print("built: %s" % app)

    if args.install:
        dest = Path("/Applications") / app.name
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(app, dest, symlinks=True)
        subprocess.run(["codesign", "--verify", "--deep", "--strict", str(dest)],
                       check=True)
        print("installed: %s" % dest)
        app = dest

    print("""
NEXT, and it is the whole point of building this:

  1. Open %s  (double-click it)
  2. It will say TRACE is NOT remembering, and show a "Fix permission…"
     button. Press it.
  3. Switch TRACE on in the Screen Recording list. If it is not listed,
     press + and pick it from Applications.
  4. Back in the app: Turn TRACE off, then Turn TRACE on.

From then on the permission belongs to TRACE itself, not to whichever
terminal or launcher happened to start it — which is why it kept coming
loose.""" % app)
    return 0


if __name__ == "__main__":
    sys.exit(main())
