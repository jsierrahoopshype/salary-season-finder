#!/usr/bin/env python3
"""HoopsMatic factoid engine (part one).

Given a (player, season, salary) triple, return the records that figure sets,
ties or approaches. The salary may be hypothetical, so the record under
evaluation is always removed from its own comparison set before ranking.

The guiding rule is precision over volume. Every family sits behind gates that
were set by the step-0 audit in scripts/audit_factoids.py; when a gate fails the
candidate is dropped and the reason is recorded, never softened into a vaguer
claim. Run the CLI with --debug to see what was dropped and why.

Part two (the daily diff engine and Slack digest) consumes this module:

    from factoids import build_index, factoids_for
    index = build_index(data)                     # build once, reuse
    facts = factoids_for(data, player, season, index=index)

Each factoid carries a stable ``id`` derived from the claim's identity, not from
its value, so the same claim keeps the same id from day to day and a diff can
compare values across runs.
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import os
import sys
from collections import defaultdict

# --------------------------------------------------------------------------
# Constants. Every gate and threshold is named here.
# --------------------------------------------------------------------------

#: Every all-time claim is scoped to the data window and says so in its text.
SCOPE_FIRST_SEASON = "1990-91"
SCOPE_SUFFIX = "since " + SCOPE_FIRST_SEASON

#: "approaches" = not the record, and either inside the top N by rank or
#: within this fraction of the record.
APPROACH_MAX_RANK = 5
APPROACH_WITHIN_PCT = 0.05

#: A comparison needs a field worth comparing against. "The highest of two" is
#: not a record and "fourth-highest of four" is not an approach.
MIN_COMPARISON_SIZE = 3
APPROACH_MIN_COMPARISON_SIZE = 10

#: Minimum distinct players before a cohort is worth ranking inside. The brief
#: names college and nationality; the other cohorts get a floor on the same
#: principle, since "highest of three" is not a factoid.
COLLEGE_MIN_PLAYERS = 15
NATIONALITY_MIN_PLAYERS = 5
DRAFT_CLASS_MIN_PLAYERS = 10
DRAFT_SLOT_MIN_PLAYERS = 10
POSITION_MIN_PLAYERS = 10

#: Agent factoids need enough clients for "highest-paid client" to mean anything.
AGENT_MIN_CLIENTS = 5

#: Career-earnings milestones, in nominal dollars.
CAREER_MILESTONES = (100e6, 150e6, 200e6, 250e6, 300e6, 400e6, 500e6)

#: Exact awards_list strings. Never pattern-matched: an award is a member of
#: these sets or it is not an All-Star / All-NBA selection.
ALL_STAR_AWARDS = frozenset({"All-Star"})
ALL_NBA_AWARDS = frozenset(
    {"All-NBA First Team", "All-NBA Second Team", "All-NBA Third Team"}
)

#: Plausibility bands for the awards audit. A season outside its band has an
#: untrustworthy selection list, so it cannot be used to prove a negative.
ALL_STAR_COUNT_MIN = 24
ALL_STAR_COUNT_MAX = 28
ALL_NBA_COUNT_EXPECTED = 15

#: A career with a gap this long is almost certainly two players merged under
#: one name (Jaren Jackson Sr and Jr, Gerald Henderson Sr and Jr, and so on).
#: Such a player is excluded from every career-level claim.
MAX_CAREER_GAP_SEASONS = 4

#: The oldest draft year that can produce a career fully inside the window.
FIRST_DRAFT_YEAR_IN_WINDOW = 1990

#: Draft slots that can form a cohort. Old drafts ran past 60 rounds deep and
#: those players are pre-window anyway.
MAX_DRAFT_SLOT = 60

#: Nationality strings that read with a definite article: "a player from the
#: United States". Exact strings from data.json's nationality field, listed out
#: rather than inferred, the same way the award strings are.
NATIONALITY_TAKES_THE = frozenset({
    "Bahamas",
    "Czech Republic",
    "DR Congo",
    "Dominican Republic",
    "Netherlands",
    "Philippines",
    "Republic of Georgia",
    "US Virgin Islands",
    "United States",
})

#: Exact position strings mapped to nouns. A position outside this map gets no
#: position cohort rather than a guessed noun.
POSITION_NOUNS = {
    "G": "guard",
    "F": "forward",
    "C": "center",
    "G-F": "guard-forward",
    "F-G": "forward-guard",
    "F-C": "forward-center",
    "C-F": "center-forward",
}

#: Cap share is ranked at the precision it is printed at, so two salaries that
#: both read "36.0%" tie instead of one sitting behind the other.
CAP_PCT_DECIMALS = 1

#: Where franchises.json lives, relative to the repository root.
FRANCHISES_PATH = os.path.join("data", "franchises.json")
DATA_PATH = os.path.join("data", "data.json")

_ORDINALS = {
    2: "second",
    3: "third",
    4: "fourth",
    5: "fifth",
    6: "sixth",
    7: "seventh",
    8: "eighth",
    9: "ninth",
    10: "10th",
}


# --------------------------------------------------------------------------
# Season and money helpers
# --------------------------------------------------------------------------


def season_key(season):
    """Sortable integer for a "1999-00" style season label (its ending year).

    Mirrors the front end's seasonYear() so the two agree on ordering and on
    which season counts as current.
    """
    if not season:
        return 0
    parts = str(season).split("-")
    if len(parts) != 2:
        return 0
    try:
        start = int(parts[0])
        end_short = int(parts[1])
    except ValueError:
        return 0
    century = (start // 100) * 100
    end = century + end_short
    if end <= start:
        end += 100
    return end


def fmt_money(value):
    """AP style money: "$47.6 million", "$50 million", "$507,336"."""
    if value is None:
        return ""
    value = float(value)
    if abs(value) >= 1e6:
        millions = value / 1e6
        text = "{:.1f}".format(millions)
        if text.endswith(".0"):
            text = text[:-2]
        return "$" + text + " million"
    return "$" + "{:,.0f}".format(value)


def ordinal(n):
    """Word ordinals for the small ranks the text actually uses."""
    return _ORDINALS.get(n, "{}th".format(n))


def _possessive(name):
    """English possessive. Names already ending in s take a bare apostrophe,
    which is what "the Knicks' highest-paid player" needs."""
    return name + "'" if name.endswith("s") else name + "'s"


# Verb forms for a contracted subject. Money that has not been paid cannot
# "be" the record, so the claim is stated conditionally and the scope note says
# why. The comparison set never contains contracted seasons at all.
def _is_verb(contracted):
    return "would be" if contracted else "is"


def _tie_verb(contracted):
    return "would tie" if contracted else "ties"


CONTRACTED_NOTE = "This salary is contracted, not money already paid."
PAID_ONLY_NOTE = (
    "Comparison set covers seasons through {}; contracted future seasons are "
    "excluded because that money has not been paid."
)


# --------------------------------------------------------------------------
# Ranking primitive
# --------------------------------------------------------------------------


class Universe:
    """A comparison set, sorted once, queried with one entry held out.

    Entries are dicts with ``value``, ``player``, ``season`` and an opaque
    ``key`` used for hold-out. Holding the subject out is what makes a
    hypothetical salary safe to evaluate: the record never competes with itself.
    """

    __slots__ = ("entries", "_neg", "_pos", "_players")

    def __init__(self, entries):
        self.entries = sorted(
            entries, key=lambda e: (-e["value"], e["player"], e["season"] or "")
        )
        self._neg = [-e["value"] for e in self.entries]
        self._pos = {}
        for i, e in enumerate(self.entries):
            self._pos.setdefault(e["key"], i)
        self._players = None

    def __len__(self):
        return len(self.entries)

    def distinct_players(self):
        if self._players is None:
            self._players = len({e["player"] for e in self.entries})
        return self._players

    def evaluate(self, value, exclude_key):
        """Rank ``value`` against this universe with ``exclude_key`` held out.

        Returns None when the universe is empty once the subject is removed.
        """
        held_out = self._pos.get(exclude_key)
        size = len(self.entries) - (1 if held_out is not None else 0)
        if size <= 0:
            return None

        # Number of entries strictly above `value`, minus the held-out one if it
        # was among them.
        above = bisect.bisect_left(self._neg, -value)
        if held_out is not None and held_out < above:
            above -= 1

        # The record to beat: the best entry that is not the subject.
        top = None
        for e in self.entries:
            if e["key"] != exclude_key:
                top = e
                break

        leaders = [e for e in self.entries[: above + 6] if e["key"] != exclude_key]
        ties = [
            e
            for e in leaders
            if e["value"] == value
        ]
        return {
            "rank": above + 1,
            "size": size,
            "top": top,
            "ties": ties,
            "leaders": leaders[:5],
        }


def classify(value, verdict):
    """sets / ties / approaches / None, from an evaluate() verdict."""
    if verdict is None:
        return None
    top = verdict["top"]
    if top is None:
        return None
    if verdict["size"] < MIN_COMPARISON_SIZE:
        return None
    if value > top["value"]:
        return "sets"
    if value == top["value"]:
        return "ties"
    if verdict["size"] < APPROACH_MIN_COMPARISON_SIZE:
        return None
    if verdict["rank"] <= APPROACH_MAX_RANK:
        return "approaches"
    if top["value"] > 0 and value >= top["value"] * (1.0 - APPROACH_WITHIN_PCT):
        return "approaches"
    return None


# --------------------------------------------------------------------------
# Index
# --------------------------------------------------------------------------


class FactoidIndex:
    """Everything the families need, precomputed once over the whole dataset."""

    def __init__(self):
        self.records = []
        self.by_key = {}
        self.by_player = defaultdict(list)
        self.current_season = ""
        self.current_key = 0
        self.seasons = []
        self.cap = {}
        self.franchises = {}
        # player-level flags
        self.truncated = set()
        self.identity_suspect = set()
        self.draft_meta_suspect = set()
        self.active_players = set()
        # awards
        self.all_star_players = set()
        self.all_nba_players = set()
        self.all_star_counts = {}
        self.all_nba_counts = {}
        self.awards_unsafe_seasons = set()
        self.awards_known_through = ""
        # agent
        self.agent_coverage = {}
        self.agent_seasons_safe = set()
        # universes
        self.u_franchise = {}
        self.u_career = None
        self.u_cohort_season = {}
        self.u_cohort_career = {}
        self.u_no_all_star = None
        self.u_no_all_nba = None
        self.u_no_all_star_todate = None
        self.u_no_all_nba_todate = None
        self.u_cap_pct_all = None
        self.u_cap_pct_season = {}
        self.u_agent = {}
        self.season_salaries = {}
        self.team_season_salaries = {}

    # -- lookups -----------------------------------------------------------

    def record(self, player, season):
        return self.by_key.get((player, season))

    def is_contracted(self, season):
        return season_key(season) > self.current_key

    def career_complete(self, player):
        """A career is complete when the player has no current or later season."""
        return player not in self.active_players

    def career_eligible(self, player):
        """Eligible for career-level rankings: full career inside the window and
        not a merged identity."""
        return player not in self.truncated and player not in self.identity_suspect


def team_amounts(record):
    """Per-team amounts for a season record.

    A mid-season move carries team_salaries; everything else is the single team
    with the full salary. Returns an ordered list of (team_code, amount).
    """
    splits = record.get("team_salaries") or {}
    if len(splits) > 1:
        return sorted(splits.items(), key=lambda kv: (-kv[1], kv[0]))
    teams = [t.strip() for t in str(record.get("team") or "").split(",") if t.strip()]
    if len(teams) == 1:
        return [(teams[0], record.get("salary") or 0)]
    if len(splits) == 1:
        return list(splits.items())
    # Several teams listed with no split available: nothing can be attributed.
    return []


def load_franchises(path=None):
    """Load data/franchises.json, searching upward from this file."""
    if path is None:
        here = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(os.path.dirname(here), FRANCHISES_PATH)
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)["franchises"]


def compute_current_season(data):
    """The newest fully-rostered season, derived exactly as the front end does.

    js/app.js computeDefaultSeason(): walk seasons_list newest-first and take the
    first season carrying at least 15 paid players per team. Never hardcoded, so
    the engine rolls forward on its own as the data does.
    """
    seasons = data.get("seasons_list") or []
    team_count = len(data.get("teams") or []) or 30
    min_rostered = team_count * 15
    counts = defaultdict(int)
    for rec in data.get("seasons") or []:
        if rec.get("salary"):
            counts[rec["season"]] += 1
    for season in seasons:
        if counts.get(season, 0) >= min_rostered:
            return season
    for season in seasons:
        if counts.get(season, 0):
            return season
    return seasons[0] if seasons else ""


def build_index(data, franchises=None):
    """Build the whole comparison structure once. This is the expensive call."""
    idx = FactoidIndex()
    idx.records = list(data.get("seasons") or [])
    idx.cap = data.get("salary_cap") or {}
    idx.franchises = franchises if franchises is not None else load_franchises()
    idx.current_season = compute_current_season(data)
    idx.current_key = season_key(idx.current_season)

    for rec in idx.records:
        idx.by_key[(rec["player"], rec["season"])] = rec
        idx.by_player[rec["player"]].append(rec)
    for player, recs in idx.by_player.items():
        recs.sort(key=lambda r: season_key(r["season"]))
    idx.seasons = sorted({r["season"] for r in idx.records}, key=season_key)

    _flag_players(idx)
    _index_awards(idx)
    _index_agents(idx)
    _build_universes(idx)
    return idx


def _flag_players(idx):
    """Truncated careers, merged identities and corrupted draft metadata.

    Audit (c) named two truncation rules. The second one ("first data season is
    1990-91 with years_exp > 0") can never fire here: years_exp in data.json
    counts seasons since the player's first season *in this file*, so every
    1990-91 record carries years_exp 0, Magic Johnson's included. The rule is
    replaced by one that works on this data: a career that opens in 1990-91
    without a matching 1990 draft year is treated as already under way.
    """
    for player, recs in idx.by_player.items():
        first = recs[0]
        first_start = int(str(first["season"]).split("-")[0])
        draft_years = {r.get("draft_year") for r in recs if r.get("draft_year")}
        draft_year = min(draft_years) if draft_years else None

        if draft_year is not None and draft_year < FIRST_DRAFT_YEAR_IN_WINDOW:
            idx.truncated.add(player)
        elif first["season"] == SCOPE_FIRST_SEASON and draft_year != FIRST_DRAFT_YEAR_IN_WINDOW:
            # Includes undrafted players and the Sr/Jr draft-metadata collisions.
            idx.truncated.add(player)

        # Draft metadata that postdates the player's debut belongs to someone
        # else (Glen Rice carrying Glen Rice Jr's 2013, and nine more).
        if draft_year is not None and draft_year > first_start:
            idx.draft_meta_suspect.add(player)

        # A long gap means two careers merged under one name.
        keys = [season_key(r["season"]) for r in recs]
        for i in range(1, len(keys)):
            if keys[i] - keys[i - 1] > MAX_CAREER_GAP_SEASONS:
                idx.identity_suspect.add(player)
                break

        if season_key(recs[-1]["season"]) >= idx.current_key:
            idx.active_players.add(player)


def _index_awards(idx):
    """Audit (b): selection counts per season, and which seasons are unsafe.

    A season whose All-Star count falls outside the plausibility band has a
    selection list we cannot trust, so it cannot be used to prove that someone
    was *never* selected. Seasons from the current one onward have no awards at
    all yet, which is expected rather than broken: they are simply unknown.
    """
    all_star = defaultdict(set)
    all_nba = defaultdict(set)
    for rec in idx.records:
        awards = set(rec.get("awards") or ())
        if awards & ALL_STAR_AWARDS:
            all_star[rec["season"]].add(rec["player"])
            idx.all_star_players.add(rec["player"])
        if awards & ALL_NBA_AWARDS:
            all_nba[rec["season"]].add(rec["player"])
            idx.all_nba_players.add(rec["player"])

    played = [s for s in idx.seasons if season_key(s) < idx.current_key]
    idx.awards_known_through = played[-1] if played else ""
    for season in idx.seasons:
        idx.all_star_counts[season] = len(all_star.get(season, ()))
        idx.all_nba_counts[season] = len(all_nba.get(season, ()))
        if season_key(season) >= idx.current_key:
            continue  # unknown, not implausible
        n_as = idx.all_star_counts[season]
        n_nba = idx.all_nba_counts[season]
        if not (ALL_STAR_COUNT_MIN <= n_as <= ALL_STAR_COUNT_MAX):
            idx.awards_unsafe_seasons.add(season)
        elif n_nba != ALL_NBA_COUNT_EXPECTED:
            idx.awards_unsafe_seasons.add(season)


def _index_agents(idx):
    """Audit (a): agent coverage per season.

    The field does vary across a player's career, but the variation is not
    trustworthy as history: 108 players revisit an earlier agent, 22 of those as
    a single-season blip sandwiched by the same name, and coverage is under half
    before 2007-08. So the family falls back to what the data does support, a
    current-clients claim on the current and contracted seasons, where the agent
    is stable per player.
    """
    totals = defaultdict(int)
    with_agent = defaultdict(int)
    for rec in idx.records:
        totals[rec["season"]] += 1
        if rec.get("agent"):
            with_agent[rec["season"]] += 1
    for season in idx.seasons:
        idx.agent_coverage[season] = (with_agent[season], totals[season])
        if season_key(season) >= idx.current_key:
            idx.agent_seasons_safe.add(season)


def _cohorts_for(record, idx):
    """The cohort keys a record belongs to, as (kind, key, phrase) triples.

    The phrase carries its own preposition so the sentence reads correctly for
    every cohort: "in the 2014 draft class", "by a No. 41 pick", "among players
    out of Kentucky".
    """
    out = []
    player = record["player"]

    if player not in idx.draft_meta_suspect:
        draft_year = record.get("draft_year")
        if draft_year:
            out.append(("draft_class", str(draft_year), "in the {} draft class".format(draft_year)))
        pick = record.get("draft_pick")
        if pick and 1 <= pick <= MAX_DRAFT_SLOT:
            out.append(("draft_slot", str(pick), "by a No. {} pick".format(pick)))
        elif pick is None and draft_year is None:
            out.append(("draft_slot", "undrafted", "by an undrafted player"))

    college = (record.get("college") or "").strip()
    if college:
        out.append(("college", college, "among players out of " + college))

    nationality = (record.get("nationality") or "").strip()
    if nationality:
        article = "the " if nationality in NATIONALITY_TAKES_THE else ""
        out.append(("nationality", nationality, "by a player from " + article + nationality))

    pos = (record.get("pos") or "").strip()
    noun = POSITION_NOUNS.get(pos)
    if noun:
        article = "an" if noun[0] in "aeiou" else "a"
        out.append(("position", pos, "by {} {}".format(article, noun)))

    return out


COHORT_MINIMUMS = {
    "draft_class": DRAFT_CLASS_MIN_PLAYERS,
    "draft_slot": DRAFT_SLOT_MIN_PLAYERS,
    "college": COLLEGE_MIN_PLAYERS,
    "nationality": NATIONALITY_MIN_PLAYERS,
    "position": POSITION_MIN_PLAYERS,
}


def _build_universes(idx):
    """Precompute every comparison set."""
    franchise_entries = defaultdict(list)
    cohort_season = defaultdict(list)
    cap_all = []
    cap_by_season = defaultdict(list)
    agent_entries = defaultdict(list)
    season_salaries = defaultdict(list)
    team_season = defaultdict(list)

    for rec in idx.records:
        player, season = rec["player"], rec["season"]
        key = (player, season)
        salary = rec.get("salary") or 0
        season_salaries[season].append((salary, player))

        # Records to beat are records that have been paid. A contracted season
        # is money on a signed deal, so it never stands as the mark another
        # season has to clear; it can only be the subject of a "would be" claim.
        paid = not idx.is_contracted(season)

        for code, amount in team_amounts(rec):
            team_season[(season, code)].append((amount, player))
            if paid and code in idx.franchises:
                franchise_entries[code].append(
                    {"value": amount, "player": player, "season": season, "key": key}
                )

        if paid:
            for kind, ckey, _label in _cohorts_for(rec, idx):
                cohort_season[(kind, ckey)].append(
                    {"value": salary, "player": player, "season": season, "key": key}
                )

        pct = rec.get("salary_cap_pct")
        if pct is not None:
            pct = round(pct, CAP_PCT_DECIMALS)
            if paid:
                cap_all.append(
                    {"value": pct, "player": player, "season": season, "key": key}
                )
            # Within-season ranking still needs every row of that same season,
            # contracted or not: they are all the same kind of money.
            cap_by_season[season].append(
                {"value": pct, "player": player, "season": season, "key": key}
            )

        agent = rec.get("agent")
        if agent and season in idx.agent_seasons_safe:
            agent_entries[(agent, season)].append(
                {"value": salary, "player": player, "season": season, "key": key}
            )

    idx.u_franchise = {k: Universe(v) for k, v in franchise_entries.items()}
    idx.u_cohort_season = {k: Universe(v) for k, v in cohort_season.items()}
    idx.u_cap_pct_all = Universe(cap_all)
    idx.u_cap_pct_season = {k: Universe(v) for k, v in cap_by_season.items()}
    idx.u_agent = {k: Universe(v) for k, v in agent_entries.items()}
    idx.season_salaries = {
        s: sorted(v, key=lambda t: -t[0]) for s, v in season_salaries.items()
    }
    idx.team_season_salaries = {
        k: sorted(v, key=lambda t: -t[0]) for k, v in team_season.items()
    }

    # Career earnings: one entry per eligible player, at his final season.
    career_entries = []
    cohort_career = defaultdict(list)
    for player, recs in idx.by_player.items():
        if not idx.career_eligible(player):
            continue
        if not idx.career_complete(player):
            continue
        last = recs[-1]
        total = last.get("career_earnings")
        if total is None:
            continue
        entry = {
            "value": total,
            "player": player,
            "season": last["season"],
            "key": (player, None),
        }
        career_entries.append(entry)
        for kind, ckey, _label in _cohorts_for(last, idx):
            cohort_career[(kind, ckey)].append(dict(entry))
    idx.u_career = Universe(career_entries)
    idx.u_cohort_career = {k: Universe(v) for k, v in cohort_career.items()}

    # Negative space: best paid season by a player with no selection on record,
    # restricted to players whose whole career sits in seasons whose selection
    # lists survived the audit.
    #
    # Two universes, because the two claims are different claims. "Never made an
    # All-Star team" is only sayable about a finished career, so it is ranked
    # against finished careers. "Without an All-Star selection" is a statement
    # about selections to date, so it has to be ranked against everyone to date,
    # active players included: ranking an active player against retired players
    # alone would call him the highest-paid non-All-Star while an active player
    # earning more sat outside the comparison.
    no_as_retired, no_nba_retired = [], []
    no_as_todate, no_nba_todate = [], []
    for player, recs in idx.by_player.items():
        if not idx.career_eligible(player):
            continue
        if any(r["season"] in idx.awards_unsafe_seasons for r in recs):
            continue
        paid_recs = [r for r in recs if not idx.is_contracted(r["season"])]
        if not paid_recs:
            continue
        best = max(paid_recs, key=lambda r: (r.get("salary") or 0, r["season"]))
        entry = {
            "value": best.get("salary") or 0,
            "player": player,
            "season": best["season"],
            "key": (player, None),
        }
        complete = idx.career_complete(player)
        if player not in idx.all_star_players:
            no_as_todate.append(dict(entry))
            if complete:
                no_as_retired.append(dict(entry))
        if player not in idx.all_nba_players:
            no_nba_todate.append(dict(entry))
            if complete:
                no_nba_retired.append(dict(entry))
    idx.u_no_all_star = Universe(no_as_retired)
    idx.u_no_all_nba = Universe(no_nba_retired)
    idx.u_no_all_star_todate = Universe(no_as_todate)
    idx.u_no_all_nba_todate = Universe(no_nba_todate)


# --------------------------------------------------------------------------
# Factoid assembly
# --------------------------------------------------------------------------


def _make(
    family,
    kind,
    claim_key,
    text,
    scope_note,
    value,
    contracted,
    rank=None,
    comparison_size=None,
    previous_holder=None,
    margin=None,
):
    """Assemble one factoid dict.

    ``id`` hashes the claim's identity, never its value, so a claim that
    persists across daily runs keeps its id and part two's diff can compare
    values instead of seeing a new factoid every time a number moves.
    """
    return {
        "id": hashlib.sha1(claim_key.encode("utf-8")).hexdigest()[:16],
        "key": claim_key,
        "family": family,
        "type": kind,
        "text": text,
        "scope_note": scope_note,
        "value": value,
        "rank": rank,
        "comparison_size": comparison_size,
        "previous_holder": previous_holder,
        "margin": margin,
        "contracted": bool(contracted),
    }


def _holder(entry):
    if entry is None:
        return None
    return {
        "player": entry["player"],
        "season": entry["season"],
        "value": entry["value"],
    }


def _behind_clause(entry, money=True, subject=None):
    """"Stephen Curry's $62.6 million (2026-27)", or "his own ..." for the
    subject himself, which otherwise reads as a comparison with a stranger."""
    value = fmt_money(entry["value"]) if money else "{:.1f}%".format(entry["value"])
    if subject is not None and entry["player"] == subject:
        return "his own {} ({})".format(value, entry["season"])
    return "{} {} ({})".format(_possessive(entry["player"]), value, entry["season"])


class _Log:
    """Collects the gate that dropped each candidate, for --debug."""

    def __init__(self, enabled):
        self.enabled = enabled
        self.items = []

    def drop(self, family, candidate, gate, detail=""):
        if self.enabled:
            self.items.append(
                {"family": family, "candidate": candidate, "gate": gate, "detail": detail}
            )


# -- family 1: franchise ---------------------------------------------------


def _family_franchise(ctx, out, log):
    idx, player, season = ctx["index"], ctx["player"], ctx["season"]
    record, contracted = ctx["record"], ctx["contracted"]

    if record is None:
        log.drop("franchise", season, "no_record", "no season record to read a team from")
        return
    amounts = team_amounts(record)
    if ctx["hypothetical"]:
        if len(amounts) > 1:
            log.drop(
                "franchise",
                season,
                "hypothetical_split",
                "a hypothetical salary cannot be apportioned across a mid-season move",
            )
            return
        if len(amounts) == 1:
            amounts = [(amounts[0][0], ctx["salary"])]
    if not amounts:
        log.drop("franchise", season, "no_team_attribution", "team list with no per-team split")
        return

    for code, amount in amounts:
        franchise = idx.franchises.get(code)
        if franchise is None:
            log.drop("franchise", code, "unmapped_team_code", "not in data/franchises.json")
            continue
        universe = idx.u_franchise.get(code)
        if universe is None:
            log.drop("franchise", code, "empty_universe")
            continue
        verdict = universe.evaluate(amount, (player, season))
        kind = classify(amount, verdict)
        if kind is None:
            log.drop("franchise", code, "not_notable", "outside the top {}".format(APPROACH_MAX_RANK))
            continue

        top = verdict["top"]
        name = franchise["name"]
        split_note = " for his {} salary alone".format(code) if len(amounts) > 1 else ""
        scope = PAID_ONLY_NOTE.format(idx.current_season)
        eras = [e["name"] for e in franchise.get("eras", [])]
        if len(eras) > 1:
            scope += " {} franchise history here includes the {} seasons.".format(
                name, " and ".join(sorted({e for e in eras[:-1]}))
            )
        if len(amounts) > 1:
            scope += " Mid-season move: only the {} share of the salary is counted.".format(code)
        if contracted:
            scope += " " + CONTRACTED_NOTE

        if kind == "sets":
            if top["player"] == player:
                text = (
                    "{} {} in {}{} {} the highest single-season salary in {} "
                    "history {}, breaking his own mark of {} in {}.".format(
                        _possessive(player), fmt_money(amount), season, split_note,
                        _is_verb(contracted), name, SCOPE_SUFFIX,
                        fmt_money(top["value"]), top["season"],
                    )
                )
            else:
                text = (
                    "{} {} in {}{} {} the highest single-season salary in {} "
                    "history {}, passing {}.".format(
                        _possessive(player), fmt_money(amount), season, split_note,
                        _is_verb(contracted), name, SCOPE_SUFFIX, _behind_clause(top, subject=player),
                    )
                )
        elif kind == "ties":
            text = (
                "{} {} in {}{} {} the highest single-season salary in {} "
                "history {}, matching {}.".format(
                    _possessive(player), fmt_money(amount), season, split_note,
                    _tie_verb(contracted), name, SCOPE_SUFFIX, _behind_clause(top, subject=player),
                )
            )
        else:
            text = (
                "{} {} in {}{} {} the {}-highest single-season salary in {} "
                "history {}, behind {}.".format(
                    _possessive(player), fmt_money(amount), season, split_note,
                    _is_verb(contracted), ordinal(verdict["rank"]), name,
                    SCOPE_SUFFIX, _behind_clause(top, subject=player),
                )
            )

        out.append(
            _make(
                "franchise", kind,
                "franchise|{}|{}|{}".format(code, player, season),
                text, scope, amount, contracted,
                rank=verdict["rank"], comparison_size=verdict["size"],
                previous_holder=_holder(top), margin=amount - top["value"],
            )
        )


# -- family 2: career earnings --------------------------------------------


def _family_career(ctx, out, log):
    idx, player, season = ctx["index"], ctx["player"], ctx["season"]
    contracted = ctx["contracted"]

    if not idx.career_eligible(player):
        reason = "truncated_career" if player in idx.truncated else "merged_identity"
        log.drop("career_earnings", player, reason)
        return

    career_total = ctx["career_total"]
    if career_total is None:
        log.drop("career_earnings", season, "no_career_total")
        return
    prior_total = ctx["prior_career_total"]

    # Milestone crossings.
    for milestone in CAREER_MILESTONES:
        if career_total < milestone:
            continue
        if prior_total is not None and prior_total >= milestone:
            continue
        label = fmt_money(milestone)
        universe = idx.u_career
        verdict = universe.evaluate(milestone, (player, None))
        reached = sum(1 for e in universe.entries if e["value"] >= milestone and e["player"] != player)
        if contracted:
            text = (
                "{} is on track to pass {} in career earnings in {} "
                "if his contract is paid in full.".format(player, label, season)
            )
        else:
            text = "{} passed {} in career earnings in {}.".format(player, label, season)
        scope = (
            "Career earnings are nominal dollars {}. Completed careers that "
            "began before 1990-91 are excluded.".format(SCOPE_SUFFIX)
        )
        if reached:
            scope += " {} completed career{} had reached it.".format(
                reached, "s" if reached != 1 else ""
            )
        out.append(
            _make(
                "career_earnings", "milestone",
                "career_milestone|{}|{}|{:.0f}".format(player, season, milestone),
                text, scope, career_total, contracted,
                comparison_size=len(universe),
                margin=career_total - milestone,
            )
        )

    # Rank against completed careers, for a completed career only.
    #
    # An active player's running total ranked against a retired-only field
    # produces claims that read as false even when the scope note is correct:
    # "Jokic's $364 million is the most in the 2014 draft class" is true against
    # completed careers and obviously wrong to a reader who knows Embiid is
    # still playing. Milestones above stay open to active players, because
    # "passed $300 million in career earnings" carries no comparison at all.
    if not idx.career_complete(player):
        log.drop(
            "career_earnings", player, "career_incomplete",
            "career-earnings rank is only claimed once a career is complete",
        )
        return

    verdict = idx.u_career.evaluate(career_total, (player, None))
    kind = classify(career_total, verdict)
    if kind is None:
        log.drop("career_earnings", player, "not_notable", "outside the career-earnings top {}".format(APPROACH_MAX_RANK))
        return
    top = verdict["top"]
    scope = (
        "Ranked against completed careers {}, in nominal dollars. Careers that "
        "began before 1990-91, names that merge two players and players who are "
        "still active are all excluded.".format(SCOPE_SUFFIX)
    )
    through = "through {}".format(season)
    if kind == "sets":
        text = (
            "{} {} in career earnings {} is more than any other completed career "
            "{}, passing {}.".format(
                _possessive(player), fmt_money(career_total), through,
                SCOPE_SUFFIX, _behind_clause(top, subject=player),
            )
        )
    elif kind == "ties":
        text = (
            "{} {} in career earnings {} ties the most by any completed career "
            "{}, matching {}.".format(
                _possessive(player), fmt_money(career_total), through,
                SCOPE_SUFFIX, _behind_clause(top, subject=player),
            )
        )
    else:
        text = (
            "{} {} in career earnings {} ranks {} among completed careers {}, "
            "behind {}.".format(
                _possessive(player), fmt_money(career_total), through,
                ordinal(verdict["rank"]), SCOPE_SUFFIX, _behind_clause(top, subject=player),
            )
        )
    out.append(
        _make(
            "career_earnings", kind,
            "career_rank|{}|{}".format(player, season),
            text, scope, career_total, contracted,
            rank=verdict["rank"], comparison_size=verdict["size"],
            previous_holder=_holder(top), margin=career_total - top["value"],
        )
    )


# -- family 3: cohorts -----------------------------------------------------


def _family_cohorts(ctx, out, log):
    idx, player, season = ctx["index"], ctx["player"], ctx["season"]
    contracted, salary = ctx["contracted"], ctx["salary"]
    record = ctx.get("identity")
    if record is None:
        log.drop("cohort", season, "no_record")
        return

    for kind_name, ckey, label in _cohorts_for(record, idx):
        minimum = COHORT_MINIMUMS[kind_name]

        universe = idx.u_cohort_season.get((kind_name, ckey))
        if universe is None:
            log.drop("cohort", "{}:{}".format(kind_name, ckey), "empty_universe")
            continue
        if universe.distinct_players() < minimum:
            log.drop(
                "cohort", "{}:{}".format(kind_name, ckey), "cohort_below_threshold",
                "{} distinct players, needs {}".format(universe.distinct_players(), minimum),
            )
            continue

        verdict = universe.evaluate(salary, (player, season))
        verdict_kind = classify(salary, verdict)
        if verdict_kind is not None:
            top = verdict["top"]
            scope = "Cohort of {} players and {} player seasons {}. {}".format(
                universe.distinct_players(), len(universe), SCOPE_SUFFIX,
                PAID_ONLY_NOTE.format(idx.current_season),
            )
            if contracted:
                scope += " " + CONTRACTED_NOTE
            if verdict_kind == "sets":
                if top["player"] == player:
                    text = "{} {} in {} {} the highest single-season salary {} {}, breaking his own mark of {} in {}.".format(
                        _possessive(player), fmt_money(salary), season,
                        _is_verb(contracted), label, SCOPE_SUFFIX,
                        fmt_money(top["value"]), top["season"],
                    )
                else:
                    text = "{} {} in {} {} the highest single-season salary {} {}, passing {}.".format(
                        _possessive(player), fmt_money(salary), season,
                        _is_verb(contracted), label, SCOPE_SUFFIX, _behind_clause(top, subject=player),
                    )
            elif verdict_kind == "ties":
                text = "{} {} in {} {} the highest single-season salary {} {}, matching {}.".format(
                    _possessive(player), fmt_money(salary), season,
                    _tie_verb(contracted), label, SCOPE_SUFFIX, _behind_clause(top, subject=player),
                )
            else:
                text = "{} {} in {} {} the {}-highest single-season salary {} {}, behind {}.".format(
                    _possessive(player), fmt_money(salary), season, _is_verb(contracted),
                    ordinal(verdict["rank"]), label, SCOPE_SUFFIX, _behind_clause(top, subject=player),
                )
            out.append(
                _make(
                    "cohort", verdict_kind,
                    "cohort_season|{}|{}|{}|{}".format(kind_name, ckey, player, season),
                    text, scope, salary, contracted,
                    rank=verdict["rank"], comparison_size=verdict["size"],
                    previous_holder=_holder(top), margin=salary - top["value"],
                )
            )

        # Career earnings inside the same cohort, completed careers only, for
        # the same reason the all-time career rank is gated that way.
        if not idx.career_eligible(player):
            continue
        if not idx.career_complete(player):
            log.drop(
                "cohort", "{}:{}".format(kind_name, ckey), "career_incomplete",
                "cohort career earnings are only claimed once a career is complete",
            )
            continue
        career_total = ctx["career_total"]
        if career_total is None:
            continue
        cu = idx.u_cohort_career.get((kind_name, ckey))
        if cu is None or len(cu) == 0:
            log.drop("cohort", "{}:{}".format(kind_name, ckey), "no_completed_careers")
            continue
        if cu.distinct_players() < minimum:
            log.drop(
                "cohort", "{}:{}".format(kind_name, ckey), "cohort_below_threshold",
                "{} completed careers, needs {}".format(cu.distinct_players(), minimum),
            )
            continue
        cverdict = cu.evaluate(career_total, (player, None))
        ckind = classify(career_total, cverdict)
        if ckind is None:
            continue
        ctop = cverdict["top"]
        scope = "Ranked against {} completed careers in this cohort {}.".format(
            cu.distinct_players(), SCOPE_SUFFIX
        )
        if ckind == "sets":
            text = "{} {} in career earnings through {} is the most {} {}, passing {}.".format(
                _possessive(player), fmt_money(career_total), season, label,
                SCOPE_SUFFIX, _behind_clause(ctop, subject=player),
            )
        elif ckind == "ties":
            text = "{} {} in career earnings through {} ties the most {} {}, matching {}.".format(
                _possessive(player), fmt_money(career_total), season, label,
                SCOPE_SUFFIX, _behind_clause(ctop, subject=player),
            )
        else:
            text = "{} {} in career earnings through {} ranks {} {} {}, behind {}.".format(
                _possessive(player), fmt_money(career_total), season,
                ordinal(cverdict["rank"]), label, SCOPE_SUFFIX, _behind_clause(ctop, subject=player),
            )
        out.append(
            _make(
                "cohort", ckind,
                "cohort_career|{}|{}|{}|{}".format(kind_name, ckey, player, season),
                text, scope, career_total, contracted,
                rank=cverdict["rank"], comparison_size=cverdict["size"],
                previous_holder=_holder(ctop), margin=career_total - ctop["value"],
            )
        )


# -- family 4: negative space ---------------------------------------------


def _family_negative_space(ctx, out, log):
    idx, player, season = ctx["index"], ctx["player"], ctx["season"]
    contracted, salary = ctx["contracted"], ctx["salary"]
    recs = idx.by_player.get(player) or []

    if not idx.career_eligible(player):
        reason = "truncated_career" if player in idx.truncated else "merged_identity"
        log.drop("negative_space", player, reason)
        return
    unsafe = [r["season"] for r in recs if r["season"] in idx.awards_unsafe_seasons]
    if unsafe:
        log.drop(
            "negative_space", player, "awards_season_unsafe",
            "played in {}, whose selection list failed the audit".format(", ".join(sorted(set(unsafe)))),
        )
        return

    active = not idx.career_complete(player)
    # An active subject is ranked against everyone's record to date; a finished
    # career is ranked against finished careers. See _build_universes.
    for label, holders, retired_u, todate_u, family_key in (
        ("All-Star", idx.all_star_players, idx.u_no_all_star, idx.u_no_all_star_todate, "no_all_star"),
        ("All-NBA", idx.all_nba_players, idx.u_no_all_nba, idx.u_no_all_nba_todate, "no_all_nba"),
    ):
        universe = todate_u if active else retired_u
        if player in holders:
            log.drop("negative_space", player, "has_selection", "has an {} selection".format(label))
            continue
        verdict = universe.evaluate(salary, (player, None))
        kind = classify(salary, verdict)
        if kind is None:
            continue
        top = verdict["top"]
        if active:
            scope = (
                "Comparison set is every player {} with no {} selection on "
                "record, active or retired, taken at each one's best paid "
                "season.".format(SCOPE_SUFFIX, label)
            )
        else:
            scope = (
                "Comparison set is completed careers {} with no {} selection on "
                "record.".format(SCOPE_SUFFIX, label)
            )
        scope += (
            " Careers that began before 1990-91, names that merge two players "
            "and seasons whose selection lists failed the audit are excluded."
        )
        scope += " " + PAID_ONLY_NOTE.format(idx.current_season)
        if active:
            scope += " {} is still active, so this describes selections to date.".format(player)
        if contracted:
            scope += " " + CONTRACTED_NOTE

        team_noun = "All-Star team" if label == "All-Star" else "All-NBA team"
        if active:
            subject = "for a player without an {} selection".format(label)
        else:
            subject = "by a player who never made an {}".format(team_noun)

        if kind == "sets":
            text = "{} {} in {} {} the highest single-season salary {} {}, passing {}.".format(
                _possessive(player), fmt_money(salary), season, _is_verb(contracted),
                SCOPE_SUFFIX, subject, _behind_clause(top, subject=player),
            )
        elif kind == "ties":
            text = "{} {} in {} {} the highest single-season salary {} {}, matching {}.".format(
                _possessive(player), fmt_money(salary), season, _tie_verb(contracted),
                SCOPE_SUFFIX, subject, _behind_clause(top, subject=player),
            )
        else:
            text = "{} {} in {} {} the {}-highest single-season salary {} {}, behind {}.".format(
                _possessive(player), fmt_money(salary), season, _is_verb(contracted),
                ordinal(verdict["rank"]), SCOPE_SUFFIX, subject, _behind_clause(top, subject=player),
            )
        out.append(
            _make(
                "negative_space", kind,
                "{}|{}|{}".format(family_key, player, season),
                text, scope, salary, contracted,
                rank=verdict["rank"], comparison_size=verdict["size"],
                previous_holder=_holder(top), margin=salary - top["value"],
            )
        )


# -- family 5: cap context -------------------------------------------------


def _family_cap(ctx, out, log):
    idx, player, season = ctx["index"], ctx["player"], ctx["season"]
    contracted, salary = ctx["contracted"], ctx["salary"]

    cap_entry = idx.cap.get(season) or {}
    cap_value = cap_entry.get("cap") if isinstance(cap_entry, dict) else cap_entry
    if not cap_value:
        log.drop("cap", season, "missing_cap", "no salary cap on file for {}".format(season))
        return

    if ctx["hypothetical"] or ctx["record"] is None:
        pct = salary / float(cap_value) * 100.0
    else:
        pct = ctx["record"].get("salary_cap_pct")
        if pct is None:
            pct = salary / float(cap_value) * 100.0
    pct = round(pct, CAP_PCT_DECIMALS)

    for scope_name, universe, family_key in (
        ("all", idx.u_cap_pct_all, "cap_pct_all"),
        (season, idx.u_cap_pct_season.get(season), "cap_pct_season"),
    ):
        if universe is None:
            continue
        verdict = universe.evaluate(pct, (player, season))
        kind = classify(pct, verdict)
        if kind is None:
            continue
        top = verdict["top"]
        share = "{:.1f}%".format(pct)
        if scope_name == "all":
            scope = "Share of that season's salary cap, ranked across every season {}. {}".format(
                SCOPE_SUFFIX, PAID_ONLY_NOTE.format(idx.current_season)
            )
            where = "the largest share of a salary cap {}".format(SCOPE_SUFFIX)
            where_n = "the {}-largest share of a salary cap {}".format(ordinal(verdict["rank"]), SCOPE_SUFFIX)
        else:
            scope = "Share of the {} salary cap, ranked within that season.".format(season)
            where = "the largest share of the cap in {}".format(season)
            where_n = "the {}-largest share of the cap in {}".format(ordinal(verdict["rank"]), season)
        if contracted:
            scope += " Contracted salary against a projected cap. " + CONTRACTED_NOTE

        takes = "would take up" if contracted else "takes up"
        if kind == "sets":
            text = "{} {} salary {} {} of the {} cap, {}, passing {}.".format(
                _possessive(player), fmt_money(salary), takes, share, season, where,
                _behind_clause(top, money=False, subject=player),
            )
        elif kind == "ties":
            text = "{} {} salary {} {} of the {} cap, tying {}, matching {}.".format(
                _possessive(player), fmt_money(salary), takes, share, season, where,
                _behind_clause(top, money=False, subject=player),
            )
        else:
            text = "{} {} salary {} {} of the {} cap, {}, behind {}.".format(
                _possessive(player), fmt_money(salary), takes, share, season, where_n,
                _behind_clause(top, money=False, subject=player),
            )
        out.append(
            _make(
                "cap_context", kind,
                "{}|{}|{}".format(family_key, player, season),
                text, scope, pct, contracted,
                rank=verdict["rank"], comparison_size=verdict["size"],
                previous_holder=_holder(top), margin=round(pct - top["value"], 2),
            )
        )


# -- family 6: agent -------------------------------------------------------


def _family_agent(ctx, out, log):
    idx, player, season = ctx["index"], ctx["player"], ctx["season"]
    contracted, salary, record = ctx["contracted"], ctx["salary"], ctx["record"]

    if season not in idx.agent_seasons_safe:
        log.drop(
            "agent", season, "agent_history_unreliable",
            "the agent field is only trusted for the current and contracted seasons",
        )
        return
    if record is None:
        log.drop("agent", season, "no_record")
        return
    agent = record.get("agent")
    if not agent:
        log.drop("agent", season, "no_agent_on_record")
        return
    universe = idx.u_agent.get((agent, season))
    if universe is None:
        log.drop("agent", agent, "empty_universe")
        return
    clients = universe.distinct_players()
    if clients < AGENT_MIN_CLIENTS:
        log.drop("agent", agent, "too_few_clients", "{} clients, needs {}".format(clients, AGENT_MIN_CLIENTS))
        return

    verdict = universe.evaluate(salary, (player, season))
    kind = classify(salary, verdict)
    if kind is None:
        return
    top = verdict["top"]
    scope = (
        "Agent representation is only reliable for the current and contracted "
        "seasons in this data, so this is a current-clients claim and carries no "
        "history. Cohort of {} clients with a {} salary on file, including him.".format(clients, season)
    )
    if kind == "sets":
        text = "{} {} is the highest {} salary among {} clients, passing {}.".format(
            _possessive(player), fmt_money(salary), season, _possessive(agent),
            _behind_clause(top, subject=player),
        )
    elif kind == "ties":
        text = "{} {} ties the highest {} salary among {} clients, matching {}.".format(
            _possessive(player), fmt_money(salary), season, _possessive(agent),
            _behind_clause(top, subject=player),
        )
    else:
        text = "{} {} is the {}-highest {} salary among {} clients, behind {}.".format(
            _possessive(player), fmt_money(salary), ordinal(verdict["rank"]), season,
            _possessive(agent), _behind_clause(top, subject=player),
        )
    out.append(
        _make(
            "agent", kind,
            "agent|{}|{}|{}".format(agent, player, season),
            text, scope, salary, contracted,
            rank=verdict["rank"], comparison_size=verdict["size"],
            previous_holder=_holder(top), margin=salary - top["value"],
        )
    )


# -- family 7: rank shifts -------------------------------------------------


def _league_rank(idx, season, player, salary):
    """League salary rank for a (possibly hypothetical) salary, self-excluded."""
    rows = idx.season_salaries.get(season)
    if not rows:
        return None, 0
    above = sum(1 for value, other in rows if other != player and value > salary)
    size = sum(1 for _value, other in rows if other != player) + 1
    return above + 1, size


def _team_rank(idx, season, team, player, amount):
    rows = idx.team_season_salaries.get((season, team))
    if not rows:
        return None, 0
    above = sum(1 for value, other in rows if other != player and value > amount)
    size = sum(1 for _value, other in rows if other != player) + 1
    return above + 1, size


def _family_rank_shift(ctx, out, log):
    idx, player, season = ctx["index"], ctx["player"], ctx["season"]
    contracted, salary, record = ctx["contracted"], ctx["salary"], ctx["record"]
    recs = idx.by_player.get(player) or []
    prior = [r for r in recs if season_key(r["season"]) < season_key(season)]

    rank, size = _league_rank(idx, season, player, salary)
    if rank is None:
        log.drop("rank_shift", season, "no_season_rows")
        return

    prior_best = min((r.get("salary_rank_league") or 10 ** 6) for r in prior) if prior else None

    if rank == 1 and (prior_best is None or prior_best > 1):
        out.append(
            _make(
                "rank_shift", "rank_shift",
                "rank_league_first|{}|{}".format(player, season),
                "{} is the highest-paid player in the league in {} for the first "
                "time in his career.".format(player, season),
                "League salary ranks cover {}. First season at No. 1.".format(SCOPE_SUFFIX),
                salary, contracted, rank=rank, comparison_size=size,
            )
        )
    elif rank <= 10 and (prior_best is None or prior_best > 10):
        out.append(
            _make(
                "rank_shift", "rank_shift",
                "rank_league_top10|{}|{}".format(player, season),
                "{} salary in {} puts him in the league's top 10 for the first "
                "time in his career.".format(_possessive(player), season),
                "League salary ranks cover {}. First season inside the top 10.".format(SCOPE_SUFFIX),
                salary, contracted, rank=rank, comparison_size=size,
            )
        )

    # Team high earner, same franchise year over year only.
    if record is None:
        log.drop("rank_shift", season, "no_record", "team rank needs a roster")
        return
    amounts = team_amounts(record)
    if len(amounts) != 1:
        log.drop(
            "rank_shift", season, "mid_season_move",
            "team high-earner shifts need a single team for the season",
        )
        return
    team, amount = amounts[0]
    if ctx["hypothetical"]:
        amount = salary
    if team not in idx.franchises:
        log.drop("rank_shift", team, "unmapped_team_code")
        return
    if not prior:
        log.drop("rank_shift", season, "no_prior_season", "nothing to compare the team rank against")
        return
    previous = prior[-1]
    if season_key(previous["season"]) != season_key(season) - 1:
        log.drop("rank_shift", season, "non_consecutive_seasons")
        return
    prev_teams = team_amounts(previous)
    if len(prev_teams) != 1 or prev_teams[0][0] != team:
        log.drop("rank_shift", season, "franchise_changed", "team high-earner shifts compare the same franchise only")
        return

    now_rank, now_size = _team_rank(idx, season, team, player, amount)
    was_rank = previous.get("salary_rank_team")
    if now_rank is None or was_rank is None:
        return
    name = idx.franchises[team]["name"]
    if now_rank == 1 and was_rank != 1:
        out.append(
            _make(
                "rank_shift", "rank_shift",
                "team_high_becomes|{}|{}".format(player, season),
                "{} is the {} highest-paid player in {} after ranking {} on the "
                "roster in {}.".format(player, _possessive(name), season, ordinal(was_rank) if was_rank > 1 else "first", previous["season"]),
                "Same franchise in consecutive seasons. Roster of {} players with a salary on file.".format(now_size),
                amount, contracted, rank=now_rank, comparison_size=now_size,
            )
        )
    elif now_rank != 1 and was_rank == 1:
        out.append(
            _make(
                "rank_shift", "rank_shift",
                "team_high_ceases|{}|{}".format(player, season),
                "{} is no longer the {} highest-paid player in {} after holding "
                "that spot in {}.".format(player, _possessive(name), season, previous["season"]),
                "Same franchise in consecutive seasons. Roster of {} players with a salary on file.".format(now_size),
                amount, contracted, rank=now_rank, comparison_size=now_size,
            )
        )


_FAMILIES = (
    _family_franchise,
    _family_career,
    _family_cohorts,
    _family_negative_space,
    _family_cap,
    _family_agent,
    _family_rank_shift,
)


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

_INDEX_CACHE = {}


def _cached_index(data):
    cache_key = (id(data), len(data.get("seasons") or ()))
    index = _INDEX_CACHE.get(cache_key)
    if index is None:
        index = build_index(data)
        _INDEX_CACHE.clear()
        _INDEX_CACHE[cache_key] = index
    return index


def factoids_for(data, player, season, salary=None, index=None, debug=False, suppressed=None):
    """Return the factoids a (player, season, salary) triple supports.

    ``salary`` overrides the salary on file, which is what makes the engine
    usable for a hypothetical. Either way the record under evaluation is held
    out of every comparison set, so a figure never beats itself.

    Pass ``index`` (from ``build_index``) to evaluate many records without
    rebuilding the comparison sets. Pass ``suppressed`` a list, or ``debug=True``,
    to collect the gate that dropped each candidate.
    """
    idx = index if index is not None else _cached_index(data)
    log = _Log(debug or suppressed is not None)

    record = idx.record(player, season)
    known_player = player in idx.by_player
    if record is None and salary is None:
        raise KeyError(
            "no record for {!r} in {!r}; pass a salary to evaluate a hypothetical".format(
                player, season
            )
        )
    if not known_player:
        log.drop("all", player, "unknown_player", "not present in data.json")
        if suppressed is not None:
            suppressed.extend(log.items)
        return []

    effective_salary = salary if salary is not None else (record.get("salary") or 0)
    hypothetical = salary is not None and (
        record is None or salary != (record.get("salary") or 0)
    )

    # Career earnings through this season. For a hypothetical, rebuild it from
    # the previous season's total so the figure stays consistent with the input.
    recs = idx.by_player.get(player) or []
    prior = [r for r in recs if season_key(r["season"]) < season_key(season)]
    prior_total = prior[-1].get("career_earnings") if prior else None
    if hypothetical or record is None:
        career_total = (prior_total or 0) + effective_salary
    else:
        career_total = record.get("career_earnings")

    # For a season the player has no record in, cohorts still need his identity
    # fields. Borrow them from his nearest season; team-bound families (franchise,
    # agent, team rank) stay suppressed, because the team is not knowable.
    identity = record
    if identity is None and recs:
        identity = prior[-1] if prior else recs[0]

    ctx = {
        "index": idx,
        "player": player,
        "season": season,
        "salary": effective_salary,
        "record": record,
        "identity": identity,
        "hypothetical": hypothetical,
        "contracted": idx.is_contracted(season),
        "career_total": career_total,
        "prior_career_total": prior_total,
    }

    out = []
    for family in _FAMILIES:
        family(ctx, out, log)

    out.sort(key=lambda f: (f["family"], f["key"]))
    if suppressed is not None:
        suppressed.extend(log.items)
    if debug:
        ctx["_suppressed"] = log.items
        factoids_for.last_suppressed = log.items
    return out


factoids_for.last_suppressed = []


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def load_data(path=None):
    if path is None:
        here = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(os.path.dirname(here), DATA_PATH)
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Factoids a player's season salary supports, real or hypothetical."
    )
    parser.add_argument("--player", required=True, help='Player name, e.g. "Nikola Jokic"')
    parser.add_argument("--season", required=True, help='Season, e.g. 2026-27')
    parser.add_argument("--salary", type=float, default=None, help="Hypothetical salary in dollars")
    parser.add_argument("--debug", action="store_true", help="List suppressed candidates and their gates")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text")
    parser.add_argument("--data", default=None, help="Path to data.json")
    args = parser.parse_args(argv)

    data = load_data(args.data)
    index = build_index(data)
    suppressed = []
    try:
        facts = factoids_for(
            data, args.player, args.season, args.salary,
            index=index, debug=args.debug, suppressed=suppressed,
        )
    except KeyError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(facts, indent=2, sort_keys=True))
    else:
        header = "{} {}".format(args.player, args.season)
        if args.salary is not None:
            header += " at {} (hypothetical)".format(fmt_money(args.salary))
        print(header)
        print("current season: {}".format(index.current_season))
        if not facts:
            print("  no factoids")
        for fact in facts:
            print("\n  [{} / {}] {}".format(fact["family"], fact["type"], fact["text"]))
            print("      scope: {}".format(fact["scope_note"]))
            if fact["rank"]:
                print("      rank {} of {}".format(fact["rank"], fact["comparison_size"]))
            print("      id {}  contracted={}".format(fact["id"], fact["contracted"]))

    if args.debug:
        print("\nsuppressed ({}):".format(len(suppressed)))
        for item in suppressed:
            detail = " - {}".format(item["detail"]) if item["detail"] else ""
            print("  {:16s} {:28s} {}{}".format(item["family"], str(item["candidate"])[:28], item["gate"], detail))
    return 0


if __name__ == "__main__":
    sys.exit(main())
