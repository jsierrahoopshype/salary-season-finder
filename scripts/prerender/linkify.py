"""Turn the names inside a sentence into links to their own pages.

Every sentence on these pages names things the site has a page for: a draft
class, a college, a country, a pick, a position, a franchise, a player. A
reader who has just read that Joel Embiid would pass Nikola Jokic has nowhere
to go from Jokic's name unless it is a link.

Three rules keep it from turning prose into a link farm: a target is linked
once per sentence, a page never links to itself, and the longest name at a
position wins, so "Jaren Jackson Jr" is never split into "Jaren Jackson".
"""

from __future__ import annotations

import re

from .render import esc, page_url


class Linker(object):
    """Names to URLs, compiled once and used on every sentence."""

    def __init__(self):
        self._targets = {}      # phrase -> (url, target id)
        self._pattern = None

    # ---- building --------------------------------------------------------
    def add(self, phrase, url, target=None):
        phrase = (phrase or "").strip()
        if not phrase or url is None:
            return
        # first one wins, so a name that means two things links to neither
        # more than once and never changes meaning between sentences
        if phrase in self._targets:
            if self._targets[phrase][0] != url:
                self._targets[phrase] = (None, None)  # ambiguous, drop it
            return
        self._targets[phrase] = (url, target or url)

    def compile(self):
        phrases = sorted(
            (p for p, (url, _t) in self._targets.items() if url),
            key=lambda p: (-len(p), p),
        )
        if not phrases:
            self._pattern = None
            return self
        self._pattern = re.compile(
            r"(?<![\w])(" + "|".join(re.escape(p) for p in phrases) + r")(?![\w])"
        )
        return self

    # ---- using -----------------------------------------------------------
    def html(self, text, current_url=None):
        """The sentence as HTML, escaped, with its names linked."""
        if not self._pattern:
            return esc(text)
        out = []
        used = set()
        last = 0
        for match in self._pattern.finditer(text):
            url, target = self._targets.get(match.group(1), (None, None))
            if url is None or url == current_url or target in used:
                continue
            used.add(target)
            out.append(esc(text[last:match.start()]))
            out.append('<a class="hm-inline-link" href="{}">{}</a>'.format(
                esc(url), esc(match.group(1))))
            last = match.end()
        out.append(esc(text[last:]))
        return "".join(out)

    def sentences_html(self, text, current_url=None):
        """Same, but the once-per-target rule restarts at each full stop."""
        parts = re.split(r"(?<=\.)(\s+)", text)
        return "".join(
            part if i % 2 else self.html(part, current_url)
            for i, part in enumerate(parts)
        )


#: Position nouns as the sentences write them, singular and plural.
POSITION_WORDS = {
    "G": ("guard", "guards"),
    "F": ("forward", "forwards"),
    "C": ("center", "centers"),
}


def build(idx, entities, cohorts):
    """One linker for the whole run.

    ``entities`` is every built entity; ``cohorts`` the cohort ones, which are
    the only pages whose names appear inside a claim in more than one shape.
    """
    linker = Linker()
    by_family = {}
    for entity in entities:
        by_family.setdefault(entity.family, []).append(entity)

    # players, under the name the page prints; a name covering two men is left
    # alone, because a sentence cannot say which one it means
    seen = {}
    for ident in by_family.get("player", []):
        seen[ident.name] = None if ident.name in seen else ident
    for name, ident in seen.items():
        if ident is not None:
            linker.add(name, page_url("player", ident.slug), ("player", ident.slug))

    for entity in by_family.get("team", []):
        url = page_url("team", entity.slug)
        linker.add(entity.name, url, ("team", entity.slug))
        # "in 76ers history" prints the last word of the name on its own
        short = entity.name.split()[-1]
        if len(entity.name.split()) > 1:
            linker.add(short, url, ("team", entity.slug))

    for entity in cohorts:
        family = entity.family
        url = page_url(family, entity.slug)
        target = (family, entity.slug)
        if family == "college":
            linker.add(entity.name, url, target)
        elif family == "country":
            linker.add(entity.name, url, target)
        elif family == "draft":
            linker.add("{} draft class".format(entity.key), url, target)
            linker.add("{} NBA draft".format(entity.key), url, target)
        elif family == "pick":
            if entity.key == "undrafted":
                linker.add("undrafted player", url, target)
                linker.add("undrafted players", url, target)
            else:
                linker.add("No. {} pick".format(entity.key), url, target)
                linker.add("No. {} picks".format(entity.key), url, target)
        elif family == "position":
            for word in POSITION_WORDS.get(entity.key, ()):
                linker.add(word, url, target)
    return linker.compile()
