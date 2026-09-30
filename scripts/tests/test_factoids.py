"""Tests for the factoid engine.

Everything below runs on synthetic fixtures small enough to reason about by
hand, so a failure names a rule rather than a data quirk. The last block also
runs the house-style rules over real records when data/data.json is present.
"""

from __future__ import annotations

import json
import os
import re

import pytest

import factoids as F
from factoids import build_index, factoids_for, season_key

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REAL_DATA = os.path.join(REPO, "data", "data.json")
FRANCHISES = F.load_franchises(os.path.join(REPO, "data", "franchises.json"))

CURRENT = "2026-27"
CONTRACTED = "2027-28"

#: Enough All-NBA rows to satisfy the audit in a fixture.
ALL_NBA_FILL = F.ALL_NBA_COUNT_EXPECTED


# --------------------------------------------------------------------------
# Fixture construction
# --------------------------------------------------------------------------


def rec(player, season, salary, team="OKC", **kw):
    """One season record with sane defaults for every field the engine reads."""
    out = {
        "player": player,
        "season": season,
        "team": team,
        "salary": salary,
        "career_earnings": kw.pop("career_earnings", salary),
        "salary_cap_pct": kw.pop("salary_cap_pct", round(salary / 100000000.0 * 100, 2)),
        "salary_rank_league": kw.pop("salary_rank_league", 1),
        "salary_rank_team": kw.pop("salary_rank_team", 1),
        "awards": kw.pop("awards", []),
        "pos": kw.pop("pos", "G"),
        "age": kw.pop("age", 25),
        "years_exp": kw.pop("years_exp", 3),
        "draft_year": kw.pop("draft_year", 2015),
        "draft_pick": kw.pop("draft_pick", 5),
        "college": kw.pop("college", "Kentucky"),
        "nationality": kw.pop("nationality", "United States"),
    }
    out.update(kw)
    return out


def make_data(records, seasons=None, caps=None):
    """A data.json-shaped dict.

    `teams` holds a single code so compute_current_season's "15 paid players per
    team" rule needs 15 records; the filler below supplies exactly that for
    CURRENT, which pins the current season without hardcoding it in the engine.
    """
    filler = [
        rec("Filler {}".format(i), CURRENT, 1000, team="BOS",
            salary_cap_pct=0.001, salary_rank_league=400, salary_rank_team=15,
            draft_year=2016, draft_pick=40 + i, college="Filler U",
            nationality="Fillerland")
        for i in range(15)
    ]
    all_records = list(records) + filler
    season_set = sorted({r["season"] for r in all_records} | set(seasons or ()), key=season_key)
    # A cap far above any fixture salary, so the impossible-salary guard never
    # fires on a fixture that is not testing it. The recorded salary_cap_pct on
    # each record is what the cap-context family ranks, and rec() still builds
    # that off $100m, so those claims read the same as before.
    cap_map = {s: {"cap": 1000000000} for s in season_set}
    if caps is not None:
        cap_map = caps
    return {
        "seasons": all_records,
        "seasons_list": sorted(season_set, key=season_key, reverse=True),
        "teams": ["OKC"],
        "agents": [],
        "awards_list": ["All-Star", "All-NBA First Team"],
        "players": sorted({r["player"] for r in all_records}),
        "salary_cap": cap_map,
    }


def tail(team="OKC", season="2019-20", n=12, base=1000000):
    """Filler deals so a comparison set clears MIN_COMPARISON_SIZE. Small enough
    that they never win anything."""
    return [
        rec("Tail {} {}".format(team, i), season, base + i * 1000, team=team,
            salary_cap_pct=0.001, salary_rank_league=300 + i, salary_rank_team=10,
            draft_year=2016, draft_pick=55, college="Filler U",
            nationality="Fillerland")
        for i in range(n)
    ]


def unselected_peers(season, n=12, base=1000000):
    """Retired players with no selection on record, in a season that passes the
    awards audit, so the negative-space universes have a field."""
    return [
        rec("Peer {} {}".format(season, i), season, base + i * 1000, team="BOS",
            salary_cap_pct=0.001, salary_rank_league=250 + i, salary_rank_team=10,
            draft_year=2016, draft_pick=55, college="Filler U",
            nationality="Fillerland")
        for i in range(n)
    ]


def all_star_class(season, n_all_star=24, n_all_nba=15):
    """A season whose selection counts land inside the audit's plausibility
    band, so negative-space claims are not suppressed for touching it."""
    out = []
    for i in range(n_all_star):
        awards = ["All-Star"]
        if i < n_all_nba:
            awards.append("All-NBA First Team")
        out.append(
            rec("Selected {} {}".format(season, i), season, 2000000 + i, team="BOS",
                awards=awards, salary_cap_pct=0.002, salary_rank_league=200 + i,
                draft_year=2016, draft_pick=50, college="Filler U",
                nationality="Fillerland")
        )
    return out


def index_for(data, splits=None):
    """Synthetic fixtures never read data/identity_splits.json: pass the entries
    a test needs explicitly, default none."""
    return build_index(data, franchises=FRANCHISES, identity_splits=splits or {})


def facts(data, player, season, salary=None, family=None, kind=None, splits=None):
    idx = index_for(data, splits)
    out = factoids_for(data, player, season, salary, index=idx)
    if family:
        out = [f for f in out if f["family"] == family]
    if kind:
        out = [f for f in out if f["type"] == kind]
    return out


def gates(data, player, season, salary=None, splits=None):
    """The set of gate names that suppressed candidates for this subject."""
    idx = index_for(data, splits)
    dropped = []
    factoids_for(data, player, season, salary, index=idx, suppressed=dropped)
    return {d["gate"] for d in dropped}


@pytest.fixture
def agents_on(monkeypatch):
    """The agent family ships off (AGENT_FACTOIDS_ENABLED). Its rules still have
    to hold, so the tests that exercise them turn it on for their duration."""
    monkeypatch.setattr(F, "AGENT_FACTOIDS_ENABLED", True)


# --------------------------------------------------------------------------
# sets / ties / approaches
# --------------------------------------------------------------------------


def _field():
    """Record Holder at $40m, Runner Up at $30m, then a tail of small deals so
    the comparison set clears APPROACH_MIN_COMPARISON_SIZE."""
    return [
        rec("Record Holder", "2020-21", 40000000),
        rec("Runner Up", "2021-22", 30000000),
    ] + [
        rec("Tail {}".format(i), "2022-23", 1000000 + i * 1000) for i in range(10)
    ]


def test_sets_when_strictly_above_the_prior_max():
    data = make_data(_field() + [rec("Challenger", "2023-24", 41000000)])
    out = facts(data, "Challenger", "2023-24", family="franchise")
    assert len(out) == 1
    assert out[0]["type"] == "sets"
    assert out[0]["rank"] == 1
    assert out[0]["previous_holder"]["player"] == "Record Holder"
    assert out[0]["margin"] == 1000000
    assert "highest single-season salary in Thunder history" in out[0]["text"]


def test_ties_when_equal_and_says_so():
    data = make_data(_field() + [rec("Challenger", "2023-24", 40000000)])
    out = facts(data, "Challenger", "2023-24", family="franchise")
    assert out[0]["type"] == "ties"
    assert out[0]["margin"] == 0
    assert "ties" in out[0]["text"]
    assert "Record Holder" in out[0]["text"]


def test_approaches_by_rank_names_the_holder_and_the_gap():
    data = make_data(_field() + [rec("Challenger", "2023-24", 35000000)])
    out = facts(data, "Challenger", "2023-24", family="franchise")
    assert out[0]["type"] == "approaches"
    assert out[0]["rank"] == 2
    assert out[0]["margin"] == -5000000
    assert "second-highest" in out[0]["text"]
    assert "behind Record Holder's $40 million (2020-21)" in out[0]["text"]


def test_approaches_by_percentage_outside_the_rank_window():
    # Seventh by rank, but inside 5% of the record, so it still counts.
    field = [rec("P{}".format(i), "2016-17", 40000000 - i) for i in range(6)]
    tail = [rec("T{}".format(i), "2017-18", 1000 + i) for i in range(6)]
    data = make_data(field + tail + [rec("Challenger", "2023-24", 39000000)])
    out = facts(data, "Challenger", "2023-24", family="franchise")
    assert out and out[0]["type"] == "approaches"
    assert out[0]["rank"] > F.APPROACH_MAX_RANK


def test_far_off_the_pace_emits_nothing():
    data = make_data(_field() + [rec("Challenger", "2023-24", 500000)])
    assert facts(data, "Challenger", "2023-24", family="franchise") == []
    assert "not_notable" in gates(data, "Challenger", "2023-24")


def test_breaking_your_own_record_is_said_that_way():
    data = make_data(tail() + [
        rec("Repeat", "2021-22", 30000000),
        rec("Repeat", "2022-23", 35000000),
    ])
    out = facts(data, "Repeat", "2022-23", family="franchise")
    assert out[0]["type"] == "sets"
    assert "breaking his own mark of $30 million in 2021-22" in out[0]["text"]


# --------------------------------------------------------------------------
# self-exclusion
# --------------------------------------------------------------------------


def test_a_record_never_competes_with_itself():
    """The standing record, re-evaluated, still reads as the record rather than
    as tying or trailing itself."""
    data = make_data(_field())
    out = facts(data, "Record Holder", "2020-21", family="franchise")
    assert out[0]["type"] == "sets"
    assert out[0]["rank"] == 1
    assert out[0]["previous_holder"]["player"] == "Runner Up"
    assert "Record Holder" not in out[0]["text"].split(" is ")[-1]


def test_comparison_size_excludes_the_subject():
    data = make_data(_field())
    out = facts(data, "Record Holder", "2020-21", family="franchise")
    assert out[0]["comparison_size"] == len(_field()) - 1


# --------------------------------------------------------------------------
# hypothetical salary
# --------------------------------------------------------------------------


def test_hypothetical_salary_flips_approaches_into_sets():
    data = make_data(_field() + [rec("Challenger", "2023-24", 25000000)])
    real = facts(data, "Challenger", "2023-24", family="franchise")
    assert real[0]["type"] == "approaches"

    what_if = facts(data, "Challenger", "2023-24", salary=45000000, family="franchise")
    assert what_if[0]["type"] == "sets"
    assert what_if[0]["value"] == 45000000
    assert "$45 million" in what_if[0]["text"]


def test_hypothetical_for_a_season_the_player_has_no_record_in():
    """Cohorts fall back to the player's nearest season for identity; the
    team-bound families stay suppressed because the team is not knowable."""
    data = make_data(_field() + [rec("Challenger", "2023-24", 25000000)])
    out = facts(data, "Challenger", "2024-25", salary=50000000)
    assert any(f["family"] == "cohort" and f["type"] == "sets" for f in out)
    assert [f for f in out if f["family"] == "franchise"] == []
    assert "no_record" in gates(data, "Challenger", "2024-25", salary=50000000)


def test_missing_record_without_a_salary_is_an_error():
    data = make_data(_field())
    with pytest.raises(KeyError):
        factoids_for(data, "Record Holder", "2024-25", index=index_for(data))


def test_hypothetical_rebuilds_career_earnings_from_the_previous_season():
    data = make_data([
        rec("Earner", "2021-22", 60000000, career_earnings=60000000),
        rec("Earner", "2022-23", 45000000, career_earnings=105000000),
    ])
    out = facts(data, "Earner", "2022-23", salary=60000000,
                family="career_earnings", kind="milestone")
    # 60m banked plus a hypothetical 60m crosses 100m, not 150m.
    labels = {f["text"] for f in out}
    assert any("$100 million" in t for t in labels)
    assert not any("$150 million" in t for t in labels)


# --------------------------------------------------------------------------
# franchise identity
# --------------------------------------------------------------------------


def test_relocated_franchise_is_one_history():
    """A Seattle-era season and a Thunder-era season share the OKC universe."""
    data = make_data(tail() + [
        rec("Sonic Era", "1995-96", 30000000, team="OKC"),
        rec("Thunder Era", "2023-24", 31000000, team="OKC"),
    ])
    out = facts(data, "Thunder Era", "2023-24", family="franchise")
    assert out[0]["type"] == "sets"
    assert out[0]["previous_holder"]["player"] == "Sonic Era"
    assert out[0]["previous_holder"]["season"] == "1995-96"
    assert "Seattle SuperSonics" in out[0]["scope_note"]


def test_separate_franchises_do_not_share_a_history():
    data = make_data(tail("OKC") + tail("BOS") + [
        rec("Thunder Guy", "2022-23", 40000000, team="OKC"),
        rec("Celtic Guy", "2023-24", 30000000, team="BOS"),
    ])
    out = facts(data, "Celtic Guy", "2023-24", family="franchise")
    assert out and "Celtics" in out[0]["text"]
    assert "Thunder Guy" not in out[0]["text"]


def test_unmapped_team_code_gets_no_franchise_factoid():
    data = make_data(tail("XXX") + [
        rec("Expansion Guy", "2023-24", 90000000, team="XXX"),
    ])
    assert facts(data, "Expansion Guy", "2023-24", family="franchise") == []
    assert "unmapped_team_code" in gates(data, "Expansion Guy", "2023-24")


# --------------------------------------------------------------------------
# mid-season split
# --------------------------------------------------------------------------


def test_split_season_emits_no_franchise_factoid():
    """team_salaries is cap-sheet allocation, not money paid on a roster.

    Westbrook's 2022-23 reads $46.3m against Utah, a team he never played for.
    Neither share is a franchise's to claim, so a split season says nothing about
    any franchise rather than saying the wrong thing about two.
    """
    data = make_data(tail("OKC") + tail("BOS") + [
        rec("Incumbent", "2021-22", 25000000, team="OKC"),
        rec("Traded", "2023-24", 40000000, team="OKC, BOS",
            team_salaries={"OKC": 22000000, "BOS": 18000000}),
    ])
    assert facts(data, "Traded", "2023-24", family="franchise") == []
    assert "split_season" in gates(data, "Traded", "2023-24")


def test_split_season_is_not_a_franchise_record_holder():
    """It cannot win the record, and it cannot be the mark someone else passes."""
    data = make_data(tail("OKC") + [
        rec("Traded", "2022-23", 90000000, team="OKC, BOS",
            team_salaries={"OKC": 60000000, "BOS": 30000000}),
        rec("Incumbent", "2021-22", 25000000, team="OKC"),
        rec("Challenger", "2023-24", 26000000, team="OKC"),
    ])
    out = facts(data, "Challenger", "2023-24", family="franchise")
    assert len(out) == 1
    assert out[0]["type"] == "sets"
    # the $60m OKC share is out of the universe, so the mark to beat is the
    # $25m single-team season
    assert out[0]["previous_holder"]["player"] == "Incumbent"


def test_split_season_still_feeds_cohort_and_cap_claims():
    """Only the franchise family is blocked. The full-season salary is real."""
    data = make_data(tail("OKC", season="2019-20") + [
        rec("Peer {}".format(i), "2019-20", 1000000 + i, college="Kentucky")
        for i in range(15)
    ] + [
        rec("Traded", "2023-24", 40000000, team="OKC, BOS",
            team_salaries={"OKC": 22000000, "BOS": 18000000}, college="Kentucky"),
    ])
    cohort = facts(data, "Traded", "2023-24", family="cohort")
    assert cohort, "cohort claims use the whole-season salary"
    assert all(f["value"] == 40000000 for f in cohort)


def test_hypothetical_salary_on_a_split_season_is_refused():
    data = make_data(tail("OKC") + tail("BOS") + [
        rec("Traded", "2023-24", 40000000, team="OKC, BOS",
            team_salaries={"OKC": 22000000, "BOS": 18000000}),
    ])
    assert facts(data, "Traded", "2023-24", salary=90000000, family="franchise") == []
    assert "split_season" in gates(data, "Traded", "2023-24", salary=90000000)


def test_a_split_season_blocks_team_high_earner_shifts():
    data = make_data(tail("OKC") + [
        rec("Mover", "2022-23", 10000000, team="OKC", salary_rank_team=3),
        rec("Mover", "2023-24", 40000000, team="OKC, BOS",
            team_salaries={"OKC": 22000000, "BOS": 18000000}),
    ])
    assert "split_season" in gates(data, "Mover", "2023-24")


# --------------------------------------------------------------------------
# truncated careers
# --------------------------------------------------------------------------


def test_truncated_career_is_excluded_from_career_rankings():
    data = make_data([
        rec("Old Timer", "1990-91", 5000000, draft_year=1985,
            career_earnings=200000000),
        rec("Old Timer", "1991-92", 5000000, draft_year=1985,
            career_earnings=205000000),
        rec("Modern", "2021-22", 60000000, draft_year=2015, career_earnings=60000000),
        rec("Modern", "2022-23", 60000000, draft_year=2015, career_earnings=120000000),
    ])
    idx = index_for(data)
    assert "Old Timer" in idx.truncated
    assert "Old Timer" not in {e["player"] for e in idx.u_career.entries}

    out = factoids_for(data, "Old Timer", "1991-92", index=idx)
    assert [f for f in out if f["family"] == "career_earnings"] == []
    assert "truncated_career" in gates(data, "Old Timer", "1991-92")


def test_a_1990_91_debut_without_a_1990_draft_year_is_treated_as_truncated():
    """years_exp is useless here, so the opening season plus draft year is what
    catches a career already under way. Also catches the Sr/Jr metadata mixups."""
    data = make_data([
        rec("Veteran", "1990-91", 5000000, draft_year=2013),  # a son's draft year
        rec("True Rookie", "1990-91", 5000000, draft_year=1990),
    ])
    idx = index_for(data)
    assert "Veteran" in idx.truncated
    assert "True Rookie" not in idx.truncated


def test_draft_year_after_the_debut_blocks_draft_cohorts():
    data = make_data(
        [rec("Namesake", "1995-96", 40000000, draft_year=2013, draft_pick=35)]
        + [rec("Peer {}".format(i), "1995-96", 1000000, draft_year=2013, draft_pick=35)
           for i in range(12)]
    )
    idx = index_for(data)
    assert "Namesake" in idx.draft_meta_suspect
    out = factoids_for(data, "Namesake", "1995-96", index=idx)
    keys = {f["key"] for f in out}
    assert not any(k.startswith("cohort_season|draft_class") for k in keys)
    assert not any(k.startswith("cohort_season|draft_slot") for k in keys)


def test_merged_identity_is_excluded_from_career_claims():
    data = make_data([
        rec("Same Name", "1995-96", 5000000, career_earnings=5000000),
        rec("Same Name", "2020-21", 50000000, career_earnings=300000000),
    ])
    idx = index_for(data)
    assert "Same Name" in idx.identity_suspect
    assert "Same Name" not in {e["player"] for e in idx.u_career.entries}
    assert "merged_identity" in gates(data, "Same Name", "2020-21")


# --------------------------------------------------------------------------
# cohort thresholds
# --------------------------------------------------------------------------


def _nationality_cohort(n, country="Tinyland"):
    return [
        rec("Native {}".format(i), "2023-24", 1000000 + i, nationality=country,
            college="Small College", draft_year=2015, draft_pick=10 + i)
        for i in range(n)
    ] + [rec("Star", "2023-24", 90000000, nationality=country,
             college="Small College", draft_year=2015, draft_pick=3)]


def test_cohort_below_threshold_emits_nothing():
    small = F.NATIONALITY_MIN_PLAYERS - 2  # +1 subject stays under the minimum
    data = make_data(_nationality_cohort(small))
    out = [f for f in facts(data, "Star", "2023-24", family="cohort")
           if "nationality" in f["key"]]
    assert out == []
    assert "cohort_below_threshold" in gates(data, "Star", "2023-24")


def test_cohort_at_threshold_emits():
    data = make_data(_nationality_cohort(F.NATIONALITY_MIN_PLAYERS - 1))
    out = [f for f in facts(data, "Star", "2023-24", family="cohort")
           if f["key"].startswith("cohort_season|nationality")]
    assert len(out) == 1
    assert out[0]["type"] == "sets"
    assert "by a player from Tinyland" in out[0]["text"]


def test_college_threshold_is_higher_than_nationality():
    assert F.COLLEGE_MIN_PLAYERS == 15
    assert F.NATIONALITY_MIN_PLAYERS == 5
    # A cohort big enough for nationality but not for college.
    n = F.COLLEGE_MIN_PLAYERS - 3
    data = make_data(_nationality_cohort(n))
    keys = {f["key"] for f in facts(data, "Star", "2023-24", family="cohort")}
    assert any("nationality" in k for k in keys)
    assert not any(k.startswith("cohort_season|college") for k in keys)


# --------------------------------------------------------------------------
# agent gating
# --------------------------------------------------------------------------


def _agent_roster(season, agent="Super Agent", n=6):
    """A roster whose top salary leads without towering over it: a contracted
    salary far clear of every other in its season is what the impossible-salary
    guard is for, and these fixtures are not testing that."""
    return [
        rec("Client {}".format(i), season, 50000000 + i * 1000, agent=agent)
        for i in range(n)
    ] + [rec("Top Client", season, 60000000, agent=agent)]


def test_agent_family_is_off_by_default():
    """Shipped off: the current agent values are unverified and at least one is
    wrong (Jokic reads "Mike Lindeman"). The rules below still have to hold."""
    assert F.AGENT_FACTOIDS_ENABLED is False
    data = make_data(_agent_roster(CURRENT))
    assert facts(data, "Top Client", CURRENT, family="agent") == []
    assert "agent_factoids_disabled" in gates(data, "Top Client", CURRENT)


def test_agent_factoid_fires_on_the_current_season(agents_on):
    data = make_data(_agent_roster(CURRENT))
    out = facts(data, "Top Client", CURRENT, family="agent")
    assert len(out) == 1
    assert out[0]["type"] == "sets"
    assert "among Super Agent's clients" in out[0]["text"]
    assert "current-clients claim" in out[0]["scope_note"]


def test_agent_factoid_fires_on_a_contracted_season(agents_on):
    data = make_data(_agent_roster(CURRENT) + _agent_roster(CONTRACTED))
    out = facts(data, "Top Client", CONTRACTED, family="agent")
    assert len(out) == 1
    assert out[0]["contracted"] is True


def test_agent_factoid_is_refused_for_a_historical_season(agents_on):
    data = make_data(_agent_roster("2015-16") + _agent_roster(CURRENT))
    assert facts(data, "Top Client", "2015-16", family="agent") == []
    assert "agent_history_unreliable" in gates(data, "Top Client", "2015-16")


def test_agent_with_too_few_clients_emits_nothing(agents_on):
    data = make_data(_agent_roster(CURRENT, n=F.AGENT_MIN_CLIENTS - 3))
    assert facts(data, "Top Client", CURRENT, family="agent") == []
    assert "too_few_clients" in gates(data, "Top Client", CURRENT)


# --------------------------------------------------------------------------
# contracted phrasing
# --------------------------------------------------------------------------


def test_contracted_season_is_phrased_conditionally():
    data = make_data(_field() + [
        # a raise a signed deal can carry, so the impossible-salary guard
        # leaves it alone
        rec("Future", CURRENT, 35000000),
        rec("Future", CONTRACTED, 45000000),
    ])
    out = facts(data, "Future", CONTRACTED, family="franchise")
    assert out[0]["contracted"] is True
    assert out[0]["type"] == "sets"
    assert "would be the highest single-season salary" in out[0]["text"]
    assert "contracted, not money already paid" in out[0]["scope_note"]


def test_contracted_career_milestone_uses_the_briefed_wording():
    data = make_data([
        rec("Future", CURRENT, 60000000, career_earnings=60000000),
        rec("Future", CONTRACTED, 60000000, career_earnings=120000000),
    ])
    out = facts(data, "Future", CONTRACTED, family="career_earnings", kind="milestone")
    assert len(out) == 1
    assert out[0]["text"] == (
        "Future is on track to pass $100 million in career earnings in "
        "2027-28 if his contract is paid in full."
    )


def test_a_contracted_season_is_never_the_record_another_season_chases():
    """Money that has not been paid cannot stand as the mark to beat."""
    data = make_data(_field() + [
        rec("Big Future", CONTRACTED, 90000000),
        rec("Now", CURRENT, 41000000),
    ])
    out = facts(data, "Now", CURRENT, family="franchise")
    assert out and out[0]["type"] == "sets"
    assert "Big Future" not in out[0]["text"]
    assert out[0]["previous_holder"]["player"] == "Record Holder"


def test_past_seasons_are_not_contracted():
    data = make_data(_field() + [rec("Past", "2022-24".replace("2022-24", "2023-24"), 41000000)])
    out = facts(data, "Past", "2023-24", family="franchise")
    assert out[0]["contracted"] is False
    assert " is the highest single-season salary" in out[0]["text"]


# --------------------------------------------------------------------------
# missing future cap
# --------------------------------------------------------------------------


def test_missing_cap_suppresses_cap_factoids():
    data = make_data(_field() + [
        rec("Capless", CONTRACTED, 90000000, salary_cap_pct=9.0)])
    caps = dict(data["salary_cap"])
    del caps[CONTRACTED]
    data["salary_cap"] = caps

    assert facts(data, "Capless", CONTRACTED, family="cap_context") == []
    assert "missing_cap" in gates(data, "Capless", CONTRACTED)
    # The other families still work; only the cap family is gated.
    assert facts(data, "Capless", CONTRACTED, family="franchise")


def test_cap_factoid_fires_when_the_cap_is_present():
    data = make_data(_field() + [rec("Rich", CURRENT, 90000000)])
    out = facts(data, "Rich", CURRENT, family="cap_context")
    assert out
    assert any("% of the" in f["text"] for f in out)


# --------------------------------------------------------------------------
# negative space
# --------------------------------------------------------------------------


def test_a_selection_blocks_the_negative_space_claim():
    data = make_data(
        all_star_class("2022-23") + unselected_peers("2022-23") + [
            rec("Selected", "2022-23", 50000000, awards=["All-Star"]),
        ]
    )
    idx = index_for(data)
    assert "2022-23" not in idx.awards_unsafe_seasons
    out = facts(data, "Selected", "2022-23", family="negative_space")
    assert not [f for f in out if f["key"].startswith("no_all_star")]
    assert "has_selection" in gates(data, "Selected", "2022-23")


def test_negative_space_fires_for_a_player_with_no_selection():
    data = make_data(
        all_star_class("2022-23") + unselected_peers("2022-23") + [
            rec("Selected", "2022-23", 30000000, awards=["All-Star"]),
            rec("Unselected", "2022-23", 40000000),
        ]
    )
    out = facts(data, "Unselected", "2022-23", family="negative_space")
    assert out
    kinds = {f["key"].split("|")[0] for f in out}
    assert "no_all_star" in kinds
    # An All-Star may still appear in the All-NBA claim, but never in the
    # All-Star one.
    all_star_claim = [f for f in out if f["key"].startswith("no_all_star")][0]
    assert "Selected" not in all_star_claim["text"]
    assert "never made an All-Star team" in all_star_claim["text"]
    all_nba_claim = [f for f in out if f["key"].startswith("no_all_nba")][0]
    assert "Selected" in all_nba_claim["text"]


def test_an_active_subject_is_compared_against_active_players_too():
    """An active non-All-Star must not be called the highest-paid non-All-Star
    while another active non-All-Star earns more."""
    data = make_data(
        all_star_class("2022-23") + unselected_peers("2022-23") + [
            rec("Active Subject", CURRENT, 40000000),
            rec("Active Richer", CURRENT, 60000000),
        ]
    )
    idx = index_for(data)
    assert "Active Subject" in idx.active_players
    assert "Active Richer" in idx.active_players
    out = facts(data, "Active Subject", CURRENT, family="negative_space")
    for fact in out:
        assert fact["type"] != "sets", fact["text"]
    assert any("Active Richer" in f["text"] for f in out)


def test_an_unsafe_awards_season_suppresses_the_claim():
    """1998-99 carries no All-Star selections at all, so a career touching it
    cannot be used to prove that someone was never selected."""
    data = make_data(tail() + [
        rec("Nineties Guy", "1998-99", 40000000),
        rec("Nineties Guy", "1999-00", 40000000),
    ])
    idx = index_for(data)
    assert "1998-99" in idx.awards_unsafe_seasons
    assert "awards_season_unsafe" in gates(data, "Nineties Guy", "1999-00")


def test_awards_string_mapping_is_exact_not_pattern_matched():
    """"All-Star MVP" alone is not treated as a selection; the data always
    carries the plain "All-Star" string alongside it."""
    data = make_data([rec("Guy", "2022-23", 1000, awards=["All-Star MVP"])])
    idx = index_for(data)
    assert "Guy" not in idx.all_star_players


# --------------------------------------------------------------------------
# output contract, for part two
# --------------------------------------------------------------------------


def test_ids_are_stable_across_runs_and_independent_of_value():
    data = make_data(_field() + [rec("Challenger", "2023-24", 25000000)])
    first = facts(data, "Challenger", "2023-24", family="franchise")[0]
    second = facts(data, "Challenger", "2023-24", family="franchise")[0]
    assert first["id"] == second["id"]

    richer = facts(data, "Challenger", "2023-24", salary=45000000, family="franchise")[0]
    assert richer["id"] == first["id"]      # same claim
    assert richer["value"] != first["value"]  # different number


def test_every_factoid_carries_the_documented_fields():
    data = make_data(_field() + [rec("Challenger", "2023-24", 41000000)])
    required = {
        "id", "family", "type", "text", "scope_note", "value", "rank",
        "comparison_size", "previous_holder", "margin", "contracted",
    }
    out = factoids_for(data, "Challenger", "2023-24", index=index_for(data))
    assert out
    for fact in out:
        assert required <= set(fact)
        assert fact["type"] in {"sets", "ties", "approaches", "milestone", "rank_shift"}
        assert isinstance(fact["contracted"], bool)


def test_unknown_player_returns_nothing_rather_than_raising():
    data = make_data(_field())
    assert factoids_for(data, "Nobody", "2023-24", salary=1, index=index_for(data)) == []


# --------------------------------------------------------------------------
# house style
# --------------------------------------------------------------------------

ALL_TIME_WORDS = ("ever", "all-time", "in NBA history")

#: Abbreviations that carry a period mid-sentence ("St. John's", "No. 41"), so
#: a period followed by a capital after one of these is not a sentence break.
_ABBREV = r"(?<!\bSt)(?<!\bNo)(?<!\bJr)(?<!\bSr)(?<!\bMt)(?<!\bJ)(?<!\bA)"
SENTENCE_BREAK = re.compile(_ABBREV + r"\.\s+[A-Z]")


def assert_house_style(fact):
    text = fact["text"]
    assert "—" not in text and "--" not in text, "em dash in: " + text
    assert text.endswith("."), "not one sentence: " + text
    assert not SENTENCE_BREAK.search(text), "more than one sentence: " + text
    lowered = text.lower()
    for word in ALL_TIME_WORDS:
        if word in lowered:
            assert F.SCOPE_SUFFIX in lowered, "{!r} without the scope: {}".format(word, text)
    for hype in ("massive", "staggering", "whopping", "eye-popping", "jaw-dropping"):
        assert hype not in lowered, "hype adjective in: " + text
    assert re.search(r"\d{4}-\d{2}", text), "no season label in: " + text


def test_house_style_on_synthetic_output():
    data = make_data(_field() + [rec("Challenger", "2023-24", 41000000)])
    out = factoids_for(data, "Challenger", "2023-24", index=index_for(data))
    assert out
    for fact in out:
        assert_house_style(fact)


def test_money_formatting_follows_ap_style():
    assert F.fmt_money(47600000) == "$47.6 million"
    assert F.fmt_money(50000000) == "$50 million"
    assert F.fmt_money(1234567) == "$1.2 million"
    assert F.fmt_money(507336) == "$507,336"


def test_no_claim_hedges_on_the_data_window():
    """"since 1990-91" in every sentence read as a hedge on figures that are,
    for everyone being compared, the whole of what he earned. The caveat is a
    note on the page now, not a clause in the claim."""
    data = make_data(_field() + [rec("Challenger", "2023-24", 41000000)])
    out = factoids_for(data, "Challenger", "2023-24", index=index_for(data))
    assert out
    for fact in out:
        assert "1990-91" not in fact["text"], fact["text"]


# --------------------------------------------------------------------------
# real data, when it is present
# --------------------------------------------------------------------------


@pytest.mark.skipif(not os.path.exists(REAL_DATA), reason="data/data.json not present")
def test_real_data_output_obeys_house_style():
    with open(REAL_DATA, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    idx = build_index(data, franchises=FRANCHISES)
    sample = [r for r in data["seasons"] if r["season"] == idx.current_season][:120]
    assert sample
    seen = 0
    for record in sample:
        for fact in factoids_for(data, record["player"], record["season"], index=idx):
            assert_house_style(fact)
            seen += 1
    assert seen > 0


@pytest.mark.skipif(not os.path.exists(REAL_DATA), reason="data/data.json not present")
def test_every_team_code_in_the_data_is_mapped():
    with open(REAL_DATA, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    codes = set()
    for record in data["seasons"]:
        for code, _amount in F.team_amounts(record):
            codes.add(code)
    assert codes <= set(FRANCHISES), sorted(codes - set(FRANCHISES))


@pytest.mark.skipif(not os.path.exists(REAL_DATA), reason="data/data.json not present")
def test_current_season_matches_the_front_end_rule():
    with open(REAL_DATA, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    # Recompute independently, the way js/app.js does it.
    counts = {}
    for record in data["seasons"]:
        if record.get("salary"):
            counts[record["season"]] = counts.get(record["season"], 0) + 1
    minimum = (len(data["teams"]) or 30) * 15
    expected = next(
        (s for s in data["seasons_list"] if counts.get(s, 0) >= minimum),
        data["seasons_list"][0],
    )
    assert F.compute_current_season(data) == expected


# --------------------------------------------------------------------------
# career ranks fire on the final season only
# --------------------------------------------------------------------------


PREVIOUS = "2025-26"  # the season before CURRENT


def opener(player, season, salary, **kw):
    """A player's first season. career_earnings is a running total, so a first
    record reads exactly its own salary; anything else is the carried-in-total
    fault the engine now gates on."""
    kw.setdefault("career_earnings", salary)
    return rec(player, season, salary, **kw)


def _finished_field(last_season="2018-19", n=12):
    """A field of completed careers, all of them ended well before CURRENT so
    none of them is caught by the status-unknown rule."""
    return [
        opener("Done {}".format(i), last_season, 1000000 + i * 1000, team="BOS",
            salary_cap_pct=0.001, salary_rank_league=300 + i, salary_rank_team=10,
            draft_year=2016, draft_pick=55, college="Filler U",
            nationality="Fillerland")
        for i in range(n)
    ]


def test_career_rank_does_not_fire_on_a_mid_career_season():
    """career_earnings on a record is a running total. Ranking a 2022-23 running
    total against other players' finished careers is what produced Westbrook's
    "$338.8 million through 2022-23 ... passing Kevin Love's $280.4 million
    (2025-26)": two numbers from different points in time."""
    data = make_data(_finished_field() + [
        opener("Grinder", "2015-16", 20000000),
        rec("Grinder", "2016-17", 20000000, career_earnings=200000000),
        rec("Grinder", "2017-18", 20000000, career_earnings=220000000),
        rec("Grinder", "2018-19", 20000000, career_earnings=240000000),
    ])
    mid = facts(data, "Grinder", "2017-18", family="career_earnings", kind="sets")
    assert mid == []
    assert "not_paid_through_season" in gates(data, "Grinder", "2017-18")


def test_career_rank_fires_on_the_final_season():
    data = make_data(_finished_field() + [
        opener("Grinder", "2015-16", 20000000),
        rec("Grinder", "2016-17", 20000000, career_earnings=200000000),
        rec("Grinder", "2017-18", 20000000, career_earnings=220000000),
        rec("Grinder", "2018-19", 20000000, career_earnings=240000000),
    ])
    out = facts(data, "Grinder", "2018-19", family="career_earnings", kind="sets")
    assert len(out) == 1
    assert out[0]["value"] == 240000000
    assert "earned $240 million in his career" in out[0]["text"]


def test_cohort_career_rank_also_waits_for_the_final_season():
    peers = [
        opener("Peer {}".format(i), "2018-19", 1000000 + i, college="Kentucky", team="BOS")
        for i in range(15)
    ]
    data = make_data(peers + [
        opener("Grinder", "2015-16", 20000000, college="Kentucky"),
        rec("Grinder", "2017-18", 20000000, career_earnings=220000000, college="Kentucky"),
        rec("Grinder", "2018-19", 20000000, career_earnings=240000000, college="Kentucky"),
    ])
    mid = [f for f in facts(data, "Grinder", "2017-18", family="cohort")
           if f["key"].startswith("cohort_career|")]
    assert mid == []
    final = [f for f in facts(data, "Grinder", "2018-19", family="cohort")
             if f["key"].startswith("cohort_career|")]
    assert final


def test_milestones_still_fire_mid_career():
    """A milestone carries no comparison, so it is not anachronistic."""
    data = make_data(_finished_field() + [
        opener("Grinder", "2015-16", 20000000),
        rec("Grinder", "2017-18", 20000000, career_earnings=220000000),
        rec("Grinder", "2018-19", 20000000, career_earnings=240000000),
    ])
    out = facts(data, "Grinder", "2017-18", family="career_earnings", kind="milestone")
    assert out, "milestones are open to any season"


# --------------------------------------------------------------------------
# completed vs status unknown
# --------------------------------------------------------------------------


def test_last_season_before_the_current_one_is_status_unknown():
    """An unsigned free agent in September is not a retired player. Westbrook
    and Terry Rozier both last appear in the previous season with no current-
    season record, which under the old rule read as a finished career."""
    data = make_data([opener("Unsigned", PREVIOUS, 20000000)])
    idx = index_for(data)
    assert idx.career_complete("Unsigned") is False
    assert idx.career_status_unknown("Unsigned") is True


def test_two_clear_seasons_of_absence_completes_a_career():
    data = make_data([opener("Retired", "2024-25", 20000000)])
    idx = index_for(data)
    assert idx.career_complete("Retired") is True
    assert idx.career_status_unknown("Retired") is False


def test_a_player_who_may_still_be_active_ranks_on_what_he_has_been_paid():
    """Money paid is money paid. He ranks, worded as a total to date, because
    leaving him out is what put retired players alone at the top of a list
    active players lead."""
    data = make_data(_finished_field() + [
        opener("Unsigned", "2024-25", 20000000),
        rec("Unsigned", PREVIOUS, 20000000, career_earnings=900000000),
    ])
    out = facts(data, "Unsigned", PREVIOUS, family="career_earnings", kind="sets")
    assert len(out) == 1
    assert "has earned $900 million through {}".format(PREVIOUS) in out[0]["text"]
    assert "in his career" not in out[0]["text"]


def test_status_unknown_player_is_out_of_the_retired_negative_space_universe():
    """Kept in the to-date universe, so he is still rankable, but never described
    as a player who never made an All-Star team."""
    data = make_data(
        all_star_class("2018-19") + unselected_peers("2018-19")
        + all_star_class(PREVIOUS)
        + [opener("Unsigned", PREVIOUS, 50000000)]
    )
    idx = index_for(data)
    retired = {e["player"] for e in idx.u_no_all_star.entries}
    todate = {e["player"] for e in idx.u_no_all_star_todate.entries}
    assert "Unsigned" not in retired
    assert "Unsigned" in todate
    out = facts(data, "Unsigned", PREVIOUS, family="negative_space")
    assert out
    assert all("never made an" not in f["text"] for f in out)
    all_star = [f for f in out if f["key"].startswith("no_all_star|")]
    assert len(all_star) == 1
    assert "without an All-Star selection" in all_star[0]["text"]
    assert "whether he is finished is unknown" in all_star[0]["scope_note"]


# --------------------------------------------------------------------------
# identity splits
# --------------------------------------------------------------------------


def _merged_pair():
    """One key covering a 1990s player and a 2010s one, the Jaren Jackson shape."""
    return [
        rec("Merged Name", "1995-96", 30000000, team="OKC", age=30),
        rec("Merged Name", "2018-19", 5000000, team="OKC", age=19),
    ]


SPLIT_ENTRY = {
    "Merged Name": {
        "split": True,
        "confirmed": False,
        "people": [
            {"display_name": "Merged Name", "first_season": "1995-96", "last_season": "1995-96"},
            {"display_name": "Merged Name Jr", "first_season": "2018-19", "last_season": "2018-19"},
        ],
    }
}


def test_unconfirmed_split_suppresses_the_earlier_segment():
    data = make_data(tail("OKC") + _merged_pair())
    assert facts(data, "Merged Name", "1995-96", splits=SPLIT_ENTRY) == []
    assert "identity_split_unconfirmed" in gates(
        data, "Merged Name", "1995-96", splits=SPLIT_ENTRY
    )


def test_unconfirmed_split_leaves_the_last_segment_alone():
    data = make_data(tail("OKC") + _merged_pair())
    idx = index_for(data, SPLIT_ENTRY)
    assert ("Merged Name", "2018-19") not in idx.split_suppressed
    assert ("Merged Name", "1995-96") in idx.split_suppressed


def test_unconfirmed_split_is_never_a_previous_holder():
    """The suppressed season cannot turn up inside someone else's sentence."""
    data = make_data(tail("OKC") + _merged_pair() + [
        rec("Challenger", "2023-24", 25000000, team="OKC"),
    ])
    plain = facts(data, "Challenger", "2023-24", family="franchise")
    assert plain and plain[0]["previous_holder"]["player"] == "Merged Name"
    assert plain[0]["previous_holder"]["season"] == "1995-96"
    split = facts(data, "Challenger", "2023-24", family="franchise", splits=SPLIT_ENTRY)
    assert split
    holder = split[0]["previous_holder"]
    # the 1995-96 season is gone from the universe; the later segment, whose name
    # is not in doubt, is still quotable
    assert (holder["player"], holder["season"]) != ("Merged Name", "1995-96")


def test_confirming_a_split_restores_the_earlier_segment():
    confirmed = {"Merged Name": dict(SPLIT_ENTRY["Merged Name"], confirmed=True)}
    data = make_data(tail("OKC") + _merged_pair())
    idx = index_for(data, confirmed)
    assert idx.split_suppressed == set()


def test_a_confirmed_comeback_lifts_the_career_level_exclusion():
    """A gap that is one man's spell abroad is not a merged identity, so once it
    is confirmed the career-level gate has no premise left."""
    entry = {
        "Merged Name": {
            "split": False,
            "confirmed": True,
            "people": [{"display_name": "Merged Name",
                        "first_season": "1995-96", "last_season": "2018-19"}],
        }
    }
    data = make_data(tail("OKC") + _merged_pair())
    assert "Merged Name" in index_for(data).identity_suspect
    assert "Merged Name" not in index_for(data, entry).identity_suspect


def test_an_unconfirmed_comeback_suppresses_nothing():
    entry = {
        "Merged Name": {
            "split": False,
            "confirmed": False,
            "people": [{"display_name": "Merged Name",
                        "first_season": "1995-96", "last_season": "2018-19"}],
        }
    }
    data = make_data(tail("OKC") + _merged_pair())
    idx = index_for(data, entry)
    assert idx.split_suppressed == set()
    assert "Merged Name" in idx.identity_suspect


def test_missing_identity_splits_file_is_not_an_error():
    assert F.load_identity_splits(os.path.join(REPO, "data", "no-such-file.json")) == {}


@pytest.mark.skipif(
    not os.path.exists(os.path.join(REPO, "data", "identity_splits.json")),
    reason="data/identity_splits.json not present",
)
def test_shipped_identity_splits_cover_every_flagged_name():
    with open(REAL_DATA, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    # built with no splits file, so identity_suspect is the raw gap flag
    idx = build_index(data, franchises=FRANCHISES, identity_splits={})
    entries = F.load_identity_splits()
    assert set(entries) == idx.identity_suspect
    for key, entry in entries.items():
        assert entry["evidence"], key
        if entry["split"]:
            assert len(entry["people"]) >= 2, key
        else:
            assert len(entry["people"]) == 1, key


# --------------------------------------------------------------------------
# awards audit: the 1998-99 lockout
# --------------------------------------------------------------------------


def test_1998_99_is_not_flagged_for_having_no_all_stars():
    """The lockout cancelled the 1999 game, so zero selections is the correct
    answer rather than a scrape that came back empty."""
    data = make_data(
        [rec("Nobody", "1998-99", 1000000)]
        + [rec("AllNBA {}".format(i), "1998-99", 2000000 + i,
               awards=["All-NBA First Team"]) for i in range(15)]
    )
    idx = index_for(data)
    assert idx.all_star_counts["1998-99"] == 0
    assert "1998-99" not in idx.awards_unsafe_seasons


def test_a_missing_all_star_list_is_still_flagged_in_an_ordinary_season():
    data = make_data(
        [rec("Nobody", "2001-02", 1000000)]
        + [rec("AllNBA {}".format(i), "2001-02", 2000000 + i,
               awards=["All-NBA First Team"]) for i in range(15)]
    )
    assert "2001-02" in index_for(data).awards_unsafe_seasons


def test_1998_99_still_fails_on_a_bad_all_nba_count():
    """The lockout exempts the All-Star count only."""
    data = make_data([
        rec("AllNBA {}".format(i), "1998-99", 2000000 + i, awards=["All-NBA First Team"])
        for i in range(9)
    ])
    assert "1998-99" in index_for(data).awards_unsafe_seasons


@pytest.mark.skipif(not os.path.exists(REAL_DATA), reason="data/data.json not present")
def test_real_awards_flags_keep_the_29_selection_seasons_and_free_1998_99():
    with open(REAL_DATA, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    idx = build_index(data, franchises=FRANCHISES, identity_splits={})
    assert "1998-99" not in idx.awards_unsafe_seasons
    assert idx.all_star_counts["1998-99"] == 0
    # 29 selections each, above the band, and not checked against the published
    # rosters, so both stay flagged
    assert {"1996-97", "2006-07"} <= idx.awards_unsafe_seasons
    # every flagged season is flagged for a stated reason, never by accident
    for season in idx.awards_unsafe_seasons:
        n_as = idx.all_star_counts[season]
        n_nba = idx.all_nba_counts[season]
        out_of_band = not (F.ALL_STAR_COUNT_MIN <= n_as <= F.ALL_STAR_COUNT_MAX)
        assert out_of_band or n_nba != F.ALL_NBA_COUNT_EXPECTED, season


# --------------------------------------------------------------------------
# positions collapse to guard / forward / center
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code,expected",
    [
        ("G", ("G", "guard")),
        ("F", ("F", "forward")),
        ("C", ("C", "center")),
        ("G-F", ("G", "guard")),
        ("F-G", ("F", "forward")),
        ("F-C", ("F", "forward")),
        ("C-F", ("C", "center")),
        ("", (None, None)),
        (None, (None, None)),
        ("X", (None, None)),
    ],
)
def test_position_group_collapses_to_three(code, expected):
    assert F.position_group(code) == expected


def test_hyphenated_positions_share_one_cohort():
    data = make_data(
        [rec("Wing {}".format(i), "2019-20", 1000000 + i, pos="F-C")
         for i in range(10)]
        + [rec("Big", "2019-20", 40000000, pos="F")]
    )
    idx = index_for(data)
    keys = {k for k in idx.u_cohort_season if k[0] == "position"}
    assert keys == {("position", "F"), ("position", "G")}  # G comes from the filler
    out = [f for f in facts(data, "Big", "2019-20", family="cohort")
           if f["key"].startswith("cohort_season|position|")]
    assert len(out) == 1
    assert "by a forward" in out[0]["text"]
    assert "forward-center" not in out[0]["text"]


# --------------------------------------------------------------------------
# awards flags are per award, not per season
# --------------------------------------------------------------------------


def test_a_short_all_nba_list_does_not_block_all_star_claims():
    """2025-26 carries 27 All-Stars and 14 All-NBA. Only the All-NBA claim is
    in doubt, so a career that touched that season can still be ranked among
    players with no All-Star selection."""
    season = "2024-25"
    data = make_data(
        all_star_class(season, n_all_star=26, n_all_nba=14)
        + unselected_peers(season)
        + [rec("Unselected", season, 40000000)]
    )
    idx = index_for(data)
    assert season not in idx.all_star_unsafe_seasons
    assert season in idx.all_nba_unsafe_seasons

    out = facts(data, "Unselected", season, family="negative_space")
    kinds = {f["key"].split("|")[0] for f in out}
    assert "no_all_star" in kinds
    assert "no_all_nba" not in kinds
    dropped = gates(data, "Unselected", season)
    assert "awards_season_unsafe" in dropped


def test_a_bad_all_star_count_still_blocks_all_star_claims():
    """The mirror image: too few All-Stars, a sound All-NBA list."""
    season = "2024-25"
    all_nba_only = [
        rec("AllNBA {} {}".format(season, i), season, 2000000 + i, team="BOS",
            awards=["All-NBA First Team"], salary_cap_pct=0.002,
            salary_rank_league=180 + i, draft_year=2016, draft_pick=50,
            college="Filler U", nationality="Fillerland")
        for i in range(ALL_NBA_FILL)
    ]
    data = make_data(
        all_star_class(season, n_all_star=3, n_all_nba=0)
        + all_nba_only
        + unselected_peers(season)
        + [rec("Unselected", season, 40000000)]
    )
    idx = index_for(data)
    assert season in idx.all_star_unsafe_seasons
    assert season not in idx.all_nba_unsafe_seasons
    kinds = {f["key"].split("|")[0]
             for f in facts(data, "Unselected", season, family="negative_space")}
    assert "no_all_star" not in kinds
    assert "no_all_nba" in kinds


def test_the_union_is_still_exposed_under_the_old_name():
    season = "2024-25"
    data = make_data(all_star_class(season, n_all_star=3, n_all_nba=14)
                     + [rec("Guy", season, 1000)])
    idx = index_for(data)
    assert idx.awards_unsafe_seasons == (
        idx.all_star_unsafe_seasons | idx.all_nba_unsafe_seasons
    )
    assert season in idx.awards_unsafe_seasons


# --------------------------------------------------------------------------
# confirmed splits: two men, two names
# --------------------------------------------------------------------------


CONFIRMED_SPLIT = {
    "Merged Name": dict(SPLIT_ENTRY["Merged Name"], confirmed=True),
}


def test_confirmed_split_prints_each_segment_its_own_name():
    data = make_data(tail("OKC") + _merged_pair())
    out = facts(data, "Merged Name", "1995-96", family="franchise", splits=CONFIRMED_SPLIT)
    assert out
    assert out[0]["text"].startswith("Merged Name's")
    later = facts(data, "Merged Name", "2018-19", splits=CONFIRMED_SPLIT)
    assert all("Merged Name Jr" in f["text"] or "Merged Name Jr" not in f["text"]
               for f in later)
    idx = index_for(data, CONFIRMED_SPLIT)
    assert idx.display_name("Merged Name", "2018-19") == "Merged Name Jr"
    assert idx.display_name("Merged Name", "1995-96") == "Merged Name"


def test_confirmed_split_never_says_his_own_mark_across_the_two_men():
    """The later segment passing the earlier one is passing someone else."""
    data = make_data(tail("OKC") + [
        rec("Merged Name", "1995-96", 30000000, team="OKC", age=30),
        rec("Merged Name", "2018-19", 40000000, team="OKC", age=19),
    ])
    plain = facts(data, "Merged Name", "2018-19", family="franchise")
    assert "his own" in plain[0]["text"]
    split = facts(data, "Merged Name", "2018-19", family="franchise",
                  splits=CONFIRMED_SPLIT)
    assert "his own" not in split[0]["text"]
    assert "Merged Name's $30 million (1995-96)" in split[0]["text"]


def test_a_confirmed_split_segment_with_no_name_stays_held_back():
    """Knowing there are two men does not make the unnamed one nameable."""
    unnamed = {
        "Merged Name": {
            "split": True,
            "confirmed": True,
            "people": [
                {"display_name": None, "first_season": "1995-96", "last_season": "1995-96"},
                {"display_name": "Merged Name", "first_season": "2018-19", "last_season": "2018-19"},
            ],
        }
    }
    data = make_data(tail("OKC") + _merged_pair())
    assert facts(data, "Merged Name", "1995-96", splits=unnamed) == []
    assert "identity_split_unnamed" in gates(data, "Merged Name", "1995-96", splits=unnamed)
    assert facts(data, "Merged Name", "2018-19", splits=unnamed) != []


def test_a_confirmed_split_is_still_shut_out_of_career_claims():
    """career_earnings on a merged record sums two men, whoever they are."""
    data = make_data(_finished_field() + _merged_pair())
    idx = index_for(data, CONFIRMED_SPLIT)
    assert "Merged Name" in idx.identity_suspect
    assert idx.career_eligible("Merged Name") is False


def test_confirmed_split_segments_have_separate_careers_for_rank_shifts():
    """"first time in his career" counts only this man's seasons."""
    data = make_data([
        rec("Merged Name", "1995-96", 90000000, team="OKC", age=30, salary_rank_league=1),
        rec("Merged Name", "2018-19", 90000000, team="OKC", age=19, salary_rank_league=1),
    ] + tail("OKC", season="2018-19"))
    plain = facts(data, "Merged Name", "2018-19", family="rank_shift")
    assert plain == [], "the 1995-96 No. 1 season blocks it when both are one man"
    split = facts(data, "Merged Name", "2018-19", family="rank_shift",
                  splits=CONFIRMED_SPLIT)
    assert any(f["key"].startswith("rank_league_first|") for f in split)


# --------------------------------------------------------------------------
# an unconfirmed gap stays out of career-level claims
# --------------------------------------------------------------------------


def test_unconfirmed_comeback_gets_no_career_rank():
    """A long gap under one name is not proof of one person, so the career
    total is not claimed until someone says it is one man."""
    entry = {
        "Comeback": {
            "split": False, "confirmed": False,
            "people": [{"display_name": "Comeback",
                        "first_season": "2005-06", "last_season": "2018-19"}],
        }
    }
    data = make_data(_finished_field() + [
        opener("Comeback", "2005-06", 20000000),
        rec("Comeback", "2018-19", 20000000, career_earnings=900000000),
    ])
    assert facts(data, "Comeback", "2018-19", family="career_earnings", splits=entry) == []
    assert "merged_identity" in gates(data, "Comeback", "2018-19", splits=entry)


def test_confirming_a_comeback_lets_it_rank_as_one_career():
    entry = {
        "Comeback": {
            "split": False, "confirmed": True,
            "people": [{"display_name": "Comeback",
                        "first_season": "2005-06", "last_season": "2018-19"}],
        }
    }
    data = make_data(_finished_field() + [
        opener("Comeback", "2005-06", 20000000),
        rec("Comeback", "2018-19", 20000000, career_earnings=900000000),
    ])
    out = facts(data, "Comeback", "2018-19", family="career_earnings", kind="sets",
                splits=entry)
    assert len(out) == 1
    assert out[0]["value"] == 900000000


@pytest.mark.skipif(
    not os.path.exists(os.path.join(REPO, "data", "identity_splits.json")),
    reason="data/identity_splits.json not present",
)
def test_only_the_confirmed_comeback_ranks_as_one_career():
    """A gap stays a career-level exclusion until a human confirms the entry.
    PJ Tucker is the one that has been confirmed."""
    with open(REAL_DATA, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    entries = F.load_identity_splits()
    comebacks = [k for k, v in entries.items() if not v["split"]]
    assert len(comebacks) == 38
    confirmed = {k for k in comebacks if entries[k]["confirmed"]}
    assert confirmed == {"PJ Tucker"}
    idx = build_index(data, franchises=FRANCHISES)
    assert "PJ Tucker" not in idx.identity_suspect
    assert idx.career_eligible("PJ Tucker") is True
    for key in set(comebacks) - confirmed:
        assert key in idx.identity_suspect, key
        assert idx.career_eligible(key) is False, key


@pytest.mark.skipif(
    not os.path.exists(os.path.join(REPO, "data", "identity_splits.json")),
    reason="data/identity_splits.json not present",
)
def test_shipped_splits_are_confirmed_and_only_the_unnamed_one_is_held_back():
    with open(REAL_DATA, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    entries = F.load_identity_splits()
    splits = {k: v for k, v in entries.items() if v["split"]}
    assert set(splits) == {
        "Brandon Williams", "Chris Smith", "Corey Brewer",
        "Gerald Henderson", "Jaren Jackson Jr",
    }
    assert all(v["confirmed"] is True for v in splits.values())
    idx = build_index(data, franchises=FRANCHISES)
    # one man, in one season, whose name nobody has
    assert idx.split_suppressed == {("Corey Brewer", "1999-00")}
    assert idx.display_name("Jaren Jackson Jr", "1997-98") == "Jaren Jackson"
    assert idx.display_name("Jaren Jackson Jr", "2026-27") == "Jaren Jackson Jr"
    assert idx.display_name("Gerald Henderson", "1990-91") == "Gerald Henderson"
    assert idx.display_name("Gerald Henderson", "2015-16") == "Gerald Henderson Jr"
    # a confirmed split is still two men, so still no career claims
    for key in splits:
        assert idx.career_eligible(key) is False, key


# --------------------------------------------------------------------------
# a son's draft metadata taints every identity cohort
# --------------------------------------------------------------------------


def _son_metadata_field():
    """A father whose record carries his son's draft year, and a cohort of
    genuine Kentucky players for him to be compared against."""
    peers = [
        rec("Peer {}".format(i), "2019-20", 1000000 + i, college="Kentucky",
            nationality="Spain", pos="C", draft_year=2016, draft_pick=20)
        for i in range(15)
    ]
    father = rec("Father Name", "1995-96", 40000000, college="Kentucky",
                 nationality="Spain", pos="C", draft_year=2014, draft_pick=3)
    return peers + [father]


def test_a_son_s_draft_metadata_blocks_every_cohort():
    data = make_data(_son_metadata_field())
    idx = index_for(data)
    assert "Father Name" in idx.draft_meta_suspect
    assert facts(data, "Father Name", "1995-96", family="cohort") == []
    assert "draft_metadata_suspect" in gates(data, "Father Name", "1995-96")


def test_a_son_s_draft_metadata_keeps_him_out_of_other_players_cohorts():
    """He is not in the comparison set either, so nobody is measured against a
    college, country or position that is not his."""
    data = make_data(_son_metadata_field() + [
        rec("Challenger", "2019-20", 20000000, college="Kentucky",
            nationality="Spain", pos="C", draft_year=2016, draft_pick=20),
    ])
    idx = index_for(data)
    for key in (("college", "Kentucky"), ("nationality", "Spain"), ("position", "C")):
        universe = idx.u_cohort_season.get(key)
        assert universe is not None, key
        assert "Father Name" not in {e["player"] for e in universe.entries}, key
    out = facts(data, "Challenger", "2019-20", family="cohort")
    assert out
    assert all(f["type"] == "sets" for f in out)
    assert all("Father Name" not in f["text"] for f in out)


def test_a_clean_record_still_gets_all_five_cohorts():
    data = make_data([
        rec("Peer {}".format(i), "2019-20", 1000000 + i, college="Kentucky",
            nationality="Spain", pos="C", draft_year=2016, draft_pick=20)
        for i in range(15)
    ] + [
        rec("Clean", "2019-20", 40000000, college="Kentucky", nationality="Spain",
            pos="C", draft_year=2016, draft_pick=20),
    ])
    idx = index_for(data)
    assert "Clean" not in idx.draft_meta_suspect
    kinds = {k for k, _c, _l in F._cohorts_for(idx.record("Clean", "2019-20"), idx)}
    assert kinds == {"draft_class", "draft_slot", "college", "nationality", "position"}


@pytest.mark.skipif(not os.path.exists(REAL_DATA), reason="data/data.json not present")
def test_only_the_vouched_for_suspects_are_in_a_cohort_universe():
    """The eleven are out by default. The three whose confirmed split says which
    segment owns the metadata are back in, for that segment only."""
    with open(REAL_DATA, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    idx = build_index(data, franchises=FRANCHISES)
    assert len(idx.draft_meta_suspect) == 11
    entries = [e for u in idx.u_cohort_season.values() for e in u.entries]
    entries += [e for u in idx.u_cohort_career.values() for e in u.entries]
    members = {e["player"] for e in entries}
    vouched = {"Brandon Williams", "Corey Brewer", "Jaren Jackson Jr"}
    assert members & idx.draft_meta_suspect == vouched
    # and only for the seasons their split vouches for
    for entry in entries:
        if entry["player"] in vouched and entry["season"] is not None:
            assert idx.cohorts_allowed(entry["player"], entry["season"]), entry


# --------------------------------------------------------------------------
# a confirmed split gets its later segment's cohorts back
# --------------------------------------------------------------------------


def _merged_with_metadata():
    """One key covering a 1990s player and a 2010s one, where the key's draft
    fields and college belong to the younger man."""
    peers = [
        rec("Peer {}".format(i), "2019-20", 1000000 + i, college="Kentucky",
            draft_year=2018, draft_pick=4, pos="C", nationality="Spain")
        for i in range(15)
    ]
    return peers + [
        rec("Merged Name", "1995-96", 30000000, team="OKC", age=30,
            college="Kentucky", draft_year=2018, draft_pick=4, pos="C",
            nationality="Spain"),
        rec("Merged Name", "2018-19", 40000000, team="OKC", age=19,
            college="Kentucky", draft_year=2018, draft_pick=4, pos="C",
            nationality="Spain"),
    ]


METADATA_SPLIT = {
    "Merged Name": {
        "split": True,
        "confirmed": True,
        "people": [
            {"display_name": "Merged Name", "first_season": "1995-96",
             "last_season": "1995-96", "draft_year": None, "draft_pick": None,
             "college": "Georgetown"},
            {"display_name": "Merged Name Jr", "first_season": "2018-19",
             "last_season": "2018-19", "draft_year": 2018, "draft_pick": 4,
             "college": "Kentucky"},
        ],
    }
}


def test_confirmed_split_gives_the_matching_segment_its_cohorts_back():
    data = make_data(_merged_with_metadata())
    idx = index_for(data, METADATA_SPLIT)
    assert "Merged Name" in idx.draft_meta_suspect
    assert idx.cohorts_allowed("Merged Name", "2018-19") is True
    kinds = {k for k, _c, _l in F._cohorts_for(idx.record("Merged Name", "2018-19"), idx)}
    assert kinds == {"draft_class", "draft_slot", "college", "nationality", "position"}
    out = facts(data, "Merged Name", "2018-19", family="cohort", splits=METADATA_SPLIT)
    assert out


def test_the_earlier_segment_stays_out_of_those_cohorts():
    data = make_data(_merged_with_metadata())
    idx = index_for(data, METADATA_SPLIT)
    assert idx.cohorts_allowed("Merged Name", "1995-96") is False
    assert F._cohorts_for(idx.record("Merged Name", "1995-96"), idx) == []
    assert facts(data, "Merged Name", "1995-96", family="cohort",
                 splits=METADATA_SPLIT) == []
    # and the $30m 1995-96 season is not in anyone's comparison set
    universe = idx.u_cohort_season[("college", "Kentucky")]
    assert ("Merged Name", "1995-96") not in {e["key"] for e in universe.entries}
    assert ("Merged Name", "2018-19") in {e["key"] for e in universe.entries}


def test_a_segment_whose_metadata_disagrees_gets_nothing_back():
    """Matching is on the key's own draft year, pick and college. A segment that
    disagrees with them is not the man the metadata describes."""
    mismatch = {
        "Merged Name": dict(
            METADATA_SPLIT["Merged Name"],
            people=[
                dict(METADATA_SPLIT["Merged Name"]["people"][0]),
                dict(METADATA_SPLIT["Merged Name"]["people"][1], draft_pick=9),
            ],
        )
    }
    data = make_data(_merged_with_metadata())
    idx = index_for(data, mismatch)
    assert idx.cohorts_allowed("Merged Name", "2018-19") is False


def test_an_unconfirmed_split_gets_no_cohorts_back():
    unconfirmed = {
        "Merged Name": dict(METADATA_SPLIT["Merged Name"], confirmed=False),
    }
    data = make_data(_merged_with_metadata())
    idx = index_for(data, unconfirmed)
    assert idx.cohorts_allowed("Merged Name", "2018-19") is False


@pytest.mark.skipif(not os.path.exists(REAL_DATA), reason="data/data.json not present")
def test_jaren_jackson_jr_is_back_in_michigan_state():
    with open(REAL_DATA, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    idx = build_index(data, franchises=FRANCHISES)
    assert idx.cohorts_allowed("Jaren Jackson Jr", "2026-27") is True
    assert idx.cohorts_allowed("Jaren Jackson Jr", "1997-98") is False
    kinds = dict(
        (k, c) for k, c, _l in F._cohorts_for(idx.record("Jaren Jackson Jr", "2026-27"), idx)
    )
    assert kinds["college"] == "Michigan St"
    assert kinds["draft_slot"] == "4"
    assert kinds["draft_class"] == "2018"
    # the other eight suspects, with no split to vouch for them, stay out
    for player in ("Glen Rice", "Gary Payton", "Tim Hardaway"):
        assert idx.cohorts_allowed(player, idx.by_player[player][0]["season"]) is False


# --------------------------------------------------------------------------
# college display names
# --------------------------------------------------------------------------


def test_college_display_spells_out_the_truncation():
    names = {"Michigan St": "Michigan State"}
    data = make_data([
        rec("Peer {}".format(i), "2019-20", 1000000 + i, college="Michigan St")
        for i in range(15)
    ] + [rec("Big", "2019-20", 40000000, college="Michigan St")])
    idx = build_index(data, franchises=FRANCHISES, identity_splits={}, college_names=names)
    out = [f for f in factoids_for(data, "Big", "2019-20", index=idx)
           if f["key"].startswith("cohort_season|college|")]
    assert len(out) == 1
    assert "out of Michigan State" in out[0]["text"]
    # the key keeps the raw value, so ids and matching do not move
    assert out[0]["key"] == "cohort_season|college|Michigan St|Big|2019-20"


def test_a_college_with_no_entry_prints_as_stored():
    data = make_data([
        rec("Peer {}".format(i), "2019-20", 1000000 + i, college="Duke")
        for i in range(15)
    ] + [rec("Big", "2019-20", 40000000, college="Duke")])
    idx = build_index(data, franchises=FRANCHISES, identity_splits={}, college_names={})
    out = [f for f in factoids_for(data, "Big", "2019-20", index=idx)
           if f["key"].startswith("cohort_season|college|")]
    assert "out of Duke" in out[0]["text"]


def test_missing_college_names_file_is_not_an_error():
    assert F.load_college_names(os.path.join(REPO, "data", "no-such-file.json")) == {}


@pytest.mark.skipif(
    not os.path.exists(os.path.join(REPO, "data", "college_names.json")),
    reason="data/college_names.json not present",
)
def test_shipped_college_names_only_change_the_printed_text():
    mapping = F.load_college_names()
    assert mapping["Michigan St"] == "Michigan State"
    assert mapping["Oklahoma St"] == "Oklahoma State"
    assert mapping["Arizona St"] == "Arizona State"
    # "St." with a full stop is Saint and is left alone
    assert "St. John's" not in mapping
    assert "St. Bonaventure" not in mapping
    # the acronyms people search for are left alone
    for acronym in ("LSU", "UCLA", "USC", "UNLV", "BYU", "TCU", "VCU", "UCF"):
        assert acronym not in mapping, acronym
    with open(REAL_DATA, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    known = {(r.get("college") or "").strip() for r in data["seasons"]}
    assert set(mapping) <= known, sorted(set(mapping) - known)


# --------------------------------------------------------------------------
# milestone tense while the current season is being played
# --------------------------------------------------------------------------


def test_a_milestone_in_the_current_season_reads_will_pass():
    data = make_data([
        opener("Earner", PREVIOUS, 60000000),
        rec("Earner", CURRENT, 60000000, career_earnings=120000000),
    ])
    idx = index_for(data)
    assert idx.current_season_in_progress is True
    out = facts(data, "Earner", CURRENT, family="career_earnings", kind="milestone")
    assert out
    assert out[0]["text"].startswith("Earner will pass $100 million")
    assert "is being played" in out[0]["scope_note"]


def test_a_finished_current_season_goes_back_to_passed():
    """The tense flips on its own once the season's selections are on record."""
    data = make_data(all_star_class(CURRENT) + [
        opener("Earner", PREVIOUS, 60000000),
        rec("Earner", CURRENT, 60000000, career_earnings=120000000),
    ])
    idx = index_for(data)
    assert idx.current_season_in_progress is False
    out = facts(data, "Earner", CURRENT, family="career_earnings", kind="milestone")
    assert out[0]["text"].startswith("Earner passed $100 million")


def test_a_past_season_milestone_is_unchanged():
    data = make_data([
        opener("Earner", "2017-18", 60000000),
        rec("Earner", "2018-19", 60000000, career_earnings=120000000),
        rec("Earner", "2019-20", 60000000, career_earnings=180000000),
    ])
    out = facts(data, "Earner", "2018-19", family="career_earnings", kind="milestone")
    assert out[0]["text"] == "Earner passed $100 million in career earnings in 2018-19."


def test_a_contracted_milestone_keeps_its_own_wording():
    data = make_data([
        opener("Earner", CURRENT, 60000000),
        rec("Earner", CONTRACTED, 60000000, career_earnings=120000000),
    ])
    out = facts(data, "Earner", CONTRACTED, family="career_earnings", kind="milestone")
    assert "is on track to pass $100 million" in out[0]["text"]
    assert "if his contract is paid in full" in out[0]["text"]


# --------------------------------------------------------------------------
# a career total that started under someone else's name
# --------------------------------------------------------------------------


def test_a_carried_in_career_total_is_out_of_career_claims():
    """career_earnings is a running total, so a first record should read exactly
    the first salary. Glen Rice Jr's reads $67.2 million, his father's career."""
    data = make_data(_finished_field() + [
        rec("Son Name", "2013-14", 500000, career_earnings=67000000),
        rec("Son Name", "2014-15", 500000, career_earnings=67500000),
    ])
    idx = index_for(data)
    assert "Son Name" in idx.career_total_carried_in
    assert idx.career_eligible("Son Name") is False
    assert facts(data, "Son Name", "2014-15", family="career_earnings") == []
    assert "career_total_carried_in" in gates(data, "Son Name", "2014-15")


def test_a_carried_in_total_is_never_quoted_by_anyone_else():
    data = make_data(_finished_field() + [
        rec("Son Name", "2013-14", 500000, career_earnings=67000000, college="Kentucky"),
        rec("Son Name", "2014-15", 500000, career_earnings=67500000, college="Kentucky"),
    ] + [
        opener("Peer {}".format(i), "2018-19", 1000000 + i, college="Kentucky")
        for i in range(15)
    ] + [
        opener("Honest", "2018-19", 5000000, college="Kentucky"),
    ])
    idx = index_for(data)
    universe = idx.u_cohort_career.get(("college", "Kentucky"))
    assert "Son Name" not in {e["player"] for e in universe.entries}
    out = [f for f in facts(data, "Honest", "2018-19", family="cohort")
           if f["key"].startswith("cohort_career|")]
    assert out
    assert all("Son Name" not in f["text"] for f in out)


def test_a_clean_first_record_stays_eligible():
    data = make_data(_finished_field() + [
        rec("Clean Start", "2013-14", 500000, career_earnings=500000),
        rec("Clean Start", "2014-15", 500000, career_earnings=1000000),
    ])
    idx = index_for(data)
    assert "Clean Start" not in idx.career_total_carried_in
    assert idx.career_eligible("Clean Start") is True


@pytest.mark.skipif(not os.path.exists(REAL_DATA), reason="data/data.json not present")
def test_the_real_carried_in_totals_are_all_caught():
    with open(REAL_DATA, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    idx = build_index(data, franchises=FRANCHISES)
    assert "Glen Rice Jr" in idx.career_total_carried_in
    assert "Tim Hardaway Jr" in idx.career_total_carried_in
    for player in idx.career_total_carried_in:
        assert idx.career_eligible(player) is False, player
    members = {e["player"] for e in idx.u_career.entries}
    members |= {e["player"] for u in idx.u_cohort_career.values() for e in u.entries}
    assert not (members & idx.career_total_carried_in)


@pytest.mark.skipif(not os.path.exists(REAL_DATA), reason="data/data.json not present")
def test_pj_tucker_is_confirmed_and_ranks_as_one_career():
    with open(REAL_DATA, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    entries = F.load_identity_splits()
    assert entries["PJ Tucker"]["split"] is False
    assert entries["PJ Tucker"]["confirmed"] is True
    idx = build_index(data, franchises=FRANCHISES)
    assert "PJ Tucker" not in idx.identity_suspect
    assert idx.career_eligible("PJ Tucker") is True


# --------------------------------------------------------------------------
# rank shifts: either season being split sinks the claim
# --------------------------------------------------------------------------


def _high_earner_pair(prev_kw=None, now_kw=None):
    """A player who goes from second on the roster to first, plus a team-mate
    above him last season so the shift has somewhere to come from."""
    prev_kw = prev_kw or {}
    now_kw = now_kw or {}
    prev_kw.setdefault("team", "OKC")
    now_kw.setdefault("team", "OKC")
    return tail("OKC", season="2022-23") + [
        rec("Rich Guy", "2022-23", 30000000, team="OKC", salary_rank_team=1),
        rec("Climber", "2022-23", 20000000, salary_rank_team=2, **prev_kw),
        rec("Climber", "2023-24", 40000000, salary_rank_team=1, **now_kw),
    ]


def test_team_high_earner_shift_fires_on_two_clean_seasons():
    data = make_data(_high_earner_pair())
    out = facts(data, "Climber", "2023-24", family="rank_shift")
    assert [f["key"] for f in out] == ["team_high_becomes|Climber|2023-24"]
    assert "after ranking second on the roster in 2022-23" in out[0]["text"]


def test_a_split_current_season_sinks_the_shift():
    data = make_data(_high_earner_pair(
        now_kw={"team": "OKC, BOS", "team_salaries": {"OKC": 22000000, "BOS": 18000000}}
    ))
    assert facts(data, "Climber", "2023-24", family="rank_shift") == []
    assert "split_season" in gates(data, "Climber", "2023-24")


def test_a_split_previous_season_sinks_the_shift():
    """The season being compared against is where salary_rank_team comes from.
    On a split season that rank belongs to no single roster."""
    data = make_data(_high_earner_pair(
        prev_kw={"team": "OKC, BOS", "team_salaries": {"OKC": 12000000, "BOS": 8000000}}
    ))
    assert facts(data, "Climber", "2023-24", family="rank_shift") == []
    dropped = gates(data, "Climber", "2023-24")
    assert "split_season" in dropped
    # named for what it is, not as a franchise change
    assert "franchise_changed" not in dropped


def test_a_contracted_rank_shift_is_stated_conditionally():
    """Nobody has been paid for a future season, so the shift is a would-be."""
    data = make_data(tail("OKC", season=CURRENT) + [
        rec("Rich Guy", CURRENT, 38000000, team="OKC", salary_rank_team=1),
        rec("Climber", CURRENT, 30000000, team="OKC", salary_rank_team=2),
        rec("Climber", "2027-28", 40000000, team="OKC", salary_rank_team=1),
    ])
    out = facts(data, "Climber", "2027-28", family="rank_shift")
    assert out, "the shift itself should still fire"
    text = " ".join(f["text"] for f in out)
    assert " would " in text, text
    assert "Climber is the" not in text


def test_league_rank_shifts_survive_a_split_season():
    """A split season's salary is the whole season's money, so the league rank
    it earns is sound. Only the team rank has no roster to belong to."""
    data = make_data(tail("OKC", season="2023-24") + [
        rec("Topper", "2023-24", 90000000, team="OKC, BOS", salary_rank_league=1,
            team_salaries={"OKC": 50000000, "BOS": 40000000}),
        rec("Topper", "2022-23", 10000000, team="OKC", salary_rank_league=200),
    ])
    out = facts(data, "Topper", "2023-24", family="rank_shift")
    assert [f["key"] for f in out] == ["rank_league_first|Topper|2023-24"]


# --------------------------------------------------------------------------
# name-variant duplicates merge into one player
# --------------------------------------------------------------------------


ALIASES = {"Split Spelling": "Split Spelling Jr"}


def _two_spellings():
    """One career filed under two spellings, the Wendell Carter shape: the
    stray season fills a hole in the other key's run."""
    return [
        rec("Split Spelling Jr", "2019-20", 5000000, career_earnings=5000000),
        rec("Split Spelling Jr", "2020-21", 5000000, career_earnings=10000000),
        rec("Split Spelling", "2021-22", 5000000, career_earnings=15000000),
        rec("Split Spelling Jr", "2022-23", 5000000, career_earnings=20000000),
        rec("Split Spelling Jr", CURRENT, 5000000, career_earnings=25000000),
    ]


def index_with_aliases(data, aliases=None):
    return build_index(data, franchises=FRANCHISES, identity_splits={},
                       name_aliases=aliases or {})


def test_two_spellings_become_one_player():
    data = make_data(_two_spellings())
    idx = index_with_aliases(data, ALIASES)
    assert "Split Spelling" not in idx.by_player
    assert len(idx.by_player["Split Spelling Jr"]) == 5
    assert idx.final_season["Split Spelling Jr"] == CURRENT
    # the stray spelling is still findable by the name data.json stores
    assert idx.record("Split Spelling", "2021-22") is not None


def test_neither_half_looks_like_a_completed_career():
    data = make_data(_two_spellings())
    unmerged = index_with_aliases(data)
    # apart, the older spelling's run ends in 2021-22 and reads as finished
    assert unmerged.career_complete("Split Spelling") is True
    merged = index_with_aliases(data, ALIASES)
    assert merged.career_complete("Split Spelling Jr") is False
    assert "Split Spelling" not in {e["player"] for e in merged.u_career.entries}


def test_merging_clears_the_carried_in_career_total():
    """Apart, the newer spelling starts mid-career with the other half's running
    total already on it, which is the carried-in fault. Together it starts at
    its own first salary."""
    data = make_data([
        rec("Split Spelling", "2019-20", 5000000, career_earnings=5000000),
        rec("Split Spelling Jr", "2020-21", 5000000, career_earnings=10000000),
        rec("Split Spelling Jr", "2021-22", 5000000, career_earnings=15000000),
    ])
    apart = index_with_aliases(data)
    assert "Split Spelling Jr" in apart.career_total_carried_in
    together = index_with_aliases(data, {"Split Spelling": "Split Spelling Jr"})
    assert together.career_total_carried_in == set()


def test_a_merged_player_appears_once_in_a_comparison_set():
    data = make_data(_two_spellings() + tail("OKC", season="2019-20"))
    idx = index_with_aliases(data, ALIASES)
    names = [e["player"] for e in idx.u_franchise["OKC"].entries]
    assert "Split Spelling" not in names
    # one entry per season, all five under the one spelling
    assert names.count("Split Spelling Jr") == 5


def test_either_spelling_can_be_asked_for():
    data = make_data(_two_spellings() + tail("OKC", season="2019-20"))
    idx = index_with_aliases(data, ALIASES)
    a = factoids_for(data, "Split Spelling", "2021-22", index=idx)
    b = factoids_for(data, "Split Spelling Jr", "2021-22", index=idx)
    assert [f["key"] for f in a] == [f["key"] for f in b]
    assert all("Split Spelling Jr" in f["text"] for f in a if "Split Spelling" in f["text"])


def test_missing_name_aliases_file_is_not_an_error():
    assert F.load_name_aliases(os.path.join(REPO, "data", "no-such-file.json")) == {}


@pytest.mark.skipif(
    not os.path.exists(os.path.join(REPO, "data", "name_aliases.json")),
    reason="data/name_aliases.json not present",
)
def test_shipped_aliases_merge_the_six_pairs_and_leave_the_fathers_alone():
    with open(REAL_DATA, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    aliases = F.load_name_aliases()
    assert aliases["Wendell Carter"] == "Wendell Carter Jr"
    # six merged players. Terrence Shannon Jr contributes two alias spellings,
    # because the canonical is his real name and neither raw spelling is it.
    canonicals = set(aliases.values())
    assert len(canonicals) == 6
    assert len(aliases) == 7
    assert aliases["Terrence Shannon"] == "Terrence Shannon Jr"
    assert aliases["Terrence Shannon Jr."] == "Terrence Shannon Jr"
    # a genuine father and son share a name but never a career
    for father_son in ("Gary Payton II", "Glen Rice Jr", "Tim Hardaway Jr",
                       "Larry Nance Jr", "Ron Harper Jr", "Glenn Robinson III",
                       "Gary Trent Jr", "Larry Drew II", "Jameer Nelson Jr"):
        assert father_son not in aliases, father_son
        assert father_son not in aliases.values(), father_son
    idx = build_index(data, franchises=FRANCHISES)
    carter = idx.by_player["Wendell Carter Jr"]
    assert len(carter) == 11
    assert [r["season"] for r in carter] == sorted(
        (r["season"] for r in carter), key=season_key
    )
    # continuous, so 2024-25 is no longer a hole
    keys = [season_key(r["season"]) for r in carter]
    assert max(keys[i] - keys[i - 1] for i in range(1, len(keys))) == 1
    assert idx.career_eligible("Wendell Carter Jr") is True
    assert "Wendell Carter Jr" not in idx.career_total_carried_in
    assert "Wendell Carter" not in idx.by_player


# --------------------------------------------------------------------------
# career earnings now rank money already paid, active players included
# --------------------------------------------------------------------------


def test_an_active_player_leads_the_career_list_over_a_retired_one():
    """The bug this fixes: a list of completed careers only called Elton Brand
    the highest-earning Duke player while Kyrie Irving sat above him."""
    data = make_data(_finished_field() + [
        opener("Retired Man", "2018-19", 20000000, career_earnings=20000000),
        rec("Retired Man", "2019-20", 20000000, career_earnings=300000000),
        opener("Still Playing", "2024-25", 20000000),
        rec("Still Playing", "2025-26", 20000000, career_earnings=380000000),
        rec("Still Playing", CURRENT, 20000000, career_earnings=400000000),
        rec("Still Playing", "2027-28", 50000000, career_earnings=450000000),
    ])
    out = facts(data, "Still Playing", CURRENT, family="career_earnings", kind="sets")
    assert len(out) == 1
    assert "has earned $400 million through {}".format(CURRENT) in out[0]["text"]
    assert "more than anyone else in NBA history" in out[0]["text"]
    # the retired man is now second, not first
    retired = facts(data, "Retired Man", "2019-20", family="career_earnings")
    assert [f["type"] for f in retired if f["key"].startswith("career_rank")] == ["approaches"]


def test_contracted_money_never_counts_towards_a_career_total():
    data = make_data(_finished_field() + [
        opener("Still Playing", "2024-25", 20000000),
        rec("Still Playing", "2025-26", 20000000, career_earnings=380000000),
        rec("Still Playing", CURRENT, 20000000, career_earnings=400000000),
        rec("Still Playing", "2027-28", 50000000, career_earnings=450000000),
    ])
    idx = index_for(data)
    assert idx.paid_through("Still Playing") == (400000000, CURRENT)
    assert facts(data, "Still Playing", "2027-28", family="career_earnings",
                 kind="sets") == []


def test_a_career_that_began_before_the_window_suppresses_a_claim_beside_it():
    """His total on file is short of what he earned, so a figure within
    PRE_WINDOW_MARGIN of it cannot be ranked against him."""
    data = make_data(_finished_field() + [
        # drafted before the window opens, so what is on file is only the part
        # of his career the data covers
        opener("Old Timer", "1990-91", 20000000, draft_year=1985, draft_pick=3),
        rec("Old Timer", "1991-92", 20000000, career_earnings=200000000,
            draft_year=1985, draft_pick=3),
        opener("Modern Man", "2017-18", 20000000),
        rec("Modern Man", "2018-19", 20000000, career_earnings=210000000),
    ])
    idx = index_for(data)
    assert idx.pre_window_career("Old Timer") is True
    ranks = [
        f for f in facts(data, "Modern Man", "2018-19", family="career_earnings")
        if f["key"].startswith("career_rank")
    ]
    assert ranks == []  # milestones still fire: they compare him to nobody
    assert "pre_window_career_too_close" in gates(data, "Modern Man", "2018-19")


def test_a_figure_far_clear_of_every_pre_window_career_still_ranks():
    data = make_data(_finished_field() + [
        opener("Old Timer", "1990-91", 20000000, draft_year=1985, draft_pick=3),
        rec("Old Timer", "1991-92", 20000000, career_earnings=100000000,
            draft_year=1985, draft_pick=3),
        opener("Modern Man", "2017-18", 20000000),
        rec("Modern Man", "2018-19", 20000000, career_earnings=400000000),
    ])
    out = facts(data, "Modern Man", "2018-19", family="career_earnings", kind="sets")
    assert len(out) == 1
    assert "$400 million" in out[0]["text"]


def test_an_all_time_cap_share_is_only_claimed_for_the_top_three():
    """A season this data does not hold could displace anything deeper."""
    field = []
    for i in range(10):
        field += [rec("Sharer {}".format(i), "2019-20", 40000000 - i * 1000000,
                      salary_cap_pct=40 - i, team="BOS")]
    data = make_data(field + tail("BOS", season="2019-20"))
    fourth = facts(data, "Sharer 3", "2019-20", family="cap_context")
    keys = [f["key"].split("|")[0] for f in fourth]
    assert "cap_pct_all" not in keys, fourth
    assert "cap_pct_season" in keys
    top = facts(data, "Sharer 0", "2019-20", family="cap_context")
    assert "cap_pct_all" in [f["key"].split("|")[0] for f in top]


def test_a_pre_window_career_gets_no_milestone_either():
    """His running total starts partway through, so the season he crosses
    $100 million in this data is not the season he crossed it."""
    data = make_data(_finished_field() + [
        opener("Old Timer", "1990-91", 20000000, draft_year=1985, draft_pick=3),
        rec("Old Timer", "1991-92", 20000000, career_earnings=150000000,
            draft_year=1985, draft_pick=3),
    ])
    assert facts(data, "Old Timer", "1991-92", family="career_earnings") == []
    assert "pre_window_career" in gates(data, "Old Timer", "1991-92")


# --------------------------------------------------------------------------
# salaries the CBA does not allow
# --------------------------------------------------------------------------


def test_a_contracted_salary_towering_over_its_season_is_flagged():
    """One salary far clear of every other in its own season is a projection,
    not a deal anyone signed."""
    data = make_data([
        rec("Tower", "2027-28", 120000000, team="SAS", salary_cap_pct=70.0),
    ] + [
        rec("Peer {}".format(i), "2027-28", 40000000 + i * 1000, team="BOS")
        for i in range(10)
    ])
    idx = index_for(data)
    assert ("Tower", "2027-28") in idx.impossible
    assert "second-biggest salary" in idx.impossible[("Tower", "2027-28")]
    assert ("Peer 1", "2027-28") not in idx.impossible


def test_a_paid_season_is_never_flagged():
    """Kobe Bryant's 51.9% of the 2013-14 cap is money he was paid. The 35%
    maximum applies to a contract when it is signed, and raises carry the late
    years of a real deal past it."""
    data = make_data([
        rec("Kobe", "2013-14", 30500000, salary_cap_pct=51.9),
        rec("Kobe", "2014-15", 23500000, salary_cap_pct=37.3),
    ] + [rec("Peer {}".format(i), "2013-14", 1000000 + i) for i in range(10)])
    idx = index_for(data)
    assert idx.impossible == {}


def test_a_contracted_leap_off_a_big_salary_is_flagged():
    """Victor Wembanyama's $112.6 million in 2030-31 is the case: $51 million
    on the same roster the season before, itself a quarter of the cap."""
    data = make_data([
        rec("Leaper", "2029-30", 51000000, team="SAS", salary_cap_pct=26.6),
        rec("Leaper", "2030-31", 112600000, team="SAS", salary_cap_pct=55.8),
    ] + [
        rec("Peer {}".format(i), "2030-31", 95000000 + i * 1000, team="BOS")
        for i in range(10)
    ] + [
        rec("Other {}".format(i), "2029-30", 40000000 + i * 1000, team="BOS")
        for i in range(10)
    ], caps={s: {"cap": 192000000} for s in
             ["2029-30", "2030-31", CURRENT]})
    idx = index_for(data)
    assert ("Leaper", "2030-31") in idx.impossible
    assert "121% jump" in idx.impossible[("Leaper", "2030-31")]


def test_a_rookie_scale_leap_into_a_maximum_is_left_alone():
    """The jump rule only fires off a season that was already a big salary, so
    a rookie deal turning into a maximum extension stays legal."""
    data = make_data([
        rec("Rookie", CURRENT, 12000000, team="SAS", salary_cap_pct=8.0),
        rec("Rookie", "2027-28", 36000000, team="SAS", salary_cap_pct=23.0),
    ] + [
        rec("Peer {}".format(i), "2027-28", 34000000 + i * 1000, team="BOS")
        for i in range(10)
    ], caps={s: {"cap": 155000000} for s in [CURRENT, "2027-28"]})
    idx = index_for(data)
    assert idx.impossible == {}


def test_a_move_between_teams_is_not_a_raise():
    """A new team can pay whatever it likes; the jump rule reads one roster."""
    data = make_data([
        rec("Mover", CURRENT, 40000000, team="SAS", salary_cap_pct=26.0),
        rec("Mover", "2027-28", 60000000, team="BOS", salary_cap_pct=38.0),
    ] + [
        rec("Peer {}".format(i), "2027-28", 55000000 + i * 1000, team="BOS")
        for i in range(10)
    ], caps={s: {"cap": 155000000} for s in [CURRENT, "2027-28"]})
    idx = index_for(data)
    assert ("Mover", "2027-28") not in idx.impossible


def test_a_flagged_record_never_enters_a_comparison_set():
    data = make_data(_field() + [
        rec("Impossible", "2027-28", 120000000, salary_cap_pct=70.0),
    ] + [
        rec("Peer {}".format(i), "2027-28", 40000000 + i * 1000, team="BOS")
        for i in range(10)
    ])
    idx = index_for(data)
    assert ("Impossible", "2027-28") in idx.impossible
    for universe in idx.u_franchise.values():
        for entry in universe.entries:
            assert entry["player"] != "Impossible"
    for universe in idx.u_cap_pct_season.values():
        for entry in universe.entries:
            assert entry["player"] != "Impossible"
    assert factoids_for(data, "Impossible", "2027-28", index=idx) == []
