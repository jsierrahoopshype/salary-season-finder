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
    cap_map = {s: {"cap": 100000000} for s in season_set}
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


def test_mid_season_move_blocks_team_high_earner_shifts():
    data = make_data(tail("OKC") + [
        rec("Mover", "2022-23", 10000000, team="OKC", salary_rank_team=3),
        rec("Mover", "2023-24", 40000000, team="OKC, BOS",
            team_salaries={"OKC": 22000000, "BOS": 18000000}),
    ])
    assert "mid_season_move" in gates(data, "Mover", "2023-24")


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
    return [
        rec("Client {}".format(i), season, 5000000 + i * 1000, agent=agent)
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
        rec("Future", CURRENT, 20000000),
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
    data = make_data(_field() + [rec("Capless", CONTRACTED, 90000000)])
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


def test_comparison_claims_carry_the_scope():
    data = make_data(_field() + [rec("Challenger", "2023-24", 41000000)])
    out = factoids_for(data, "Challenger", "2023-24", index=index_for(data))
    comparisons = [f for f in out if f["type"] in {"sets", "ties", "approaches"}]
    assert comparisons
    for fact in comparisons:
        if fact["family"] in {"agent", "cap_context"}:
            continue  # scoped to one season, not to all time
        assert F.SCOPE_SUFFIX in fact["text"], fact["text"]


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


def _finished_field(last_season="2018-19", n=12):
    """A field of completed careers, all of them ended well before CURRENT so
    none of them is caught by the status-unknown rule."""
    return [
        rec("Done {}".format(i), last_season, 1000000 + i * 1000,
            career_earnings=10000000 + i * 100000, team="BOS",
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
        rec("Grinder", "2016-17", 20000000, career_earnings=200000000),
        rec("Grinder", "2017-18", 20000000, career_earnings=220000000),
        rec("Grinder", "2018-19", 20000000, career_earnings=240000000),
    ])
    mid = facts(data, "Grinder", "2017-18", family="career_earnings", kind="sets")
    assert mid == []
    assert "not_final_season" in gates(data, "Grinder", "2017-18")


def test_career_rank_fires_on_the_final_season():
    data = make_data(_finished_field() + [
        rec("Grinder", "2016-17", 20000000, career_earnings=200000000),
        rec("Grinder", "2017-18", 20000000, career_earnings=220000000),
        rec("Grinder", "2018-19", 20000000, career_earnings=240000000),
    ])
    out = facts(data, "Grinder", "2018-19", family="career_earnings", kind="sets")
    assert len(out) == 1
    assert out[0]["value"] == 240000000
    assert "through 2018-19" in out[0]["text"]


def test_cohort_career_rank_also_waits_for_the_final_season():
    peers = [
        rec("Peer {}".format(i), "2018-19", 1000000 + i,
            career_earnings=5000000 + i, college="Kentucky", team="BOS")
        for i in range(15)
    ]
    data = make_data(peers + [
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
    data = make_data([rec("Unsigned", PREVIOUS, 20000000, career_earnings=200000000)])
    idx = index_for(data)
    assert idx.career_complete("Unsigned") is False
    assert idx.career_status_unknown("Unsigned") is True


def test_two_clear_seasons_of_absence_completes_a_career():
    data = make_data([rec("Retired", "2024-25", 20000000, career_earnings=200000000)])
    idx = index_for(data)
    assert idx.career_complete("Retired") is True
    assert idx.career_status_unknown("Retired") is False


def test_status_unknown_player_gets_no_career_rank():
    data = make_data(_finished_field() + [
        rec("Unsigned", PREVIOUS, 20000000, career_earnings=900000000),
    ])
    assert facts(data, "Unsigned", PREVIOUS, family="career_earnings", kind="sets") == []
    assert "career_status_unknown" in gates(data, "Unsigned", PREVIOUS)


def test_status_unknown_player_is_out_of_the_retired_negative_space_universe():
    """Kept in the to-date universe, so he is still rankable, but never described
    as a player who never made an All-Star team."""
    data = make_data(
        all_star_class("2018-19") + unselected_peers("2018-19")
        + all_star_class(PREVIOUS)
        + [rec("Unsigned", PREVIOUS, 50000000, career_earnings=200000000)]
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
    idx = build_index(data, franchises=FRANCHISES, identity_splits={})
    entries = F.load_identity_splits()
    assert set(entries) == idx.identity_suspect
    for key, entry in entries.items():
        assert entry["confirmed"] is False, key
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
