"""A player page's season-by-season timeline: what changed, season by season.

The season table says what he was paid. The summary says what his career adds
up to. Neither says what any one season *did*, which is the thing a reader
scrolling a career wants: the year he became the highest-paid man out of his
college, the year somebody took it off him, the year his raise was the biggest
in the league.

So this module makes one chronological sweep of the whole file and works out,
for every man and every season, what that season changed. Everything is judged
as it stood at the time: the record he set in 2013-14 was the record then, and
a salary paid in 2019-20 is not held against it. Nothing is asserted that the
sweep did not see happen, and a season where nothing changed gets no line.

A status is said twice at most, when it starts and when it ends, never in the
seasons between, so a man who was the highest-paid Duke player for nine years
reads as two lines rather than nine.
"""

from __future__ import annotations

import collections
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import factoids as F  # noqa: E402
from digest_nuggets import MILESTONES  # noqa: E402

from . import config as C  # noqa: E402
from .seasons import _noun  # noqa: E402

#: The cohorts a timeline talks about. Each has a page, so each can be linked,
#: and each is one a reader recognises as a group. Pick ranges and college
#: positions are left out: they are real cohorts and they are not how anybody
#: describes a career.
KINDS = ("college", "nationality", "draft_class", "draft_slot", "position",
         "region")

#: How far down a career-earnings list is worth a line. Past this, moving a
#: place is arithmetic rather than news: a man's rank inside his draft class
#: shuffles every season as his peers get paid, and a page that reported each
#: shuffle would say nothing else.
LIST_DEPTH = 5

#: How near the top of a season's salaries is worth saying once.
LEAGUE_DEPTH = 10

#: Raises and cuts inside the league's biggest this many are worth a line.
MOVE_DEPTH = 5

#: Events a season can carry, and at most this many of them.
PER_SEASON = 2

#: Strength, lowest first. Widest and rarest first: what the whole league saw,
#: then a record that stood until now, then who held a title that season, then
#: how the money moved, then what a career adds up to.
STRENGTH = {
    "league_top_start": 0, "league_top_end": 1,
    "list_top": 7,
    "high_set": 2, "high_lost": 3,
    "top_start": 4, "top_end": 5,
    "move_top": 6, "league_top10_first": 7,
    "milestone": 8, "move_five": 9,
    "list_up": 10, "list_down": 11,
}


def build(idx):
    """{(player, season): [event, ...]}, strongest first, at most PER_SEASON.

    One sweep, oldest season first, carrying the state a reader would have had
    at the time: who held what, what the records were, what every career had
    reached.
    """
    seasons = sorted(idx.seasons, key=F.season_key)
    by_player = collections.defaultdict(list)
    for season in seasons:
        for record in idx.records:
            if record["season"] == season:
                by_player[idx.canonical(record["player"])].append(record)

    scopes_of = {}
    for record in idx.records:
        scopes_of[id(record)] = _scopes(idx, record)
    sizes = _sizes(idx)

    found = collections.defaultdict(list)
    high = {}          # scope -> (value, player): the record, as it stood
    career = collections.Counter()
    members = collections.defaultdict(set)
    held = collections.defaultdict(set)   # player -> scopes he was top of
    ranked = collections.defaultdict(dict)  # scope -> {player: his place}
    passed = set()     # milestones each man has already crossed
    seen_top10 = set()
    previous = {}      # player -> his salary the season before

    for season in seasons:
        rows = [r for r in idx.records if r["season"] == season]
        paid = not idx.is_contracted(season)
        here = {idx.canonical(r["player"]): r for r in rows}

        _statuses(idx, season, rows, sizes, scopes_of, held, found, here)
        if paid:
            _records(idx, season, rows, sizes, scopes_of, high, found, here)
        _moves(idx, season, rows, previous, found)
        _league(idx, season, rows, found, seen_top10, here)

        if paid:
            for record in rows:
                player = idx.canonical(record["player"])
                career[player] += record.get("salary") or 0
                for scope in scopes_of[id(record)]:
                    members[scope].add(player)
            _milestones(season, rows, idx, career, passed, found)
            _lists(idx, season, rows, sizes, scopes_of, career, members, ranked, found)

        for record in rows:
            if paid:
                previous[idx.canonical(record["player"])] = (
                    record.get("salary") or 0)

    for key, events in found.items():
        events.sort(key=lambda e: (STRENGTH[e["kind"]], -e.get("size", 0),
                                   str(e.get("scope"))))
        found[key] = _thin(events)[:PER_SEASON]
    return found


def _thin(events):
    """One event per kind of group, strongest first.

    "Set the biggest salary ever paid to an international player" and "became
    the first European player paid that much" are the same fact told twice,
    and so are a record lost and a title lost on the same scope. One per group
    leaves a season saying two different things.
    """
    out, seen = [], set()
    for event in events:
        scope = event.get("scope")
        family = scope[0] if scope else event["kind"].split("_")[0]
        if family in seen:
            continue
        seen.add(family)
        out.append(event)
    return out


# -- the scopes one record belongs to ---------------------------------------

def _scopes(idx, record):
    """Every group a season of his is measured inside, cohorts and his team."""
    out = []
    for kind, key, _phrase in F._cohorts_for(record, idx):
        if kind in KINDS:
            out.append((kind, key))
    for code in F.team_codes(record):
        if code in idx.franchises:
            out.append(("franchise", code))
    return out


def _sizes(idx):
    """{scope: how many men it has ever held}.

    Counted here rather than read off the engine's comparison sets, which are
    keyed for hold-out queries and carry no head count. How many men a scope
    holds is how strong a claim about it is, and it is also the floor a cohort
    has to clear before "the highest-paid" means anything.
    """
    out = collections.defaultdict(set)
    for record in idx.records:
        player = idx.canonical(record["player"])
        for kind, key, _phrase in F._cohorts_for(record, idx):
            if kind in KINDS:
                out[(kind, key)].add(player)
        for code in F.team_codes(record):
            if code in idx.franchises:
                out[("franchise", code)].add(player)
    return {scope: len(players) for scope, players in out.items()}


# -- who was the highest paid, and when that started and stopped ------------

def _statuses(idx, season, rows, sizes, scopes_of, held, found, here):
    """The highest-paid man in each scope that season, as a status.

    Said once when it starts and once when it ends, and the man who took it
    named where it ended, because "lost it" without a name is half a fact.
    """
    best = {}
    for record in rows:
        player = idx.canonical(record["player"])
        salary = record.get("salary") or 0
        if not salary:
            continue
        for scope in scopes_of[id(record)]:
            if scope not in best or salary > best[scope][0]:
                best[scope] = (salary, player)

    tops = collections.defaultdict(set)
    for scope, (_salary, player) in best.items():
        if _countable(scope, sizes):
            tops[player].add(scope)

    for player, record in here.items():
        was, now = held.get(player, set()), tops.get(player, set())
        for scope in now - was:
            found[(player, season)].append({
                "kind": "top_start", "scope": scope,
                "size": sizes.get(scope, 0)})
        for scope in was - now:
            # Leaving a team is not losing a title to anybody, so a franchise
            # he is no longer on gets no line: he did not get passed, he went.
            if scope[0] == "franchise" and scope not in scopes_of[id(record)]:
                continue
            taken = best.get(scope, (0, ""))[1]
            if not taken:
                continue
            found[(player, season)].append({
                "kind": "top_end", "scope": scope, "to": taken,
                "size": sizes.get(scope, 0)})
    for player in set(tops) | set(held):
        if player in tops:
            held[player] = tops[player]
        elif player in here:
            held[player] = set()


def _countable(scope, sizes):
    """Whether a scope is big enough for "the highest-paid" to mean anything.

    The same floors the engine holds its cohort claims to, so a page never says
    "the highest-paid player out of somewhere" about a group of three.
    """
    kind, _key = scope
    if kind == "franchise":
        return True
    floor = F.COHORT_MINIMUMS.get(kind)
    return floor is None or sizes.get(scope, 0) >= floor


# -- the records, as they stood ---------------------------------------------

def _records(idx, season, rows, sizes, scopes_of, high, found, here):
    """An all-time single-season high set, or taken off the man who held it."""
    best = {}
    for record in rows:
        if F.is_split_season(record):
            continue
        player = idx.canonical(record["player"])
        if (player, season) in idx.impossible:
            continue
        salary = record.get("salary") or 0
        for scope in scopes_of[id(record)]:
            if not _countable(scope, sizes):
                continue
            if scope not in best or salary > best[scope][0]:
                best[scope] = (salary, player)

    for scope, (salary, player) in sorted(best.items(), key=lambda kv: str(kv[0])):
        standing = high.get(scope)
        if standing is not None and salary <= standing[0]:
            continue
        held_by = standing[1] if standing else ""
        if held_by and held_by != player and held_by in here:
            # A franchise record he set before leaving is still his to lose,
            # but only where he is still on those books to lose it on.
            on_it = scope[0] != "franchise" or scope in scopes_of[id(here[held_by])]
            if on_it:
                found[(held_by, season)].append({
                    "kind": "high_lost", "scope": scope, "to": player,
                    "size": sizes.get(scope, 0)})
        if standing is None or standing[1] != player:
            found[(player, season)].append({
                "kind": "high_set", "scope": scope, "value": salary,
                "previous": standing[1] if standing else "",
                "size": sizes.get(scope, 0)})
        high[scope] = (salary, player)


# -- how the money moved ----------------------------------------------------

def _moves(idx, season, rows, previous, found):
    """The biggest raises and cuts in the league that season.

    A raise is against the season before, which is how a reader hears one. A
    split season is left out on both sides: a salary spread over two teams is
    not one man's pay packet going up. So is a salary the guard flagged.
    """
    moves = []
    for record in rows:
        player = idx.canonical(record["player"])
        before = previous.get(player)
        if before is None or not before or F.is_split_season(record):
            continue
        if (player, season) in idx.impossible:
            continue
        moves.append((((record.get("salary") or 0) - before), player))
    ups = sorted((m for m in moves if m[0] > 0), key=lambda m: -m[0])
    downs = sorted((m for m in moves if m[0] < 0), key=lambda m: m[0])
    for table, top, five in ((ups, "raise", "raise"), (downs, "cut", "cut")):
        for place, (amount, player) in enumerate(table[:MOVE_DEPTH], start=1):
            found[(player, season)].append({
                "kind": "move_top" if place == 1 else "move_five",
                "way": top, "amount": abs(amount), "place": place})


# -- what the whole league saw ----------------------------------------------

def _league(idx, season, rows, found, seen_top10, here):
    """The league's highest salary, as a status, and a first top-ten season."""
    ordered = sorted(
        ((r.get("salary") or 0, idx.canonical(r["player"])) for r in rows),
        key=lambda pair: -pair[0])
    for place, (_salary, player) in enumerate(ordered[:LEAGUE_DEPTH], start=1):
        if place == 1:
            continue
        if player not in seen_top10:
            seen_top10.add(player)
            found[(player, season)].append({
                "kind": "league_top10_first", "place": place})
    if ordered:
        _salary, leader = ordered[0]
        seen_top10.add(leader)
        was = _league.holder
        if was != leader:
            found[(leader, season)].append({"kind": "league_top_start"})
            if was and was in here:
                found[(was, season)].append({
                    "kind": "league_top_end", "to": leader})
        _league.holder = leader


_league.holder = ""


# -- what a career adds up to ----------------------------------------------

def _milestones(season, rows, idx, career, passed, found):
    """The round numbers a career goes past."""
    for record in rows:
        player = idx.canonical(record["player"])
        if not idx.career_rankable(player):
            continue
        total = career[player]
        for milestone in MILESTONES:
            if total >= milestone and (player, milestone) not in passed:
                passed.add((player, milestone))
                found[(player, season)].append({
                    "kind": "milestone", "value": milestone})


def _lists(idx, season, rows, sizes, scopes_of, career, members, ranked, found):
    """A place moved on a cohort's all-time career-earnings list."""
    touched = set()
    for record in rows:
        for scope in scopes_of[id(record)]:
            if scope[0] != "franchise" and _countable(scope, sizes):
                touched.add(scope)
    here = {idx.canonical(r["player"]) for r in rows}
    for scope in sorted(touched, key=str):
        table = sorted(
            ((career[p], p) for p in members[scope] if career[p]),
            key=lambda pair: (-pair[0], pair[1]))
        places = {player: place for place, (_total, player)
                  in enumerate(table, start=1)}
        before = ranked[scope]
        for player, place in places.items():
            was = before.get(player)
            if player in here and place <= LIST_DEPTH and was != place:
                found[(player, season)].append({
                    # The top of a list is not a place on it: "climbed to
                    # first" is a sentence nobody writes.
                    "kind": "list_top" if place == 1
                            else "list_up" if was is None or place < was
                            else "list_down",
                    "scope": scope, "place": place, "was": was,
                    "size": sizes.get(scope, 0)})
        ranked[scope] = places


def stable_pick(options, *parts):
    """One of ``options``, fixed by what it is about so a rebuild repeats it."""
    key = "|".join(str(part) for part in parts)
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
    return options[int(digest, 16) % len(options)]


# --------------------------------------------------------------------------
# writing it
# --------------------------------------------------------------------------

#: Ranks a line spells out. Past these a digit is how a place is read.
WORDS = ("", "first", "second", "third", "fourth", "fifth", "sixth",
         "seventh", "eighth", "ninth", "tenth")

#: The three tenses a line can be in. A past season happened, the season being
#: played is happening, and a contracted one is money nobody has been paid, so
#: it can only ever be put conditionally.
PAST, NOW, LATER = "past", "now", "later"


def lines(idx, records, events):
    """[(season, one line)] for one man, newest season first.

    ``events`` is what build() found, and a season it found nothing in gets no
    line at all. Openings are varied and never repeat from the line before, so
    a career does not read as the same sentence forty times.
    """
    out, above = [], ""
    if not records:
        return out
    player = idx.canonical(records[0]["player"])
    seasons = sorted({r["season"] for r in records},
                     key=F.season_key, reverse=True)
    for season in seasons:
        found = events.get((player, season)) or []
        if not found:
            continue
        # The first clause of a line avoids how the line above it opened; a
        # clause after it avoids the one in front of it. Both rules at once, so
        # neither two lines nor two clauses start the same way.
        said, avoid = [], above
        for event in found:
            text = _say(idx, event, _tense(idx, season), season, avoid=avoid)
            if text:
                said.append(text)
                avoid = _opening(text)
        if said:
            out.append((season, " ".join(said)))
            above = _opening(said[0])
    return out


def _tense(idx, season):
    key = F.season_key(season)
    if key > idx.current_key:
        return LATER
    return NOW if key == idx.current_key else PAST


def _opening(text):
    """The first two words of a line, which is what must not repeat."""
    return " ".join(text.split()[:2]).lower()


def _say(idx, event, tense, season, avoid=""):
    """One event as one clause, in the tense the season takes."""
    kind = event["kind"]
    # A franchise takes its own wording: "the Bucks single-season record" is a
    # sentence and "the player on the Bucks single-season record" is not.
    if (event.get("scope") or ("", ""))[0] == "franchise":
        options = _PHRASES.get(kind + "_team") or _PHRASES.get(kind)
    else:
        options = _PHRASES.get(kind)
    if not options:
        return ""
    bits = _bits(idx, event, season)
    if bits is None:
        return ""
    forms = options.get(tense) or options.get(PAST) or ()
    if not forms:
        return ""
    choices = [f for f in forms if _opening(f.format(**bits)) != avoid] or list(forms)
    form = stable_pick(choices, event["kind"], season, bits.get("who", ""),
                       bits.get("name", ""))
    return form.format(**bits)


def _bits(idx, event, season):
    """The words one event lends its sentence, or None where it has none."""
    out = {"name": event.get("to") or event.get("previous") or "",
           "season": season}
    scope = event.get("scope")
    if scope is not None:
        kind, key = scope
        noun = _noun(idx, kind, key)
        if not noun:
            return None
        out["team"] = noun
        out["who"] = ("player on the {}".format(noun) if kind == "franchise"
                      else noun)
        out["a"] = "an" if noun[:1].lower() in "aeiou" else "a"
        out["group"] = F._rival_plural("any other " + noun)
    if "value" in event:
        out["money"] = F.fmt_money(event["value"])
    if "amount" in event:
        out["money"] = F.fmt_money(event["amount"])
    if "place" in event and event["place"]:
        place = event["place"]
        out["place"] = (WORDS[place] if place < len(WORDS)
                        else F.ordinal(place))
    out["way"] = event.get("way") or ""
    return out


#: Two ways of saying each event, so no line reads like the one above it, and
#: one per tense, because a contracted season has not happened.
_PHRASES = {
    "league_top_start": {
        PAST: ("Became the highest-paid player in the NBA.",
               "Took over the biggest salary in the league."),
        NOW: ("Is the highest-paid player in the NBA.",
              "Holds the biggest salary in the league."),
        LATER: ("Would become the highest-paid player in the NBA.",
                "Would take over the biggest salary in the league."),
    },
    "league_top_end": {
        PAST: ("Lost the league's biggest salary to {name}.",
               "Gave up the NBA's top salary to {name}."),
        NOW: ("No longer holds the league's biggest salary, which is {name}'s.",
              "Has been passed by {name} at the top of the league."),
        LATER: ("Would lose the league's biggest salary to {name}.",
                "Would give up the NBA's top salary to {name}."),
    },
    "high_set": {
        PAST: ("Set the biggest single-season salary ever paid to {a} {who}, {money}.",
               "Became the first {who} paid {money} in a season."),
        NOW: ("Holds the biggest single-season salary ever paid to {a} {who}, {money}.",
              "Is the first {who} paid {money} in a season."),
        LATER: ("Would set the biggest single-season salary ever paid to {a} {who}, {money}.",
                "Would be the first {who} paid {money} in a season."),
    },
    "high_set_team": {
        PAST: ("Set the biggest single-season salary in {team} history, {money}.",
               "Pushed the {team} single-season record to {money}."),
        NOW: ("Holds the biggest single-season salary in {team} history, {money}.",
              "Carries the {team} single-season record, {money}."),
        LATER: ("Would set the biggest single-season salary in {team} history, {money}.",
                "Would push the {team} single-season record to {money}."),
    },
    "high_lost": {
        PAST: ("Saw {name} pass the biggest salary ever paid to {a} {who}.",
               "Lost the single-season high among {group} to {name}."),
        NOW: ("Has seen {name} pass the biggest salary ever paid to {a} {who}.",
              "No longer holds the single-season high among {group}, which is {name}'s."),
        LATER: ("Would see {name} pass the biggest salary ever paid to {a} {who}.",
                "Would lose the single-season high among {group} to {name}."),
    },
    "high_lost_team": {
        PAST: ("Lost the {team} single-season record to {name}.",
               "Saw {name} pass his {team} single-season record."),
        NOW: ("Has lost the {team} single-season record to {name}.",
              "Watches {name} hold the {team} single-season record he set."),
        LATER: ("Would lose the {team} single-season record to {name}.",
                "Would see {name} pass his {team} single-season record."),
    },
    "top_start": {
        PAST: ("Became the highest-paid {who}.",
               "Took over as the highest-paid {who}."),
        NOW: ("Is the highest-paid {who}.",
              "Stands as the highest-paid {who}."),
        LATER: ("Would become the highest-paid {who}.",
                "Would take over as the highest-paid {who}."),
    },
    "top_end": {
        PAST: ("Lost the highest-paid {who} spot to {name}.",
               "Was passed as the highest-paid {who} by {name}."),
        NOW: ("Has been passed as the highest-paid {who} by {name}.",
              "No longer the highest-paid {who}, a spot {name} holds."),
        LATER: ("Would lose the highest-paid {who} spot to {name}.",
                "Would be passed as the highest-paid {who} by {name}."),
    },
    "move_top": {
        PAST: ("Took the biggest {way} in the league, {money}.",
               "His {money} {way} was the biggest in the NBA."),
        NOW: ("Takes the biggest {way} in the league, {money}.",
              "His {money} {way} is the biggest in the NBA."),
        LATER: ("Would take the biggest {way} in the league, {money}.",
                "His {money} {way} would be the biggest in the NBA."),
    },
    "move_five": {
        PAST: ("Took one of the five biggest {way}s in the league, {money}.",
               "His {money} {way} was among the five biggest in the NBA."),
        NOW: ("Takes one of the five biggest {way}s in the league, {money}.",
              "His {money} {way} is among the five biggest in the NBA."),
        LATER: ("Would take one of the five biggest {way}s in the league, {money}.",
                "His {money} {way} would be among the five biggest in the NBA."),
    },
    "league_top10_first": {
        PAST: ("Broke into the league's top 10 salaries for the first time, {place}.",
               "Reached the NBA's top 10 salaries for the first time, {place}."),
        NOW: ("Is inside the league's top 10 salaries for the first time, {place}.",
              "Reaches the NBA's top 10 salaries for the first time, {place}."),
        LATER: ("Would reach the league's top 10 salaries for the first time, {place}.",
                "Would break into the NBA's top 10 salaries, {place}."),
    },
    "milestone": {
        PAST: ("Passed {money} in career earnings.",
               "Crossed {money} in career earnings."),
        NOW: ("Passes {money} in career earnings.",
              "Crosses {money} in career earnings."),
        LATER: ("Would pass {money} in career earnings.",
                "Would cross {money} in career earnings."),
    },
    "list_up": {
        PAST: ("Moved up to {place} among {group} in career earnings.",
               "Climbed to {place} on the list of highest-paid {group} ever."),
        NOW: ("Sits {place} among {group} in career earnings.",
              "Ranks {place} on the list of highest-paid {group} ever."),
        LATER: ("Would move up to {place} among {group} in career earnings.",
                "Would climb to {place} on the list of highest-paid {group} ever."),
    },
    "list_top": {
        PAST: ("Became the biggest career earner among {group}.",
               "Moved to the top of the career-earnings list for {group}."),
        NOW: ("Is the biggest career earner among {group}.",
              "Leads the career-earnings list for {group}."),
        LATER: ("Would become the biggest career earner among {group}.",
                "Would move to the top of the career-earnings list for {group}."),
    },
    "list_down": {
        PAST: ("Dropped to {place} on the list of highest-paid {group} ever.",
               "Slipped to {place} among {group} in career earnings."),
        NOW: ("Sits {place} on the list of highest-paid {group} ever.",
              "Ranks {place} among {group} in career earnings."),
        LATER: ("Would drop to {place} on the list of highest-paid {group} ever.",
                "Would slip to {place} among {group} in career earnings."),
    },
}
