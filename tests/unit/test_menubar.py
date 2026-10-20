"""THE FACE — a menubar item that says whether TRACE is WATCHING (tree:
08_running > The menubar > Status item, which was at 0%).

WHY IT IS NOT COSMETIC. The founder's eye was blind for hours and every
signal he could reach said otherwise: the daemon was up, the socket answered,
`claims stored` climbed (with witnesses about not seeing), and nothing
anywhere said the word BLIND. macOS grants Screen Recording to an APPLICATION
IDENTITY and TRACE had none — just a Python binary launchd happened to run.

The one thing this must never do is repeat that. So the state it shows is
derived from whether the eye is ALIVE, never from whether a process exists:

    ● watching   the eye organ reports alive
    ⚠ blind      a daemon is up and perceiving nothing
    ○ off        no daemon at all

"Running" and "watching" are different facts. These tests hold that they can
never collapse into one.

The AppKit half is not tested here and is not claimed: a status item needs a
window server, and an oracle that cannot run in CI is an oracle that silently
stops running. What IS tested is every decision the UI makes — which is all of
the part that can be wrong in a way that misleads somebody.
"""
from tracemem.app.menubar import (BLIND, OFF, WATCHING, eye_problem,
                                  read_state)


# ---- the three states must never collapse --------------------------------

def test_no_daemon_is_off():
    assert read_state(None) == OFF
    assert read_state({"ok": False, "error": "x"}) == OFF


def test_a_daemon_with_a_dead_eye_is_BLIND_not_running():
    """THE ORACLE. This exact state persisted for hours while everything the
    founder could check reported health."""
    assert read_state({"ok": True, "bound": 20684,
                       "organs": {"eye": "dead"}}) == BLIND


def test_only_a_live_eye_counts_as_watching():
    assert read_state({"ok": True, "organs": {"eye": "alive"}}) == WATCHING
    for state in ("dead", "benched", "parked"):
        assert read_state({"ok": True, "organs": {"eye": state}}) == BLIND


def test_a_daemon_with_no_organ_report_is_not_assumed_to_be_watching():
    """An older daemon, or one that cannot answer about its organs, must not
    be given the benefit of the doubt. Under-claiming sight is the safe
    error; over-claiming it is the failure this product refuses."""
    assert read_state({"ok": True, "bound": 100}) == BLIND


def test_a_waking_daemon_is_not_yet_watching():
    """While the store is binding, nobody knows whether the eye works."""
    assert read_state({"ok": True, "waking": True,
                       "bound": 2048, "total": 20684}) == BLIND


# ---- and it must say WHY ---------------------------------------------------

def test_the_permission_problem_is_named_in_words_a_person_can_act_on():
    """TCC -3801 is the real error text. Showing it raw would be true and
    useless; the founder is not going to look up an error domain."""
    msg = eye_problem({"ok": True, "organs": {
        "eye": "dead",
        "eye_detail": {"last_error": "RuntimeError('SCShareableContent "
                                     "failed ... Code=-3801 declined TCCs')"}}})

    assert "Screen Recording" in msg
    assert "-3801" not in msg, "the error code is not a sentence"


def test_waking_reports_progress_rather_than_alarm():
    """Binding 20,000 memories takes a minute. Showing NOT WATCHING with no
    explanation during a normal startup would teach the founder to ignore the
    warning that matters."""
    msg = eye_problem({"ok": True, "waking": True,
                       "bound": 2048, "total": 20684})

    assert "2,048" in msg and "20,684" in msg
    assert "Screen Recording" not in msg


def test_index_warming_is_named_instead_of_showing_fake_bind_progress():
    msg = eye_problem({"ok": True, "waking": True,
                       "phase": "search index", "bound": 634000,
                       "total": 634000})
    assert "search index" in msg
    assert "634,000 of 634,000" not in msg


def test_a_watching_eye_has_nothing_to_complain_about():
    assert eye_problem({"ok": True, "organs": {"eye": "alive"}}) == ""


def test_an_unknown_failure_is_shown_rather_than_swallowed():
    msg = eye_problem({"ok": True, "organs": {
        "eye": "dead", "eye_detail": {"last_error": "disk on fire"}}})

    assert "disk on fire" in msg


def test_a_dead_eye_with_no_detail_still_says_it_is_not_watching():
    """Silence about a blind eye is the one thing that must not happen, even
    when the reason is unavailable."""
    assert eye_problem({"ok": True, "organs": {"eye": "dead"}}) != ""


# ---- it drives the daemon through the front door, never around it ---------

def test_the_face_never_writes_to_the_store():
    """A face that captured or appended would be a second writer to a store
    whose entire integrity argument is that it has exactly one. It starts and
    stops the daemon through the same ./trace a person would type."""
    import inspect

    from tracemem.app import menubar

    src = inspect.getsource(menubar)
    assert "ClaimLog" not in src
    assert "append(" not in src
    # The one sqlite read it does is a COUNT for the status line, and it is
    # read-only-first by construction.
    assert "insert" not in src.lower()


class _Ran:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_folder_picker_pauses_one_writer_and_restores_it(tmp_path):
    """The post-install folder door must never race the immutable log."""
    from tracemem.app import menubar

    helper = tmp_path / "TRACE"
    helper.write_text("signed helper")
    helper.chmod(0o700)
    calls = []

    def run(command, **_kw):
        calls.append(command)
        if command[:2] == ["launchctl", "print"]:
            return _Ran(0)
        if command[-1:] == ["--cold-start"]:
            return _Ran(0, "files: 2 indexed")
        return _Ran(0)

    state, message = menubar.cold_start_from_picker(run, helper)

    assert state == "complete"
    assert "resumed watching" in message
    assert [c[1] if c[0] == "launchctl" else c[-1] for c in calls] == [
        "print", "bootout", "--cold-start", "bootstrap", "kickstart"]


def test_cancelled_folder_picker_reads_nothing_and_still_restores(tmp_path):
    from tracemem.app import menubar

    helper = tmp_path / "TRACE"
    helper.write_text("signed helper")
    helper.chmod(0o700)
    calls = []

    def run(command, **_kw):
        calls.append(command)
        if command[:2] == ["launchctl", "print"]:
            return _Ran(0)
        if command[-1:] == ["--cold-start"]:
            return _Ran(70, stderr="TRACE helper: cold start cancelled")
        return _Ran(0)

    state, message = menubar.cold_start_from_picker(run, helper)

    assert state == "cancelled"
    assert "No folder was read" in message
    assert any(c[:2] == ["launchctl", "kickstart"] for c in calls)


def test_folder_picker_respects_an_intentional_pause(tmp_path):
    from tracemem.app import menubar

    helper = tmp_path / "TRACE"
    helper.write_text("signed helper")
    helper.chmod(0o700)
    calls = []

    def run(command, **_kw):
        calls.append(command)
        if command[:2] == ["launchctl", "print"]:
            return _Ran(1)
        return _Ran(0, stdout="done")

    state, _message = menubar.cold_start_from_picker(run, helper)

    assert state == "complete"
    assert not any(c[0] == "launchctl" and c[1] in
                   {"bootout", "bootstrap", "kickstart"} for c in calls)


def test_key_renewal_uses_the_signed_helper_without_key_material(tmp_path):
    from tracemem.app import menubar

    helper = tmp_path / "TRACE"
    helper.write_text("signed helper")
    calls = []

    def run(command, **_kw):
        calls.append(command)
        return _Ran(0, "renewed")

    ok, _message = menubar.renew_scoped_key_access(run, helper)

    assert ok
    assert calls == [[str(helper), "--renew-key-access"]]


def test_folder_reader_spawn_failure_still_restores_the_writer(tmp_path):
    from tracemem.app import menubar

    helper = tmp_path / "TRACE"
    helper.write_text("signed helper")
    helper.chmod(0o700)
    calls = []

    def run(command, **_kw):
        calls.append(command)
        if command[:2] == ["launchctl", "print"]:
            return _Ran(0)
        if command[-1:] == ["--cold-start"]:
            raise OSError("could not execute")
        return _Ran(0)

    state, message = menubar.cold_start_from_picker(run, helper)

    assert state == "failed"
    assert "could not execute" in message
    assert any(c[:2] == ["launchctl", "kickstart"] for c in calls)
