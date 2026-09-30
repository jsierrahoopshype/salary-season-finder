#!/usr/bin/env python3
"""Refresh data/headshots.json from jsierrahoopshype/nba-headshots.

The pre-render never goes to the network: it reads the committed mapping. Run
this by hand (or from a clone of the headshot repo) when new faces land there.

    python3 scripts/fetch_headshot_ids.py
    python3 scripts/fetch_headshot_ids.py --from-clone ../nba-headshots
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import unicodedata

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = "https://jsierrahoopshype.github.io/nba-headshots"
METADATA = "players/metadata/players_all.json"
FACE2 = "players/headshots/face2-160"
FACE = "players/headshots/face"
OUT = os.path.join(REPO, "data", "headshots.json")


def norm(name):
    """Match key: no diacritics, no punctuation, no case.

    "A.J. Price" and "AJ Price" are one man, and so are "Nikola Jokic" and
    "Nikola Jokić", so punctuation is deleted rather than turned into a space.
    """
    text = unicodedata.normalize("NFKD", str(name))
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = re.sub(r"[^a-z0-9\s]+", "", text)
    return " ".join(text.split())


def _from_clone(path):
    meta = json.load(open(os.path.join(path, METADATA), "r", encoding="utf-8"))

    def listing(folder, suffix):
        out = subprocess.run(
            ["git", "-C", path, "ls-tree", "--name-only", "HEAD", folder + "/"],
            capture_output=True, text=True, check=True,
        ).stdout.splitlines()
        return {
            os.path.basename(line)[: -len(suffix)]
            for line in out if line.endswith(suffix)
        }

    return meta, listing(FACE2, ".webp"), listing(FACE, ".png")


def _from_network():
    import urllib.request

    with urllib.request.urlopen(BASE + "/" + METADATA, timeout=60) as fh:
        meta = json.loads(fh.read().decode("utf-8"))
    # The Pages site has no directory listing, so the metadata's own filenames
    # are the source of truth and both sizes are assumed present.
    stems = {
        "{}-{}".format(p["nba_id"], p["slug"])
        for p in meta["players"] if (p.get("headshot") or {}).get("face")
    }
    return meta, stems, stems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--from-clone", default=None,
                        help="path to a checkout of the headshot repo")
    args = parser.parse_args(argv)

    if args.from_clone:
        meta, face2, face = _from_clone(args.from_clone)
    else:
        meta, face2, face = _from_network()

    faces, fallback = {}, {}
    for player in meta["players"]:
        stem = "{}-{}".format(player["nba_id"], player["slug"])
        key = norm(player["full_name"])
        if stem in face2:
            faces.setdefault(key, stem)
        elif stem in face:
            fallback.setdefault(key, stem)

    payload = {
        "readme": (
            "Player name (no diacritics, no punctuation, lower case) to the "
            "file stem of his headshot in jsierrahoopshype/nba-headshots. "
            "'faces' are in players/headshots/face2-160 as .webp, 'fallback' "
            "only in players/headshots/face as .png. Anything missing gets "
            "fallbacks/player_silhouette.svg. Refresh with "
            "scripts/fetch_headshot_ids.py."
        ),
        "source": BASE,
        "generated_from": meta.get("generated_at"),
        "faces": dict(sorted(faces.items())),
        "fallback": dict(sorted(fallback.items())),
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=1) + "\n")
    print("{} faces, {} fallback only -> {}".format(len(faces), len(fallback), OUT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
