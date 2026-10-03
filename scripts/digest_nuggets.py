#!/usr/bin/env python3
"""The second sentence of a digest item: what the money means.

One module per question, because each of these is a different search of the
data and the digest prints the strongest one that answers:

a) record          a record or near-record the engine already found, paid
                   seasons only, because a contracted season is not a record
b) career          the career-salary milestone this crosses and when, measured
                   against a career it clears where one of them is a name the
                   reader knows, or against his place in the list where he is
                   near the top of it
c) raise           his raise against every raise the digest has seen since the
                   league year opened on July 1
d) peers           the men at his position putting up his numbers, and the
                   real money they are paid
e) horizon         how far the money runs, in the age he will be when it ends

Nothing here says "guaranteed": the data holds salaries, not contract terms,
and a figure in a future season is what a sheet says a team has on its books.
"""

from __future__ import annotations

import collections
import datetime
import hashlib
import re
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import factoids as F  # noqa: E402

#: Career-salary milestones, in the order a career crosses them.
MILESTONES = (
    50000000, 100000000, 150000000, 200000000, 250000000, 300000000,
    400000000, 500000000, 600000000,
)

#: A nugget of kind (a) has to be this rank or better to be worth a sentence.
NEAR_RECORD_RANK = 3

#: Where a career-earnings rank stops being worth a sentence. Inside the top
#: 25 it is a fact about him; past it, it is a number.
STANDING_MAX_RANK = 25

#: Ranks a sentence spells out. Past these the digit is how a rank is read.
RANK_WORDS = ("", "first", "second", "third", "fourth", "fifth", "sixth",
              "seventh", "eighth", "ninth", "tenth")


def _rank_word(rank):
    if rank and rank < len(RANK_WORDS):
        return RANK_WORDS[rank]
    return _ordinal(rank)

#: A raise outside the ten biggest of the league year is not news, and "the
#: 64th biggest raise since July 1" is a sentence nobody would print.
RAISE_MAX_RANK = 10

#: Which second number a position is read by. Points is always the first.
SECOND_STAT = {"G": "apg", "F": "rpg", "C": "rpg"}
STAT_WORD = {"apg": "assists", "rpg": "rebounds", "ppg": "points"}

#: The peer band is the whole-number band holding each of his figures, and is
#: never widened: a band stretched until it finds him five men is no longer a
#: description of how he played.
PEER_MIN_GAMES = 40
PEER_MIN_PLAYERS = 5

#: Games played in the season under way before that season is the one a
#: comparison reads. Ten games is enough for a per-game average to describe how
#: a man is playing; under that the season played out is the better description,
#: and it is the one a reader means by "last season".
PEER_CURRENT_MIN_GAMES = 10

#: Who is worth naming as a career passed on the way to a milestone. A man has
#: to carry one of these, which is a short list on purpose: "past Josh Howard on
#: the way" is a name that means nothing to the reader of a salary line. An
#: all-time team first; an All-NBA First Team season if none of those applies;
#: nobody at all if neither.
MARQUEE = ("Hall of Fame", "NBA Top-75", "NBA Top-50", "HoopsHype Top-78")
MARQUEE_FALLBACK = ("All-NBA First Team",)

#: Decorations that rank one qualifying career above another, in this order.
DECORATIONS = ("Hall of Fame", "NBA Top-75", "NBA Top-50", "HoopsHype Top-78",
               "Most Valuable Player", "All-NBA First Team",
               "All-NBA Second Team", "All-NBA Third Team", "All-Star")

#: The league year the raise ranking is measured from.
LEAGUE_YEAR_OPENS = (7, 1)


def league_year_start(today=None):
    """July 1 of the league year that is open on this date."""
    today = today or datetime.date.today()
    month, day = LEAGUE_YEAR_OPENS
    opened = datetime.date(today.year, month, day)
    if today < opened:
        opened = datetime.date(today.year - 1, month, day)
    return opened


# ── (a) a record the engine already found ─────────────────────────────
#: A claim about where a future salary sits inside a season's own books. Nobody
#: has been paid any of it, the books for that season are not closed, and a
#: ranking inside them says only what has been signed so far.
FUTURE_RANK_KEYS = ("rank_league", "rank_team", "team_high", "cap_pct_season")


def record_nugget(idx, item, factoids):
    """A record or near-record for one of the seasons this change is about.

    Paid seasons only. A contracted season has no rank inside it: nobody has
    been paid that money, so it ranks against nothing.
    """
    for season in _seasons(item):
        if idx.is_contracted(season):
            continue
        for fact in factoids.get("{}|{}".format(item["player"], season)) or []:
            if (fact.get("rank") or 99) > NEAR_RECORD_RANK:
                continue
            if _ranks_a_future_season(idx, fact):
                continue
            if (fact.get("key") or "").startswith("cap_pct"):
                # The digest talks in money. A share of the cap is a ratio the
                # reader has to do arithmetic on to picture.
                continue
            text = _trim_rival(
                _his_own(fact.get("text") or "", item["player"])).strip()
            if text:
                return {
                    "kind": "record",
                    "opener": text.rstrip("."),
                    "tail": "",
                    "entities": _fact_entities(fact),
                    "detail": {"rank": fact.get("rank"),
                               "key": fact.get("key"),
                               "season": season,
                               "previous_holder": fact.get("previous_holder")},
                }
    return None


#: How the engine ends a comparison. A page has room for the other man's figure
#: and the season he set it in; a digest line does not, and the name alone
#: carries the comparison.
RIVAL_CLAUSES = (", ahead of ", ", passing ", ", behind ", ", matching ",
                 ", breaking ", ", level with ")


def _trim_rival(text):
    """Keep the man a record is measured against, drop his figure and season.

    "behind Brook Lopez's $238.7 million (2026-27)" becomes "behind Brook
    Lopez", which is two fewer numbers in a sentence allowed two.
    """
    for clause in RIVAL_CLAUSES:
        at = text.find(clause)
        if at < 0:
            continue
        rest = text[at + len(clause):]
        name = re.split(r"'s |' |\s+\$", rest, maxsplit=1)[0].strip().rstrip(".")
        if not name or name.startswith("his own"):
            return text[:at].rstrip(".") + "."
        return "{}{}{}.".format(text[:at], clause, name)
    return text


def _ranks_a_future_season(idx, fact):
    """A claim that places a season's money inside that season's own books.

    Safe for a season already paid; never for one still to come, where the
    books are open and the standing is only the standing so far.
    """
    key = (fact.get("key") or "")
    if not any(key.startswith(prefix) for prefix in FUTURE_RANK_KEYS):
        return False
    holder = (fact.get("previous_holder") or {}).get("season") or ""
    return bool(holder) and idx.is_contracted(holder)


def _his_own(text, player):
    """The engine names the man; the digest's first sentence already did.

    The lead opens with his name and links it, so a second sentence repeating
    it reads like two items stapled together.
    """
    if not player or not text:
        return text
    if text.startswith(player + "'s "):
        return "His " + text[len(player) + 3:]
    if text.startswith(player + "' "):
        return "His " + text[len(player) + 2:]
    if text.startswith(player + " "):
        return "He " + text[len(player) + 1:]
    return text


def _fact_entities(fact):
    """Everything a factoid names: its cohort, and the men it measures against."""
    out = []
    parts = (fact.get("key") or "").split("|")
    if len(parts) >= 3 and parts[0].startswith("cohort"):
        out.append((parts[1], parts[2]))
    holder = (fact.get("previous_holder") or {}).get("player")
    if holder:
        out.append(("player", holder))
    # A second or third place names everyone above him, and every name in a
    # digest line is a link to his page.
    for name in _names_in(fact.get("ahead") or ""):
        out.append(("player", name))
    return out


def _names_in(clause):
    """"Brook Lopez and Robin Lopez" -> both names."""
    if not clause:
        return []
    head, _sep, last = clause.rpartition(" and ")
    parts = [p.strip() for p in head.split(",")] + [last.strip()]
    return [p for p in parts if p and not p.startswith("his own")]


# ── (b) the career total ──────────────────────────────────────────────
def career_nugget(idx, item, money):
    """The milestone this money crosses, who it passes, and where he stands."""
    player = item["player"]
    paid, through = idx.paid_through(player)
    if not paid or not idx.career_rankable(player):
        return None

    records = idx.by_player.get(player) or []
    running, crossing = paid, None
    for record in records:
        if F.season_key(record["season"]) <= F.season_key(through):
            continue
        running += record.get("salary") or 0
        for milestone in MILESTONES:
            if paid < milestone <= running and crossing is None:
                crossing = (milestone, record["season"])
                break
        if crossing:
            break
    if not crossing:
        return None
    milestone, season = crossing

    passed = _legends_between(idx, paid, milestone)
    rank_all, rank_active = _career_ranks(idx, player, paid)

    # Three forms, in this order. A name the reader knows is worth more than a
    # rank, so a career he is about to clear takes the sentence where one of
    # them carries a name. Failing that, a place near the top of the list is
    # worth saying; past the top of it, a rank is a number and the milestone
    # stands on its own.
    if passed:
        text = "He'd pass {} in career earnings in {}, {}".format(
            money(milestone), season,
            _legend_clause(idx, player, season, passed[0]))
    elif rank_all and rank_all <= STANDING_MAX_RANK:
        text = "He's already {} in career earnings and would pass {} in {}".format(
            _rank_word(rank_all), money(milestone), season)
    else:
        text = "He'd pass {} in career earnings in {}".format(
            money(milestone), season)
    return {
        "kind": "career",
        "opener": text,
        "tail": text[0].lower() + text[1:],
        "entities": [("player", p["name"]) for p in passed],
        "detail": {
            "paid": paid, "through": through, "milestone": milestone,
            "crosses_in": season, "legends": passed,
            "rank_all": rank_all, "rank_active": rank_active,
        },
    }


#: The two ways of measuring a milestone against a career it clears. Both say
#: the same thing; which one a nugget takes is fixed by the hash below, so a
#: line does not change wording between builds, and the digest does not read as
#: one sentence printed over and over.
LEGEND_FORMS = (
    "more than {name} made in his whole career",
    "more than {name} earned in his {seasons} seasons",
)


def _legend_clause(idx, player, season, legend):
    """"more than Latrell Sprewell made in his whole career".

    The season count is only offered where the data holds his career whole,
    which is what _legends_between already requires of him, so "his 13
    seasons" is his 13 seasons and not the 13 this file happens to hold.
    """
    # "his 1 seasons" is not a sentence, so a one-season career only ever
    # takes the first form.
    seasons = legend.get("seasons") or 0
    forms = LEGEND_FORMS if seasons > 1 else LEGEND_FORMS[:1]
    key = "{}|{}|{}".format(player, season, legend["name"])
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
    form = forms[int(digest, 16) % len(forms)]
    return form.format(name=legend["name"], seasons=seasons)


def _legends_between(idx, paid, milestone):
    """The most decorated finished careers between here and the milestone.

    Only a career this data holds whole: a man who was already playing in
    1990-91 has a total that is a part of a career, so passing him means
    nothing. Ranked by MVPs, then All-NBA, then All-Star.
    """
    found = []
    for player, records in idx.by_player.items():
        if player in idx.truncated or not idx.career_rankable(player):
            continue
        if player in idx.active_players:
            continue
        total, _through = idx.paid_through(player)
        if not total or not (paid < total < milestone):
            continue
        counts = collections.Counter()
        for record in records:
            for award in record.get("awards") or []:
                counts[award] += 1
        found.append({
            "name": player, "total": total,
            # distinct seasons, so a row the sheets carry twice cannot inflate
            # "his 13 seasons" into 14
            "seasons": len({r["season"] for r in records}),
            "decorations": {name: counts.get(name, 0) for name in DECORATIONS},
            "rank_key": tuple(counts.get(name, 0) for name in DECORATIONS),
        })
    found.sort(key=lambda entry: (
        [-n for n in entry["rank_key"]], -entry["total"], entry["name"]))
    # An all-time-team man first; only if there is none does an All-NBA First
    # Team season qualify. Neither and the clause is left off altogether.
    best = [e for e in found if _carries(e, MARQUEE)][:2]
    if not best:
        best = [e for e in found if _carries(e, MARQUEE_FALLBACK)][:2]
    return best


def _carries(entry, awards):
    return any(entry["decorations"].get(name) for name in awards)


def _career_ranks(idx, player, paid):
    """Where a total stands among everyone, and among the men still playing."""
    totals = []
    for other in idx.by_player:
        if not idx.career_rankable(other):
            continue
        total, _through = idx.paid_through(other)
        if total:
            totals.append((other, total))
    everyone = sorted(totals, key=lambda row: -row[1])
    active = [row for row in everyone if row[0] in idx.active_players]
    rank_all = next((i for i, row in enumerate(everyone, 1) if row[0] == player), None)
    rank_active = next((i for i, row in enumerate(active, 1) if row[0] == player), None)
    return rank_all, rank_active


# ── (c) the raise, against the league year's raises ───────────────────
def raise_nugget(item, raises, money, opened):
    """Where this raise sits among every raise since the league year opened."""
    amount = item.get("raise_amount")
    if not amount or amount <= 0:
        return None
    bigger = sum(1 for entry in raises if (entry.get("amount") or 0) > amount)
    rank = bigger + 1
    if len(raises) < 2 or rank > RAISE_MAX_RANK:
        return None
    if rank == 1:
        text = "No team has taken on a bigger raise since {}".format(
            _ap_date(opened))
    elif rank == 2:
        text = "Only one raise since {} is bigger".format(_ap_date(opened))
    else:
        text = "Only {} raises since {} are bigger".format(
            _spell(rank - 1), _ap_date(opened))
    tail = text[0].lower() + text[1:]
    return {
        "kind": "raise",
        "opener": text,
        "tail": tail,
        "entities": [],
        "detail": {"amount": amount, "rank": rank, "pool": len(raises),
                   "money": money(amount)},
    }


# ── (d) peers ─────────────────────────────────────────────────────────
def peer_nugget(idx, item, data, money):
    """What the men playing like him are paid.

    Real money on both sides. A share of the cap is a ratio a reader has to do
    arithmetic on; what they want to know is what these players make.

    Which season the numbers come from is decided once, by him, and then holds
    for everybody in the comparison: his figures and theirs are always the same
    season, and the sentence says which.
    """
    player = item["player"]
    season, when, games_floor = _peer_window(idx, player)
    if not season:
        return None
    # His own line in that season and no other: his numbers from one season set
    # against theirs from a different one is not a comparison.
    mine = idx.record(player, season)
    if not mine:
        return None
    position, _noun = F.position_group(mine.get("pos"))
    if not position:
        return None
    second = SECOND_STAT.get(position, "rpg")
    points, other = mine.get("ppg"), mine.get(second)
    if points is None or other is None:
        return None

    # The whole-number band holding each of his figures, and no other. A band
    # that has to be widened to find him company is not the company he keeps.
    low_p, high_p = _band(points, 0)
    low_o, high_o = _band(other, 0)
    group = [
        record for record in data["seasons"]
        if record["season"] == season
        and (record.get("gp") or 0) >= games_floor
        and F.position_group(record.get("pos"))[0] == position
        and record.get("ppg") is not None and record.get(second) is not None
        and low_p <= record["ppg"] <= high_p
        and low_o <= record[second] <= high_o
    ]
    peers = [record for record in group if record["player"] != player]
    if len(peers) < PEER_MIN_PLAYERS:
        return None

    # What they are paid now, not what they were paid for the season they put
    # those numbers up in.
    paid = []
    for record in peers:
        now = idx.record(record["player"], idx.current_season)
        if now and now.get("salary"):
            paid.append((now["salary"], record["player"]))
    if len(paid) < PEER_MIN_PLAYERS:
        return None
    paid.sort()
    median = paid[len(paid) // 2][0]
    best_salary, best_player = paid[-1]

    # A season still being played takes the perfect: he has averaged this much
    # so far. A season played out is finished, and so is the verb.
    now = when == "this season"
    phrase = "{} who {} {} to {} points and {} to {} {} {}".format(
        _plural(position).capitalize(), "have averaged" if now else "averaged",
        _num(low_p), _num(high_p), _num(low_o), _num(high_o),
        STAT_WORD[second], when)
    # "this season" twice in one clause says it once too often: where the
    # numbers are from this season, the money plainly is too.
    paid_when = "" if now else " this season"
    body = ("{} make a median {}{}; the best-paid of them, {}, "
            "is on {}".format(phrase, money(median), paid_when, best_player,
                              money(best_salary)))
    return {
        "kind": "peers",
        "opener": body,
        "tail": body[0].lower() + body[1:],
        "entities": [("player", best_player)],
        # the exact words the link has to sit on, carried rather than found
        # again by the sentence writer, which is what broke the last time the
        # wording changed
        "link_phrase": phrase,
        "peer_link": {
            "season": season, "pos": position, "gp_min": games_floor,
            "ppg": (low_p, high_p), second: (low_o, high_o),
            "second_stat": second, "count": len(group),
            "others": len(peers),
        },
        "detail": {
            "season": season, "median": median, "best": (best_player, best_salary),
            "peers": [(name, salary) for salary, name in reversed(paid)],
            "bands": {"ppg": (low_p, high_p), second: (low_o, high_o)},
            "others": len(peers), "in_band": len(group), "paid_now": len(paid),
        },
    }


def _band(value, widen=0):
    low = int(value // 1) - widen
    return max(0, low), int(value // 1) + 1 + widen


def _plural(group):
    return {"G": "guards", "F": "forwards", "C": "centres"}[group]


def _num(value):
    return "{:g}".format(value)


def _last_completed(idx):
    """The last season that was played out, which is not the one under way.

    On the first of October the current season has a handful of games in it, so
    a 40-game floor would empty the comparison. The season before it is the one
    a reader means by "last season".
    """
    for season in reversed(idx.seasons):
        if F.season_key(season) < idx.current_key and not idx.is_contracted(season):
            return season
    return ""


def _peer_window(idx, player):
    """(season, how a sentence says it, the games floor) for a comparison.

    The season under way once he has ten games in it, and the season played out
    until then. One answer for the whole comparison, so his numbers and theirs
    are never read off different seasons.
    """
    current = idx.current_season
    mine = idx.record(player, current) or {}
    if (mine.get("gp") or 0) >= PEER_CURRENT_MIN_GAMES \
            and mine.get("ppg") is not None:
        # A season still being played has no 40-game men in it until the new
        # year, so the ten games that opened this window is the bar everybody
        # in it clears. A season played out keeps the full floor.
        floor = (PEER_CURRENT_MIN_GAMES if idx.current_season_in_progress
                 else PEER_MIN_GAMES)
        return current, "this season", floor
    season = _last_completed(idx)
    if not season:
        return "", "", 0
    return season, "last season", PEER_MIN_GAMES


# ── (e) where the money ends ──────────────────────────────────────────
def horizon_nugget(idx, item):
    """How far the money runs, in the age he will be when it stops."""
    members = item.get("members") or []
    if len(members) < 3:
        return None
    player, season = item["player"], item["season"]
    age = _age_in(idx, player, season)
    if age is None:
        return None
    return {
        "kind": "horizon", "entities": [],
        "opener": "The money runs through his age-{} season".format(age),
        "tail": "the money runs through his age-{} season".format(age),
        "detail": {"season": season, "age": age},
    }


#: Small numbers read better spelled out, which is AP style and also what keeps
#: a sentence to the one or two figures it is allowed.
NUMBER_WORDS = ("zero", "one", "two", "three", "four", "five", "six", "seven",
                "eight", "nine")


def _spell(n):
    return NUMBER_WORDS[n] if 0 <= n < len(NUMBER_WORDS) else str(n)


def _age_in(idx, player, season):
    """His age in a season, carried forward from the last one the data knows."""
    known = [r for r in (idx.by_player.get(player) or []) if r.get("age")]
    if not known:
        return None
    anchor = known[-1]
    return anchor["age"] + (F.season_key(season) - F.season_key(anchor["season"]))


# ── choosing ──────────────────────────────────────────────────────────
#: The order that decides which one an item prints. A record splits in two by
#: whether the day made it: one the day set, or moved him inside, is what
#: happened today and leads; one he already held the day before is true but old
#: news, and the career milestone is worth more than a repeat of it.
NUGGET_ORDER = ("record", "career", "record_held", "raise", "peers", "horizon")


def nuggets_for(item, idx, data, factoids, raises, money, opened, limit=1,
                record_news=True):
    """The strongest of the five that have something to say, in NUGGET_ORDER.

    ``record_news`` is whether the day made the record this item would print.
    Where it did not, the record falls in behind the career milestone, so an
    item does not open on a record he has held for weeks while the thing that
    happened today goes unsaid.
    """
    record = _safely(lambda: record_nugget(idx, item, factoids))
    builders = [lambda: career_nugget(idx, item, money)]
    if record is not None and record_news:
        builders.insert(0, lambda: record)
    else:
        builders.append(lambda: record)
    builders.extend([
        lambda: raise_nugget(item, raises, money, opened),
        lambda: peer_nugget(idx, item, data, money),
        lambda: horizon_nugget(idx, item),
    ])

    found = []
    for build in builders:
        nugget = _safely(build)
        if nugget:
            found.append(nugget)
        if len(found) >= limit:
            break
    return found


def _safely(build):
    """One nugget, or None where the data it wanted was not there."""
    try:
        return build()
    except (KeyError, TypeError, ValueError, IndexError):
        return None


def _seasons(item):
    return [m["season"] for m in item.get("members") or []] or [item["season"]]


def _join(names):
    if len(names) == 1:
        return names[0]
    return "{} and {}".format(", ".join(names[:-1]), names[-1])


def _ordinal(number):
    if number is None:
        return ""
    if 10 <= number % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return "{}{}".format(number, suffix)


MONTHS = ("Jan.", "Feb.", "March", "April", "May", "June", "July", "Aug.",
          "Sept.", "Oct.", "Nov.", "Dec.")


def _ap_date(when):
    return "{} {}".format(MONTHS[when.month - 1], when.day)
