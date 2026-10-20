"""OWNS: the settings a person is allowed to change, and where they live.

ONE FILE, OUTSIDE THE PROJECT: `~/.trace/settings.json`. Not in the repo,
because settings are the user's and a repo is the developer's; and not behind
macOS's Documents wall, because TRACE.app cannot read ~/Documents and a
settings screen that only works from a terminal is not a settings screen.

WHY THIS UNBLOCKS FOUR SEPARATE LEAVES AT ONCE. `app/matcher.py` has had a
working `Blocklist` since July and no way for anybody to put a rule in it —
the engine was built and the door was missing. The same is true of the night's
budget and the model choice: real machinery, no surface. This module is the
door, and the app's Settings screen is a form over it.

EVERY SETTING DEFAULTS TO THE SAFE ANSWER, and "safe" here means *less*
recording rather than more: an unreadable or corrupt file yields the defaults
and says so, never a silently permissive state. A settings file that fails
open would turn a typo into surveillance of something the founder had
excluded.

NOTHING HERE MAY EVER WIDEN CAPTURE WITHOUT BEING ASKED. The blocklist can
only ADD exclusions; there is no setting that removes a wall built elsewhere
(the sacred OFF, the secure-input guard, the entitlements). A settings file is
a place a mistake gets persisted, so it is deliberately not a place where the
product's refusals can be edited.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

SETTINGS_PATH = Path.home() / ".trace" / "settings.json"

# The night's own ceiling. 0 means "no token budget declared", which the
# night already reads as "spend nothing on the reasoner" — the safe default,
# because the alternative is a background process quietly buying tokens.
DEFAULT_NIGHT_TOKENS = 0

# Minutes of no input before the eye closes. The founder set this at 15
# (F-EYELID) with the trade-off named; it is exposed here so he can change his
# own mind without anyone editing code, and the ruling is cited in the schema
# so nobody later "tunes" it not knowing it was a decision.
DEFAULT_IDLE_MINUTES = 15

# EXCLUDED BEFORE ANYONE ASKS. The leaf's brief calls for these and a fresh
# install had none: on 2026-08-25 `./trace settings` on this machine said
# "(nothing yet — TRACE records every window it can see)", password manager
# included.
#
# A person should not have to think of their vault before it has already been
# recorded, and the first ten minutes of a memory product are exactly when
# they have not thought of it. So the default is protective and the founder
# can always remove one — a rule they delete stays deleted, because a file
# that exists is their answer and this list only fills the silence.
#
# BEST EFFORT, NOT A GUARANTEE, and said plainly because a privacy list that
# implies completeness is worse than one that admits its edges: it covers the
# managers common on macOS and it cannot know about yours. The real guard is
# that anything here is easy to add.
SACRED_DEFAULT_BUNDLES = (
    "com.apple.keychainaccess",
    "com.1password.1password",
    "com.agilebits.onepassword",
    "com.agilebits.onepassword7",
    "com.bitwarden.desktop",
    "org.keepassxc.keepassxc",
    "com.lastpass.LastPass",
    "com.dashlane.dashlanephonefinal",
)


@dataclass
class Settings:
    """What a person may change. Every field has a safe default."""
    # Exclusions. ADD-ONLY: see the module docstring.
    blocked_bundles: List[str] = field(
        default_factory=lambda: list(SACRED_DEFAULT_BUNDLES))
    blocked_titles: List[str] = field(default_factory=list)
    blocked_hosts: List[str] = field(default_factory=list)

    # The night.
    night_tokens: int = DEFAULT_NIGHT_TOKENS
    night_on_battery: bool = False          # safe default: never on a cell
    eyelid_idle_minutes: int = DEFAULT_IDLE_MINUTES

    # The mouth. Empty means "whatever the CLI was told", so a settings file
    # that has never been touched cannot silently repoint the model.
    model: str = ""

    # Read failures are reported, never hidden. A settings screen that shows
    # defaults while a broken file sits on disk is a screen that lies.
    problem: Optional[str] = None
    # THE FILE EXISTS AND COULD NOT BE READ — which is NOT the same as "no
    # settings yet", and conflating them is how a typo becomes surveillance.
    # A fresh install has no file and no exclusions, and that is fine. A
    # corrupt file has UNKNOWN exclusions, and an empty blocklist is then a
    # guess in the permissive direction. Callers must branch on this.
    unreadable: bool = False

    def as_dict(self) -> Dict[str, object]:
        # `problem` and `unreadable` describe THIS READ, not the user's
        # choices, and writing them back would persist a transient failure
        # as configuration.
        return {k: v for k, v in self.__dict__.items()
                if k not in ("problem", "unreadable")}

    def blocklist(self):
        """The live Blocklist these rules describe."""
        from tracemem.app.matcher import Blocklist
        return Blocklist.from_spec(bundles=self.blocked_bundles,
                                   titles=self.blocked_titles,
                                   hosts=self.blocked_hosts)


def _coerce(raw: dict) -> Settings:
    s = Settings()
    if not isinstance(raw, dict):
        s.problem = "settings file is not a JSON object — using defaults"
        return s
    bad = []
    for key in ("blocked_bundles", "blocked_titles", "blocked_hosts"):
        # ABSENT IS NOT EMPTY. `raw.get(key, [])` would have overwritten the
        # sacred defaults with [] for every settings file written before they
        # existed — silently un-excluding password managers on upgrade. A key
        # that is present is the founder's answer, including when it is
        # shorter than the default; a key that is missing is silence, and
        # silence keeps the protective default.
        if key not in raw:
            continue
        v = raw[key]
        if isinstance(v, list) and all(isinstance(x, str) for x in v):
            setattr(s, key, [x for x in v if x.strip()])
        else:
            bad.append(key)
    for key, caster in (("night_tokens", int), ("eyelid_idle_minutes", int)):
        if key in raw:
            try:
                val = caster(raw[key])
                # NEGATIVES ARE REFUSED, not clamped silently: a negative
                # idle threshold would close the eye instantly and for ever.
                if val < 0:
                    bad.append(key)
                else:
                    setattr(s, key, val)
            except (TypeError, ValueError):
                bad.append(key)
    if "night_on_battery" in raw:
        if isinstance(raw["night_on_battery"], bool):
            s.night_on_battery = raw["night_on_battery"]
        else:
            bad.append("night_on_battery")
    if "model" in raw:
        if isinstance(raw["model"], str):
            s.model = raw["model"].strip()
        else:
            bad.append("model")
    if bad:
        s.problem = ("ignored unusable setting(s): %s — those fell back to "
                     "their safe defaults" % ", ".join(sorted(bad)))
    return s


def load(path: Optional[Path] = None) -> Settings:
    """Read the settings, or safe defaults with the reason attached."""
    p = Path(path) if path else SETTINGS_PATH
    if not p.exists():
        return Settings()
    try:
        raw = json.loads(p.read_text())
    except Exception as e:                               # noqa: BLE001
        # THE DEFAULTS ARE NOT SAFE FOR EXCLUSIONS, and this used to claim
        # they were. An empty blocklist excludes NOTHING — so a corrupt
        # settings file meant TRACE quietly captured the very things the
        # founder had told it to ignore, which is the exact failure the
        # module docstring forbids ("a settings file that fails open would
        # turn a typo into surveillance"). The defaults for the night and the
        # model genuinely are conservative; the blocklist's are the opposite.
        # So this no longer pretends: it flags the read as unreadable and
        # leaves the decision to a caller that knows what it is about to do.
        s = Settings()
        s.problem = ("could not read %s (%s) — the exclusions you set are "
                     "UNKNOWN, not empty" % (p.name, type(e).__name__))
        s.unreadable = True
        return s
    return _coerce(raw)


def save(settings: Settings, path: Optional[Path] = None) -> Path:
    """Write settings atomically. A half-written settings file is a config
    nobody can predict the behaviour of."""
    p = Path(path) if path else SETTINGS_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".writing")
    tmp.write_text(json.dumps(settings.as_dict(), indent=1, sort_keys=True))
    tmp.replace(p)
    return p


def describe(settings: Settings) -> str:
    """The settings as a person reads them, for the app and the CLI."""
    lines = ["WHAT TRACE IGNORES"]
    yours = [b for b in settings.blocked_bundles
             if b not in SACRED_DEFAULT_BUNDLES]
    if not (yours or settings.blocked_titles or settings.blocked_hosts):
        lines.append("  nothing of your own yet — TRACE records every window "
                     "it can see,")
        lines.append("  except the password managers it ignores by default.")
    for label, items in (("apps", yours),
                         ("window titles containing", settings.blocked_titles),
                         ("websites", settings.blocked_hosts)):
        for it in items:
            lines.append("  %s: %s" % (label, it))
    # SHOWN, NOT HIDDEN, AND SHOWN AS A DEFAULT. A protective rule the user
    # did not write is still a rule about their machine, and a settings
    # screen that conceals what it is doing is not a settings screen.
    kept = [b for b in settings.blocked_bundles if b in SACRED_DEFAULT_BUNDLES]
    if kept:
        lines.append("  password managers (ignored by default): %d app(s)"
                     % len(kept))
    lines += [
        "",
        "THE NIGHT",
        # SAID ACCURATELY. This used to read "0 — it reasons for free and
        # buys nothing", which described a setting nothing consulted. Zero
        # means the built-in allowance applies, because a literal zero cap
        # would make every night unaffordable and stop the brain thinking.
        "  thinking budget : %s" % (
            "%d tokens a night, as you set" % settings.night_tokens
            if settings.night_tokens
            else "the built-in nightly allowance (you have not set one)"),
        "  on battery      : %s" % ("yes" if settings.night_on_battery
                                    else "no — only while plugged in"),
        "  eye closes after: %d minutes of no typing" % settings.eyelid_idle_minutes,
        "",
        "THE MOUTH",
        "  model           : %s" % (settings.model or "whatever the daemon was started with"),
    ]
    if settings.problem:
        lines += ["", "PROBLEM", "  " + settings.problem]
    return "\n".join(lines)


class SettingsWatcher:
    """The settings as they are RIGHT NOW, not as they were at boot.

    WHY THIS EXISTS, measured 2026-08-25. `__main__` did this, once:

        _rules = _settings.load().blocklist()
        organs = [lambda submit: EyeOrgan(submit=submit, blocklist=_rules)]

    The rules were frozen for the lifetime of the process. A person could add
    their bank to the blocklist, see the settings screen list it as active,
    and TRACE would keep capturing that app until the daemon was next
    restarted — which might be days. The screen said excluded; the eye
    carried on looking. A privacy control that reports success and does
    nothing is worse than one that is missing, because the missing one does
    not get trusted.

    The leaf's oracle is "add an app in UI → excluded within one beat", and
    nothing in the product could satisfy it.

    CHEAP ENOUGH TO CALL FROM THE CAPTURE BEAT. One `stat()` at most every
    `poll_seconds`, and a re-parse only when the file's mtime or size has
    actually moved. The rules are cached between times.

    IT KEEPS THE LAST GOOD RULES. If the file becomes unreadable while
    running, the exclusions the founder set STAY IN FORCE. Dropping to an
    empty blocklist mid-session — which is what a naive reload would do —
    would silently start capturing whatever they had excluded, at exactly the
    moment nobody is looking at a settings screen.
    """

    def __init__(self, path: Optional[Path] = None, poll_seconds: float = 2.0,
                 clock=None) -> None:
        self._path = Path(path) if path else SETTINGS_PATH
        self._poll_s = poll_seconds
        if clock is None:
            import time as _time
            clock = _time.monotonic
        self._clock = clock
        self._checked_at = None
        self._stamp = None
        self._settings = None
        self.reloads = 0
        self.kept_last_good = 0

    def _stamp_now(self):
        try:
            st = self._path.stat()
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None                      # no file yet, or gone

    def current(self) -> Settings:
        """The live settings, re-read only when the file has changed."""
        now = self._clock()
        if (self._settings is not None and self._checked_at is not None
                and now - self._checked_at < self._poll_s):
            return self._settings
        self._checked_at = now
        stamp = self._stamp_now()
        if self._settings is not None and stamp == self._stamp:
            return self._settings

        fresh = load(self._path)
        if fresh.unreadable and self._settings is not None:
            # THE RULES THE FOUNDER SET STAY IN FORCE. See the class
            # docstring: a reload that fails must never widen capture.
            self.kept_last_good += 1
            self._settings.problem = fresh.problem
            self._stamp = stamp
            return self._settings
        self._stamp = stamp
        if self._settings is not None:
            self.reloads += 1
        self._settings = fresh
        return fresh

    def blocklist(self):
        """A Blocklist reflecting the file as it stands now."""
        return self.current().blocklist()


# -- changing them ---------------------------------------------------------
#
# ADD AND REMOVE, and only for exclusions. Nothing here can widen capture
# beyond where it already was: removing a rule the founder typed returns
# TRACE to watching what it watched before that rule existed, which is a
# different act from editing away one of the product's own refusals (the
# sacred OFF, the secure-input guard). Those are not settings.

_FIELDS = {"app": "blocked_bundles", "title": "blocked_titles",
           "site": "blocked_hosts"}


class UnknownRuleKind(ValueError):
    pass


def _field_for(kind: str) -> str:
    if kind not in _FIELDS:
        raise UnknownRuleKind(
            "rule kind must be one of %r, got %r — never guess which of "
            "someone's exclusions they meant" % (sorted(_FIELDS), kind))
    return _FIELDS[kind]


def add_rule(kind: str, value: str, path: Optional[Path] = None) -> Settings:
    """Exclude something, now. Returns the settings as they now stand."""
    field_name = _field_for(kind)
    value = str(value or "").strip()
    if not value:
        raise ValueError("an empty rule would exclude nothing and read as a "
                         "rule that works")
    s = load(path)
    if s.unreadable:
        # Refuse rather than overwrite. Saving on top of a file we could not
        # parse would silently DELETE exclusions the founder set and cannot
        # currently see.
        raise ValueError(s.problem or "settings are unreadable")
    current = list(getattr(s, field_name))
    if value not in current:
        current.append(value)
        setattr(s, field_name, current)
        save(s, path)
    return s


# The values a person may set, with the bounds each one is allowed to take.
# A settings screen that accepts anything is a screen that persists mistakes:
# a negative idle threshold closes the eye for ever, and an idle threshold of
# a week means it never closes at all. Both are silent.
_LIMITS = {
    "night_tokens": (int, 0, 10_000_000,
                     "how many tokens the night may spend"),
    "eyelid_idle_minutes": (int, 1, 240,
                            "minutes of no typing before the eye closes"),
    "night_on_battery": (bool, None, None,
                         "whether it may think on battery"),
    "model": (str, None, None, "which mouth answers"),
}


class UnknownSetting(ValueError):
    pass


def settable() -> Dict[str, str]:
    """What may be changed, and what each one means — for any face that wants
    to offer them without hard-coding a list that drifts."""
    return {k: v[3] for k, v in _LIMITS.items()}


def set_value(name: str, value, path: Optional[Path] = None) -> Settings:
    """Change one setting, refusing anything out of bounds. Returns the new
    settings.

    REFUSES RATHER THAN CLAMPS. Silently turning 500 minutes into 240 leaves
    a person believing something that is not true about their own machine,
    which is the same failure as a settings screen that does not take effect.
    """
    if name not in _LIMITS:
        raise UnknownSetting(
            "%r is not something you can set — try %s"
            % (name, ", ".join(sorted(_LIMITS))))
    kind, lo, hi, _ = _LIMITS[name]
    if kind is bool:
        if isinstance(value, str):
            v = value.strip().lower()
            if v not in ("yes", "no", "true", "false", "on", "off"):
                raise ValueError("say yes or no, not %r" % value)
            value = v in ("yes", "true", "on")
        value = bool(value)
    elif kind is int:
        try:
            value = int(value)
        except (TypeError, ValueError):
            raise ValueError("%s must be a whole number, got %r"
                             % (name, value))
        if value < lo or value > hi:
            raise ValueError("%s must be between %d and %d, got %d — "
                             "refused rather than clamped, so you are never "
                             "left believing something untrue about your own "
                             "machine" % (name, lo, hi, value))
    else:
        value = str(value).strip()

    s = load(path)
    if s.unreadable:
        raise ValueError(s.problem or "settings are unreadable")
    setattr(s, name, value)
    save(s, path)
    return s


def remove_rule(kind: str, value: str, path: Optional[Path] = None) -> Settings:
    """Stop excluding something. Only ever removes a rule a person added."""
    field_name = _field_for(kind)
    s = load(path)
    if s.unreadable:
        raise ValueError(s.problem or "settings are unreadable")
    current = [v for v in getattr(s, field_name) if v != value]
    if current != list(getattr(s, field_name)):
        setattr(s, field_name, current)
        save(s, path)
    return s
