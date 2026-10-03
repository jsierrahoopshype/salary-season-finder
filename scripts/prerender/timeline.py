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
#: The career totals worth a line: the first fifty million, and every hundred
#: million after it. The engine's own ladder has rungs at 150 and 250, which on
#: a timeline reads as a man passing something every other season.
MARKS = (50000000, 100000000, 200000000, 300000000, 400000000, 500000000,
         600000000, 700000000)

from . import config as C  # noqa: E402
from .seasons import _noun  # noqa: E402

#: The cohorts a timeline talks about. Each is one a reader recognises as a
#: group. Pick ranges and college positions are left out: they are real
#: cohorts and they are not how anybody describes a career. So is the exact
#: pick: "the highest salary ever for a No. 27 pick" is a group of one draft
#: slot and nobody's idea of an achievement, so a draft standing is read off
#: the ranges below instead.
KINDS = ("college", "nationality", "draft_class", "position", "region")

#: Where a draft runs out, and what each boundary is called. A man is measured
#: against everyone taken after the boundary he cleared, so a 27th pick is one
#: of the men drafted outside the top 5, outside the top 10, outside the
#: lottery and outside the top 20. The claim worth making is the broadest of
#: those he holds, which is the smallest cutoff: being the best-paid man taken
#: outside the top 5 says more than being the best-paid taken outside the top
#: 20, because it is the bigger field.
DRAFT_RANGES = (
    (5, "player drafted outside the top 5"),
    (10, "player drafted outside the top 10"),
    (14, "player drafted outside the lottery"),
    (20, "player drafted outside the top 20"),
    (30, "player drafted in the second round"),
)

#: What an undrafted man is measured against.
UNDRAFTED = "undrafted player"

#: The round figures a "first to be paid this much" claim is allowed to use.
#: The exact salary is not a threshold anybody crossed on purpose, and "the
#: first Duke player paid $31,742,000 in a season" is a coincidence rather
#: than a milestone.
STEPS = (10000000, 20000000, 30000000, 40000000, 50000000, 60000000)

#: Seasons a draft class has to be old before a claim about it means anything.
#: Four, so a class counts from its fifth season on. Until then its men are on
#: rookie-scale deals and the order inside it is the order the scale set: the
#: top pick leads the class because he was the top pick, which is a fact about
#: the scale and not about him. The fifth season is the first one every man in
#: the class could have signed for himself.
CLASS_GRACE = 4

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
    "high_set": 2, "high_lost": 3,
    "threshold_first": 4,
    "top_start": 5, "top_end": 6,
    "move_top": 7, "league_top10_first": 8,
    "list_top": 9, "milestone": 10, "move_five": 11,
    "list_up": 12, "list_down": 13,
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
    steps = {}         # scope -> the biggest round figure anyone in it crossed
    seen_top10 = set()
    previous = {}      # player -> his salary the season before

    for season in seasons:
        rows = [r for r in idx.records if r["season"] == season]
        paid = not idx.is_contracted(season)
        here = {idx.canonical(r["player"]): r for r in rows}

        _statuses(idx, season, rows, sizes, scopes_of, held, found, here)
        if paid:
            _records(idx, season, rows, sizes, scopes_of, high, found, here)
            _thresholds(idx, season, rows, sizes, scopes_of, steps, found)
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
    """Every group a season of his is measured inside, cohorts and his team.

    A draft standing comes in as one scope per boundary he cleared, so the
    writer can take the broadest of them he holds rather than naming his pick.
    """
    out = []
    allowed = F._cohorts_for(record, idx)
    for kind, key, _phrase in allowed:
        if kind in KINDS:
            out.append((kind, key))
    if allowed:
        # The same identity guard _cohorts_for applies: a name whose draft
        # fields belong to a son of the same name joins no draft group.
        for scope in _draft_scopes(record):
            out.append(scope)
    for code in F.team_codes(record):
        if code in idx.franchises:
            out.append(("franchise", code))
    return out


def _draft_scopes(record):
    """Every draft boundary a man was taken after, broadest first."""
    pick = record.get("draft_pick")
    if pick is None and record.get("draft_year") is None:
        return [("draft_range", "undrafted")]
    if not pick:
        return []
    return [("draft_range", str(cut)) for cut, _label in DRAFT_RANGES
            if pick > cut]


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
        if _countable(scope, sizes) and _green(scope, season):
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


def _green(scope, season):
    """Whether a scope is old enough that season for a claim about it.

    A draft class takes two seasons to mean anything: in the first of them the
    top pick leads it because the rookie scale says so.
    """
    kind, key = scope
    if kind != "draft_class":
        return True
    try:
        first = int(key)
    except (TypeError, ValueError):
        return True
    # season_key("2011-12") is 2012, so the class of 2011 plays its first
    # season on key 2012 and its fifth on 2016, which is the first it counts.
    return F.season_key(season) >= first + 1 + CLASS_GRACE


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
            if not _countable(scope, sizes) or not _green(scope, season):
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

def _thresholds(idx, season, rows, sizes, scopes_of, steps, found):
    """The first man in a group paid a round figure or more in one season.

    The figure is one of STEPS, never his salary: being the first Duke player
    paid $30 million or more is a threshold somebody crossed; being the first
    paid $31,742,000 is arithmetic. Only the man who crossed it first gets the
    line, which is what the running high per group is for.
    """
    best = {}
    for record in rows:
        if F.is_split_season(record):
            continue
        player = idx.canonical(record["player"])
        if (player, season) in idx.impossible:
            continue
        salary = record.get("salary") or 0
        for scope in scopes_of[id(record)]:
            if not _countable(scope, sizes) or not _green(scope, season):
                continue
            if scope not in best or salary > best[scope][0]:
                best[scope] = (salary, player)

    for scope, (salary, player) in sorted(best.items(), key=lambda kv: str(kv[0])):
        standing = steps.get(scope, 0)
        crossed = max((step for step in STEPS if salary >= step), default=0)
        if crossed > standing:
            steps[scope] = crossed
            found[(player, season)].append({
                "kind": "threshold_first", "scope": scope, "value": crossed,
                "size": sizes.get(scope, 0)})


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
        for milestone in MARKS:
            if total >= milestone and (player, milestone) not in passed:
                passed.add((player, milestone))
                found[(player, season)].append({
                    "kind": "milestone", "value": milestone})


def _lists(idx, season, rows, sizes, scopes_of, career, members, ranked, found):
    """A place moved on a cohort's all-time career-earnings list."""
    touched = set()
    for record in rows:
        for scope in scopes_of[id(record)]:
            if (scope[0] != "franchise" and _countable(scope, sizes)
                    and _green(scope, season)):
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


def _range_noun(key):
    """What a draft boundary is called in a sentence."""
    if key == "undrafted":
        return UNDRAFTED
    for cut, label in DRAFT_RANGES:
        if str(cut) == key:
            return label
    return ""


WORDS = ("", "first", "second", "third", "fourth", "fifth", "sixth",
         "seventh", "eighth", "ninth", "tenth")

#: The three tenses a line can be in. A past season happened, the season being
#: played is happening, and a contracted one is money nobody has been paid, so
#: it can only ever be put conditionally.
PAST, NOW, LATER = "past", "now", "later"


def lines(idx, records, events):
    """[(season, one sentence)] for one man, newest season first.

    One sentence a season. Where the season's two events share a number or a
    subject they are said together, with the number given once; where they
    have nothing to do with each other only the stronger is kept, because two
    unrelated facts in one line read as a list.

    A season build() found nothing in gets no line. No two lines open on the
    same word, and no phrasing runs for more than two seasons together, so a
    career does not read as the same sentence forty times.
    """
    out = []
    if not records:
        return out
    player = idx.canonical(records[0]["player"])
    seasons = sorted({r["season"] for r in records},
                     key=F.season_key, reverse=True)
    word, recent = "", []
    for season in seasons:
        found = events.get((player, season)) or []
        if not found:
            continue
        tense = _tense(idx, season)
        text, pattern = _merged(idx, found, tense, season, word, recent)
        if not text:
            text, pattern = _single(idx, found, tense, season, word, recent)
        if not text:
            continue
        out.append((season, text))
        word = _first_word(text)
        recent = (recent + [pattern])[-2:]
    return out


def _first_word(text):
    """The word a line opens on, which is what must not repeat."""
    return (text.split() or [""])[0].strip(",.'").lower()


def _allowed(forms, kind, word, recent):
    """The forms a line may take here, after the two repetition rules.

    A form is out if it opens on the word the line above opened on, and out if
    its phrasing has already run for the last two seasons. Where that leaves
    nothing, the word rule is dropped first: a repeated opening is a smaller
    fault than saying the wrong thing.
    """
    tired = [i for i, _f in enumerate(forms)
             if recent.count((kind, i)) >= 2]
    fresh = [i for i, form in enumerate(forms)
             if i not in tired and _first_word(form) != word]
    return fresh or [i for i, _f in enumerate(forms) if i not in tired] \
        or list(range(len(forms)))


def _pick(forms, kind, word, recent, *parts):
    """(the form, its pattern) for one clause."""
    options = _allowed(forms, kind, word, recent)
    chosen = options[int(hashlib.sha1(
        "|".join(str(p) for p in parts).encode("utf-8")).hexdigest(),
        16) % len(options)]
    return forms[chosen], (kind, chosen)


def _single(idx, found, tense, season, word, recent):
    """The strongest event a season had, on its own."""
    for event in found:
        options = _PHRASES.get(_family(event))
        bits = _bits(idx, event, season)
        if not options or bits is None:
            continue
        forms = options.get(tense) or options.get(PAST) or ()
        if not forms:
            continue
        form, pattern = _pick(forms, _family(event), word, recent,
                              event["kind"], season, bits.get("who", ""))
        return form.format(**bits), pattern
    return "", None


def _family(event):
    """Which set of phrasings an event takes.

    A franchise says it its own way: "the Bucks single-season record" is a
    sentence and "the player on the Bucks single-season record" is not.
    """
    kind = event["kind"]
    if (event.get("scope") or ("", ""))[0] == "franchise" \
            and kind + "_team" in _PHRASES:
        return kind + "_team"
    return kind


#: What can be said in one breath, as (the strongest kind, the one after it).
#: A record and a record share a figure; a milestone and a place on a list are
#: both about the same career total.
MERGES = (("high_set", "high_set"),
          ("milestone", "list_up"), ("milestone", "list_down"),
          ("milestone", "list_top"))


def _merged(idx, found, tense, season, word, recent):
    """One sentence carrying both of a season's events, where they belong together."""
    if len(found) < 2:
        return "", None
    first, second = found[0], found[1]
    if (first["kind"], second["kind"]) not in MERGES:
        return "", None
    one, two = _bits(idx, first, season), _bits(idx, second, season)
    if one is None or two is None:
        return "", None

    if first["kind"] == "high_set":
        if first.get("value") != second.get("value"):
            return "", None
        # The cohort reads first and the team second, which is how the fact is
        # spoken: the most ever paid to an international player, and a club
        # record on top of it.
        cohort, team = first, second
        if (first.get("scope") or ("",))[0] == "franchise":
            cohort, team = second, first
        one, two = _bits(idx, cohort, season), _bits(idx, team, season)
        tail = ("a {} record".format(two["team"])
                if (team.get("scope") or ("",))[0] == "franchise"
                else "the most ever for {} {}".format(two["a"], two["who"]))
        forms = _MERGED["high_set"].get(tense) or _MERGED["high_set"][PAST]
        form, pattern = _pick(forms, "merged_high", word, recent,
                              season, one.get("who", ""))
        return form.format(tail=tail, **one), pattern

    place = ("the most of any {}".format(two["group"])
             if second["kind"] == "list_top"
             else "{} among {}".format(two["place"], two["group"]))
    forms = _MERGED["milestone"].get(tense) or _MERGED["milestone"][PAST]
    form, pattern = _pick(forms, "merged_milestone", word, recent,
                          season, one.get("money", ""))
    return form.format(place=place, **one), pattern


_MERGED = {
    "high_set": {
        PAST: ("His {money} was the most ever paid to {a} {who} and {tail}.",
               "At {money} he was the best-paid {who} the league had seen, and {tail}."),
        NOW: ("His {money} is the most ever paid to {a} {who} and {tail}.",
              "At {money} he is the best-paid {who} the league has seen, and {tail}."),
        LATER: ("His {money} would be the most ever paid to {a} {who} and {tail}.",
                "At {money} he would be the best-paid {who} the league has seen, and {tail}."),
    },
    "milestone": {
        PAST: ("Passed {money} in career earnings, {place}.",
               "Crossed {money} in career earnings, {place}."),
        NOW: ("Will pass {money} in career earnings this season, {place}.",
              "Will cross {money} in career earnings this season, {place}."),
        LATER: ("Would pass {money} in career earnings, {place}.",
                "Would cross {money} in career earnings, {place}."),
    },
}


def _tense(idx, season):
    """Which tense a season takes: it happened, it is happening, or it would."""
    key = F.season_key(season)
    if key > idx.current_key:
        return LATER
    return NOW if key == idx.current_key else PAST


def _bits(idx, event, season):
    """The words one event lends its sentence, or None where it has none."""
    out = {"name": event.get("to") or event.get("previous") or "",
           "season": season}
    scope = event.get("scope")
    if scope is not None:
        kind, key = scope
        noun = _range_noun(key) if kind == "draft_range" else _noun(idx, kind, key)
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
#: one set per tense, because a contracted season has not happened.
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
               "{name} took over the NBA's top salary."),
        NOW: ("No longer holds the league's biggest salary, which is {name}'s.",
              "{name} has the NBA's top salary now."),
        LATER: ("Would lose the league's biggest salary to {name}.",
                "{name} would take over the NBA's top salary."),
    },
    "high_set": {
        PAST: ("Set a new high for {a} {who}, {money}.",
               "Raised the single-season record among {group} to {money}."),
        NOW: ("Holds the single-season high among {group}, {money}.",
              "Carries the single-season record among {group}, {money}."),
        LATER: ("Would set a new high for {a} {who}, {money}.",
                "Would raise the single-season record among {group} to {money}."),
    },
    "high_set_team": {
        PAST: ("Set a new high for a {team} player, {money}.",
               "Raised the {team} single-season record to {money}."),
        NOW: ("Holds the {team} single-season record, {money}.",
              "Carries the biggest salary in {team} history, {money}."),
        LATER: ("Would set a new high for a {team} player, {money}.",
                "Would raise the {team} single-season record to {money}."),
    },
    "high_lost": {
        PAST: ("Lost the single-season record among {group} to {name}.",
               "{name} took over the single-season high among {group}."),
        NOW: ("No longer holds the single-season high among {group}, which is {name}'s.",
              "{name} holds the single-season record among {group} now."),
        LATER: ("Would lose the single-season record among {group} to {name}.",
                "{name} would take over the single-season high among {group}."),
    },
    "high_lost_team": {
        PAST: ("Lost the {team} single-season record to {name}.",
               "{name} took over the {team} single-season record."),
        NOW: ("No longer holds the {team} single-season record, which is {name}'s.",
              "{name} holds the {team} single-season record now."),
        LATER: ("Would lose the {team} single-season record to {name}.",
                "{name} would take over the {team} single-season record."),
    },
    "threshold_first": {
        PAST: ("Became the first {who} paid {money} or more in a season.",
               "Was the first {who} to be paid {money} or more in a season."),
        NOW: ("Is the first {who} paid {money} or more in a season.",
              "Becomes the first {who} paid {money} or more in a season."),
        LATER: ("Would be the first {who} paid {money} or more in a season.",
                "Would become the first {who} paid {money} or more in a season."),
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
               "{name} took over as the highest-paid {who}."),
        NOW: ("No longer the highest-paid {who}, a spot {name} holds.",
              "{name} is the highest-paid {who} now."),
        LATER: ("Would lose the highest-paid {who} spot to {name}.",
                "{name} would take over as the highest-paid {who}."),
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
        NOW: ("Will pass {money} in career earnings this season.",
              "Will cross {money} in career earnings this season."),
        LATER: ("Would pass {money} in career earnings.",
                "Would cross {money} in career earnings."),
    },
    "list_top": {
        PAST: ("Became the biggest career earner among {group}.",
               "Moved to the top of the career-earnings list for {group}."),
        NOW: ("Is the biggest career earner among {group}.",
              "Leads the career-earnings list for {group}."),
        LATER: ("Would become the biggest career earner among {group}.",
                "Would move to the top of the career-earnings list for {group}."),
    },
    "list_up": {
        PAST: ("Moved up to {place} among {group} in career earnings.",
               "Climbed to {place} on the list of highest-paid {group} ever."),
        NOW: ("Sits {place} among {group} in career earnings.",
              "Ranks {place} on the list of highest-paid {group} ever."),
        LATER: ("Would move up to {place} among {group} in career earnings.",
                "Would climb to {place} on the list of highest-paid {group} ever."),
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
