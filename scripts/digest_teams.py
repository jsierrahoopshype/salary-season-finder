#!/usr/bin/env python3
"""What a day's changes did to what a team has committed, season by season.

A digest of individual salaries misses the thing an editor would lead on: a
team quietly building the biggest future commitment in the league. So after the
day's changes are read, every team one of them touched is measured on the money
it has committed to its three biggest salaries in each season still to come,
and a line is printed only where that day moved it past something:

- it became the league high for that season, as the books stand today;
- or it crossed half of that season's projected cap;
- or it crossed the whole of it.

Three salaries rather than the whole payroll, because a future season's books
are not a roster: they hold the men already under contract for it, which for
most teams is a handful, and the three biggest are the commitment a reader can
hold in their head.
"""

from __future__ import annotations

import collections
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import factoids as F  # noqa: E402

#: How many salaries make up a team's committed figure.
TOP_N = 3

#: The lines a crossing is worth saying.
CAP_SHARES = (1.0, 0.5)


def commitments(data, idx, top_n=TOP_N):
    """{(team, season): (total, [(player, salary), ...])} for every team.

    Only seasons still to come, and only money the engine has not flagged as
    impossible: a projection no contract can pay is not a commitment.
    """
    by_team = collections.defaultdict(list)
    for record in data.get("seasons") or ():
        season = record["season"]
        if not idx.is_contracted(season):
            continue
        player = idx.canonical(record["player"])
        if (player, season) in idx.impossible:
            continue
        for code, amount in F.team_amounts(record):
            if code and amount:
                by_team[(code, season)].append((amount, player))

    out = {}
    for key, rows in by_team.items():
        rows.sort(key=lambda row: (-row[0], row[1]))
        top = rows[:top_n]
        out[key] = (sum(amount for amount, _p in top),
                    [(player, amount) for amount, player in top])
    return out


def crossings(before, after, idx, touched):
    """What today moved past, for the teams today touched.

    ``before`` and ``after`` are commitments() over the two builds. A team is
    reported once per season, on the biggest thing it crossed.
    """
    out = []
    highs_before = _league_highs(before)
    highs_after = _league_highs(after)
    for code, season in sorted(touched):
        key = (code, season)
        was = before.get(key, (0, []))[0]
        now, top = after.get(key, (0, []))
        if now <= was:
            continue
        cap = _cap(idx, season)
        reason, detail = None, {}
        if highs_after.get(season) == key and highs_before.get(season) != key:
            reason = "league_high"
        elif cap:
            for share in CAP_SHARES:
                if was < cap * share <= now:
                    reason = "cap_share"
                    detail["share"] = share
                    break
        if reason is None:
            continue
        out.append({
            "team": code, "season": season, "total": now, "was": was,
            "top": top, "cap": cap, "reason": reason, "detail": detail,
        })
    # The biggest commitment first, which is the one worth the reader's eye.
    out.sort(key=lambda row: -row["total"])
    return out


def line(entry, name, money, link=None, top_n=TOP_N):
    """The one sentence a crossing is worth.

    "so far", always: a future season's books are not closed, and today's
    league high is only the high of what has been signed by today.
    """
    who = _join([player for player, _amount in entry["top"]])
    if link:
        who = _join([link(player) for player, _amount in entry["top"]])
    opening = "{} now has {} committed to {} for {}".format(
        name, money(entry["total"]), who, entry["season"])
    if entry["reason"] == "league_high":
        return "{}, the most any team has tied up in {} players for that " \
            "season so far.".format(opening, _spell(top_n))
    share = entry["detail"]["share"]
    if share >= 1.0:
        return "{}, more than the whole projected cap for it.".format(opening)
    return "{}, more than half the projected cap for it.".format(opening)


def touched_teams(items, idx):
    """(team, season) pairs a day's changes moved, both sides of a move."""
    out = set()
    for item in items:
        for member in item.get("members") or [item]:
            season = member.get("season") or item["season"]
            if not idx.is_contracted(season):
                continue
            for field in ("team", "was_team"):
                for code in (member.get(field) or item.get(field) or "").split(","):
                    code = code.strip()
                    if code:
                        out.add((code, season))
    return out


def _league_highs(table):
    """{season: the (team, season) key holding the most for it}."""
    best = {}
    for key, (total, _top) in table.items():
        _code, season = key
        if season not in best or total > table[best[season]][0]:
            best[season] = key
    return best


def _cap(idx, season):
    entry = (idx.cap or {}).get(season) or {}
    return entry.get("cap") if isinstance(entry, dict) else entry


def _join(names):
    if not names:
        return "nobody"
    if len(names) == 1:
        return names[0]
    return "{} and {}".format(", ".join(names[:-1]), names[-1])


NUMBER_WORDS = ("zero", "one", "two", "three", "four", "five")


def _spell(n):
    return NUMBER_WORDS[n] if 0 <= n < len(NUMBER_WORDS) else str(n)
