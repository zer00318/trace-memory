#!/usr/bin/env python3
"""The falsification experiment moved into the package (tracemem.evalx.
falsify_cli) so it runs inside the signed app, the only process allowed to
read the memory's key:

    ./trace falsify --author        # write the question template
    ./trace falsify FILE            # run your questions, get a verdict
"""
import sys

if __name__ == "__main__":
    sys.exit(__doc__)
