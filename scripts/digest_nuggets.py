#!/usr/bin/env python3
"""The second sentence of a digest item: what the money means.

One module per question, because each of these is a different search of the
data and the digest only prints the first two that answer:

a) record          a record or near-record the engine already found, paid
                   seasons only, because a contracted season is not a record
b) career          the career-salary milestone this crosses and when, the most
                   decorated retired men passed on the way, and where he stands
                   today
c) raise           his raise against every raise the digest has seen since the
                   league year opened on July 1
d) peers           the men at his position putting up his numbers, by what
                   share of the cap they take
e) horizon         how far the money runs, in the age he will be when it ends

Nothing here says "guaranteed": the data holds salaries, not contract terms,
and a figure in a future season is what a sheet says a team has on its books.
"""

from __future__ import annotations

import collections
import datetime
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

#: A raise outside the ten biggest of the league year is not news, and "the
#: 64th biggest raise since July 1" is a sentence nobody would print.
RAISE_MAX_RANK = 10

#: A career-salary standing past this is a number rather than a fact.
STANDING_MAX_RANK = 100

#: Which second number a position is read by. Points is always the first.
SECOND_STAT = {"G": "apg", "F": "rpg", "C": "rpg"}
STAT_WORD = {"apg": "assists", "rpg": "rebounds", "ppg": "points"}

#: Peer bands start as the whole-number band holding each figure and widen by
#: one on each side, twice at most, until the group is big enough to mean
#: something.
PEER_MIN_GAMES = 40
PEER_MIN_PLAYERS = 5
PEER_WIDENINGS = 2

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
    """Everything a factoid names: its cohort, and the man it measures against."""
    out = []
    parts = (fact.get("key") or "").split("|")
    if len(parts) >= 3 and parts[0].startswith("cohort"):
        out.append((parts[1], parts[2]))
    holder = (fact.get("previous_holder") or {}).get("player")
    if holder:
        out.append(("player", holder))
    return out


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

    text = "He would reach {} in career earnings by {}".format(
        money(milestone), season)
    standing = _standing(rank_all, rank_active)
    # One or the other, never both: a name the reader knows is worth more than
    # a rank, and a sentence carrying the milestone, the season, two names and
    # a standing is a stat list rather than a line.
    if passed:
        text += ", going past {} on the way".format(
            _join([p["name"] for p in passed]))
    elif standing:
        text += ", where he already stands {}".format(standing)
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


def _standing(rank_all, rank_active):
    """The more telling of the two ranks, which is the shorter number.

    Past the hundredth name neither is telling, so neither is printed.
    """
    best = min([r for r in (rank_all, rank_active) if r] or [0])
    if not best or best > STANDING_MAX_RANK:
        return ""
    if rank_active is not None and (rank_all is None or rank_active < rank_all):
        return "{} among players on a roster".format(_ordinal(rank_active))
    return "{} of all time".format(_ordinal(rank_all))


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
def peer_nugget(idx, item, data, pct):
    """The men at his position putting up his numbers, by share of the cap."""
    player = item["player"]
    season = _last_completed(idx)
    if not season:
        return None
    mine = idx.record(player, season) or _latest_with_stats(idx, player)
    if not mine:
        return None
    position, _noun = F.position_group(mine.get("pos"))
    if not position:
        return None
    second = SECOND_STAT.get(position, "rpg")
    points, other = mine.get("ppg"), mine.get(second)
    if points is None or other is None:
        return None

    pool = [
        record for record in data["seasons"]
        if record["season"] == season
        and (record.get("gp") or 0) >= PEER_MIN_GAMES
        and F.position_group(record.get("pos"))[0] == position
        and record.get("ppg") is not None and record.get(second) is not None
    ]
    for widen in range(PEER_WIDENINGS + 1):
        low_p, high_p = _band(points, widen)
        low_o, high_o = _band(other, widen)
        # The group is whoever the bands hold, him included where he cleared the
        # games floor himself, because that is what the link has to return.
        group = [
            record for record in pool
            if low_p <= record["ppg"] <= high_p and low_o <= record[second] <= high_o
        ]
        if len(group) >= PEER_MIN_PLAYERS:
            break
    if len(group) < PEER_MIN_PLAYERS:
        return None
    peers = [record for record in group if record["player"] != player]
    if not peers:
        return None

    shares = sorted(
        (record.get("salary_cap_pct") or 0.0, record["player"]) for record in peers)
    median = shares[len(shares) // 2][0] if shares else 0.0
    mine_share = _cap_share(idx, item)
    if mine_share is None:
        return None
    # The count is of the other men, because he is not one of his own peers and
    # the median and the maximum below are taken over them alone. The link still
    # reproduces the whole band, him included, which is what the tool's filters
    # describe; peer_link carries both numbers so a check can use the right one.
    phrase = (
        "{} other {} who averaged {} to {} points and {} to {} {} "
        "last season".format(
            len(peers), _plural(position),
            _num(low_p), _num(high_p), _num(low_o), _num(high_o),
            STAT_WORD[second])
    )
    if mine_share > shares[-1][0]:
        body = "He takes {} of the cap, more than any of the {}".format(
            pct(mine_share), phrase)
    else:
        body = "He takes {} of the cap against a median {} for the {}".format(
            pct(mine_share), pct(median), phrase)
    return {
        "kind": "peers",
        "opener": body,
        "tail": body[0].lower() + body[1:],
        "entities": [],
        # the exact words the link has to sit on, carried rather than found
        # again by the sentence writer, which is what broke the last time the
        # wording changed
        "link_phrase": phrase,
        "peer_link": {
            "season": season, "pos": position, "gp_min": PEER_MIN_GAMES,
            "ppg": (low_p, high_p), second: (low_o, high_o),
            "second_stat": second, "count": len(group),
            "others": len(peers),
        },
        "detail": {
            "season": season, "mine": mine_share, "median": median,
            "others": len(peers), "in_band": len(group),
            "peers": [(name, share) for share, name in reversed(shares)],
            "bands": {"ppg": (low_p, high_p), second: (low_o, high_o)},
            "widened": widen,
        },
    }


def _band(value, widen):
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


def _latest_with_stats(idx, player):
    for record in reversed(idx.by_player.get(player) or []):
        if record.get("ppg") is not None:
            return record
    return None


def _cap_share(idx, item):
    record = item.get("record") or {}
    if record.get("salary_cap_pct"):
        return record["salary_cap_pct"]
    entry = idx.cap.get(item["season"]) or {}
    cap = entry.get("cap") if isinstance(entry, dict) else entry
    salary = item.get("salary")
    if cap and salary:
        return salary / float(cap) * 100.0
    return None


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
def nuggets_for(item, idx, data, factoids, raises, money, pct, opened, limit=2):
    """The first two of the five that have something to say, in order."""
    found = []
    for build in (
        lambda: record_nugget(idx, item, factoids),
        lambda: career_nugget(idx, item, money),
        lambda: raise_nugget(item, raises, money, opened),
        lambda: peer_nugget(idx, item, data, pct),
        lambda: horizon_nugget(idx, item),
    ):
        try:
            nugget = build()
        except (KeyError, TypeError, ValueError, IndexError):
            nugget = None
        if nugget:
            found.append(nugget)
        if len(found) >= limit:
            break
    return found


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
