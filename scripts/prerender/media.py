"""Headshots and flags, both from files we control.

Headshots come from jsierrahoopshype/nba-headshots, the same GitHub Pages
site the other HoopsMatic tools read, through the mapping committed at
data/headshots.json so the build never goes to the network. A name with no
headshot on file gets the silhouette that repository ships.

Flags are the flag-icons SVGs copied into assets/flags (licence beside them),
the same set nba-career-map uses. Nothing is hotlinked from flagcdn or any
other host.

Every image carries its own width and height so a row cannot grow after the
page has painted, and everything below the fold is lazy.
"""

from __future__ import annotations

import json
import os
import re
import unicodedata

from . import config as C
from .render import esc

HEADSHOT_BASE = "https://jsierrahoopshype.github.io/nba-headshots"
FACE_SIZE = 28
FLAG_W, FLAG_H = 21, 16

HEADSHOTS_PATH = os.path.join("data", "headshots.json")
FLAGS_PATH = os.path.join("data", "country_flags.json")


def match_key(name):
    """The name with diacritics, punctuation and case taken off both sides."""
    text = unicodedata.normalize("NFKD", str(name))
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = re.sub(r"[^a-z0-9\s]+", "", text)
    return " ".join(text.split())


def _load(repo, path, key):
    full = os.path.join(repo, path)
    if not os.path.exists(full):
        return {}
    with open(full, "r", encoding="utf-8") as fh:
        return json.load(fh).get(key) or {}


class Media(object):
    def __init__(self, repo):
        self.faces = _load(repo, HEADSHOTS_PATH, "faces")
        self.fallback = _load(repo, HEADSHOTS_PATH, "fallback")
        self.flags = _load(repo, FLAGS_PATH, "codes")

    # ---- headshots -------------------------------------------------------
    def face_src(self, names):
        """The best headshot for any spelling of a name, or the silhouette."""
        for name in names:
            key = match_key(name)
            if key in self.faces:
                return "{}/players/headshots/face2-160/{}.webp".format(
                    HEADSHOT_BASE, self.faces[key])
            if key in self.fallback:
                return "{}/players/headshots/face/{}.png".format(
                    HEADSHOT_BASE, self.fallback[key])
        return HEADSHOT_BASE + "/fallbacks/player_silhouette.svg"

    def face(self, ident):
        names = [ident.name, ident.data_key]
        names += sorted({r["player"] for r in ident.records})
        return (
            '<img class="hm-face" src="{}" width="{}" height="{}" '
            'loading="lazy" decoding="async" alt="{}">'
        ).format(esc(self.face_src(names)), FACE_SIZE, FACE_SIZE, esc(ident.name))

    # ---- flags -----------------------------------------------------------
    def flag_src(self, country):
        code = self.flags.get((country or "").strip())
        if not code:
            return None
        return "{}/assets/flags/{}.svg".format(C.TOOL_ROOT, code)

    def flag(self, country, label=None):
        src = self.flag_src(country)
        if not src:
            return ""
        return (
            '<img class="hm-flag" src="{}" width="{}" height="{}" '
            'loading="lazy" decoding="async" alt="{}">'
        ).format(esc(src), FLAG_W, FLAG_H, esc(label or country))

    def player_flag(self, ident):
        """A player's flag, from the last season that names a country."""
        for record in reversed(ident.records):
            country = (record.get("nationality") or "").strip()
            if country:
                return self.flag(country)
        return ""
