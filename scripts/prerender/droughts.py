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
    """One list: its page, its award and the words it is described in.

    Two forms of the group, and only two. These lists rank career earnings
    among men an award has not come to, so the present tense describes anyone
    on the list now and the past tense describes a season before his first
    selection. Neither "never" nor "yet to" appears: one claims a career is
    over and the other claims it is not, and a salary table knows neither.
    """

    __slots__ = ("key", "slug", "award", "labels", "present", "past",
                 "singular", "made", "one_past", "title_phrase", "selection",
                 "won")

    def __init__(self, key, slug, award, labels, present, past, singular,
                 made, one_past, title_phrase, selection, won=False):
        self.key = key
        self.slug = slug
        self.award = award
        self.labels = labels
        #: "who have not made an All-Star team", for anyone on the list now
        self.present = present
        #: "who had not made an All-Star team", for a season before he was picked
        self.past = past
        #: "who has not won MVP", after "more than any player"
        self.singular = singular
        #: "Made his first All-Star team" / "Won his first MVP"
        self.made = made
        #: "who had not made one", where the award is already named
        self.one_past = one_past
        #: the page's own heading and title
        self.title_phrase = title_phrase
        self.selection = selection
        self.won = won

    def phrase(self, selected_ever):
        """The group, in the tense the sentence needs."""
        return self.past if selected_ever else self.present


#: The three lists, in the order a tie between them is broken: a man who is on
#: more than one is described by the most notable of them.
LISTS = (
    Drought(
        "all-star", "never-all-star", "All-Star",
        frozenset({"All-Star", "All-Star MVP"}),
        present="who have not made an All-Star team",
        past="who had not made an All-Star team",
        singular="who has not made an All-Star team",
        made="Made his first All-Star team",
        one_past="who had not made one",
        title_phrase="Most career earnings by players who have not made an "
                     "All-Star team",
        selection="All-Star selection",
    ),
    Drought(
        "all-nba", "never-all-nba", "All-NBA",
        frozenset({"All-NBA First Team", "All-NBA Second Team",
                   "All-NBA Third Team"}),
        present="who have not made an All-NBA team",
        past="who had not made an All-NBA team",
        singular="who has not made an All-NBA team",
        made="Made his first All-NBA team",
        one_past="who had not made one",
        title_phrase="Most career earnings by players who have not made an "
                     "All-NBA team",
        selection="All-NBA selection",
    ),
    Drought(
        "mvp", "never-mvp", "MVP",
        frozenset({"Most Valuable Player"}),
        present="who have not won MVP",
        past="who had not won MVP",
        singular="who has not won MVP",
        made="Won his first MVP",
        one_past="who had not won one",
        title_phrase="Most career earnings by players who have not won MVP",
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
            return spec.phrase(player in first)

        total = collections.Counter()
        seen_in = collections.defaultdict(set)
        peaked = {}
        reigns, events, standing = [], collections.defaultdict(list), []
        places_now = {}
        leader = None

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
                        "made": spec.made,
                        "one_past": spec.one_past,
                    })

            for player, place in places.items():
                if place > TOP:
                    continue
                seen_in[player].add(place)
                # the first season he stood at his best place on this list,
                # which is only knowable once the sweep is over
                best = peaked.get(player)
                if best is None or place < best[0]:
                    peaked[player] = (place, season)

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
                places_now = dict(places)

        # One line a career for how far up a list he got. No. 1 has its own
        # event and says more, and a man still playing who is at his best
        # right now is described by the summary rather than by his own past.
        here = {player for player in peaked if player in active}
        for player, (place, season) in peaked.items():
            if place < 2 or place > TOP:
                continue
            if player in here and places_now.get(player) == place:
                continue
            events[(player, season)].append({
                "kind": "drought_peak", "list": spec.key,
                "rank": place, "drought": phrase(player),
            })

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
    """(the line, the line with his total in it), or ("", "").

    Top ten only, and one list per man: a player inside two of them is said to
    be in the more notable, which is the order LISTS is written in. The rank is
    named because second on a list of this kind is a different fact from tenth
    on it. Two forms, because the figure is worth giving only where no other
    sentence on the page has given it already.
    """
    for spec in LISTS:
        lst = built[spec.key]
        place = lst["places"].get(player)
        if not place or place > TOP:
            continue
        if place == 1:
            line = "He has earned more than any player {}.".format(spec.singular)
            return line, line
        short = "He is {} in career earnings among players {}.".format(
            F.ordinal(place), spec.present)
        full = "He is {} in career earnings among players {}, on {} paid to " \
            "date.".format(F.ordinal(place), spec.present,
                           F.fmt_money(lst["totals"].get(player) or 0))
        return short, full
    return "", ""
