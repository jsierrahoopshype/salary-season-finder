"""The written summary at the top of a cohort page.

The engine writes one sentence per claim, which is right for a Slack digest
and wrong for a page: eight of them in a list repeat each other and bury the
three things a reader came for. This writer takes the same numbers and writes
them as prose.

Four things are worth saying about a cohort: who has earned the most, whose
single season is the biggest, who is paid most right now, and what the signed
money ahead would do. Saying all four, in that order, in the same shape, on
220 pages, reads like a form letter. So every slot has a pool of phrasings,
the order and the length vary, and which variant a page gets is decided by a
hash of its own slug: stable between builds, different between pages.

Rules that hold on every page: no two sentences in a row open on the same
word, the cohort is named at most twice, a subject who comes back is "he",
money that has not been paid is conditional, and there are no em dashes.
"""

from __future__ import annotations

import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import factoids as F  # noqa: E402

from . import config as C  # noqa: E402
from .phrasing import drop_mirrors  # noqa: E402

#: A ratio is worth printing only when it is this lopsided.
RATIO_MIN = 2.0

#: Fewer members than this on a roster and "the best paid of them" says
#: nothing, so the slot is dropped unless one man is the last one left.
CURRENT_MIN_MEMBERS = 3

#: How many seasons of a contract a sentence lists before it stops.
FUTURE_SEASONS = 3


# --------------------------------------------------------------------------
# naming the cohort
# --------------------------------------------------------------------------

def _adjectives():
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "data", "country_adjectives.json",
    )
    if not os.path.exists(path):
        return {}
    import json
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh).get("adjectives") or {}


COUNTRY_ADJECTIVES = _adjectives()


class Names(object):
    """Every way one cohort can be named inside a sentence.

    ``one`` and ``many`` name it plainly. ``label`` is the one that does not
    name it at all, which is how a summary mentions a cohort twice instead of
    five times: "the program", "the class", "the slot".
    """

    def __init__(self, family, key, name):
        self.family = family
        self.key = key
        self.name = name
        lower = name.lower()
        if family == "college":
            self.one = "{} player".format(name)
            self.many = "{} players".format(name)
            self.label = "the program"
            self.of = "out of {}".format(name)
        elif family == "country":
            adjective = COUNTRY_ADJECTIVES.get(name)
            self.one = "{} player".format(adjective) if adjective else "player from {}".format(name)
            self.many = "{} players".format(adjective) if adjective else "players from {}".format(name)
            self.label = "the country"
            self.of = "from {}".format(name)
        elif family == "draft":
            self.one = "player from the {} class".format(name)
            self.many = "the {} class".format(name)
            self.label = "the class"
            self.of = "from the {} draft".format(name)
        elif family == "pick":
            if key == "undrafted":
                self.one = "undrafted player"
                self.many = "undrafted players"
                self.label = "the undrafted"
                self.of = "who went undrafted"
            else:
                self.one = "No. {} pick".format(key)
                self.many = "No. {} picks".format(key)
                self.label = "the slot"
                self.of = "taken at No. {}".format(key)
        elif family == "position":
            single = lower[:-1] if lower.endswith("s") else lower
            self.one = single
            self.many = lower
            self.label = "the position"
            self.of = "at {}".format(single)
        else:
            self.one = "client of {}".format(name)
            self.many = "clients of {}".format(name)
            self.label = "the agency"
            self.of = "with {}".format(name)

    #: The token that names this cohort outright, for the twice-a-summary cap.
    @property
    def token(self):
        if self.family == "pick":
            return "No. {}".format(self.key) if self.key != "undrafted" else "undrafted"
        if self.family == "position":
            return self.one
        return self.name

    def mentions(self, text):
        return text.count(self.token)

    def generic(self):
        """The same cohort, named by what it is rather than which one it is.

        A summary says "Duke" twice at most; after that the sentences say "the
        program" and "anyone on this list", which are true of the page and
        stop it reading like a form letter.
        """
        other = Names.__new__(Names)
        other.family = self.family
        other.key = self.key
        other.name = self.name
        other.one = "player on this list"
        other.many = "this list"
        other.label = self.label
        other.of = "on this list"
        return other


# --------------------------------------------------------------------------
# deterministic variety
# --------------------------------------------------------------------------

def pick(slug, salt, count):
    """A stable choice in [0, count), from the page's own slug.

    The same page picks the same phrasing on every build, and two pages with
    different slugs pick independently. Nothing about the numbers goes into
    the hash, so wording only moves when the writer changes.
    """
    if count <= 1:
        return 0
    digest = hashlib.sha1("{}|{}".format(slug, salt).encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") % count


def _money(value):
    return F.fmt_money(value)


def _possessive(name):
    return name + "'" if name.endswith("s") else name + "'s"


def _merge_seasons(rows):
    """"$18.1 million in 2027-28 and $19 million in 2028-29"."""
    parts = ["{} in {}".format(_money(value), season) for season, value in rows]
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return "{} and {}".format(*parts)
    return "{} and {}".format(", ".join(parts[:-1]), parts[-1])


def _ratio(top, rival):
    if not rival or not top or rival <= 0:
        return None
    factor = top / float(rival)
    if factor >= 3:
        return "more than three times"
    if factor >= RATIO_MIN:
        return "more than double"
    return None


# --------------------------------------------------------------------------
# the four slots, each with a pool
# --------------------------------------------------------------------------

#: {name}, {money}, {through}, {one}, {many}, {label}, {of}, {he}, {his}
CAREER_POOL = (
    "No {one} has made more in the NBA than {name}, at {money} through {through}.",
    "{name} tops {many} with {money} through {through}.",
    "{money} through {through} puts {name} ahead of every other {one}.",
    "Nobody {of} has been paid more than {name}, {money} through {through}.",
    "{name} has out-earned every other {one}, {money} through {through}.",
)

#: Used when the single-season record came first and the same man leads the
#: career list, so his name does not open two sentences in a row.
CAREER_SAME_POOL = (
    "He has also earned more than any other {one}, {money} through {through}.",
    "Nobody {of} has been paid more across a career either: {money} through {through}.",
    "His {money} through {through} leads {many} as well.",
    "That career is the richest of them too, {money} through {through}.",
)

SEASON_POOL = (
    "{name} owns {label}'s biggest single season, {money} in {season}.",
    "The largest single salary belongs to {name}: {money} in {season}.",
    "No {one} has been paid more in one year than {money}, which {name} drew in {season}.",
    "{money} in {season} is the biggest single season any {one} has drawn, and it is {possessive}.",
    "Nothing tops {possessive} {money} in {season} for one season's work.",
)

#: Used when the career leader and the record holder are the same man, so the
#: two slots become one sentence.
SEASON_SAME_POOL = (
    "His {money} in {season} is the largest salary any {one} has drawn.",
    "He also owns the biggest single season, {money} in {season}.",
    "No single season beats his own {money} in {season}.",
    "That {money} in {season} is the biggest one year any {one} has had.",
)

CURRENT_POOL = (
    "In {season} the best-paid {one} is {name}, at {money}.",
    "{name} heads the {season} payroll list at {money}.",
    "{money} makes {name} the highest-paid {one} on a roster this season.",
    "This season it is {name} earning the most of them, {money}.",
)

CURRENT_SAME_POOL = (
    "He is still the best-paid {one} in {season}, at {money}.",
    "He leads them again in {season}, at {money}.",
    "{money} keeps him at the top of the {season} list.",
    "In {season} he is the best paid of them once more, at {money}.",
)

LAST_ONE_POOL = (
    "{name} is the last {one} still on an NBA payroll, at {money} this season.",
    "Only {name} is still drawing an NBA salary, {money} in {season}.",
    "{name} alone is left on a roster, earning {money} in {season}.",
    "One {one} is still being paid: {name}, {money} in {season}.",
)

#: Contracts that would beat everything the cohort has been paid. The first
#: two pools refer back to the record, so they are only used where the
#: sentence before them is the one that stated it.
FUTURE_TOPS_AFTER_POOL = (
    "{possessive} contract keeps raising that bar: {list}.",
    "That mark will not last: {name} is due {list}.",
    "The money ahead is bigger still, with {name} signed for {list}.",
    "{name} is signed past it, for {list}.",
)

FUTURE_TOPS_AFTER_SAME_POOL = (
    "His contract keeps raising that bar: {list}.",
    "He is signed past it, for {list}.",
    "The deal runs on and climbs: {list}.",
    "Ahead of him sit {list}.",
)

FUTURE_TOPS_POOL = (
    "{name} is signed for {list}, more than any {one} has been paid for a season.",
    "The biggest season is still ahead: {name} is due {list}.",
    "No {one} has been paid what {name} is owed, {list}.",
    "{possessive} contract goes past all of it, {list}.",
)

FUTURE_TOPS_SAME_POOL = (
    "His contract goes past all of it: {list}.",
    "He is signed for {list}, more than any {one} has been paid for a season.",
    "The deal climbs from there: {list}.",
    "Nothing a {one} has been paid matches what he is owed next, {list}.",
)

#: Contracts worth naming that do not beat the cohort's own record.
FUTURE_POOL = (
    "{name} is due {list}.",
    "{possessive} deal is worth {list}.",
    "Among the money still to come, {name} is down for {list}.",
    "{name} has {list} left on his deal.",
)

FUTURE_SAME_POOL = (
    "He is due {list} on top of it.",
    "His deal still carries {list}.",
    "The contract runs on: {list}.",
    "Ahead of him sit {list}.",
)


# --------------------------------------------------------------------------
# the writer
# --------------------------------------------------------------------------

class _CareerField(object):
    """The career table as a universe, so the engine's own pre-window guard
    can read it. One attribute is all ``pre_window_shadow`` touches."""

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

    The engine decides what counts as notable: a season only has a claim here
    if it ranks inside the cohort's own list, and a salary the engine flagged
    as impossible has no claim at all.
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
    return {
        "subject": subject,
        "name": idx.display_name(subject, rows[0][0]),
        "rows": rows,
        "tops": any(fact["type"] == "sets" for _season, fact in by_subject[subject]),
    }


def _fill(template, **kw):
    return template.format(**kw)


def _first_word(sentence):
    word = sentence.split(" ", 1)[0].strip("“\"'")
    return word.lower().rstrip(".,:")


def _choose(pool, slug, salt, avoid_word=None, **kw):
    """A phrasing from the pool, never opening on ``avoid_word``.

    The hash picks first; the walk after it is the tie-break, so the result is
    still the same on every build.
    """
    start = pick(slug, salt, len(pool))
    for step in range(len(pool)):
        text = _fill(pool[(start + step) % len(pool)], **kw)
        if avoid_word is None or _first_word(text) != avoid_word:
            return text
    return _fill(pool[start], **kw)


def cohort_summary(idx, family, key, name, career, paid, current, facts,
                   slug=None):
    """Two or three sentences about one cohort.

    ``career`` is [(total, identity, last record)], ``paid`` and ``current``
    are [(record, identity)], all sorted biggest first. ``facts`` is
    [(season, fact)] for this cohort straight from factoids.json.
    """
    slug = slug or "{}/{}".format(family, key)
    plain = Names(family, key, name)
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
        rival = next((r.get("salary") for r, i in paid if i is not ident), None)
        record = {
            "ident": ident, "name": ident.name, "salary": top.get("salary"),
            "season": top["season"], "rival": rival,
        }

    now = None
    if current:
        top, ident = current[0]
        only = len(current) == 1
        # The current leader is worth a sentence of his own only where it is
        # not the sentence above it: a record set this season by this man is
        # one fact, not two.
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

    # ---- which slots, in which order -------------------------------------
    season_first = bool(pick(slug, "order", 2)) and leader is not None and record
    # Two sentences or three, by the slug. A contract that would beat
    # everything the cohort has been paid always earns the third: it is the
    # one thing on the page a reader cannot get from the tables.
    want = 3 if (
        pick(slug, "length", 2)
        or (future and future["tops"])
        or (now and now["only"])
    ) else 2

    out = []
    budget = [2]          # times this summary may name the cohort outright
    previous = [None]     # the identity the last sentence was about
    previous_slot = [None]

    def names_for():
        return plain if budget[0] > 0 else plain.generic()

    def last_word():
        return _first_word(out[-1]) if out else None

    def add(text):
        if out and _first_word(text) == last_word():
            return False
        out.append(text)
        budget[0] -= plain.mentions(text)
        return True

    def career_sentence():
        names = names_for()
        same = previous[0] is not None and previous[0] is leader["ident"]
        pool = CAREER_SAME_POOL if same else CAREER_POOL
        text = _choose(
            pool, slug, "career", avoid_word=last_word(),
            name=leader["name"], money=_money(leader["total"]),
            through=leader["through"], one=names.one, many=names.many,
            label=names.label, of=names.of,
        )
        ratio = _ratio(leader["total"], leader["rival"])
        if ratio and budget[0] - plain.mentions(text) > 0:
            text += " That is {} what any other {} has earned.".format(
                ratio, names.one)
        return text, leader["ident"]

    def season_sentence():
        names = names_for()
        same = previous[0] is not None and previous[0] is record["ident"]
        pool = SEASON_SAME_POOL if same else SEASON_POOL
        text = _choose(
            pool, slug, "season", avoid_word=last_word(),
            name=record["name"], possessive=_possessive(record["name"]),
            money=_money(record["salary"]), season=record["season"],
            one=names.one, many=names.many, label=names.label,
        )
        return text, record["ident"]

    def current_sentence():
        names = names_for()
        same = previous[0] is not None and previous[0] is now["ident"]
        if now["only"]:
            pool = LAST_ONE_POOL
        elif same:
            pool = CURRENT_SAME_POOL
        else:
            pool = CURRENT_POOL
        text = _choose(
            pool, slug, "current", avoid_word=last_word(),
            name=now["name"], money=_money(now["salary"]),
            season=idx.current_season, one=names.one, many=names.many,
            label=names.label,
        )
        return text, now["ident"]

    def future_sentence():
        names = names_for()
        same = previous[0] is not None and future["name"] == previous[0].name
        after_record = previous_slot[0] == "season"
        if future["tops"] and after_record:
            pool = FUTURE_TOPS_AFTER_SAME_POOL if same else FUTURE_TOPS_AFTER_POOL
        elif future["tops"]:
            pool = FUTURE_TOPS_SAME_POOL if same else FUTURE_TOPS_POOL
        else:
            pool = FUTURE_SAME_POOL if same else FUTURE_POOL
        text = _choose(
            pool, slug, "future", avoid_word=last_word(),
            name=future["name"], possessive=_possessive(future["name"]),
            one=names.one, list=_merge_seasons(future["rows"]),
        )
        return text, None

    order = []
    if season_first:
        order.append(("season", season_sentence))
        order.append(("career", career_sentence))
    else:
        if leader:
            order.append(("career", career_sentence))
        if record:
            order.append(("season", season_sentence))
    # A contract that would beat the cohort's own record is the one thing the
    # tables below cannot show, so it goes ahead of who leads this season's
    # payroll. Anything smaller waits its turn.
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
        if add(text):
            previous[0] = subject
            previous_slot[0] = slot

    # A cohort with a career close to the top that began before the data did
    # gets no leader sentence, so it gets the reason instead: with one sentence
    # left the page reads as if there were nothing to say.
    if shadowed and len(out) < 2:
        out.append(
            "Some of the biggest careers here began before the salary data "
            "does, so the career totals on this page are not the whole of what "
            "those men earned."
        )
    return out[:C.SUMMARY_SENTENCES]
