"""The launchd agent definition: parseable, and its laws intact."""
from __future__ import annotations

import plistlib
from pathlib import Path

PLIST = Path(__file__).resolve().parents[2] / "resources" / "com.trace.watcher.plist"


def test_plist_parses_and_keeps_its_laws():
    data = plistlib.loads(PLIST.read_bytes())
    assert data["Label"] == "com.trace.watcher"
    # clean stops are respected; only crashes resurrect
    assert data["KeepAlive"] == {"SuccessfulExit": False}
    # launchd spacing cooperates with the crash-loop brake
    assert data["ThrottleInterval"] >= 10
    assert data["RunAtLoad"] is True
    # logs go where macOS keeps logs; /tmp is purged under a running daemon
    assert data["StandardErrorPath"] == "~/Library/Logs/TRACE/daemon.log"
    assert not data["StandardOutPath"].startswith("/tmp/")
    # launchd enters through the installed signed helper, never an ambient
    # interpreter that can read the master directly.
    assert data["ProgramArguments"] == [
        "/Applications/TRACE.app/Contents/MacOS/TRACE", "--daemon"]


# -- THE ORACLE THAT WAS MISSING -------------------------------------------
#
# Every assertion above is about the plist's SHAPE, and all of them passed
# while the agent was a no-op. `ProgramArguments` named
# `tracemem.app.daemon`, a module with no main(): it defines classes and exits
# 0. With KeepAlive.SuccessfulExit=false that is the worst failure available —
# launchd loads the agent, the process "succeeds" instantly, launchd correctly
# declines to resurrect a clean exit, and TRACE looks installed while watching
# nothing. Silent, total, and a green suite over the top of it.

def _entrypoint(module_name):
    """What `python -m <module_name>` actually executes."""
    import importlib
    try:
        return importlib.import_module(module_name + ".__main__")
    except ImportError:
        return importlib.import_module(module_name)


def test_the_agent_points_at_the_signed_native_helper():
    data = plistlib.loads(PLIST.read_bytes())
    args = data["ProgramArguments"]
    assert args == ["/Applications/TRACE.app/Contents/MacOS/TRACE", "--daemon"]


def test_the_agent_asks_for_the_eye_and_brain_and_the_ear_only_when_chosen():
    """`python -m tracemem` with no flags captures nothing and answers
    nothing: --eye is the explicit consent to capture and --ask builds the
    brain and the socket. An agent missing either is a daemon that boots
    blind, or mute, forever."""
    args = plistlib.loads(PLIST.read_bytes())["ProgramArguments"]

    assert args[-1] == "--daemon"
    helper = (PLIST.parents[1] / "packaging" / "TraceHelper.swift").read_text()
    assert 'arguments: ["--daemon"]' in helper
    generated = (PLIST.parents[1] / "packaging" / "make_app.py").read_text()
    assert "'--eye', '--ask'" in generated
    # The ear is opt-in (founder, 2026-10-07): the mic opening every 15s put
    # Bluetooth headphones into call mode and changed media volume.
    assert "(['--ear'] if _ear_on() else [])" in generated
    assert "get('ear') is True" in generated


def test_signed_helper_owns_the_microphone_capture_boundary():
    """The ambient interpreter must not own the TCC identity or spawn an
    unsigned recorder.  The sealed native executable requests permission,
    records a bounded chunk, and passes it through stdout."""
    root = PLIST.parents[1]
    helper = (root / "packaging" / "TraceHelper.swift").read_text()
    generated = (root / "packaging" / "make_app.py").read_text()

    assert 'args.first == "--capture-audio"' in helper
    assert "AVCaptureDevice.authorizationStatus" in helper
    assert "done.wait(timeout: .now() + 10)" in helper
    assert "permission decision timed out" in helper
    assert "seconds > 0 && seconds <= 60" in helper
    assert "FileHandle.standardOutput.write(try captureAudio" in helper
    assert "AVAudioEngine()" in helper
    assert "NSTemporaryDirectory" not in helper
    assert "AVAudioRecorder" not in helper
    assert '"-framework", "AVFoundation"' in generated


def test_build_seals_native_helpers_and_gives_runtime_only_the_helper_root():
    root = PLIST.parents[1]
    helper = (root / "packaging" / "TraceHelper.swift").read_text()
    generated = (root / "packaging" / "make_app.py").read_text()

    assert '"TRACE_HELPERS_PATH"' in helper
    assert 'appendingPathComponent("Contents/Resources/Helpers")' in helper
    assert 'helper_sources = ROOT / "plugins"' in generated
    assert '"xcrun", "swiftc"' in generated
    assert 'manifest["sha256"] = hashlib.sha256' in generated
    assert generated.index('"codesign", "--force"') < generated.rindex(
        '"codesign", "--force"')


def test_helper_verifies_bundle_before_launching_runtime():
    helper = (PLIST.parents[1] / "packaging" / "TraceHelper.swift").read_text()
    launch = helper.split("func launch", 1)[1]
    assert launch.index("try verifyBundle()") < launch.index("try p.run()")


def test_signed_helper_can_renew_acl_without_exporting_or_replacing_the_key():
    helper = (PLIST.parents[1] / "packaging" / "TraceHelper.swift").read_text()
    renewal = helper.split("func renewMasterAccess", 1)[1].split("func verifyBundle", 1)[0]
    assert "readMaster(interactive: true)" in renewal
    assert "lockItemToSignedHelper()" in renewal
    assert "SecItemAdd" not in renewal
    assert "importMaster" not in renewal
    assert '"--renew-key-access"' in helper


def test_native_supervisor_forwards_termination_to_its_daemon_child():
    helper = (PLIST.parents[1] / "packaging" / "TraceHelper.swift").read_text()
    launch = helper.split("func launch", 1)[1]
    assert "DispatchSource.makeSignalSource" in launch
    assert "SIGTERM" in launch
    assert "p.terminate()" in launch
    assert launch.index("makeSignalSource") < launch.index("p.waitUntilExit()")


# -- the installer ---------------------------------------------------------

def test_the_installed_agent_points_at_this_machine_not_the_founders():
    """The checked-in plist carries the founder's absolute paths. A path in a
    repo is DOCUMENTATION: copy it onto another machine, or move the repo,
    and launchd silently fails at every login pointing at a Python that is
    not there. So the agent is generated, never copied."""
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(PLIST.parents[1] / "tools"))
    from install_agent import build_plist

    root = _Path(__file__).resolve().parents[2]
    data = build_plist(root, verify_app=False)

    assert data["ProgramArguments"] == [
        "/Applications/TRACE.app/Contents/MacOS/TRACE", "--daemon"]
    assert data["WorkingDirectory"] == str(root)


def test_the_installed_agent_inherits_every_law_from_the_template():
    """KeepAlive, ThrottleInterval and the flags are NOT retyped in the
    installer. A second source of truth for a law is a law that drifts."""
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(PLIST.parents[1] / "tools"))
    from install_agent import build_plist

    template = plistlib.loads(PLIST.read_bytes())
    built = build_plist(_Path(__file__).resolve().parents[2], verify_app=False)

    assert built["KeepAlive"] == template["KeepAlive"]
    assert built["ThrottleInterval"] == template["ThrottleInterval"]
    assert built["RunAtLoad"] is template["RunAtLoad"]
    assert built["ProgramArguments"][1:] == template["ProgramArguments"][1:]


def test_uninstall_never_touches_the_store():
    """Someone uninstalling the app is asking for the app to stop, not for
    their memory to be erased. The two must never be the same command."""
    import inspect
    import sys as _sys
    _sys.path.insert(0, str(PLIST.parents[1] / "tools"))
    import install_agent

    src = inspect.getsource(install_agent.uninstall)
    for forbidden in ("store", "sqlite", "rmtree", "unlink(data"):
        assert forbidden not in src.replace(
            "the store stays at data/", ""), (
            "uninstall must not be able to reach the store: found %r"
            % forbidden)


# ---- install must VERIFY, never assert ------------------------------------

def test_install_verifies_the_eye_instead_of_declaring_success():
    """THIS COST THE FOUNDER HOURS OF BLIND CAPTURE.

    `install()` used to end with "TRACE now starts itself at login. It is
    already running." — true, and the most misleading sentence available,
    because the thing it started could not capture a single frame.

    macOS grants Screen Recording to the RESPONSIBLE process. From a terminal
    that was the terminal, which had been granted. launchd spawns the
    interpreter directly, the grant does not carry, SCShareableContent answers
    TCC -3801, and the eye is dark from the first second — while the daemon
    ran, the socket answered, the store grew with witnesses, and the night
    even started thinking, because a dead eye frees the GPU.

    A source-level oracle: install() must not be able to report success
    without consulting the daemon about the eye.
    """
    import inspect

    from tools import install_agent

    src = inspect.getsource(install_agent.install)
    assert "_verify_it_can_actually_see" in src, (
        "install() must end by CHECKING whether the eye can see, not by "
        "announcing that it is running")
    assert '"\\nTRACE now starts itself at login. It is already running."' not in src


def test_install_clears_a_persistent_disabled_launchd_override():
    """A disabled label makes bootstrap fail with opaque error 5 even when
    the plist and signature are valid. Installing explicitly enables it."""
    import inspect

    from tools import install_agent

    src = inspect.getsource(install_agent.install)
    assert src.index('_launchctl("enable"') < src.index(
        '_launchctl("bootstrap"')


def test_the_verifier_does_not_try_to_predict_the_permission():
    """Predicting TCC from outside is guesswork; macOS decides on the
    responsible process, which is not knowable before the fact. The verifier
    waits and LOOKS."""
    import inspect

    from tools import install_agent

    src = inspect.getsource(install_agent._verify_it_can_actually_see)
    assert '"op":"stats"' in src or "'op':'stats'" in src
    assert "alive" in src


def test_a_dead_eye_makes_install_exit_non_zero(monkeypatch, tmp_path):
    """The exit code is what a script — or a person reading the last line —
    actually acts on. A blinded install must not look like a clean one."""
    from tools import install_agent
    monkeypatch.setattr(install_agent, "_verify_installed_app", lambda: None)

    monkeypatch.setattr(install_agent, "AGENT_DIR", tmp_path)
    monkeypatch.setattr(install_agent, "AGENT", tmp_path / "com.trace.watcher.plist")
    monkeypatch.setattr(install_agent, "_launchctl",
                        lambda *a: type("R", (), {"returncode": 0,
                                                  "stdout": "", "stderr": ""})())
    monkeypatch.setattr(install_agent, "_stop_orphaned_runtime", lambda: True)

    monkeypatch.setattr(install_agent, "_verify_it_can_actually_see",
                        lambda *a, **k: 1)
    assert install_agent.install() == 1, "a blind install must not exit 0"

    monkeypatch.setattr(install_agent, "_verify_it_can_actually_see",
                        lambda *a, **k: 0)
    assert install_agent.install() == 0


def test_install_refuses_to_race_an_orphaned_store_writer(monkeypatch, tmp_path):
    from tools import install_agent
    monkeypatch.setattr(install_agent, "_verify_installed_app", lambda: None)

    monkeypatch.setattr(install_agent, "AGENT_DIR", tmp_path)
    monkeypatch.setattr(install_agent, "AGENT", tmp_path / "agent.plist")
    monkeypatch.setattr(install_agent, "_launchctl",
                        lambda *a: type("R", (), {"returncode": 0,
                                                  "stdout": "", "stderr": ""})())
    monkeypatch.setattr(install_agent, "_stop_orphaned_runtime", lambda: False)
    monkeypatch.setattr(
        install_agent, "_verify_it_can_actually_see",
        lambda: (_ for _ in ()).throw(AssertionError("started a second writer")))

    assert install_agent.install() == 1


def test_orphan_cleanup_forces_only_the_sealed_runtime_after_grace(monkeypatch):
    """A wedged owner cannot service SIGTERM, but must not block every future
    login or install.  The force target is resolved from the exact sealed
    runtime marker and checked again before a second writer can start.
    """
    from tools import install_agent

    outputs = iter([
        "4242 /Applications/TRACE.app/Contents/Resources/runtime/entry.py --daemon\n"
        "77 /usr/bin/python unrelated.py\n",
        "4242 /Applications/TRACE.app/Contents/Resources/runtime/entry.py --daemon\n",
        "",
    ])

    def run(*_a, **_kw):
        return type("R", (), {"stdout": next(outputs), "returncode": 0,
                               "stderr": ""})()

    killed = []
    monkeypatch.setattr(install_agent.subprocess, "run", run)
    monkeypatch.setattr(install_agent.os, "kill",
                        lambda pid, sig: killed.append((pid, sig)))
    monkeypatch.setattr(install_agent.time, "sleep", lambda _s: None)

    assert install_agent._stop_orphaned_runtime(timeout_s=0) is True
    assert killed == [(4242, 15), (4242, 9)]
