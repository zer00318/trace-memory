"""OWNS: one moment, rendered as something a person can read.

`Ask/Tap to source` — the leaf that turns a citation from decoration into
evidence. Every answer already comes back stamped `[a3b4115f]`, and until now
there was nothing behind the stamp: no way to go and look at what the machine
actually saw. A citation nobody can follow asks for trust rather than earning
it, which is the opposite of what this product is for.

TEXT-FIRST, BECAUSE THE PIXELS ARE DEAD. There is no screenshot to show and
there never will be — frames die on the device that captured them. The claims
ARE the memory, so this renders them: when, where, which app, and the lines
in the order they were read.

THE GAPS ARE PART OF THE MOMENT. A stretch with a witness in it says so.
Rendering only what was seen would make a moment with a ten-minute hole look
identical to one without, which is the failure the whole witness vocabulary
exists to prevent — and this is the surface where a person would actually
notice.

IT IS ALSO THE GRADING SURFACE. Reading a moment is how someone decides that
what the machine believes about it is wrong, so the render ends by saying how
to say so.
"""
from __future__ import annotations

import time
from typing import List, Optional

# How many claim lines one moment shows before it stops. A day's reading is
# not a page, and a "readable page" that scrolls for a thousand lines is not
# readable. What is dropped is COUNTED and said, never silently cut.
MAX_LINES = 120
CITE_LEN = 8


def _fmt(t_ms: int) -> str:
    return time.strftime("%H:%M:%S", time.localtime(t_ms / 1000.0))


def _day(t_ms: int) -> str:
    return time.strftime("%A %d %B %Y", time.localtime(t_ms / 1000.0))


def find_claims(claim_log, cite: str, window_ms: int = 5 * 60_000,
                graph_store=None) -> List:
    """The claims of the moment containing `cite`, in time order.

    `cite` is the 8-character stamp an answer shows, so this matches on a
    PREFIX — that is what the founder has in front of them, and asking them
    to find a full uuid to follow their own citation would defeat the point.

    A prefix can in principle match more than one claim. Rather than guess,
    the caller is told: see `resolve`.
    """
    kind, value = _resolve_reference(claim_log, cite, graph_store)
    if kind is None:
        return []
    if kind == "claim":
        t = value.coord.t_ms
        out = [c for c in _all(claim_log)
               if abs(c.coord.t_ms - t) <= window_ms]
    else:
        ids = []
        resolver = getattr(graph_store, "resolve_episode_ids", None)
        getter = getattr(graph_store, "claim_ids_for_episode", None)
        if getter is None:
            return []
        for episode_id in value:
            current = resolver(episode_id) if resolver is not None else [episode_id]
            for resolved_id in current:
                ids.extend(getter(resolved_id))
        ids = list(dict.fromkeys(ids))
        reader = getattr(claim_log, "read_ids", None)
        if reader is not None:
            out = reader(ids)
        else:
            wanted = set(ids)
            out = [c for c in _all(claim_log) if c.id in wanted]
    out.sort(key=lambda c: (c.coord.t_ms, c.id))
    return out


def _all(claim_log):
    try:
        return list(claim_log.all_claims())
    except Exception:                                        # noqa: BLE001
        return []


def resolve(claim_log, cite: str):
    """The one claim this citation names, or None.

    NEVER GUESSES BETWEEN TWO. An ambiguous prefix returns None rather than
    the first match: showing the founder a different moment from the one they
    clicked, with no sign anything was chosen, is worse than showing nothing.
    """
    cite = str(cite or "").strip().lower()
    if not cite:
        return None
    hits = [c for c in _all(claim_log) if str(c.id).lower().startswith(cite)]
    return hits[0] if len(hits) == 1 else None


def _nodes(graph_store):
    if graph_store is None:
        return []
    iterator = getattr(graph_store, "iter_nodes", None)
    if iterator is not None:
        return list(iterator())
    values = getattr(getattr(graph_store, "nodes", None), "values", None)
    return list(values()) if values is not None else []


def _resolve_reference(claim_log, cite: str, graph_store=None):
    """Resolve a citation namespace without guessing across namespaces.

    Answers legitimately cite raw claims, durable episodes, and graph nodes.
    A prefix matching more than one object—or one object in two namespaces—
    resolves to nothing. A click must never silently choose different proof.
    """
    prefix = str(cite or "").strip().lower()
    if not prefix:
        return None, None
    claim_hits = [c for c in _all(claim_log)
                  if str(c.id).lower().startswith(prefix)]
    episode_ids = (getattr(graph_store, "episode_ids", lambda: [])()
                   if graph_store is not None else [])
    episode_hits = [eid for eid in episode_ids
                    if str(eid).lower().startswith(prefix)]
    node_hits = [n for n in _nodes(graph_store)
                 if str(n.id).lower().startswith(prefix)]
    total = len(claim_hits) + len(episode_hits) + len(node_hits)
    if total != 1:
        return None, None
    if claim_hits:
        return "claim", claim_hits[0]
    if episode_hits:
        return "episodes", episode_hits
    episodes = []
    for attr in node_hits[0].attributes:
        episodes.extend(attr.source_episode_ids)
    return "episodes", list(dict.fromkeys(episodes))


def render(claims, cite: str = "") -> str:
    """One moment as a readable page."""
    if not claims:
        return ("Nothing in memory carries that citation.\n\n"
                "Citations are the 8 characters an answer shows in square "
                "brackets, like [a3b4115f].")
    t0, t1 = claims[0].coord.t_ms, claims[-1].coord.t_ms
    apps = [c.coord.capture.app for c in claims if c.coord.capture.app]
    places = [c.coord.world.place_key for c in claims if c.coord.world.place_key]
    out = [
        "THE MOMENT AROUND [%s]" % (cite or "-" * CITE_LEN),
        "=" * 56,
        "%s" % _day(t0),
        "%s – %s" % (_fmt(t0), _fmt(t1)),
    ]
    if apps:
        out.append("in %s" % ", ".join(dict.fromkeys(apps)))
    if places:
        out.append("at %s" % ", ".join(dict.fromkeys(places)))
    out.append("")
    header_len = len(out)

    # THE CITED LINE IS NEVER THE ONE THAT GETS CUT.
    #
    # WITNESSED on the founder's real store: `--moment 9081b271` rendered 120
    # lines of a busy moment and the cited claim was not among them, because
    # truncation took the FIRST 120. The one line the page exists to show was
    # the one it dropped. On a fixture with five claims this is invisible;
    # on a real moment holding hundreds it is the normal case.
    #
    # So the window is centred on the anchor rather than taken from the top.
    shown, cut_before = _window(claims, cite)
    for c in shown:
        mark = ">>" if str(c.id).lower().startswith(str(cite).lower()) and cite \
            else "  "
        # THE WITNESSES ARE NOT FOOTNOTES. A gap belongs in the timeline where
        # it happened, at the moment it happened, or a moment with a hole in
        # it reads exactly like one without.
        out.append("%s %s  [%s]  %s"
                   % (mark, _fmt(c.coord.t_ms), str(c.id)[:CITE_LEN],
                      _one_line(c)))
    dropped = len(claims) - len(shown)
    if dropped > 0:
        # SAID, NEVER SILENTLY CUT, and said on the SIDE it was cut. A page
        # that stops without saying so is a page claiming the moment ended
        # there; a page that only ever apologises at the bottom hides that it
        # also began late.
        if cut_before:
            out.insert(header_len,
                       "   … %d earlier line(s) in this moment, not shown."
                       % cut_before)
        after = dropped - cut_before
        if after > 0:
            out.append("")
            out.append("… and %d more line(s) in this moment, not shown."
                       % after)
    out += ["", "-" * 56,
            "Wrong about this? Ask about it and press \"That's wrong\", or:",
            "  ./trace ask grade <line-id> wrong \"what really happened\""]
    return "\n".join(out)


def _window(claims, cite):
    """At most MAX_LINES claims, centred on the cited one.

    Returns (shown, how_many_were_cut_from_the_front).
    """
    if len(claims) <= MAX_LINES:
        return claims, 0
    cite = str(cite or "").lower()
    at = 0
    if cite:
        for i, c in enumerate(claims):
            if str(c.id).lower().startswith(cite):
                at = i
                break
    start = max(0, min(at - MAX_LINES // 2, len(claims) - MAX_LINES))
    return claims[start:start + MAX_LINES], start


def _one_line(claim) -> str:
    perceiver = str(getattr(claim.provenance, "perceiver", "") or "?")
    text = " ".join(str(claim.text or "").split())
    if len(text) > 96:
        text = text[:95] + "…"
    return "%-4s %s" % (perceiver[:4], text)


def supply_moment(claim_log, cite: str, graph_store=None) -> str:
    """The whole path: a citation in, a readable page out."""
    return render(find_claims(claim_log, cite, graph_store=graph_store), cite)
