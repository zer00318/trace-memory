"""OWNS: getting your memory OUT — the leaving-well half of owning it.

`10_faces > Uninstall & reset > Export on the way out`. A product whose whole
claim is "this is yours" has to be able to hand it back, and in a form that
outlives the program: one JSON Lines file, one claim per line, plain text,
readable by anything.

WHY IT MATTERS MORE THAN IT LOOKS: the store is now ENCRYPTED and keyed to
this Mac's Keychain. That is the right default and it means a person who
stops using TRACE, or loses the machine, has a file nobody can read. Export
is what stops "yours alone" from quietly meaning "nobody's, including yours".

IT DECRYPTS ON THE WAY OUT, deliberately and loudly. The export is PLAINTEXT
— that is the point of an export — so the caller is told, in the return value,
exactly what it just wrote and where, and it is the caller's job to say so.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


def export_claims(claim_log, dest: str, limit: Optional[int] = None) -> dict:
    """Write every readable claim to `dest` as JSON Lines. Returns a report.

    Refuses to overwrite: an export that silently replaced last month's
    export would be a data-loss tool wearing a backup's name.
    """
    p = Path(dest).expanduser()
    if p.exists():
        raise FileExistsError(
            "%s already exists — refusing to overwrite an export. Pick "
            "another name." % p)
    p.parent.mkdir(parents=True, exist_ok=True)

    written = 0
    unreadable = 0
    tmp = p.with_suffix(p.suffix + ".writing")
    with tmp.open("w", encoding="utf-8") as fh:
        for claim in claim_log.all_claims():
            if limit is not None and written >= limit:
                break
            fh.write(json.dumps({
                "id": claim.id,
                "t_ms": claim.coord.t_ms,
                "text": claim.text,
                "app": claim.coord.capture.app,
                "place": claim.coord.world.place_key,
                "perceiver": claim.provenance.perceiver,
                "grade": getattr(claim.grade, "name", str(claim.grade)),
                "structured": claim.structured or {},
            }, ensure_ascii=False) + "\n")
            written += 1
    tmp.replace(p)
    try:
        unreadable = claim_log.unreadable()
    except Exception:                                    # noqa: BLE001
        unreadable = -1
    return {"path": str(p), "claims": written, "unreadable": unreadable,
            "plaintext": True}
