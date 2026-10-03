"""The things that get a page, built through the factoid engine's own index.

Every identity question, who a player is, which cohorts a season belongs to,
whether a career total can be trusted, is answered by the engine rather than
re-derived here. A page and a factoid can then never disagree.
"""

from __future__ import annotations

import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import factoids as F  # noqa: E402

from . import config as C  # noqa: E402


class Entity(object):
    """One page's worth of subject matter."""

    __slots__ = ("family", "key", "name", "slug", "records", "players", "extra")

    def __init__(self, family, key, name, records=None, players=None, **extra):
        self.family = family
        self.key = key
        self.name = name
        self.slug = None          # filled by assign_slugs
        self.records = records or []
        self.players = players or []
        self.extra = extra

    @property
    def path(self):
        return "{}/{}".format(C.FAMILIES[self.family]["dir"], self.slug)

    @property
    def url(self):
        return "{}/{}/".format(C.TOOL_ROOT, self.path)

    @property
    def indexable(self):
        return bool(C.FAMILIES[self.family]["indexable"])

    def __repr__(self):
        return "<{} {}>".format(self.family, self.key)


class PlayerIdentity(Entity):
    """One man.

    Usually one data key. Where a confirmed split says a key covers two men,
    each named segment is its own identity with its own page, and a segment the
    file cannot name gets none. Where a name alias merges two spellings, the two
    are one identity under the canonical name.
    """

    def __init__(self, key, name, records, segment=None, **extra):
        Entity.__init__(self, "player", key, name, records=records, **extra)
        self.extra["segment"] = segment

    @property
    def data_key(self):
        """The name the engine and factoids.json file this man under."""
        return self.extra["data_key"]


def _season_key(season):
    return F.season_key(season)


def build_player_identities(idx):
    """Every player who gets a page, in engine terms."""
    out = []
    for data_key, recs in sorted(idx.by_player.items()):
        entry = (idx.identity_splits or {}).get(data_key) or {}
        people = entry.get("people") or []
        confirmed = bool(entry.get("confirmed"))

        if entry.get("split") and confirmed and len(people) >= 2:
            # Two men under one key, and the file says which seasons are whose.
            for i, person in enumerate(people):
                name = person.get("display_name")
                if not name:
                    # a real man this file cannot name, so no page carries him
                    continue
                first = _season_key(person.get("first_season") or "")
                last = _season_key(person.get("last_season") or "")
                segment = [
                    r for r in recs if first <= _season_key(r["season"]) <= last
                ]
                if not segment:
                    continue
                out.append(PlayerIdentity(
                    "{}#{}".format(data_key, i), name, segment, segment=i,
                    data_key=data_key,
                    # a split key's career total sums two men, so no page of
                    # either half claims a career figure
                    career_trustworthy=False,
                    factoids_allowed=True,
                ))
            continue

        unconfirmed_split = bool(entry.get("split")) and not confirmed
        out.append(PlayerIdentity(
            data_key, data_key, recs, segment=None, data_key=data_key,
            career_trustworthy=idx.career_eligible(data_key),
            # An unconfirmed split prints one name over two men, so the page
            # exists but says nothing that names him in a claim.
            factoids_allowed=not unconfirmed_split,
        ))
    return out


def _display_college(idx, value):
    return idx.college_display(value)


#: The sentence form of a position inside a college, and of a position on its
#: own, so a page can say "Duke guards" as well as "Duke Guards".
POSITION_PLURAL = {"G": "Guards", "F": "Forwards", "C": "Centers"}
POSITION_NOUN = {"G": "guards", "F": "forwards", "C": "centers"}

PICK_RANGE_LABEL = {
    "top-10": "Top-10 Picks",
    "lottery": "Lottery Picks",
    "second-round": "Second-Round Picks",
}


def _cohort_label(idx, family, key):
    if family == "college":
        return _display_college(idx, key)
    if family == "position":
        return POSITION_PLURAL[key]
    if family == "pick":
        return "Undrafted" if key == "undrafted" else key
    if family == "region":
        return F.REGION_PHRASES[key]["title"]
    if family == "pick_range":
        return PICK_RANGE_LABEL[key]
    if family == "college_position":
        college, group = key.split("|", 1)
        return "{} {}".format(_display_college(idx, college), POSITION_PLURAL[group])
    return key


def _cohort_nouns(idx, family, key, name):
    """What a sentence calls this cohort: (plural, singular, its record).

    "European Players" is a page title; "European players" is what a heading
    wants, and lowercasing the name would take Duke with it. None for a family
    whose wording summary.py already knows.
    """
    if family == "region":
        spec = F.REGION_PHRASES[key]
        return spec["many"], spec["one"], spec["record"]
    if family == "pick_range":
        many = name.lower()
    elif family == "college_position":
        college, group = key.split("|", 1)
        many = "{} {}".format(_display_college(idx, college), POSITION_NOUN[group])
    else:
        return None, None, None
    one = many[:-1] if many.endswith("s") else many
    return many, one, "the {} single-season record".format(one)


def build_cohorts(idx, identities):
    """Cohort pages, with the same membership the engine ranks inside.

    A record whose bio metadata belongs to a son of the same name joins nothing,
    and a season a confirmed split cannot name is not in the data either, both
    because ``_cohorts_for`` refuses them.
    """
    by_data_key = collections.defaultdict(list)
    for ident in identities:
        by_data_key[ident.data_key].append(ident)

    def owner(record):
        """Which identity a record belongs to."""
        data_key = idx.canonical(record["player"])
        candidates = by_data_key.get(data_key) or []
        if len(candidates) == 1:
            return candidates[0]
        for ident in candidates:
            if any(r is record for r in ident.records):
                return ident
        return None

    kinds = {
        spec["cohort"]: family
        for family, spec in C.FAMILIES.items() if spec["cohort"]
    }
    buckets = collections.defaultdict(lambda: {"records": [], "players": set()})
    for record in idx.records:
        if (idx.canonical(record["player"]), record["season"]) in idx.split_suppressed:
            continue
        ident = owner(record)
        if ident is None:
            continue
        for kind, ckey, _label in F._cohorts_for(record, idx):
            family = kinds.get(kind)
            if family is None:
                continue
            bucket = buckets[(family, ckey)]
            bucket["records"].append(record)
            bucket["players"].add(ident.key)

        agent = record.get("agent")
        if agent and record["season"] in idx.agent_seasons_safe:
            bucket = buckets[("agent", agent)]
            bucket["records"].append(record)
            bucket["players"].add(ident.key)

    by_key = {ident.key: ident for ident in identities}
    out = []
    for (family, ckey), bucket in buckets.items():
        minimum = C.AGENT_MINIMUM if family == "agent" else C.cohort_minimum(family)
        if len(bucket["players"]) < minimum:
            continue
        # the key breaks the tie, so two men filed under one name keep the
        # same order from one build to the next
        players = sorted(
            (by_key[k] for k in bucket["players"]), key=lambda p: (p.name, p.key)
        )
        label = _cohort_label(idx, family, ckey)
        many, one, record = _cohort_nouns(idx, family, ckey, label)
        out.append(Entity(
            family, ckey, label,
            records=bucket["records"], players=players,
            noun=many, noun_one=one, noun_record=record,
        ))
    out.sort(key=lambda e: (e.family, e.key))
    return out


def build_teams(idx, identities):
    """One page per current franchise."""
    by_key = {}
    for ident in identities:
        for record in ident.records:
            by_key[(idx.canonical(record["player"]), record["season"])] = ident

    buckets = collections.defaultdict(lambda: {"records": [], "players": set()})
    for record in idx.records:
        ident = by_key.get((idx.canonical(record["player"]), record["season"]))
        if ident is None:
            continue
        for code in F.team_codes(record):
            if code in idx.franchises:
                buckets[code]["records"].append(record)
                buckets[code]["players"].add(ident.key)

    lookup = {ident.key: ident for ident in identities}
    out = []
    for code, bucket in buckets.items():
        franchise = idx.franchises[code]
        out.append(Entity(
            "team", code, franchise["name"],
            records=bucket["records"],
            players=sorted(
                (lookup[k] for k in bucket["players"]),
                key=lambda p: (p.name, p.key),
            ),
            code=code, eras=franchise.get("eras", []),
        ))
    out.sort(key=lambda e: e.key)
    return out


def build_seasons(idx, identities):
    """One page per season on file."""
    by_key = {}
    for ident in identities:
        for record in ident.records:
            by_key[(idx.canonical(record["player"]), record["season"])] = ident

    buckets = collections.defaultdict(lambda: {"records": [], "players": set()})
    for record in idx.records:
        ident = by_key.get((idx.canonical(record["player"]), record["season"]))
        if ident is None:
            continue
        buckets[record["season"]]["records"].append(record)
        buckets[record["season"]]["players"].add(ident.key)

    lookup = {ident.key: ident for ident in identities}
    out = []
    for season, bucket in buckets.items():
        out.append(Entity(
            "season", season, season,
            records=bucket["records"],
            players=sorted(
                (lookup[k] for k in bucket["players"]),
                key=lambda p: (p.name, p.key),
            ),
        ))
    out.sort(key=lambda e: F.season_key(e.key))
    return out


def assign_slugs(book, entities, idx):
    """Give every entity its published slug, minting only what is new."""
    for entity in entities:
        if entity.family == "player":
            first = entity.records[0] if entity.records else {}
            entity.slug = book.get(
                "player", entity.key, entity.name,
                disambiguator=first.get("draft_year") or first.get("season"),
            )
        elif entity.family == "season":
            entity.slug = entity.key
        elif entity.family == "pick":
            entity.slug = "undrafted" if entity.key == "undrafted" else str(entity.key)
        elif entity.family == "draft":
            entity.slug = str(entity.key)
        elif entity.family == "position":
            entity.slug = {"G": "guard", "F": "forward", "C": "center"}[entity.key]
        elif entity.family in ("region", "pick_range"):
            # the key is already the slug: europe, lottery, second-round
            entity.slug = entity.key
        else:
            # the slug reads the printed name, so a college page says
            # michigan-state rather than the michigan-st the data stores
            entity.slug = book.get(entity.family, entity.key, entity.name)
    return entities


def build_all(idx, book):
    identities = build_player_identities(idx)
    cohorts = build_cohorts(idx, identities)
    teams = build_teams(idx, identities)
    seasons = build_seasons(idx, identities)
    everything = identities + cohorts + teams + seasons
    assign_slugs(book, everything, idx)
    return {
        "players": identities,
        "cohorts": cohorts,
        "teams": teams,
        "seasons": seasons,
        "all": everything,
    }
