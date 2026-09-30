"""The written summary at the top of a cohort page.

The engine writes one sentence per claim, which is right for a Slack digest
and wrong for a page: eight of them in a list repeat each other and bury the
three things a reader came for. This writer takes the same numbers and writes
them the way a person would say them.

House rules, all of them tested:

* the player is the subject and the verb is plain, "has earned", "made",
  "holds", "is due";
* the number never comes before the name, and no sentence is turned inside
  out to make room for one;
* the first sentence names the cohort, so a reader knows which list he is on;
* two or three short sentences, never more;
* money nobody has been paid is named as a contract;
* no em dashes.

Variety comes from which facts are picked and how they are combined, not from
odd phrasing: where one man holds the career and the single-season record, one
sentence says both. Which combination a page gets comes from a hash of its own
slug, so a page reads the same on every build and two pages do not fall into
step.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import factoids as F  # noqa: E402

from . import config as C  # noqa: E402
from .phrasing import drop_mirrors  # noqa: E402

#: A ratio is worth printing only when it is this lopsided.
RATIO_MIN = 2.0

#: Fewer members than this on a roster and "the best paid" says nothing, so
#: the slot is dropped unless one man is the last one left.
CURRENT_MIN_MEMBERS = 3

#: Contract seasons a sentence will name before it stops listing and gives the
#: last one instead.
FUTURE_SEASONS = 3


def _adjectives():
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "data", "country_adjectives.json",
    )
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh).get("adjectives") or {}


COUNTRY_ADJECTIVES = _adjectives()


class Names(object):
    """How one cohort is named inside a sentence.

    ``one`` and ``many`` are the noun ("Duke player", "Canadian players").
    ``record`` is what its single-season record is called. Nothing here is a
    filler phrase: a sentence that may not name the cohort says nothing about
    it at all rather than reaching for "on this list".
    """

    def __init__(self, family, key, name):
        self.family = family
        self.key = key
        self.name = name
        lower = name.lower()
        if family == "college":
            self.one = "{} player".format(name)
            self.many = "{} players".format(name)
            self.record = "the {} single-season record".format(name)
            self.rival = "anyone else from the school"
        elif family == "country":
            adjective = COUNTRY_ADJECTIVES.get(name)
            self.one = (
                "{} player".format(adjective) if adjective
                else "player from {}".format(name)
            )
            self.many = (
                "{} players".format(adjective) if adjective
                else "players from {}".format(name)
            )
            self.record = (
                "the {} single-season record".format(adjective) if adjective
                else "the single-season record for {}".format(name)
            )
            self.rival = "anyone else from the country"
        elif family == "draft":
            self.one = "player from the {} class".format(name)
            self.many = "the {} class".format(name)
            self.record = "the {} class single-season record".format(name)
            self.rival = "anyone else in the class"
        elif family == "pick":
            if key == "undrafted":
                self.one = "undrafted player"
                self.many = "undrafted players"
                self.record = "the undrafted single-season record"
                self.rival = "any other undrafted player"
            else:
                self.one = "No. {} pick".format(key)
                self.many = "No. {} picks".format(key)
                self.record = "the No. {} single-season record".format(key)
                self.rival = "any other pick at that spot"
        elif family == "position":
            single = lower[:-1] if lower.endswith("s") else lower
            self.one = single
            self.many = lower
            self.record = "the single-season record for a {}".format(single)
            self.rival = "any other {}".format(single)
        else:
            self.one = "client of {}".format(name)
            self.many = "clients of {}".format(name)
            self.record = "the single-season record at {}".format(name)
            self.rival = "any other client"

    @property
    def token(self):
        """The word that names this cohort outright, for the twice-a-page cap."""
        if self.family == "pick":
            return "No. {}".format(self.key) if self.key != "undrafted" else "undrafted"
        if self.family == "position":
            return self.one
        return self.name

    @property
    def tokens(self):
        """Every word that names it. France is named by "French" too."""
        out = [self.token]
        if self.family == "country":
            adjective = COUNTRY_ADJECTIVES.get(self.name)
            if adjective and adjective != self.token:
                out.append(adjective)
        return out

    def mentions(self, text):
        return sum(text.count(token) for token in self.tokens)


# --------------------------------------------------------------------------
# deterministic variety
# --------------------------------------------------------------------------

def pick(slug, salt, count):
    """A stable choice in [0, count), from the page's own slug."""
    if count <= 1:
        return 0
    digest = hashlib.sha1("{}|{}".format(slug, salt).encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") % count


def _money(value):
    return F.fmt_money(value)


def _possessive(name):
    return name + "'" if name.endswith("s") else name + "'s"


def _ratio(top, rival):
    if not rival or not top or rival <= 0:
        return None
    factor = top / float(rival)
    if factor >= 3:
        return "more than three times"
    if factor >= RATIO_MIN:
        return "more than double"
    return None


def _first_word(sentence):
    return sentence.split(" ", 1)[0].strip("“\"'").lower().rstrip(".,:")


# --------------------------------------------------------------------------
# the pools
#
# Every line is one a person could say out loud. The NAMED pools carry the
# cohort, the PLAIN pools carry none, and the SAME pools are for a sentence
# about the man the sentence before it was about.
# --------------------------------------------------------------------------

CAREER_NAMED = (
    "{name} has earned more than any other {one}: {money} through {through}.",
    "{name} is the top career earner among {many}, {money} through {through}.",
    "{name} has made more money than any other {one}, {money} through {through}.",
    "{name} tops every {one} in career earnings with {money} through {through}.",
)

CAREER_PLAIN = (
    "{name} has earned the most, {money} through {through}.",
    "{name} leads the career earnings table, {money} through {through}.",
    "{name} has been paid the most over a career, {money} through {through}.",
    "{name} has the biggest career total, {money} through {through}.",
)

CAREER_SAME = (
    "He has also earned the most over a career, {money} through {through}.",
    "His career earnings lead as well, {money} through {through}.",
    "He has been paid the most across a career too, {money} through {through}.",
    "His career total is the biggest as well, {money} through {through}.",
)

RECORD_NAMED = (
    "{name} holds {record} for one season, {money} in {season}.",
    "{name} has drawn the biggest single salary of any {one}, {money} in {season}.",
    "{name} has the biggest single season of any {one}, {money} in {season}.",
    "{name} set the single-season mark for {many}, {money} in {season}.",
)

RECORD_PLAIN = (
    "{name} made the most in one season, {money} in {season}.",
    "{name} holds the single-season record, {money} in {season}.",
    "{name} drew the biggest single salary, {money} in {season}.",
    "{name} set the single-season mark, {money} in {season}.",
)

RECORD_SAME = (
    "He also made the most in one season, {money} in {season}.",
    "He holds the single-season record too, {money} in {season}.",
    "His biggest season is the record as well, {money} in {season}.",
    "He set the single-season mark too, {money} in {season}.",
)

#: One man holding both titles gets one sentence for both.
BOTH_NAMED = (
    "{name} is the highest-paid {one} ever, both over a career "
    "({career} through {through}) and in one season ({best} in {season}).",
    "{name} leads every {one} twice over: {career} in career earnings through "
    "{through}, and {best} in {season} for a single year.",
    "{name} has earned more than any other {one}, {career} through {through}, "
    "and his {best} in {season} is the biggest single season too.",
    "{name} tops {many} both ways, {career} in career earnings through "
    "{through} and {best} in {season} for one season.",
)

CURRENT_NAMED = (
    "{name} is the highest-paid {one} this season, {money}.",
    "{name} leads {many} on this season's payroll at {money}.",
    "{name} is the best-paid {one} in {season}, at {money}.",
    "{name} earns more than any other {one} this season, {money}.",
)

CURRENT_PLAIN = (
    "{name} earns the most this season, {money}.",
    "{name} leads the {season} payroll at {money}.",
    "{name} is the best paid this season, {money}.",
    "{name} tops the current payroll at {money}.",
)

CURRENT_SAME = (
    "He is still the best paid this season, {money}.",
    "He leads the {season} payroll as well, {money}.",
    "He earns the most again this season, {money}.",
    "He is top of the payroll once more, {money}.",
)

LAST_ONE_SAME = (
    "He is the last {one} still on an NBA payroll, {money} this season.",
    "He is the only one left on a roster, {money} in {season}.",
    "He is the last still drawing an NBA salary, {money} in {season}.",
    "He is the only one still being paid, {money} this season.",
)

LAST_ONE_PLAIN = (
    "{name} is the last one still on an NBA payroll, {money} this season.",
    "{name} is the only one left on a roster, {money} in {season}.",
    "Only {name} is still drawing an NBA salary, {money} in {season}.",
    "{name} is the last still being paid, {money} this season.",
)

LAST_ONE_NAMED = (
    "{name} is the last {one} still on an NBA payroll, {money} this season.",
    "{name} is the only {one} left on a roster, {money} in {season}.",
    "Only {name} is still drawing an NBA salary, {money} in {season}.",
    "{name} is the last of {many} still being paid, {money} this season.",
)

#: A contract that keeps climbing, about the man the sentence before named.
#: These point back at the figure that sentence gave, so they are only used
#: where it was the single-season record or what he earns now.
FUTURE_CLIMB_AFTER = (
    "His contract raises it every year until it reaches {peak} in {peak_season}.",
    "His contract keeps pushing that higher, to {peak} by {peak_season}.",
    "He is due more each year after that, up to {peak} in {peak_season}.",
    "His deal climbs to {peak} in {peak_season}.",
)

FUTURE_CLIMB_SAME = (
    "He is due more each year, up to {peak} in {peak_season}.",
    "He is signed through {peak_season}, when he is due {peak}.",
    "He has a deal that climbs to {peak} in {peak_season}.",
    "He will be paid {peak} in {peak_season} on his current deal.",
)

FUTURE_CLIMB = (
    "{name} is due more each year, up to {peak} in {peak_season}.",
    "{name} is signed through {peak_season}, when he is due {peak}.",
    "{name} has a deal that climbs to {peak} in {peak_season}.",
    "{name} will be paid {peak} in {peak_season} on his current deal.",
)

FUTURE_FLAT = (
    "{name} is due {list}.",
    "{name} still has {list} to come.",
    "{name} is signed for {list}.",
    "{name} will be paid {list} on his current deal.",
)

FUTURE_FLAT_SAME = (
    "He is due {list}.",
    "He still has {list} to come.",
    "He is signed for {list}.",
    "His deal still carries {list}.",
)


def _merge_seasons(rows):
    parts = ["{} in {}".format(_money(value), season) for season, value in rows]
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return "{} and {}".format(*parts)
    return "{} and {}".format(", ".join(parts[:-1]), parts[-1])


# --------------------------------------------------------------------------
# the facts
# --------------------------------------------------------------------------

class _CareerField(object):
    """The career table as a universe, so the engine's own pre-window guard
    can read it."""

    def __init__(self, entries):
        self.entries = entries


def _as_universe(idx, career):
    return _CareerField([
        {"value": total, "pre_window": idx.pre_window_career(ident.data_key),
         "display": ident.name}
        for total, ident, _last in career
    ])


def _future_claims(idx, entries, limit=FUTURE_SEASONS):
    """Contracted single-season claims, one player, nearest season first.

    The engine decides what is notable: a season has a claim here only if it
    ranks inside the cohort's own list, and a salary the engine flagged as
    impossible has no claim at all.
    """
    by_subject = {}
    for season, fact in entries:
        if not fact.get("contracted"):
            continue
        if not fact["key"].startswith("cohort_season|"):
            continue
        subject = fact["key"].split("|")[-2]
        by_subject.setdefault(subject, []).append((season, fact))
    if not by_subject:
        return None
    subject = max(
        sorted(by_subject),
        key=lambda s: max(f.get("value") or 0 for _season, f in by_subject[s]),
    )
    rows = sorted(
        {(season, fact.get("value")) for season, fact in by_subject[subject]},
        key=lambda row: F.season_key(row[0]),
    )[:limit]
    if not rows:
        return None
    climbs = len(rows) > 1 and all(
        rows[i][1] > rows[i - 1][1] for i in range(1, len(rows))
    )
    return {
        "subject": subject,
        "name": idx.display_name(subject, rows[0][0]),
        "rows": rows,
        "climbs": climbs,
        "peak": rows[-1][1],
        "peak_season": rows[-1][0],
        "tops": any(fact["type"] == "sets" for _season, fact in by_subject[subject]),
    }


def _choose(pool, slug, salt, avoid_word=None, **kw):
    """A phrasing from the pool, never opening on ``avoid_word``.

    The hash picks first; the walk after it is the tie-break, so the result is
    the same on every build.
    """
    start = pick(slug, salt, len(pool))
    for step in range(len(pool)):
        text = pool[(start + step) % len(pool)].format(**kw)
        if avoid_word is None or _first_word(text) != avoid_word:
            return text
    return pool[start].format(**kw)


# --------------------------------------------------------------------------
# the writer
# --------------------------------------------------------------------------

def cohort_summary(idx, family, key, name, career, paid, current, facts,
                   slug=None):
    """Two or three sentences about one cohort.

    ``career`` is [(total, identity, last record)], ``paid`` and ``current``
    are [(record, identity)], all sorted biggest first. ``facts`` is
    [(season, fact)] for this cohort straight from factoids.json.
    """
    slug = slug or "{}/{}".format(family, key)
    names = Names(family, key, name)
    entries = drop_mirrors(list(facts))

    # ---- what there is to say --------------------------------------------
    shadowed = bool(career) and F.pre_window_shadow(
        _as_universe(idx, career), career[0][0])
    leader = None
    if career and not shadowed:
        total, ident, last = career[0]
        _total, through = idx.paid_through(ident.data_key)
        leader = {
            "ident": ident, "name": ident.name, "total": total,
            "through": through or last["season"],
            "rival": career[1][0] if len(career) > 1 else None,
        }

    record = None
    if paid:
        top, ident = paid[0]
        record = {
            "ident": ident, "name": ident.name, "salary": top.get("salary"),
            "season": top["season"],
        }

    now = None
    if current:
        top, ident = current[0]
        only = len(current) == 1
        repeats = (
            record is not None and record["ident"] is ident
            and record["season"] == idx.current_season
        )
        if not repeats and (only or len(current) >= CURRENT_MIN_MEMBERS):
            now = {
                "ident": ident, "name": ident.name, "salary": top.get("salary"),
                "only": only,
            }

    future = _future_claims(idx, entries)
    both = (
        leader is not None and record is not None
        and leader["ident"] is record["ident"]
    )

    # ---- the plan ---------------------------------------------------------
    out = []
    budget = [2]        # times this summary may name the cohort
    previous = [None]   # the identity the sentence before was about
    previous_slot = [None]

    def last_word():
        return _first_word(out[-1]) if out else None

    def add(text, subject, slot):
        if out and _first_word(text) == last_word():
            return False
        out.append(text)
        budget[0] -= names.mentions(text)
        previous[0] = subject
        previous_slot[0] = slot
        return True

    def may_name():
        return budget[0] > 0

    def same(ident):
        return bool(out) and previous[0] is not None and previous[0] is ident

    # ---- the sentences ----------------------------------------------------
    def both_sentence():
        text = _choose(
            BOTH_NAMED, slug, "both", avoid_word=last_word(),
            name=leader["name"], one=names.one, many=names.many,
            career=_money(leader["total"]), through=leader["through"],
            best=_money(record["salary"]), season=record["season"],
        )
        return text, leader["ident"]

    def career_sentence():
        if same(leader["ident"]):
            pool, kw = CAREER_SAME, {}
        elif may_name() or not out:
            pool, kw = CAREER_NAMED, {"one": names.one, "many": names.many}
        else:
            pool, kw = CAREER_PLAIN, {}
        text = _choose(
            pool, slug, "career", avoid_word=last_word(),
            name=leader["name"], money=_money(leader["total"]),
            through=leader["through"], **kw
        )
        ratio = _ratio(leader["total"], leader["rival"])
        if ratio:
            # the cohort is not named twice inside one sentence
            rival = (
                names.rival if names.mentions(text)
                else "any other {}".format(names.one)
            )
            text = "{}, {} {}.".format(text.rstrip("."), ratio, rival)
        return text, leader["ident"]

    def record_sentence():
        if same(record["ident"]):
            pool, kw = RECORD_SAME, {}
        elif may_name() or not out:
            pool, kw = RECORD_NAMED, {
                "one": names.one, "many": names.many, "record": names.record}
        else:
            pool, kw = RECORD_PLAIN, {}
        text = _choose(
            pool, slug, "record", avoid_word=last_word(),
            name=record["name"], money=_money(record["salary"]),
            season=record["season"], **kw
        )
        return text, record["ident"]

    def current_sentence():
        if now["only"] and same(now["ident"]):
            pool, kw = LAST_ONE_SAME, {"one": names.one}
        elif now["only"] and (may_name() or not out):
            pool, kw = LAST_ONE_NAMED, {"one": names.one, "many": names.many}
        elif now["only"]:
            pool, kw = LAST_ONE_PLAIN, {}
        elif same(now["ident"]):
            pool, kw = CURRENT_SAME, {}
        elif may_name() or not out:
            pool, kw = CURRENT_NAMED, {"one": names.one, "many": names.many}
        else:
            pool, kw = CURRENT_PLAIN, {}
        text = _choose(
            pool, slug, "current", avoid_word=last_word(),
            name=now["name"], money=_money(now["salary"]),
            season=idx.current_season, **kw
        )
        return text, now["ident"]

    def future_sentence():
        same_man = (
            previous[0] is not None and future["name"] == previous[0].name
        )
        points_back = previous_slot[0] in ("record", "current", "both")
        if future["climbs"]:
            if same_man:
                pool = FUTURE_CLIMB_AFTER if points_back else FUTURE_CLIMB_SAME
            else:
                pool = FUTURE_CLIMB
            kw = {"peak": _money(future["peak"]), "peak_season": future["peak_season"]}
        else:
            pool = FUTURE_FLAT_SAME if same_man else FUTURE_FLAT
            kw = {"list": _merge_seasons(future["rows"])}
        text = _choose(
            pool, slug, "future", avoid_word=last_word(),
            name=future["name"], **kw
        )
        return text, None

    # A page gets two sentences or three. A contract that would beat the
    # cohort's record, and a man who is the last of his group still being
    # paid, are both worth the third.
    want = 3 if (
        pick(slug, "length", 2)
        or (future and future["tops"])
        or (now and now["only"])
    ) else 2

    order = []
    # One man holding both titles can have one sentence for both or two, the
    # second of them about "he". Either reads; the slug decides which.
    # ...but never where merging would leave one sentence standing alone.
    merge = both and pick(slug, "merge", 2) and (now or future)
    if merge:
        order.append(("both", both_sentence))
    elif both:
        order.append(("career", career_sentence))
        order.append(("record", record_sentence))
    else:
        record_first = bool(pick(slug, "order", 2)) and record and leader
        if record_first:
            order.append(("record", record_sentence))
            if leader:
                order.append(("career", career_sentence))
        else:
            if leader:
                order.append(("career", career_sentence))
            if record:
                order.append(("record", record_sentence))
    if future and future["tops"]:
        order.append(("future", future_sentence))
        if now:
            order.append(("current", current_sentence))
    else:
        if now:
            order.append(("current", current_sentence))
        if future:
            order.append(("future", future_sentence))

    for slot, build in order:
        if len(out) >= want:
            break
        text, subject = build()
        add(text, subject, slot)

    # A cohort with a career close to the top that began before the data did
    # gets no leader sentence, so it gets the reason instead.
    if shadowed and len(out) < 2:
        out.append(
            "Some of the biggest careers here began before the salary data "
            "does, so no career total on this page is the whole of what the "
            "man earned."
        )
    return out[:3]
