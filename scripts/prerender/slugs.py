"""Slugs that never change once published.

data/slugs.json is read first every run and only ever grows. A URL that has been
crawled keeps its slug even if the name behind it is corrected later, because a
slug that moves is a 404 for everyone who linked to it.
"""

from __future__ import annotations

import json
import os
import re
import unicodedata

_NON_SLUG = re.compile(r"[^a-z0-9]+")


def slugify(value):
    """Lowercase ASCII, diacritics stripped: Jokic, not Jokić."""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    text = _NON_SLUG.sub("-", text).strip("-")
    return text or "unnamed"


class SlugBook:
    """The published slug for every entity, loaded, extended and saved."""

    def __init__(self, existing=None):
        # {family: {identity key: slug}}
        self.assigned = {k: dict(v) for k, v in (existing or {}).items()}
        self._taken = {
            family: set(mapping.values()) for family, mapping in self.assigned.items()
        }
        self.added = 0

    def get(self, family, key, name, disambiguator=None):
        """The slug for one entity, minting it on first sight.

        ``key`` identifies the entity for all time and is what the file is keyed
        on; ``name`` is only the text the slug is derived from. A collision takes
        the disambiguator (a draft year for a player), then -2, -3 and so on.
        """
        mapping = self.assigned.setdefault(family, {})
        taken = self._taken.setdefault(family, set())
        if key in mapping:
            return mapping[key]

        base = slugify(name)
        candidate = base
        if candidate in taken and disambiguator:
            candidate = "{}-{}".format(base, slugify(disambiguator))
        suffix = 2
        while candidate in taken:
            candidate = "{}-{}".format(base, suffix)
            suffix += 1

        mapping[key] = candidate
        taken.add(candidate)
        self.added += 1
        return candidate

    def to_json(self):
        return {
            family: dict(sorted(mapping.items()))
            for family, mapping in sorted(self.assigned.items())
        }


def load(path):
    if not os.path.exists(path):
        return SlugBook()
    with open(path, "r", encoding="utf-8") as fh:
        return SlugBook(json.load(fh).get("slugs") or {})


def save(book, path):
    payload = {
        "readme": (
            "The published slug for every entity page. Read first on every run "
            "and only ever added to: a slug that moves is a 404 for everyone "
            "who linked to it. The key on the left identifies the entity for "
            "all time; the slug on the right is what the URL says."
        ),
        "slugs": book.to_json(),
    }
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=1) + "\n")
