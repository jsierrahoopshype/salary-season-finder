"""Career earnings without an All-Star, All-NBA or MVP selection.

Three lists of the same shape: what a man has been paid, counted only while he
had never been selected. A selection ends his place on the list from the season
it came in, so the lists are a running state rather than a snapshot, and the
question a reader actually asks of them is who has led and what ended it.

Everything here is derived in one sweep over the index, seasons oldest first,
carrying a running career total. The same pass yields the current standings,
the list of men who have led, and the timeline events a player page prints,
so a page and a timeline can never disagree about who led when.

The window matters. Career earnings in this data begin at the first season on
file, so the earliest leaders are where the count begins rather than men who
took the lead from anyone. The pages say so, and the first reign is worded as
holding the lead when the count opens, not as taking it.
"""

from __future__ import annotations

import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import factoids as F  # noqa: E402


class Drought:
    """One list: its page, its award and the words it is described in."""

    __slots__ = ("key", "slug", "award", "labels", "heading", "never", "yet",
                 "had_never", "selection", "won")

    def __init__(self, key, slug, award, labels, never, yet, had_never,
                 selection, won=False):
        self.key = key
        self.slug = slug
        self.award = award
        self.labels = labels
        self.never = never
        self.yet = yet
        self.had_never = had_never
        self.selection = selection
        self.won = won

    def phrase(self, retired, selected_since):
        """How to describe a man held to this list.

        "Never" is a claim about a whole career, so it is kept for the men
        whose careers are over. A man still playing has not run out of
        chances, and a man who has since been selected had never been chosen
        only in the past, which is the one of the three that is a tense rather
        than a hedge.
        """
        if selected_since:
            return self.had_never
        if retired:
            return self.never
        return self.yet


#: The three lists, in the order a tie between them is broken: a man who is on
#: more than one is described by the most notable of them.
LISTS = (
    Drought(
        "all-star", "never-all-star", "All-Star",
        frozenset({"All-Star", "All-Star MVP"}),
        never="never named an All-Star",
        yet="yet to make an All-Star team",
        had_never="who had never been named an All-Star",
        selection="All-Star selection",
    ),
    Drought(
        "all-nba", "never-all-nba", "All-NBA",
        frozenset({"All-NBA First Team", "All-NBA Second Team",
                   "All-NBA Third Team"}),
        never="never named All-NBA",
        yet="yet to make an All-NBA team",
        had_never="who had never been named All-NBA",
        selection="All-NBA selection",
    ),
    Drought(
        "mvp", "never-mvp", "MVP",
        frozenset({"Most Valuable Player"}),
        never="never named MVP",
        yet="yet to win MVP",
        had_never="who had never won MVP",
        selection="MVP",
        won=True,
    ),
)

BY_KEY = {spec.key: spec for spec in LISTS}

#: How far down a page lists, and how far down a man has to be before entering
#: it is worth a line of his timeline.
PAGE_ROWS = 25
TOP = 10


def _first_selections(idx, spec):
    """{player: the season his first selection came in}."""
    out = {}
    for record in idx.records:
        if not (set(record.get("awards") or []) & spec.labels):
            continue
        player = idx.canonical(record["player"])
        season = record["season"]
        if player not in out or F.season_key(season) < F.season_key(out[player]):
            out[player] = season
    return out


def build(idx):
    """{list key: everything the pages and the timelines need}."""
    seasons = sorted({r["season"] for r in idx.records}, key=F.season_key)
    paid = [s for s in seasons if not idx.is_contracted(s)]
    rows_by_season = collections.defaultdict(list)
    for record in idx.records:
        rows_by_season[record["season"]].append(record)

    active = {idx.canonical(r["player"]) for r in idx.records
              if r["season"] == idx.current_season}

    out = {}
    for spec in LISTS:
        first = _first_selections(idx, spec)

        # spec and first are bound now, not read when the closure is called:
        # one of these is kept per list and the loop moves on.
        def phrase(player, spec=spec, first=first):
            return spec.phrase(player not in active, player in first)

        total = collections.Counter()
        seen_in = collections.defaultdict(set)
        reigns, events, standing = [], collections.defaultdict(list), []
        entered, leader = set(), None

        for season in paid:
            for record in rows_by_season[season]:
                total[idx.canonical(record["player"])] += record.get("salary") or 0

            def qualifies(player):
                got = first.get(player)
                return got is None or F.season_key(season) < F.season_key(got)

            table = sorted(
                ((money, player) for player, money in total.items()
                 if money and qualifies(player)),
                key=lambda pair: (-pair[0], pair[1]),
            )
            places = {player: i + 1 for i, (_m, player) in enumerate(table)}

            # a man whose first selection came this season leaves the list, and
            # the line is worth printing where he was inside the top ten.
            for player, got in first.items():
                if got != season:
                    continue
                was = seen_in[player]
                if was and min(was) <= TOP:
                    events[(player, season)].append({
                        "kind": "drought_left", "list": spec.key,
                        "rank": min(was), "selection": spec.selection,
                        "won": spec.won, "drought": phrase(player),
                    })

            for player, place in places.items():
                if place > TOP:
                    continue
                seen_in[player].add(place)
                if player not in entered:
                    entered.add(player)
                    events[(player, season)].append({
                        "kind": "drought_top10", "list": spec.key,
                        "rank": place, "drought": phrase(player),
                    })

            top = table[0][1] if table else None
            if top != leader:
                if leader is not None and first.get(leader) != season:
                    # he did not leave through a selection, so he was passed
                    events[(leader, season)].append({
                        "kind": "drought_passed", "list": spec.key, "to": top,
                        "drought": phrase(leader),
                    })
                if top is not None:
                    events[(top, season)].append({
                        "kind": "drought_first", "list": spec.key,
                        "opening": not reigns, "drought": phrase(top),
                    })
                    reigns.append({"player": top, "from": season, "to": season,
                                   "total": table[0][0]})
                leader = top
            elif reigns:
                reigns[-1]["to"] = season
                reigns[-1]["total"] = table[0][0]

            if season == idx.current_season:
                standing = table

        for i, reign in enumerate(reigns):
            later = reigns[i + 1]["player"] if i + 1 < len(reigns) else None
            got = first.get(reign["player"])
            if got and F.season_key(got) == F.season_key(reign["to"]) + 1:
                reign["why"] = "selected"
                reign["detail"] = got
            elif later:
                reign["why"] = "passed"
                reign["detail"] = later
            else:
                reign["why"] = "holds"
                reign["detail"] = None
            reign["opening"] = i == 0

        out[spec.key] = {
            "spec": spec,
            "phrase": phrase,
            "first": first,
            "reigns": reigns,
            "events": dict(events),
            "standing": standing,
            "places": {player: i + 1 for i, (_m, player) in enumerate(standing)},
            "totals": {player: money for money, player in standing},
        }
    return out


def retired(idx, player):
    """Whether his last season on file is behind the current one."""
    return not any(
        idx.canonical(r["player"]) == player and r["season"] == idx.current_season
        for r in idx.records
    )


def summary_line(built, player):
    """His one sentence about these lists, or "" where he is not near one.

    Top ten only, and one list per man: a player inside two of them is said to
    be in the more notable, which is the order LISTS is written in. The rank
    is named because second on a list of this kind is a different fact from
    tenth on it.
    """
    for spec in LISTS:
        lst = built[spec.key]
        place = lst["places"].get(player)
        if not place or place > TOP:
            continue
        where = ("the highest-paid player" if place == 1
                 else "{} among players".format(F.ordinal(place)))
        drought = lst["phrase"](player)
        if place == 1:
            return "He is {} {}.".format(where, drought)
        return "He is {} {}, on {} paid to date.".format(
            where, drought, F.fmt_money(lst["totals"].get(player) or 0))
    return ""
