#!/usr/bin/env python3
"""Every path scripts/prerender_pages.py writes, one per line.

The workflows that commit the prerendered pages used to carry the list by hand,
and a family left off one of them is not merely left uncommitted. Its files stay
unstaged, the "git pull --rebase" that follows refuses to run, and the push
fails, so no page lands at all: that is how region, pick-range and
college-position stopped the daily pages on 2026-10-03 and left 700 of them a
build behind. The quieter version of the same bug is worse. data-build.yml
committed data/page_hashes.json while leaving four families out of its list, so
the next build read those hashes, found the stale files already on disk and
called them unchanged. Nothing failed and nothing said so.

So the list lives here, beside the config the pages are built from, and the
workflows read it:

    mapfile -t PAGE_PATHS < <(python scripts/page_paths.py)
    git add -A "${PAGE_PATHS[@]}"

Adding a family to scripts/prerender/config.py, or a list to
scripts/prerender/droughts.py, is enough: nothing here has to be touched and
neither workflow has to be remembered.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from prerender import config as C  # noqa: E402
from prerender import droughts  # noqa: E402

#: Written by the prerender but not a page family: the tool's own entry points
#: and the two files that record what has been built.
FIXED_PATHS = (
    C.NOT_FOUND_PATH,
    C.SITEMAP_PATH,
    C.SLUGS_PATH,
    C.PAGE_HASHES_PATH,
    "index.html",
    os.path.join("js", "player-pages.js"),
)


def page_dirs():
    """Every directory a prerendered page lands in, families and hubs alike."""
    out = []
    for family in C.FAMILIES.values():
        out.append(family["dir"])
        if family.get("hub"):
            out.append(family["hub"])
    for spec in droughts.LISTS:
        out.append(spec.slug)
    out.append(C.DROUGHT_HUB)
    # sorted and de-duplicated, so two runs give byte-identical output and a
    # diff of this list reads as a change rather than a reshuffle
    return sorted(set(out))


def all_paths():
    return page_dirs() + sorted(FIXED_PATHS)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--dirs-only" in argv:
        rows = page_dirs()
    else:
        rows = all_paths()
    for row in rows:
        print(row)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
