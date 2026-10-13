"""Group leaders: the line a player page opens "What the numbers say" with.

When a man is No. 1 in one of his groups, in single-season salary or in career
earnings, his page says so: "Fred VanVleet is the highest-paid undrafted player
ever, both in a single season ($42.8 million in 2024-25) and in career earnings
($215.7 million through 2026-27)."

The numbers are the group pages' own. A group is a cohort page (undrafted,
draft pick, draft class, college, country, region, position), its members are
that page's members, its single-season leader is the top of its "Highest
single-season salaries" table and its career leader the top of its "Highest
career earnings" table, so a player page and the group page it links to never
disagree. A group without a page (a college under its page minimum) cannot
produce a line.

The gates, each one named below:

* No. 1 only, and never a tie.
* A career line needs at least CAREER_MIN_SEASONS paid seasons, or a rookie
  deal would lead its draft class.
* "Ever" has to be true. Salary data starts in 1990-91. Every salary before it
  was smaller than the biggest one of 1990-91, so a single-season record above
  that figure is the biggest ever. A career that began before 1990-91 is short
  by its first seasons, so a career line is dropped when the leader's own
  career began before the data does, when anyone whose career did sits within
  the engine's PRE_WINDOW_MARGIN of him, or when the total is under that
  margin, where a career that ended before 1990-91 could still be bigger.
* A narrower group says nothing a wider one has said: a country line goes when
  he also leads a region he is in, and a region line goes when he also leads
  international players.
"""

from __future__ import annotations

import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import factoids as F  # noqa: E402

from .render import money_short, plain_name  # noqa: E402
from .summary import COUNTRY_ADJECTIVES  # noqa: E402

#: The page families a leader line can name, widest first within each kind.
FAMILIES = ("pick", "draft", "college", "country", "region", "position")

#: Paid seasons a career needs before it can lead a group's career table in a
#: sentence. Without it Cooper Flagg leads the 2025 draft class on one rookie
#: deal.
CAREER_MIN_SEASONS = 4

#: Groups left out of this rule. South Sudan: Luol Deng, born there, is filed
#: under Great Britain, so "the highest-paid South Sudanese player ever" would
#: name Khaman Maluach at $6.3 million and a reader would call it wrong.
SKIP = frozenset({("country", "South Sudan")})

SINGLE = "single"
CAREER = "career"


class Lead(object):
    """One group a man leads, in one measure."""

    __slots__ = ("family", "key", "noun", "measure", "value", "season",
                 "passer", "pass_season")

    def __init__(self, family, key, noun, measure, value, season,
                 passer=None, pass_season=None):
        self.family = family
        self.key = key
        self.noun = noun
        self.measure = measure
        self.value = value
        self.season = season
        self.passer = passer
        self.pass_season = pass_season

    @property
    def field(self):
        """The factoid engine's (kind, key) for this group."""
        return (FAMILY_KIND[self.family], self.key)


FAMILY_KIND = {
    "pick": "draft_slot", "draft": "draft_class", "college": "college",
    "country": "nationality", "region": "region", "position": "position",
}


# --------------------------------------------------------------------------
# the inventory
# --------------------------------------------------------------------------

def build(idx, cohorts, identities):
    """{identity key: [Lead]} for every man who leads one of his groups."""
    owners = {}
    for ident in identities:
        for record in ident.records:
            owners[id(record)] = ident
    ceiling = _pre_window_ceiling(idx)

    leads = collections.defaultdict(list)
    for entity in cohorts:
        if entity.family not in FAMILIES or (entity.family, entity.key) in SKIP:
            continue
        records, players = _members(entity)
        noun = _noun(idx, entity)

        single = _single_leader(idx, records, owners, ceiling)
        if single:
            ident, record, passer = single
            leads[ident.key].append(Lead(
                entity.family, entity.key, noun, SINGLE, record["salary"],
                record["season"], *(passer or (None, None))))

        career = _career_leader(idx, players)
        if career:
            ident, total, through = career
            leads[ident.key].append(Lead(
                entity.family, entity.key, noun, CAREER, total, through))

    return {key: _nest(idx, found) for key, found in leads.items()}


def _members(entity):
    """The records and players a group page ranks.

    A draft class page ranks the men taken in it; those who went undrafted that
    year are listed below on their own and lead nothing there.
    """
    if entity.family != "draft":
        return entity.records, entity.players
    went = {id(r) for r in entity.records if F.is_undrafted(r)}
    records = [r for r in entity.records if id(r) not in went]
    players = [p for p in entity.players
               if not any(id(r) in went for r in p.records)]
    return records, players


def _pre_window_ceiling(idx):
    """The biggest salary of the first season on file. Every salary before
    1990-91 was smaller, so a record above it is a record ever."""
    return max(
        (r.get("salary") or 0 for r in idx.records
         if r["season"] == F.SCOPE_FIRST_SEASON and not idx.is_impossible(r)),
        default=0)


def _single_leader(idx, records, owners, ceiling):
    """(identity, record, (passer name, season) or None), or None.

    The top paid season, as the group page's single-season table orders it.
    None where the top two men are level, where the leader is not a man the
    page names, or where the record is not above the pre-1990-91 ceiling.
    """
    paid = sorted(
        (r for r in records
         if not idx.is_contracted(r["season"]) and r.get("salary")
         and not idx.is_impossible(r)),
        key=lambda r: (-(r.get("salary") or 0), r["player"], r["season"]))
    if not paid:
        return None
    top = paid[0]
    leader = owners.get(id(top))
    if leader is None or not leader.extra.get("factoids_allowed"):
        return None
    for record in paid[1:]:
        if owners.get(id(record)) is not leader:
            if record["salary"] == top["salary"]:
                return None
            break
    if top["salary"] <= ceiling:
        return None
    return leader, top, _passer(idx, records, owners, leader, top["salary"])


def _passer(idx, records, owners, leader, mark):
    """Another man signed to take the record, and when.

    Taking it means beating the record and whatever the leader himself is
    signed for by then: the No. 2 pick's fourth season beats the No. 1 pick's
    rookie salary, but not the No. 1 pick's fourth season. The earliest season
    that does it; the biggest salary in that season where two do.
    """
    by_season = collections.defaultdict(list)
    for r in records:
        if idx.is_contracted(r["season"]) and r.get("salary") \
                and not idx.is_impossible(r) and owners.get(id(r)) is not None:
            by_season[r["season"]].append(r)
    for season in sorted(by_season, key=F.season_key):
        rows = by_season[season]
        mark = max([mark] + [r["salary"] for r in rows
                             if owners.get(id(r)) is leader])
        beats = sorted(
            (r for r in rows
             if owners.get(id(r)) is not leader and r["salary"] > mark),
            key=lambda r: (-r["salary"], r["player"]))
        if beats:
            return plain_name(owners[id(beats[0])].name), season
    return None


def _career_leader(idx, players):
    """(identity, total, season it runs through), or None.

    The top of the group page's career table: money already paid, the season
    being played included, through each man's last season that is not
    contracted.
    """
    entries = []
    for ident in players:
        if ident.extra.get("segment") is not None:
            continue
        if not idx.career_rankable(ident.data_key):
            continue
        total, through = idx.paid_through(ident.data_key)
        if total is None:
            continue
        entries.append((total, ident, through))
    if not entries:
        return None
    entries.sort(key=lambda t: (-t[0], t[1].name, t[1].key))
    total, leader, through = entries[0]
    if len(entries) > 1 and entries[1][0] == total:
        return None
    if not leader.extra.get("factoids_allowed"):
        return None
    paid_seasons = {r["season"] for r in leader.records
                    if not idx.is_contracted(r["season"])}
    if len(paid_seasons) < CAREER_MIN_SEASONS:
        return None
    # "ever" over a career: his whole career is on file, nobody whose career
    # is not could be within reach of him, and the total is past what a career
    # that ended before 1990-91 could have reached
    if total < F.PRE_WINDOW_MARGIN:
        return None
    if idx.pre_window_career(leader.data_key):
        return None
    for other, ident, _through in entries[1:]:
        if idx.pre_window_career(ident.data_key) \
                and other >= total - F.PRE_WINDOW_MARGIN:
            return None
    return leader, total, through


def _nest(idx, leads):
    """Drop a narrower group a wider one he also leads already covers."""
    held = {(lead.family, lead.key, lead.measure) for lead in leads}
    out = []
    for lead in leads:
        if lead.family == "country":
            region = idx.continents.get(lead.key)
            if ("region", region, lead.measure) in held \
                    or ("region", "international", lead.measure) in held:
                continue
        if lead.family == "region" and lead.key != "international" \
                and ("region", "international", lead.measure) in held:
            continue
        out.append(lead)
    out.sort(key=lambda lead: (FAMILIES.index(lead.family), lead.key))
    return out


# --------------------------------------------------------------------------
# naming a group
# --------------------------------------------------------------------------

def _noun(idx, entity):
    """The singular after "the highest-paid": "undrafted player", "No. 41
    pick", "Serbian player", "center"."""
    family, key = entity.family, entity.key
    if family == "pick":
        return "undrafted player" if key == "undrafted" else "No. {} pick".format(key)
    if family == "draft":
        return "player from the {} draft class".format(key)
    if family == "college":
        return "{} player".format(idx.college_display(key))
    if family == "country":
        adjective = COUNTRY_ADJECTIVES.get(key)
        if adjective:
            return "{} player".format(adjective)
        article = "the " if key in F.NATIONALITY_TAKES_THE else ""
        return "player from {}{}".format(article, key)
    if family == "region":
        return entity.extra.get("noun_one") or "player"
    if family == "position":
        return F.position_group(key)[1] or "player"
    return "player"


#: Initialisms read letter by letter whose first letter opens on a vowel
#: sound: "an LSU player", "an SMU player".
_VOWEL_LETTERS = set("AEFHILMNORSX")


def _article(noun):
    """"a" or "an" for the noun as it is read aloud."""
    word = noun.split(" ", 1)[0]
    letters = word.replace(".", "").replace("&", "")
    if len(letters) > 1 and letters.isupper() and letters.isalpha():
        return "an" if letters[0] in _VOWEL_LETTERS else "a"
    if word.lower().startswith("un"):
        return "an"
    if word[:1].upper() in "AEIO":
        return "an"
    return "a"


def _a(noun):
    return "{} {}".format(_article(noun), noun)


def _join(items, word):
    items = list(items)
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return "{} {} {}".format(items[0], word, items[1])
    return "{} {} {}".format(", ".join(items[:-1]), word, items[-1])


# --------------------------------------------------------------------------
# the sentences
# --------------------------------------------------------------------------

def sentences(name, leads):
    """(sentences, gives the career total) for one man's leads, or ([], False).

    Never more than three: the groups he leads both ways, then what he leads
    one way only, then any record another man is already signed to pass.
    """
    if not leads:
        return [], False
    name = plain_name(name)
    single = [lead for lead in leads if lead.measure == SINGLE]
    career = [lead for lead in leads if lead.measure == CAREER]
    single_keys = {(lead.family, lead.key) for lead in single}
    career_keys = {(lead.family, lead.key) for lead in career}
    both = [lead for lead in single if (lead.family, lead.key) in career_keys]
    single_only = [lead for lead in single
                   if (lead.family, lead.key) not in career_keys]
    career_only = [lead for lead in career
                   if (lead.family, lead.key) not in single_keys]

    best = single[0] if single else None
    total = career[0] if career else None
    out = []
    if both:
        # One group names the sentence; "the highest-paid No. 7 pick, player
        # from the 2009 draft class and guard ever" is a list, not a sentence,
        # so any others follow in the next one.
        out.append(
            "{} is the highest-paid {} ever, both in a single season ({} in {}) "
            "and in career earnings ({} through {}).".format(
                name, both[0].noun, money_short(best.value), best.season,
                money_short(total.value), total.season))
        parts = []
        if both[1:]:
            parts.append("both are also records for {}".format(
                _or(both[1:])))
        if single_only:
            parts.append("his {} salary is {}the highest ever for {}".format(
                best.season, "" if parts else "also ", _or(single_only)))
        if career_only:
            parts.append("his career total is {}the most ever by {}".format(
                "" if parts else "also ", _or(career_only)))
        if parts:
            out.append(_cap(_clauses(parts)) + ".")
    else:
        parts = []
        if single_only:
            parts.append(
                "{} {} in {} is the highest single-season salary ever paid to "
                "{}".format(_possessive(name), money_short(best.value),
                            best.season, _or(single_only)))
        if career_only:
            parts.append(
                "{} {} in career earnings through {} is the most ever by "
                "{}".format("his" if parts else _possessive(name),
                            money_short(total.value), total.season,
                            _or(career_only)))
        out.append(_clauses(parts) + ".")

    passing = _passing(single)
    if passing:
        out.append(passing)
    return out, bool(career)


def _or(leads):
    return _join([_a(lead.noun) for lead in leads], "or")


def _passing(single):
    """"Austin Reaves is contracted to pass the single-season mark in 2027-28."

    One sentence however many marks are about to fall. The mark is named by its
    group where the paragraph has named more than one.
    """
    by_passer = collections.OrderedDict()
    for lead in single:
        if lead.passer:
            by_passer.setdefault((lead.passer, lead.pass_season), []).append(lead)
    if not by_passer:
        return ""
    clauses = []
    for i, ((passer, season), passed) in enumerate(by_passer.items()):
        if len(single) == 1:
            mark = "the single-season mark"
        elif i == 0:
            mark = "the single-season mark for {}".format(_or(passed))
        else:
            mark = "the one for {}".format(_or(passed))
        if i == 0:
            clauses.append("{} is contracted to pass {} in {}".format(
                passer, mark, season))
        else:
            clauses.append("{} {} in {}".format(passer, mark, season))
    return _clauses(clauses) + "."


def _clauses(parts):
    """Whole clauses, joined the way independent clauses take "and"."""
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + ", and " + parts[-1]


def _cap(text):
    return text[:1].upper() + text[1:]


def _possessive(name):
    return name + ("'" if name.endswith("s") else "'s")
