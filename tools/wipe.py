"""./trace wipe — make TRACE forget, and prove it.

DRY RUN UNLESS TOLD TWICE. `./trace wipe` shows what would go. Removing it
needs `--really`, and `--really` needs you to have said what happens to your
memories first: either you exported them or you said out loud that you do not
want them. `Export on the way out`'s oracle is exactly that — the wipe is
unreachable before export-or-decline — because the one moment a person is
most likely to destroy something they wanted is the moment they are annoyed
enough to uninstall.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tracemem.app.wipe import audit, plan, render, wipe   # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="trace wipe")
    ap.add_argument("--really", action="store_true",
                    help="actually delete. Irreversible.")
    ap.add_argument("--exported", metavar="PATH",
                    help="the file you exported your memories to")
    ap.add_argument("--no-export", action="store_true",
                    help="you have decided you do not want them")
    args = ap.parse_args(argv)

    targets = plan()
    result = wipe(targets, really=False)          # always survey first

    if args.really:
        if not (args.exported or args.no_export):
            print(render(result, audit(targets), really=False))
            print("")
            print("REFUSING, because you have not said what happens to your")
            print("memories. This is the moment people destroy something they")
            print("wanted, so it takes one more sentence:")
            print("")
            print("  ./trace export ~/my-memories.jsonl")
            print("  ./trace wipe --really --exported ~/my-memories.jsonl")
            print("")
            print("or, if you truly do not want them:")
            print("")
            print("  ./trace wipe --really --no-export")
            return 2
        if args.exported:
            p = Path(args.exported).expanduser()
            if not p.exists() or p.stat().st_size == 0:
                print("  ! %s does not exist or is empty — that is not an "
                      "export.\n    Refusing to wipe against a file that "
                      "would not bring you back." % p)
                return 2
        result = wipe(targets, really=True)

    print(render(result, audit(targets), really=args.really))
    return 0


if __name__ == "__main__":
    sys.exit(main())
