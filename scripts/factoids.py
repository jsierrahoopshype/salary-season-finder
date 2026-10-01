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

#: The first season on file. No claim says so any more: "since 1990-91" in
#: every sentence read as a hedge on figures that are, for every player anyone
#: is comparing, the whole of his earnings. The caveat lives in the note below,
#: printed once per page, and in the two safeguards that go with it: a career
#: claim is dropped when a pre-window career sits close enough to it to be
#: wrong (PRE_WINDOW_MARGIN), and an all-time cap share is only claimed for the
#: top few, where the missing seasons cannot reach.
SCOPE_FIRST_SEASON = "1990-91"
DATA_START_NOTE = (
    "Salary data starts in 1990-91; earlier salaries were far smaller in "
    "dollar terms."
)

#: A career that began before the window is short by its first seasons. Any
#: career-earnings claim landing within this of one of those totals is dropped
#: rather than printed with a comparison the missing money could overturn.
PRE_WINDOW_MARGIN = 25000000

#: How an all-time claim names its field now that it names no window.
ALL_TIME = "in NBA history"

#: All-time cap share is only claimed this far down. A rank deeper than this
#: could be displaced by a season the data does not have.
CAP_ALL_TIME_MAX_RANK = 3

# --------------------------------------------------------------------------
# Salaries the CBA does not allow.
#
# A supermax is 35% of the cap. A record above that is a data fault, not a
# contract, and one of them at the top of a list makes every claim under it
# wrong: Victor Wembanyama's $112.6 million in 2030-31 is a projection nobody
# can be paid. Two rules catch them, and a caught record is invisible to every
# claim, every ranking and every summary.
# --------------------------------------------------------------------------

#: A contracted salary this many times the second-biggest of its own season is
#: a projection, not a deal. No real season has one salary towering over the
#: whole league by that much.
IMPOSSIBLE_SEASON_LEAD = 1.25

#: A contracted season above this much more than the same player's last season
#: on the same team is a projection, not a signed raise. Inside a deal the CBA
#: allows 8% a year.
IMPOSSIBLE_RAISE = 0.40

#: ...but only where the season it jumps from was already a big salary. A
#: rookie-scale season into a maximum extension is a legal leap, and that is
#: what most of them are: the test is for a big salary becoming an impossible
#: one, so the season underneath has to be big to begin with.
IMPOSSIBLE_RAISE_BASE_CAP_PCT = 25.0

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

#: Agent factoids are off. The agent field is not only unreliable as history
#: (audit (a)); the current values are unverified too, and at least one is
#: plainly wrong: Nikola Jokic's 2026-27 record reads "Mike Lindeman". Naming an
#: agent in published copy on that basis is not a risk worth the factoid. The
#: family, its gates and its tests all stay in place, so flipping this back to
#: True once the field is verified is a one-line change.
AGENT_FACTOIDS_ENABLED = False

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

#: A career with a gap this long is either two players merged under one name or
#: a spell abroad. Either way the career total cannot be read as a career, so
#: the player is excluded from every career-level claim. data/identity_splits.json
#: says which of the two each flagged name is; see load_identity_splits.
MAX_CAREER_GAP_SEASONS = 4

#: Where identity_splits.json lives. Read by the engine only: build_data.py and
#: data.json are untouched by it.
IDENTITY_SPLITS_PATH = os.path.join("data", "identity_splits.json")

#: College values print through data/college_names.json, which spells out the
#: truncations data.json stores: "Michigan St" reads "Michigan State". The raw
#: value stays the matching key everywhere, so cohort keys and factoid ids do
#: not move; only the sentence changes. The prerender script reads the same file.
COLLEGE_NAMES_PATH = os.path.join("data", "college_names.json")

#: Two spellings of one player, merged into one identity. data.json files
#: "Wendell Carter" for 2024-25 and "Wendell Carter Jr" for the rest of the same
#: career, which without this reads as two men: one finished, one starting
#: mid-career with the other's running total already on him. Read by the engine
#: only, and by the prerender script; build_data.py never sees it.
NAME_ALIASES_PATH = os.path.join("data", "name_aliases.json")

#: 1998-99 held no All-Star Game. The lockout cut the season to 50 games and the
#: February game was cancelled, so no selections exist and a count of zero is the
#: correct answer rather than a scrape that came back empty. 1996-97 and 2006-07
#: both carry 29, above the 24-28 band, and stay flagged: injury replacements
#: plausibly explain the extra names, but "plausibly" is not the same as checked
#: against the published rosters, and the cheaper error is losing factoids.
SEASONS_WITHOUT_ALL_STAR_GAME = frozenset({"1998-99"})

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

#: Positions collapse to three groups by the first letter of the pos code, so a
#: "forward-center" is a forward and a "guard-forward" is a guard. Seven raw
#: codes and seven cohorts split the field into slices too thin to rank inside
#: and read as false precision in the text: nobody searches for the
#: highest-paid center-forward. A code whose first letter is not G, F or C gets
#: no position cohort rather than a guessed noun.
POSITION_GROUP_NOUNS = {
    "G": "guard",
    "F": "forward",
    "C": "center",
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
        self.career_total_carried_in = set()
        self.career_incomplete = set()
        self.active_players = set()
        self.recently_active = set()
        self.final_season = {}
        self.paid_career = {}           # player -> (money already paid, season)
        self.impossible = {}            # (player, season) -> why it cannot be real
        # display names
        self.college_names = {}
        self.name_aliases = {}
        # identity splits
        self.identity_splits = {}
        self.split_suppressed = set()   # (player, season) that must not be named
        self.segment_of = {}            # (player, season) -> which person, 0-based
        self.segment_display = {}        # (player, season) -> the name to print, or None
        self.segment_owns_metadata = set()  # (player, season) whose segment matches the key's draft fields
        # awards
        self.all_star_players = set()
        self.all_nba_players = set()
        self.all_star_counts = {}
        self.all_nba_counts = {}
        self.awards_unsafe_seasons = set()
        self.all_star_unsafe_seasons = set()
        self.all_nba_unsafe_seasons = set()
        self.awards_known_through = ""
        self.current_season_in_progress = True
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

    def person_of(self, player, season):
        """Which man a season belongs to, as an opaque identity.

        For all but the confirmed splits this is just the name. For a confirmed
        split it is the name plus the segment, so Jaren Jackson Sr's 1997-98 and
        Jaren Jackson Jr's 2026-27 do not compare as the same man and the engine
        never writes "breaking his own mark" across the two.
        """
        return (player, self.segment_of.get((player, season), 0))

    def display_name(self, player, season):
        """The name to print for a season. The data key unless a confirmed split
        says that segment belongs to someone else."""
        return self.segment_display.get((player, season), player)

    def canonical(self, player):
        """The one spelling this engine files a player under.

        data.json can hold one career under two spellings, so every read of a
        player's name goes through here: comparison sets, career totals, the
        rank-shift history and the printed name all have to agree on who he is.
        """
        return self.name_aliases.get(player, player)

    def college_display(self, college):
        """The college name to print. The raw value is still what matches."""
        return self.college_names.get(college, college)

    def cohorts_allowed(self, player, season):
        """Whether a record's identity fields can be trusted for cohorts.

        The eleven names carrying a son's draft metadata are out by default:
        college, nationality and position come off the same player record as the
        draft fields, so none of them can be trusted on those names.

        A confirmed split is the exception. Once identity_splits.json says which
        seasons belong to which man, the segment whose own draft year, pick and
        college match what the key carries is the segment that metadata
        describes, so that segment gets its cohorts back. Jaren Jackson Jr's
        2018-19 onward seasons are Michigan State, No. 4, 2018; his father's
        1990s seasons under the same key are not, and stay out.
        """
        if player not in self.draft_meta_suspect:
            return True
        return (player, season) in self.segment_owns_metadata

    def career_complete(self, player):
        """Complete when the player has no record in the current season and none
        in the one before it.

        "Absent from the current season" alone is not retirement. The current
        season's rosters fill through the autumn, so in September an unsigned
        free agent who played last season reads as a completed career and gets
        ranked among finished careers and called a player who "never made an
        All-Star team". One clear season of absence is the cheapest rule that
        does not do that.
        """
        return player not in self.recently_active

    def career_status_unknown(self, player):
        """Last season on file is the one before the current season: he may be
        unsigned, he may be finished, and the data cannot tell which."""
        return player in self.recently_active and player not in self.active_players

    def is_final_season(self, player, season):
        """The player's last season on file, where the running career-earnings
        total is the career total rather than a total to date."""
        return self.final_season.get(player) == season

    def career_eligible(self, player):
        """Eligible for claims about a finished career: a career that starts
        inside the window, under one name, with a running total that starts at
        zero."""
        return (
            player not in self.truncated
            and player not in self.identity_suspect
            and player not in self.career_total_carried_in
            and player not in self.career_incomplete
        )

    def career_rankable(self, player):
        """Eligible for a career-earnings ranking.

        Money already paid is money already paid whether or not the man has
        retired, so an active player belongs in the list: leaving him out is
        what made Elton Brand read as the highest-earning Duke player while
        Kyrie Irving was above him. A career that began before 1990-91 belongs
        in it too, at the total this data holds, with PRE_WINDOW_MARGIN
        guarding the claims its missing seasons could overturn.

        What stays out is a total that is not one man's: a name covering two
        players, and a running total that was already running when he arrived.
        """
        return (
            player not in self.identity_suspect
            and player not in self.career_total_carried_in
            and player not in self.career_incomplete
        )

    def paid_through(self, player):
        """(money already paid, the season it runs through), or (None, None).

        The last season that is not contracted: a signed season nobody has been
        paid for cannot be part of what a man has earned.
        """
        return self.paid_career.get(player) or (None, None)

    def is_paid_through(self, player, season):
        """This season is where the player's paid-to-date total is measured."""
        return self.paid_career.get(player, (None, None))[1] == season

    def is_impossible(self, record):
        """This salary cannot be a real contract, so nothing may use it."""
        if not record:
            return False
        return (self.canonical(record["player"]), record["season"]) in self.impossible

    def impossible_reason(self, player, season):
        return self.impossible.get((self.canonical(player), season))

    def pre_window_career(self, player):
        """His career began before the data does, so his total is short."""
        return player in self.truncated


def position_group(pos):
    """Collapse a pos code to (initial, noun): "F-C" -> ("F", "forward").

    Returns (None, None) for an empty code or one that does not start with
    G, F or C.
    """
    code = (pos or "").strip().upper()
    if not code:
        return None, None
    initial = code[0]
    noun = POSITION_GROUP_NOUNS.get(initial)
    if noun is None:
        return None, None
    return initial, noun


def is_split_season(record):
    """True when the record's salary is spread over more than one team.

    team_salaries is a cap-sheet allocation, not money paid while on a roster:
    Russell Westbrook's 2022-23 reads $46.3 million against UTA, a team he never
    played a game for, while the Lakers paid most of that season; DeMar DeRozan's
    2026-27 is split DEN/SAC before a game has been played. Neither share can be
    read as what a franchise paid a player to play for it, so a split record is
    kept out of franchise claims entirely rather than attributed to the wrong
    team. Season, cohort, cap and career claims use the full-season salary and
    are unaffected.
    """
    return len(record.get("team_salaries") or {}) > 1


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


def load_college_names(path=None):
    """Load data/college_names.json. Missing file is not an error: every college
    then prints exactly as data.json stores it."""
    if path is None:
        here = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(os.path.dirname(here), COLLEGE_NAMES_PATH)
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh).get("mapping") or {}


def load_name_aliases(path=None):
    """Load data/name_aliases.json as {alias spelling: canonical spelling}.
    Missing file is not an error: every spelling is then its own player."""
    if path is None:
        here = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(os.path.dirname(here), NAME_ALIASES_PATH)
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh).get("alias_to_canonical") or {}


def load_identity_splits(path=None):
    """Load data/identity_splits.json. Missing file is not an error.

    Additive by design: the engine reads it, build_data.py does not know it
    exists, and data.json is untouched. An empty dict here reproduces the
    behaviour from before the file existed.
    """
    if path is None:
        here = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(os.path.dirname(here), IDENTITY_SPLITS_PATH)
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh).get("entries") or {}


def _segment_matches_metadata(person, record):
    """Does this person's own draft year, pick and college match the record's?

    All three have to agree, and at least one has to be present, so a segment
    with nothing on file never inherits the key's metadata by default.
    """
    pairs = (
        (person.get("draft_year"), record.get("draft_year")),
        (person.get("draft_pick"), record.get("draft_pick")),
        (
            (person.get("college") or "").strip() or None,
            (record.get("college") or "").strip() or None,
        ),
    )
    if not any(mine is not None for mine, _theirs in pairs):
        return False
    return all(mine == theirs for mine, theirs in pairs)


def _index_identity_splits(idx):
    """Work out, per season, which man a merged name's record belongs to.

    A merged key prints one name over two people, and it is usually the earlier
    segment that gets the wrong one: a 1997-98 record filed under "Jaren Jackson
    Jr" is Jaren Jackson Sr.

    Unconfirmed split
        Nobody has checked which seasons belong to whom, so every season in an
        earlier segment is held back, as the subject of a factoid and as the
        previous holder inside someone else's.

    Confirmed split
        The people block is trusted. Each segment prints its own display_name
        and counts as its own man, so "his own mark" never reaches across the
        two. A segment whose display_name is null is a man whose name is not
        known, and it stays held back: the split being real does not make him
        nameable. Career-level claims stay closed for the whole key either way,
        because career_earnings on a merged record sums two men.

    split: false
        One man with a gap in his career. Nothing is suppressed and nothing is
        renamed. A long gap under one name is not proof of one person, so the
        career-level exclusion the gap triggered stays until someone confirms
        the entry; confirming it lifts that exclusion.

    career_incomplete
        One man, confirmed, whose seasons this data does not all hold: Hot Rod
        Williams has his four Cleveland and Phoenix seasons on file and not the
        four Phoenix ones between them. The name is his alone, so his seasons
        rank and his gap is not a second man, but the total under it is not his
        career and no career-level claim may use it, his own or anyone else's
        measured against it. Independent of confirmed and of split.
    """
    for key, entry in (idx.identity_splits or {}).items():
        recs = idx.by_player.get(key)
        if not recs:
            continue
        people = entry.get("people") or []
        confirmed = bool(entry.get("confirmed"))

        if entry.get("career_incomplete"):
            idx.career_incomplete.add(key)

        if not entry.get("split"):
            if confirmed:
                idx.identity_suspect.discard(key)
            continue
        if len(people) < 2:
            continue

        for i, person in enumerate(people):
            first = season_key(person.get("first_season") or "")
            last = season_key(person.get("last_season") or "")
            is_last_segment = i == len(people) - 1
            for rec in recs:
                k = season_key(rec["season"])
                if not (first <= k <= last):
                    continue
                pair = (key, rec["season"])
                idx.segment_of[pair] = i
                if not confirmed:
                    # nothing checked yet: hold back every earlier segment
                    if not is_last_segment:
                        idx.split_suppressed.add(pair)
                    continue
                name = person.get("display_name")
                if not name:
                    # a real second man whose name we do not have
                    idx.split_suppressed.add(pair)
                    continue
                idx.segment_display[pair] = name
                # The key carries one set of draft fields. Whichever segment's
                # own draft year, pick and college match them is the segment
                # that metadata describes, so its identity cohorts are sound.
                if _segment_matches_metadata(person, rec):
                    idx.segment_owns_metadata.add(pair)


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


def build_index(data, franchises=None, identity_splits=None, college_names=None,
                name_aliases=None):
    """Build the whole comparison structure once. This is the expensive call."""
    idx = FactoidIndex()
    idx.records = list(data.get("seasons") or [])
    idx.cap = data.get("salary_cap") or {}
    idx.franchises = franchises if franchises is not None else load_franchises()
    idx.identity_splits = (
        identity_splits if identity_splits is not None else load_identity_splits()
    )
    idx.college_names = (
        college_names if college_names is not None else load_college_names()
    )
    idx.name_aliases = (
        name_aliases if name_aliases is not None else load_name_aliases()
    )
    idx.current_season = compute_current_season(data)
    idx.current_key = season_key(idx.current_season)

    for rec in idx.records:
        name = idx.canonical(rec["player"])
        idx.by_key[(name, rec["season"])] = rec
        if name != rec["player"]:
            # still findable by the spelling data.json actually stores
            idx.by_key[(rec["player"], rec["season"])] = rec
        idx.by_player[name].append(rec)
    for player, recs in idx.by_player.items():
        recs.sort(key=lambda r: season_key(r["season"]))
    idx.seasons = sorted({r["season"] for r in idx.records}, key=season_key)

    _flag_players(idx)
    _flag_impossible_salaries(idx)
    _index_identity_splits(idx)
    _index_awards(idx)
    _index_agents(idx)
    _build_universes(idx)
    return idx


def _flag_impossible_salaries(idx):
    """Contracted records carrying a salary no contract can pay.

    Only contracted seasons are tested. A salary already paid is a fact
    whatever it looks like: the 35% maximum applies to a contract when it is
    signed, and an 8%-a-year raise on a maximum deal outruns the cap, so the
    late years of real contracts sit well above it. Kobe Bryant's 51.9% of the
    2013-14 cap was money he was paid.

    What is tested is a projection that cannot be a deal: a salary towering
    over the whole of its own season, and a jump on one roster that no signed
    raise can carry, from a season that was already a big salary.
    """
    second_best = {}
    for season in idx.seasons:
        salaries = sorted(
            (r.get("salary") or 0 for r in idx.records if r["season"] == season),
            reverse=True,
        )
        if len(salaries) > 1:
            second_best[season] = salaries[1]

    for player, recs in idx.by_player.items():
        previous = None
        for record in recs:
            season = record["season"]
            if not idx.is_contracted(season):
                previous = record
                continue
            salary = record.get("salary") or 0
            reason = None

            runner_up = second_best.get(season)
            if runner_up and salary > runner_up * IMPOSSIBLE_SEASON_LEAD:
                reason = (
                    "{} is {:.2f} times the second-biggest salary of {}, "
                    "{}".format(
                        fmt_money(salary), salary / float(runner_up), season,
                        fmt_money(runner_up))
                )

            if reason is None and previous is not None:
                teams = [c for c, _a in team_amounts(record)]
                prev_teams = [c for c, _a in team_amounts(previous)]
                same_team = (
                    len(teams) == 1 and len(prev_teams) == 1 and teams[0] == prev_teams[0]
                )
                was = previous.get("salary") or 0
                base_share = _cap_share(idx, previous)
                if (
                    same_team and was > 0
                    and salary > was * (1 + IMPOSSIBLE_RAISE)
                    and base_share is not None
                    and base_share > IMPOSSIBLE_RAISE_BASE_CAP_PCT
                ):
                    reason = (
                        "a contracted {:.0f}% jump over {} on the same roster, "
                        "{} to {}, off a season already worth {:.1f}% of the "
                        "cap".format(
                            (salary / float(was) - 1) * 100.0, previous["season"],
                            fmt_money(was), fmt_money(salary), base_share)
                    )

            if reason is not None:
                idx.impossible[(player, season)] = reason
            previous = record


def _cap_share(idx, record):
    """What share of its season's cap a salary is, or None."""
    entry = idx.cap.get(record["season"]) or {}
    cap = entry.get("cap") if isinstance(entry, dict) else entry
    if cap:
        return (record.get("salary") or 0) / float(cap) * 100.0
    return record.get("salary_cap_pct")


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

        # career_earnings is a running total, so a player's first record should
        # read exactly his first salary. Where it reads more, the total was
        # already running under someone else's name when he arrived: Glen Rice
        # Jr's first season carries $67.2 million, which is his father's career.
        # The figure is not his, so no career-level claim can use it, his own or
        # anyone else's measured against it.
        first_total = first.get("career_earnings")
        first_salary = first.get("salary") or 0
        if first_total is not None and first_total > first_salary + 1:
            idx.career_total_carried_in.add(player)

        # A long gap means two careers merged under one name.
        keys = [season_key(r["season"]) for r in recs]
        for i in range(1, len(keys)):
            if keys[i] - keys[i - 1] > MAX_CAREER_GAP_SEASONS:
                idx.identity_suspect.add(player)
                break

        idx.final_season[player] = recs[-1]["season"]
        last_key = season_key(recs[-1]["season"])
        if last_key >= idx.current_key:
            idx.active_players.add(player)
        if last_key >= idx.current_key - 1:
            idx.recently_active.add(player)


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
        name = idx.canonical(rec["player"])
        if awards & ALL_STAR_AWARDS:
            all_star[rec["season"]].add(name)
            idx.all_star_players.add(name)
        if awards & ALL_NBA_AWARDS:
            all_nba[rec["season"]].add(name)
            idx.all_nba_players.add(name)

    played = [s for s in idx.seasons if season_key(s) < idx.current_key]
    idx.awards_known_through = played[-1] if played else ""

    # Whether the current season has finished, read off the data rather than a
    # clock, so a build stays reproducible. Selections are made at the end of a
    # season, so a current season with no All-Star and no All-NBA names on
    # record has not finished yet. It flips on its own the moment the awards
    # land, which is the earliest this file can know.
    idx.current_season_in_progress = not (
        all_star.get(idx.current_season) or all_nba.get(idx.current_season)
    )
    for season in idx.seasons:
        idx.all_star_counts[season] = len(all_star.get(season, ()))
        idx.all_nba_counts[season] = len(all_nba.get(season, ()))
        if season_key(season) >= idx.current_key:
            continue  # unknown, not implausible
        # Flagged per award, not per season. A season can carry a sound
        # All-Star list and a short All-NBA one: 2025-26 has 27 All-Stars,
        # which is in band, and 14 All-NBA selections, which is one short.
        # Condemning the whole season for that would throw away every All-Star
        # negative-space claim from the newest season in the file, when the
        # only thing in doubt is All-NBA.
        n_as = idx.all_star_counts[season]
        n_nba = idx.all_nba_counts[season]
        if season in SEASONS_WITHOUT_ALL_STAR_GAME:
            # No game was played, so no selections exist. Zero is the right
            # answer here and proves nothing about the scrape.
            pass
        elif not (ALL_STAR_COUNT_MIN <= n_as <= ALL_STAR_COUNT_MAX):
            idx.all_star_unsafe_seasons.add(season)
        if n_nba != ALL_NBA_COUNT_EXPECTED:
            idx.all_nba_unsafe_seasons.add(season)
    # Kept as the union so anything reading the old name still sees every
    # season with a problem of either kind.
    idx.awards_unsafe_seasons = idx.all_star_unsafe_seasons | idx.all_nba_unsafe_seasons


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
    player = idx.canonical(record["player"])

    # Eleven names carry draft metadata that postdates their own debut, which
    # means it belongs to a son of the same name: Glen Rice reads draft 2013,
    # pick 35, Georgia Tech, which is Glen Rice Jr. The draft fields are not the
    # only ones that travel together. College, nationality and position come off
    # the same player record, so Glen Rice's college reads Georgia Tech when he
    # went to Michigan, and Gary Payton's reads Oregon St where Gary Payton II's
    # is Oregon St too but for a different man. None of the five identity
    # cohorts can be trusted on these names, so they join none of them, as
    # subjects or as members of anyone else's comparison set.
    if not idx.cohorts_allowed(player, record["season"]):
        return out

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
        # the cohort key stays the raw value; only the sentence is spelled out
        out.append((
            "college", college,
            "among players out of " + idx.college_display(college),
        ))

    nationality = (record.get("nationality") or "").strip()
    if nationality:
        article = "the " if nationality in NATIONALITY_TAKES_THE else ""
        out.append(("nationality", nationality, "by a player from " + article + nationality))

    group, noun = position_group(record.get("pos"))
    if group:
        article = "an" if noun[0] in "aeiou" else "a"
        out.append(("position", group, "by {} {}".format(article, noun)))

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
        player, season = idx.canonical(rec["player"]), rec["season"]
        key = (player, season)
        salary = rec.get("salary") or 0
        season_salaries[season].append((salary, player))

        # A season filed under a merged name whose split is not confirmed yet
        # cannot be named in any sentence, so it never enters a comparison set
        # and can never turn up as a previous holder.
        if key in idx.split_suppressed:
            continue
        # Neither can a salary the CBA does not allow: it is a data fault, and
        # one of them in a comparison set makes every claim under it wrong.
        if key in idx.impossible:
            continue
        display = idx.display_name(player, season)
        person = idx.person_of(player, season)

        # Records to beat are records that have been paid. A contracted season
        # is money on a signed deal, so it never stands as the mark another
        # season has to clear; it can only be the subject of a "would be" claim.
        paid = not idx.is_contracted(season)

        # A split season is cap-sheet allocation, not money a franchise paid a
        # player to play for it, so it is no franchise's record to hold. It stays
        # in the team-rank rows below, which are a within-season ordering rather
        # than a claim about franchise history.
        split = is_split_season(rec)
        for code, amount in team_amounts(rec):
            team_season[(season, code)].append((amount, player))
            if paid and not split and code in idx.franchises:
                franchise_entries[code].append(
                    {"value": amount, "player": player, "season": season, "key": key,
                 "display": display, "person": person}
                )

        if paid:
            for kind, ckey, _label in _cohorts_for(rec, idx):
                cohort_season[(kind, ckey)].append(
                    {"value": salary, "player": player, "season": season, "key": key,
                 "display": display, "person": person}
                )

        pct = rec.get("salary_cap_pct")
        if pct is not None:
            pct = round(pct, CAP_PCT_DECIMALS)
            if paid:
                cap_all.append(
                    {"value": pct, "player": player, "season": season, "key": key,
                 "display": display, "person": person}
                )
            # Within-season ranking still needs every row of that same season,
            # contracted or not: they are all the same kind of money.
            cap_by_season[season].append(
                {"value": pct, "player": player, "season": season, "key": key,
                 "display": display, "person": person}
            )

        agent = rec.get("agent")
        if agent and season in idx.agent_seasons_safe:
            agent_entries[(agent, season)].append(
                {"value": salary, "player": player, "season": season, "key": key,
                 "display": display, "person": person}
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

    # Career earnings: one entry per player, at the money already paid.
    #
    # Everyone who has been paid is in here, active or retired. The figure is
    # the running total through his last season that is not contracted, so a
    # signed deal nobody has been paid for never counts, and an active player
    # is compared on the same footing as a finished one.
    career_entries = []
    cohort_career = defaultdict(list)
    for player, recs in idx.by_player.items():
        paid = [
            r for r in recs
            if not idx.is_contracted(r["season"])
            and (player, r["season"]) not in idx.split_suppressed
            and r.get("career_earnings") is not None
        ]
        if not paid:
            continue
        last = paid[-1]
        idx.paid_career[player] = (last["career_earnings"], last["season"])
        if not idx.career_rankable(player):
            continue
        entry = {
            "value": last["career_earnings"],
            "player": player,
            "season": last["season"],
            "key": (player, None),
            "display": idx.display_name(player, last["season"]),
            "person": idx.person_of(player, last["season"]),
            "complete": idx.career_complete(player),
            "pre_window": idx.pre_window_career(player),
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
        paid_recs = [
            r for r in recs
            if not idx.is_contracted(r["season"])
            and (player, r["season"]) not in idx.split_suppressed
            and (player, r["season"]) not in idx.impossible
        ]
        if not paid_recs:
            continue
        best = max(paid_recs, key=lambda r: (r.get("salary") or 0, r["season"]))
        entry = {
            "value": best.get("salary") or 0,
            "player": player,
            "season": best["season"],
            "key": (player, None),
            "display": idx.display_name(player, best["season"]),
            "person": idx.person_of(player, best["season"]),
        }
        complete = idx.career_complete(player)
        # Each award judges its own seasons: a career that touched a season with
        # a short All-NBA list can still prove a negative about All-Star.
        as_safe = not any(r["season"] in idx.all_star_unsafe_seasons for r in recs)
        nba_safe = not any(r["season"] in idx.all_nba_unsafe_seasons for r in recs)
        if as_safe and player not in idx.all_star_players:
            no_as_todate.append(dict(entry))
            if complete:
                no_as_retired.append(dict(entry))
        if nba_safe and player not in idx.all_nba_players:
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


def _name(entry):
    """The name to print for a comparison entry: the corrected one where a
    confirmed split says the data key covers two men."""
    return entry.get("display") or entry["player"]


def _holder(entry):
    if entry is None:
        return None
    holder = {
        "player": _name(entry),
        "season": entry["season"],
        "value": entry["value"],
    }
    if holder["player"] != entry["player"]:
        # the name in data.json, kept so a consumer can still find the record
        holder["data_key"] = entry["player"]
    return holder


def _behind_clause(entry, money=True, subject=None):
    """"Stephen Curry's $62.6 million (2026-27)", or "his own ..." for the
    subject himself, which otherwise reads as a comparison with a stranger.

    ``subject`` is the person identity from FactoidIndex.person_of, not a bare
    name, so a confirmed split never writes "his own mark" across two men filed
    under one key.
    """
    value = fmt_money(entry["value"]) if money else "{:.1f}%".format(entry["value"])
    if subject is not None and entry.get("person") == subject:
        return "his own {} ({})".format(value, entry["season"])
    return "{} {} ({})".format(_possessive(_name(entry)), value, entry["season"])


def pre_window_shadow(universe, value, margin=PRE_WINDOW_MARGIN):
    """The pre-window career that makes this figure unsafe to rank, if any.

    A career that began before 1990-91 is on file short of its first seasons.
    Where one of those totals lands within ``margin`` of the figure being
    claimed, the missing money could put it on the other side, so the claim is
    not made. A subject whose own career began before the window fails this on
    his own entry, at a distance of zero, which is the answer we want: his
    total is the one that is short.
    """
    for entry in universe.entries:
        if not entry.get("pre_window"):
            continue
        if abs((entry.get("value") or 0) - value) <= margin:
            return entry
    return None


def _career_gate(idx, player):
    """Which of the career-level gates a player fails, for the debug log."""
    if player in idx.truncated:
        return "truncated_career"
    if player in idx.identity_suspect:
        return "merged_identity"
    if player in idx.career_total_carried_in:
        return "career_total_carried_in"
    if player in idx.career_incomplete:
        return "career_incomplete"
    return "career_ineligible"


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
    # ``player`` keys the data; ``subject_name`` is what the sentence prints and
    # ``subject_person`` is who the sentence is about. They differ only where a
    # confirmed split says one data key covers two men.
    subject_name, subject_person = ctx["name"], ctx["person"]

    if record is None:
        log.drop("franchise", season, "no_record", "no season record to read a team from")
        return
    if is_split_season(record):
        log.drop(
            "franchise", season, "split_season",
            "team_salaries splits this season across {} teams, which is cap-sheet "
            "allocation rather than money paid while on a roster".format(
                len(record.get("team_salaries") or {})
            ),
        )
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
        # A record reaching here has a single team, so there is no share to note.
        split_note = ""
        scope = PAID_ONLY_NOTE.format(idx.current_season)
        eras = [e["name"] for e in franchise.get("eras", [])]
        if len(eras) > 1:
            scope += " {} franchise history here includes the {} seasons.".format(
                name, " and ".join(sorted({e for e in eras[:-1]}))
            )
        if contracted:
            scope += " " + CONTRACTED_NOTE

        if kind == "sets":
            if top.get("person") == subject_person:
                text = (
                    "{} {} in {}{} {} the highest single-season salary in {} "
                    "history, breaking his own mark of {} in {}.".format(
                        _possessive(subject_name), fmt_money(amount), season, split_note,
                        _is_verb(contracted), name,
                        fmt_money(top["value"]), top["season"],
                    )
                )
            else:
                text = (
                    "{} {} in {}{} {} the highest single-season salary in {} "
                    "history, passing {}.".format(
                        _possessive(subject_name), fmt_money(amount), season, split_note,
                        _is_verb(contracted), name, _behind_clause(top, subject=subject_person),
                    )
                )
        elif kind == "ties":
            text = (
                "{} {} in {}{} {} the highest single-season salary in {} "
                "history, matching {}.".format(
                    _possessive(subject_name), fmt_money(amount), season, split_note,
                    _tie_verb(contracted), name, _behind_clause(top, subject=subject_person),
                )
            )
        else:
            text = (
                "{} {} in {}{} {} the {}-highest single-season salary in {} "
                "history, behind {}.".format(
                    _possessive(subject_name), fmt_money(amount), season, split_note,
                    _is_verb(contracted), ordinal(verdict["rank"]), name,
                    _behind_clause(top, subject=subject_person),
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
    # ``player`` keys the data; ``subject_name`` is what the sentence prints and
    # ``subject_person`` is who the sentence is about. They differ only where a
    # confirmed split says one data key covers two men.
    subject_name, subject_person = ctx["name"], ctx["person"]

    if not idx.career_rankable(player):
        log.drop("career_earnings", player, _career_gate(idx, player))
        return
    if idx.pre_window_career(player):
        # His running total starts partway through his career, so the season
        # he crosses a milestone in this data is not the season he crossed it.
        log.drop(
            "career_earnings", player, "pre_window_career",
            "career began before {}, so the running total is short of what he "
            "had earned".format(SCOPE_FIRST_SEASON),
        )
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
        in_progress = season == idx.current_season and idx.current_season_in_progress
        if contracted:
            text = (
                "{} is on track to pass {} in career earnings in {} "
                "if his contract is paid in full.".format(subject_name, label, season)
            )
        elif in_progress:
            # The season is being played, so the salary is not earned yet.
            # "Passed" would be wrong until it ends.
            text = "{} will pass {} in career earnings in {}.".format(
                subject_name, label, season
            )
        else:
            text = "{} passed {} in career earnings in {}.".format(subject_name, label, season)
        scope = "Career earnings are nominal dollars. {}".format(DATA_START_NOTE)
        if in_progress:
            scope += (
                " {} is being played: no selections are on record for it yet, "
                "so this salary is not earned in full.".format(season)
            )
        if reached:
            scope += " {} other player{} had reached it.".format(
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

    # Rank on money already paid, active players included.
    #
    # A career-earnings list that holds only finished careers reads as false to
    # anyone who follows the league: it called Elton Brand the highest-earning
    # Duke player while Kyrie Irving and Jayson Tatum, both above him, were left
    # out for still playing. The figure is the running total through the last
    # season that is not contracted, so nothing here counts money that has not
    # been paid, and an active man's total is worded as a total to date.
    if not idx.career_rankable(player):
        log.drop("career_earnings", player, _career_gate(idx, player))
        return
    if not idx.is_paid_through(player, season):
        log.drop(
            "career_earnings", player, "not_paid_through_season",
            "career_earnings through {} is not the money-already-paid total".format(season),
        )
        return
    paid_total, _paid_season = idx.paid_through(player)
    if paid_total is None:
        return

    shadow = pre_window_shadow(idx.u_career, paid_total)
    if shadow is not None:
        log.drop(
            "career_earnings", player, "pre_window_career_too_close",
            "{}'s career began before {}, so his {} on file is short of what he "
            "earned, and it sits within {} of this figure".format(
                shadow["display"], SCOPE_FIRST_SEASON, fmt_money(shadow["value"]),
                fmt_money(PRE_WINDOW_MARGIN),
            ),
        )
        return

    verdict = idx.u_career.evaluate(paid_total, (player, None))
    kind = classify(paid_total, verdict)
    if kind is None:
        log.drop("career_earnings", player, "not_notable", "outside the career-earnings top {}".format(APPROACH_MAX_RANK))
        return
    top = verdict["top"]
    active = not idx.career_complete(player)
    scope = (
        "Ranked on money already paid, active players included, in nominal "
        "dollars. Contracted seasons are excluded, and so are names that merge "
        "two players. {}".format(DATA_START_NOTE)
    )
    if active:
        scope += " {} is still playing, so this is his total to date.".format(subject_name)
    out.append(
        _make(
            "career_earnings", kind,
            "career_rank|{}|{}".format(player, season),
            _career_rank_text(
                kind, subject_name, subject_person, paid_total, verdict, top,
                active, through=season,
            ),
            scope, paid_total, contracted,
            rank=verdict["rank"], comparison_size=verdict["size"],
            previous_holder=_holder(top), margin=paid_total - top["value"],
        )
    )


#: "more than X" needs the cohort as a rival, not as a place. The season
#: sentences say "the highest single-season salary among players out of Duke";
#: a career sentence has to say "more than any other Duke player".
def _career_rival(label):
    if label.startswith("among players out of "):
        return "any other player out of " + label[len("among players out of "):]
    if label.startswith("by an "):
        return "any other " + label[len("by an "):]
    if label.startswith("by a "):
        return "any other " + label[len("by a "):]
    if label.startswith("in the "):
        return "anyone else " + label
    return "anyone else " + label


def _career_rank_text(kind, subject_name, subject_person, total, verdict, top,
                      active, label=ALL_TIME, through=None):
    """One career-earnings sentence, active or finished, all-time or cohort.

    An active man "has earned ... through 2026-27", naming the last season the
    total covers rather than leaving "to date" to be guessed at; a finished
    career "earned ... in his career". Neither says "since 1990-91": the page
    carries that note once.
    """
    money = fmt_money(total)
    if active:
        opening = "{} has earned {} through {}".format(subject_name, money, through)
    else:
        opening = "{} earned {} in his career".format(subject_name, money)
    if kind == "sets":
        return "{}, more than {}, passing {}.".format(
            opening, _career_rival(label), _behind_clause(top, subject=subject_person))
    if kind == "ties":
        return "{}, level with the most {}, matching {}.".format(
            opening, label, _behind_clause(top, subject=subject_person))
    return "{}, the {}-most {}, behind {}.".format(
        opening, ordinal(verdict["rank"]), label,
        _behind_clause(top, subject=subject_person))


# -- family 3: cohorts -----------------------------------------------------


def _family_cohorts(ctx, out, log):
    idx, player, season = ctx["index"], ctx["player"], ctx["season"]
    contracted, salary = ctx["contracted"], ctx["salary"]
    # ``player`` keys the data; ``subject_name`` is what the sentence prints and
    # ``subject_person`` is who the sentence is about. They differ only where a
    # confirmed split says one data key covers two men.
    subject_name, subject_person = ctx["name"], ctx["person"]
    record = ctx.get("identity")
    if record is None:
        log.drop("cohort", season, "no_record")
        return

    if not idx.cohorts_allowed(player, season):
        log.drop(
            "cohort", player, "draft_metadata_suspect",
            "draft metadata postdates his debut, so it belongs to a son of the "
            "same name; college, country and position come off the same record "
            "and cannot be trusted either",
        )
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
            scope = "Cohort of {} players and {} player seasons. {} {}".format(
                universe.distinct_players(), len(universe),
                PAID_ONLY_NOTE.format(idx.current_season), DATA_START_NOTE,
            )
            if contracted:
                scope += " " + CONTRACTED_NOTE
            if verdict_kind == "sets":
                if top.get("person") == subject_person:
                    text = "{} {} in {} {} the highest single-season salary {}, breaking his own mark of {} in {}.".format(
                        _possessive(subject_name), fmt_money(salary), season,
                        _is_verb(contracted), label,
                        fmt_money(top["value"]), top["season"],
                    )
                else:
                    text = "{} {} in {} {} the highest single-season salary {}, passing {}.".format(
                        _possessive(subject_name), fmt_money(salary), season,
                        _is_verb(contracted), label, _behind_clause(top, subject=subject_person),
                    )
            elif verdict_kind == "ties":
                text = "{} {} in {} {} the highest single-season salary {}, matching {}.".format(
                    _possessive(subject_name), fmt_money(salary), season,
                    _tie_verb(contracted), label, _behind_clause(top, subject=subject_person),
                )
            else:
                text = "{} {} in {} {} the {}-highest single-season salary {}, behind {}.".format(
                    _possessive(subject_name), fmt_money(salary), season, _is_verb(contracted),
                    ordinal(verdict["rank"]), label, _behind_clause(top, subject=subject_person),
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

        # Career earnings inside the same cohort, on money already paid.
        if not idx.career_rankable(player):
            continue
        if not idx.is_paid_through(player, season):
            log.drop(
                "cohort", "{}:{}".format(kind_name, ckey), "not_paid_through_season",
                "career_earnings through {} is not the money-already-paid total".format(season),
            )
            continue
        career_total, _paid_season = idx.paid_through(player)
        if career_total is None:
            continue
        cu = idx.u_cohort_career.get((kind_name, ckey))
        if cu is None or len(cu) == 0:
            log.drop("cohort", "{}:{}".format(kind_name, ckey), "no_careers")
            continue
        if cu.distinct_players() < minimum:
            log.drop(
                "cohort", "{}:{}".format(kind_name, ckey), "cohort_below_threshold",
                "{} careers, needs {}".format(cu.distinct_players(), minimum),
            )
            continue
        shadow = pre_window_shadow(cu, career_total)
        if shadow is not None:
            log.drop(
                "cohort", "{}:{}".format(kind_name, ckey), "pre_window_career_too_close",
                "{}'s career began before {}, so his {} on file is short, and it "
                "sits within {} of this figure".format(
                    shadow["display"], SCOPE_FIRST_SEASON, fmt_money(shadow["value"]),
                    fmt_money(PRE_WINDOW_MARGIN),
                ),
            )
            continue
        cverdict = cu.evaluate(career_total, (player, None))
        ckind = classify(career_total, cverdict)
        if ckind is None:
            continue
        ctop = cverdict["top"]
        cactive = not idx.career_complete(player)
        scope = (
            "Ranked on money already paid against {} careers in this cohort, "
            "active players included. {}".format(cu.distinct_players(), DATA_START_NOTE)
        )
        if cactive:
            scope += " {} is still playing, so this is his total to date.".format(subject_name)
        out.append(
            _make(
                "cohort", ckind,
                "cohort_career|{}|{}|{}|{}".format(kind_name, ckey, player, season),
                _career_rank_text(
                    ckind, subject_name, subject_person, career_total, cverdict,
                    ctop, cactive, label=label, through=season,
                ),
                scope, career_total, contracted,
                rank=cverdict["rank"], comparison_size=cverdict["size"],
                previous_holder=_holder(ctop), margin=career_total - ctop["value"],
            )
        )


# -- family 4: negative space ---------------------------------------------


def _family_negative_space(ctx, out, log):
    idx, player, season = ctx["index"], ctx["player"], ctx["season"]
    contracted, salary = ctx["contracted"], ctx["salary"]
    # ``player`` keys the data; ``subject_name`` is what the sentence prints and
    # ``subject_person`` is who the sentence is about. They differ only where a
    # confirmed split says one data key covers two men.
    subject_name, subject_person = ctx["name"], ctx["person"]
    recs = idx.by_player.get(player) or []

    if not idx.career_eligible(player):
        log.drop("negative_space", player, _career_gate(idx, player))
        return
    active = not idx.career_complete(player)
    # An active subject is ranked against everyone's record to date; a finished
    # career is ranked against finished careers. See _build_universes.
    for label, holders, retired_u, todate_u, family_key in (
        ("All-Star", idx.all_star_players, idx.u_no_all_star, idx.u_no_all_star_todate, "no_all_star"),
        ("All-NBA", idx.all_nba_players, idx.u_no_all_nba, idx.u_no_all_nba_todate, "no_all_nba"),
    ):
        unsafe_set = (
            idx.all_star_unsafe_seasons if label == "All-Star"
            else idx.all_nba_unsafe_seasons
        )
        unsafe = sorted({r["season"] for r in recs if r["season"] in unsafe_set})
        if unsafe:
            log.drop(
                "negative_space", player, "awards_season_unsafe",
                "played in {}, whose {} list failed the audit".format(
                    ", ".join(unsafe), label
                ),
            )
            continue
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
                "Comparison set is every player with no {} selection on "
                "record, active or retired, taken at each one's best paid "
                "season.".format(label)
            )
        else:
            scope = (
                "Comparison set is completed careers with no {} selection on "
                "record.".format(label)
            )
        scope += (
            " Careers that began before 1990-91, names that merge two players "
            "and seasons whose selection lists failed the audit are excluded."
        )
        scope += " " + PAID_ONLY_NOTE.format(idx.current_season)
        if active and idx.career_status_unknown(player):
            scope += (
                " {} has no {} record and last appears in {}, so whether he is "
                "finished is unknown and this describes selections to date.".format(
                    subject_name, idx.current_season, idx.final_season.get(player, "")
                )
            )
        elif active:
            scope += " {} is still active, so this describes selections to date.".format(subject_name)
        if contracted:
            scope += " " + CONTRACTED_NOTE

        team_noun = "All-Star team" if label == "All-Star" else "All-NBA team"
        if active:
            subject = "for a player without an {} selection".format(label)
        else:
            subject = "by a player who never made an {}".format(team_noun)

        if kind == "sets":
            text = "{} {} in {} {} the highest single-season salary {}, passing {}.".format(
                _possessive(subject_name), fmt_money(salary), season, _is_verb(contracted),
                subject, _behind_clause(top, subject=subject_person),
            )
        elif kind == "ties":
            text = "{} {} in {} {} the highest single-season salary {}, matching {}.".format(
                _possessive(subject_name), fmt_money(salary), season, _tie_verb(contracted),
                subject, _behind_clause(top, subject=subject_person),
            )
        else:
            text = "{} {} in {} {} the {}-highest single-season salary {}, behind {}.".format(
                _possessive(subject_name), fmt_money(salary), season, _is_verb(contracted),
                ordinal(verdict["rank"]), subject, _behind_clause(top, subject=subject_person),
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
    # ``player`` keys the data; ``subject_name`` is what the sentence prints and
    # ``subject_person`` is who the sentence is about. They differ only where a
    # confirmed split says one data key covers two men.
    subject_name, subject_person = ctx["name"], ctx["person"]

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
        # An all-time cap share is only claimed for the top few. Deeper than
        # that, a season this data does not hold could displace it, and the
        # claim no longer says anything the missing seasons cannot overturn.
        if scope_name == "all" and verdict["rank"] > CAP_ALL_TIME_MAX_RANK:
            log.drop(
                "cap", season, "outside_all_time_top_{}".format(CAP_ALL_TIME_MAX_RANK),
                "all-time cap share is claimed for the top {} only".format(
                    CAP_ALL_TIME_MAX_RANK),
            )
            continue
        top = verdict["top"]
        share = "{:.1f}%".format(pct)
        if scope_name == "all":
            scope = "Share of that season's salary cap, ranked across every season on file. {} {}".format(
                PAID_ONLY_NOTE.format(idx.current_season), DATA_START_NOTE
            )
            where = "the largest share of a salary cap in NBA history"
            where_n = "the {}-largest share of a salary cap in NBA history".format(ordinal(verdict["rank"]))
        else:
            scope = "Share of the {} salary cap, ranked within that season.".format(season)
            where = "the largest share of the cap in {}".format(season)
            where_n = "the {}-largest share of the cap in {}".format(ordinal(verdict["rank"]), season)
        if contracted:
            scope += " Contracted salary against a projected cap. " + CONTRACTED_NOTE

        takes = "would take up" if contracted else "takes up"
        if kind == "sets":
            text = "{} {} salary {} {} of the {} cap, {}, passing {}.".format(
                _possessive(subject_name), fmt_money(salary), takes, share, season, where,
                _behind_clause(top, money=False, subject=subject_person),
            )
        elif kind == "ties":
            text = "{} {} salary {} {} of the {} cap, tying {}, matching {}.".format(
                _possessive(subject_name), fmt_money(salary), takes, share, season, where,
                _behind_clause(top, money=False, subject=subject_person),
            )
        else:
            text = "{} {} salary {} {} of the {} cap, {}, behind {}.".format(
                _possessive(subject_name), fmt_money(salary), takes, share, season, where_n,
                _behind_clause(top, money=False, subject=subject_person),
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
    # ``player`` keys the data; ``subject_name`` is what the sentence prints and
    # ``subject_person`` is who the sentence is about. They differ only where a
    # confirmed split says one data key covers two men.
    subject_name, subject_person = ctx["name"], ctx["person"]

    if not AGENT_FACTOIDS_ENABLED:
        log.drop(
            "agent", season, "agent_factoids_disabled",
            "the agent field is unverified, so the family is off (AGENT_FACTOIDS_ENABLED)",
        )
        return
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
            _possessive(subject_name), fmt_money(salary), season, _possessive(agent),
            _behind_clause(top, subject=subject_person),
        )
    elif kind == "ties":
        text = "{} {} ties the highest {} salary among {} clients, matching {}.".format(
            _possessive(subject_name), fmt_money(salary), season, _possessive(agent),
            _behind_clause(top, subject=subject_person),
        )
    else:
        text = "{} {} is the {}-highest {} salary among {} clients, behind {}.".format(
            _possessive(subject_name), fmt_money(salary), ordinal(verdict["rank"]), season,
            _possessive(agent), _behind_clause(top, subject=subject_person),
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
    # ``player`` keys the data; ``subject_name`` is what the sentence prints and
    # ``subject_person`` is who the sentence is about. They differ only where a
    # confirmed split says one data key covers two men.
    subject_name, subject_person = ctx["name"], ctx["person"]
    recs = idx.by_player.get(player) or []
    # "for the first time in his career" has to mean this man's career. Where a
    # confirmed split says the key covers two men, only the seasons belonging to
    # the same one count as prior.
    prior = [
        r for r in recs
        if season_key(r["season"]) < season_key(season)
        and idx.person_of(player, r["season"]) == subject_person
    ]

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
                "{} {} the highest-paid player in the league in {} for the first "
                "time in his career.".format(
                    subject_name, _is_verb(contracted), season),
                "First season at No. 1. {}".format(DATA_START_NOTE),
                salary, contracted, rank=rank, comparison_size=size,
            )
        )
    elif rank <= 10 and (prior_best is None or prior_best > 10):
        out.append(
            _make(
                "rank_shift", "rank_shift",
                "rank_league_top10|{}|{}".format(player, season),
                "{} salary in {} {} him in the league's top 10 for the first "
                "time in his career.".format(
                    _possessive(subject_name), season,
                    "would put" if contracted else "puts"),
                "First season inside the top 10. {}".format(DATA_START_NOTE),
                salary, contracted, rank=rank, comparison_size=size,
            )
        )

    # Team high earner, same franchise year over year only.
    if record is None:
        log.drop("rank_shift", season, "no_record", "team rank needs a roster")
        return
    if is_split_season(record):
        log.drop(
            "rank_shift", season, "split_season",
            "a team high-earner shift compares two seasons on one roster, and "
            "{} splits across {} teams in team_salaries, which is cap-sheet "
            "allocation rather than money paid while on a roster".format(
                season, len(record.get("team_salaries") or {})
            ),
        )
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
    # Either season being split sinks the claim, not just this one. The season
    # being compared against is where salary_rank_team comes from, and on a
    # split season there is no single roster that rank belongs to.
    if is_split_season(previous):
        log.drop(
            "rank_shift", previous["season"], "split_season",
            "the season being compared against splits across {} teams, so its "
            "roster rank belongs to no single team".format(
                len(previous.get("team_salaries") or {})
            ),
        )
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
                "{} {} the {} highest-paid player in {} after ranking {} on the "
                "roster in {}.".format(
                    subject_name, _is_verb(contracted), _possessive(name), season,
                    ordinal(was_rank) if was_rank > 1 else "first",
                    previous["season"]),
                "Same franchise in consecutive seasons. Roster of {} players with a salary on file.".format(now_size),
                amount, contracted, rank=now_rank, comparison_size=now_size,
            )
        )
    elif now_rank != 1 and was_rank == 1:
        out.append(
            _make(
                "rank_shift", "rank_shift",
                "team_high_ceases|{}|{}".format(player, season),
                "{} {} no longer the {} highest-paid player in {} after holding "
                "that spot in {}.".format(
                    subject_name, _is_verb(contracted), _possessive(name), season,
                    previous["season"]),
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

    # A caller may name either spelling; the engine works in the canonical one.
    player = idx.canonical(player)
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

    if (player, season) in idx.split_suppressed:
        entry = (idx.identity_splits or {}).get(player) or {}
        gate = (
            "identity_split_unnamed" if entry.get("confirmed")
            else "identity_split_unconfirmed"
        )
        log.drop(
            "all", player, gate,
            "{} covers more than one player and {} belongs to a segment this "
            "engine cannot put a name to".format(player, season),
        )
        if suppressed is not None:
            suppressed.extend(log.items)
        return []

    # A salary the CBA does not allow says nothing about the player, only about
    # the file. Nothing is claimed from it, about him or about anyone measured
    # against him. A hypothetical salary passed in by hand is exempt: the caller
    # is asking what such a number would mean, which is a different question.
    if salary is None and (player, season) in idx.impossible:
        log.drop(
            "all", player, "impossible_salary", idx.impossible[(player, season)]
        )
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
        "name": idx.display_name(player, season),
        "person": idx.person_of(player, season),
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
