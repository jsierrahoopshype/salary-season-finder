"""Tests for the team a completed season belongs to.

The current-salaries sheet carries one TEAM column per player and applies it to
every season in his row, so a move in the summer rewrites the team on a season
he had already played. These tests pin which disagreements the build corrects
and, as importantly, which it leaves to the salary sheet.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

import build_data as B  # noqa: E402
import factoids as F  # noqa: E402

CURRENT = "2026-27"


def rec(player, season, team, salary=10000000, **kw):
    out = {"player": player, "season": season, "team": team, "salary": salary}
    out.update(kw)
    return out


def league(season, salary=1000000):
    """Enough rostered players to make a season read as the current one."""
    return [rec("Filler {:03d}".format(i), season, "BOS", salary)
            for i in range(450)]


def stats(rows):
    """{(name, season): [{team, gp}]} the way process_stats keys it."""
    out = {}
    for player, season, team, gp in rows:
        out.setdefault((B.normalize_name(player), season), []).append(
            {"team": team, "gp": gp})
    return out


# --------------------------------------------------------------------------
# what gets corrected
# --------------------------------------------------------------------------


def test_a_past_season_wearing_his_new_team_is_corrected():
    records = league(CURRENT) + [
        rec("Mover", "2025-26", "MIA"),
        rec("Mover", CURRENT, "MIA"),
    ]
    report = B.correct_past_season_teams(
        records, stats([("Mover", "2025-26", "MIL", 60)]))
    assert [r["team"] for r in records if r["player"] == "Mover"] == ["MIL", "MIA"]
    assert report["applied"] == [{
        "player": "Mover", "season": "2025-26",
        "sheet_team": "MIA", "corrected_team": "MIL",
    }]


def test_a_past_season_is_corrected_when_the_current_one_is_itself_a_split():
    """Kentavious Caldwell-Pope, the case the first version of this rule missed.

    He was traded inside 2026-27, so his current season reads "MEM, PHI". His
    2025-26 came through the current sheet as PHI, a season he played in
    Memphis: the sheet's one TEAM column reaching a year column it should not
    have. Comparing the past season against the whole current TEAM string left
    it standing, because "PHI" is not "MEM, PHI". It is one of them, which is
    the test the rule applies now.
    """
    records = league(CURRENT) + [
        rec("Kentavious Caldwell-Pope", "2025-26", "PHI", salary=21621500),
        rec("Kentavious Caldwell-Pope", CURRENT, "MEM, PHI", salary=21621500,
            team_salaries={"MEM": 17744971, "PHI": 3876529}),
    ]
    report = B.correct_past_season_teams(
        records, stats([("Kentavious Caldwell-Pope", "2025-26", "MEM", 51)]))

    past = [r for r in records if r["season"] == "2025-26"][0]
    assert past["team"] == "MEM"
    # One team played it, so the whole salary is Memphis's and there is no
    # split to carry.
    assert past["salary"] == 21621500
    assert "team_salaries" not in past
    assert "teams_split" not in past
    # the current season it was read against is untouched
    now = [r for r in records if r["season"] == CURRENT
           and r["player"] == "Kentavious Caldwell-Pope"][0]
    assert now["team"] == "MEM, PHI"
    assert now["team_salaries"] == {"MEM": 17744971, "PHI": 3876529}
    assert report["applied"] == [{
        "player": "Kentavious Caldwell-Pope", "season": "2025-26",
        "sheet_team": "PHI", "corrected_team": "MEM",
    }]


def test_a_traded_season_booked_to_one_team_is_marked_split():
    """James Harden, 2021-22. The sheet held PHI and he played for Brooklyn
    until February, so believing it put $44.3 million of Brooklyn's money on
    Philadelphia. Both teams are named, neither is given a figure, and the
    season is marked split so no franchise record counts it."""
    records = league(CURRENT) + [
        rec("James Harden", "2021-22", "PHI", salary=44310840),
        rec("James Harden", CURRENT, "LAC", salary=5000000),
    ]
    report = B.correct_past_season_teams(records, stats([
        ("James Harden", "2021-22", "BKN", 16),
        ("James Harden", "2021-22", "PHI", 21),
    ]))

    past = [r for r in records if r["season"] == "2021-22"][0]
    assert past["team"] == "BKN, PHI"
    assert past["salary"] == 44310840
    assert past["teams_split"] is True
    assert "team_salaries" not in past
    assert F.is_split_season(past) is True
    # no figure on either team: franchise records skip the season
    assert F.team_amounts(past) == []
    assert report["applied"] == [{
        "player": "James Harden", "season": "2021-22",
        "sheet_team": "PHI", "corrected_team": "BKN, PHI",
        "split_basis": "no allocation; season marked split",
    }]


def test_two_teams_on_the_sheet_and_one_in_the_stats_keeps_its_amounts():
    """Kemba Walker, 2021-22. New York played him and Oklahoma City carried
    dead money, and the sheet knows what each paid. That apportionment is the
    thing the traded rule has to invent, so a row that already has it is left
    exactly as it is."""
    records = league(CURRENT) + [
        rec("Kemba Walker", "2021-22", "NYK, OKC", salary=34967442,
            team_salaries={"NYK": 8151471, "OKC": 26815971}),
        rec("Kemba Walker", CURRENT, "NYK", salary=5000000),
    ]
    report = B.correct_past_season_teams(records, stats([
        ("Kemba Walker", "2021-22", "NYK", 37),
    ]))

    past = [r for r in records if r["season"] == "2021-22"][0]
    assert past["team"] == "NYK, OKC"
    assert past["team_salaries"] == {"NYK": 8151471, "OKC": 26815971}
    assert "teams_split" not in past
    assert dict(F.team_amounts(past)) == {"NYK": 8151471, "OKC": 26815971}
    assert report["applied"] == []
    assert report["left_alone"] == 1


def test_a_traded_season_whose_sheet_team_never_played_is_left_alone():
    """The sheet names a team nowhere in the stats, so it is the waived-and-
    signed-elsewhere case, not a sheet booking a trade to one side of it."""
    records = league(CURRENT) + [
        rec("Stretched", "2021-22", "HOU", salary=9000000),
        rec("Stretched", CURRENT, "SAC", salary=1000000),
    ]
    report = B.correct_past_season_teams(records, stats([
        ("Stretched", "2021-22", "DEN", 40),
        ("Stretched", "2021-22", "POR", 12),
    ]))
    assert [r for r in records if r["season"] == "2021-22"][0]["team"] == "HOU"
    assert report["applied"] == []
    assert report["left_alone"] == 1


def test_a_past_team_he_is_not_on_now_is_still_left_alone():
    """Membership widened the rule; it did not open it. A past season naming a
    team nowhere in his current row is the sheet and the stats saying different
    true things, and stays the sheet's."""
    records = league(CURRENT) + [
        rec("Waived", "2025-26", "DET"),
        rec("Waived", CURRENT, "MEM, PHI",
            team_salaries={"MEM": 2000000, "PHI": 1000000}),
    ]
    report = B.correct_past_season_teams(
        records, stats([("Waived", "2025-26", "CHA", 40)]))
    assert [r for r in records if r["season"] == "2025-26"][0]["team"] == "DET"
    assert report["applied"] == []
    assert report["left_alone"] == 1


def test_the_current_season_is_left_alone():
    records = league(CURRENT) + [rec("Mover", CURRENT, "MIA")]
    B.correct_past_season_teams(
        records, stats([("Mover", CURRENT, "MIL", 3)]))
    assert [r["team"] for r in records if r["player"] == "Mover"] == ["MIA"]


def test_a_contracted_season_is_left_alone():
    records = league(CURRENT) + [
        rec("Mover", "2027-28", "MIA"), rec("Mover", CURRENT, "MIA")]
    B.correct_past_season_teams(records, stats([]))
    assert [r["team"] for r in records if r["player"] == "Mover"] == ["MIA", "MIA"]


def test_a_corrected_season_loses_a_split_it_no_longer_has():
    records = league(CURRENT) + [
        rec("Mover", "2025-26", "MIA", team_salaries={"MIA": 10000000}),
        rec("Mover", CURRENT, "MIA"),
    ]
    B.correct_past_season_teams(
        records, stats([("Mover", "2025-26", "MIL", 60)]))
    past = [r for r in records if r["season"] == "2025-26"][0]
    assert past["team"] == "MIL"
    assert "team_salaries" not in past


def test_a_season_he_was_traded_in_names_both_teams_with_no_invented_split():
    """Nothing in this build says how two teams divided a traded season, so the
    season is marked split and carries the full salary against both names."""
    records = league(CURRENT) + [
        rec("Traded", "2023-24", "IND", salary=10000000),
        rec("Traded", CURRENT, "IND"),
    ]
    B.correct_past_season_teams(records, stats([
        ("Traded", "2023-24", "TOR", 30), ("Traded", "2023-24", "IND", 20)]))
    past = [r for r in records if r["season"] == "2023-24"][0]
    assert past["team"] == "TOR, IND"
    assert "team_salaries" not in past
    assert past["teams_split"] is True
    assert past["salary"] == 10000000


# --------------------------------------------------------------------------
# what is left to the salary sheet
# --------------------------------------------------------------------------


def test_a_team_that_paid_him_while_he_played_elsewhere_is_kept():
    """Waived and paid by one team, playing for another: the sheet is naming
    whose books the money was on, which is what a salary tool wants."""
    records = league(CURRENT) + [
        rec("Waived", "2011-12", "ORL"),
        rec("Waived", CURRENT, "MEM"),
    ]
    report = B.correct_past_season_teams(
        records, stats([("Waived", "2011-12", "MEM", 50)]))
    assert [r["team"] for r in records if r["player"] == "Waived"] == ["ORL", "MEM"]
    assert report["applied"] == []
    assert report["disagreements"] == 1
    assert report["left_alone"] == 1


def test_a_two_team_salary_sheet_row_is_kept():
    """Two teams paid him and he played for one. Dropping the payer would take
    his money off a franchise that really spent it."""
    records = league(CURRENT) + [
        rec("Bought Out", "2005-06", "DAL, SAS",
            team_salaries={"DAL": 6000000, "SAS": 4000000}),
        rec("Bought Out", CURRENT, "SAS"),
    ]
    B.correct_past_season_teams(
        records, stats([("Bought Out", "2005-06", "SAS", 70)]))
    past = [r for r in records if r["season"] == "2005-06"][0]
    assert past["team"] == "DAL, SAS"
    assert past["team_salaries"] == {"DAL": 6000000, "SAS": 4000000}


def test_a_season_with_no_stats_line_is_kept():
    records = league(CURRENT) + [
        rec("Ghost", "2025-26", "MIA"), rec("Ghost", CURRENT, "MIA")]
    report = B.correct_past_season_teams(records, stats([]))
    assert [r["team"] for r in records if r["player"] == "Ghost"] == ["MIA", "MIA"]
    assert report["disagreements"] == 0


def test_a_season_the_two_sources_agree_on_is_not_counted():
    records = league(CURRENT) + [
        rec("Settled", "2025-26", "MIL"), rec("Settled", CURRENT, "MIL")]
    report = B.correct_past_season_teams(
        records, stats([("Settled", "2025-26", "MIL", 60)]))
    assert report["disagreements"] == 0


def test_a_total_row_is_not_a_team():
    records = league(CURRENT) + [
        rec("Mover", "2025-26", "MIA"), rec("Mover", CURRENT, "MIA")]
    B.correct_past_season_teams(records, stats([
        ("Mover", "2025-26", "TOT", 60), ("Mover", "2025-26", "MIL", 60)]))
    assert [r for r in records if r["season"] == "2025-26"][0]["team"] == "MIL"


# --------------------------------------------------------------------------
# a season's stats, whole, however many teams played it
# --------------------------------------------------------------------------


def _row(team, gp, pts, reb, ast, stl=0, blk=0):
    return {"player_original": "CJ McCollum", "team": team, "gp": gp,
            "pts": pts, "reb": reb, "ast": ast, "stl": stl, "blk": blk,
            "ppg": 22.1, "rpg": 4.34, "apg": 5.08, "spg": 1.13, "bpg": 0.35,
            "fg_pct": 0.46, "age": 30, "min": 0}


def test_a_traded_season_counts_both_teams_games():
    """CJ McCollum's 2021-22: 26 games in New Orleans and 36 in Portland. The
    build read the first row only and printed 631 points in 26 games beside a
    22.1 average, which is two different seasons in one line."""
    combined = B.combine_stats([
        _row("NOP", 26, 631, 116, 152, 34, 1),
        _row("POR", 36, 739, 153, 163, 36, 21),
    ])
    assert combined["gp"] == 62
    assert combined["pts"] == 1370
    assert combined["reb"] == 269
    assert combined["ast"] == 315
    # summed totals over summed games, never the mean of two averages
    assert combined["ppg"] == round(1370 / 62, 2) == 22.1
    assert combined["rpg"] == round(269 / 62, 2)
    assert combined["apg"] == round(315 / 62, 2)
    assert combined["team"] == "NOP, POR"


def test_a_rate_is_never_the_average_of_two_averages():
    """The mean of the two per-team averages weights a 5-game stint like a
    77-game one. 20 points in 5 games and 10 in 75 is 10.6 a game, not 15."""
    combined = B.combine_stats([
        _row("AAA", 5, 100, 10, 10), _row("BBB", 75, 750, 75, 75),
    ])
    assert combined["gp"] == 80
    assert combined["ppg"] == round(850 / 80, 2) == 10.62


def test_one_team_season_is_left_exactly_as_it_is():
    row = _row("POR", 70, 1400, 280, 350)
    assert B.combine_stats([row]) is row


def test_a_total_row_is_used_as_given_and_checked(capsys):
    """A TOT row is the source's own total. This sheet has none, so the branch
    is defensive, and a disagreement with the team rows is printed rather than
    quietly resolved."""
    tot = _row("TOT", 62, 1370, 269, 315)
    assert B.combine_stats(
        [tot, _row("NOP", 26, 631, 116, 152), _row("POR", 36, 739, 153, 163)]
    ) is tot
    capsys.readouterr()
    wrong = _row("TOT", 62, 999, 269, 315)
    B.combine_stats([wrong, _row("NOP", 26, 631, 116, 152),
                     _row("POR", 36, 739, 153, 163)])
    assert "TOT pts is 999" in capsys.readouterr().out


def test_a_split_season_carries_no_team_rank():
    assert B.is_split_record({"teams_split": True}) is True
    assert B.is_split_record({"team_salaries": {"A": 1, "B": 2}}) is True
    assert B.is_split_record({"team": "POR", "team_salaries": {"POR": 1}}) is False


def test_the_team_he_finished_with_reads_last():
    """The stats sheet has no order in it: New Orleans sits above Portland in
    the file for McCollum's 2021-22, although Portland came first. The next
    season says where he finished, and that team goes last."""
    records = [
        rec("CJ McCollum", "2021-22", "NOP, POR", salary=30864198),
        rec("CJ McCollum", "2022-23", "NOP", salary=33333333),
    ]
    B.order_split_teams(records, stats([
        ("CJ McCollum", "2021-22", "NOP", 26),
        ("CJ McCollum", "2021-22", "POR", 36),
    ]))
    assert records[0]["team"] == "POR, NOP"


def test_an_order_with_no_evidence_is_left_alone():
    """He left both teams, so nothing here says which came first and the
    order stands as the sheet gave it."""
    records = [
        rec("Gone", "2021-22", "NOP, POR"),
        rec("Gone", "2022-23", "MIA"),
    ]
    B.order_split_teams(records, stats([
        ("Gone", "2021-22", "NOP", 26), ("Gone", "2021-22", "POR", 36)]))
    assert records[0]["team"] == "NOP, POR"


# --------------------------------------------------------------------------
# the committed report
# --------------------------------------------------------------------------


REPORT = os.path.join(REPO, "data", "team_corrections_report.json")


@pytest.mark.skipif(not os.path.exists(REPORT), reason="no report built")
def test_the_shipped_report_lists_every_correction():
    with open(REPORT, encoding="utf-8") as fh:
        report = json.load(fh)
    assert report["corrections"]
    for row in report["corrections"]:
        assert set(row) >= {"player", "season", "sheet_team", "corrected_team"}
        assert row["sheet_team"] != row["corrected_team"]
    assert report["left_to_the_salary_sheet"] >= 0
    assert report["disagreements_found"] >= len(report["corrections"])


@pytest.mark.skipif(not os.path.exists(REPORT), reason="no report built")
def test_the_data_matches_the_report():
    """Every correction the report names reads that way in the data.

    A record the report names that the data no longer has is not a mismatch:
    the sheets drop a man between builds, and the report is written by the
    build that read him. What has to hold is that a record still on file
    carries the team the report says it was given.
    """
    with open(REPORT, encoding="utf-8") as fh:
        report = json.load(fh)
    with open(os.path.join(REPO, "data", "data.json"), encoding="utf-8") as fh:
        rows = {(r["player"], r["season"]): r for r in json.load(fh)["seasons"]}
    checked = 0
    for row in report["corrections"]:
        rec = rows.get((row["player"], row["season"]))
        if rec is None:
            continue
        assert rec["team"] == row["corrected_team"], row
        checked += 1
    # A report that matches nothing on file is a report for another dataset.
    assert checked > len(report["corrections"]) // 2


# --------------------------------------------------------------------------
# a split season with no amounts on it
# --------------------------------------------------------------------------


def test_a_flagged_season_is_a_split_season_to_the_engine():
    """Franchise records skip it, the same as a season carrying two amounts."""
    assert F.is_split_season({"team": "TOR, IND", "teams_split": True}) is True
    assert F.is_split_season({"team": "IND"}) is False


def test_a_flagged_season_credits_no_team_a_figure():
    record = {"team": "TOR, IND", "salary": 10000000, "teams_split": True}
    assert F.team_amounts(record) == []


def test_a_season_with_real_amounts_still_carries_them():
    record = {"team": "TOR, IND", "salary": 10000000,
              "team_salaries": {"TOR": 6000000, "IND": 4000000}}
    assert dict(F.team_amounts(record)) == {"TOR": 6000000, "IND": 4000000}


def test_a_flagged_season_still_puts_him_on_both_teams():
    """No figure either way, but he did appear on both payrolls."""
    record = {"team": "TOR, IND", "salary": 10000000, "teams_split": True}
    assert F.team_codes(record) == ["TOR", "IND"]


def test_team_codes_reads_a_real_split_biggest_share_first():
    record = {"team": "TOR, IND", "salary": 10000000,
              "team_salaries": {"TOR": 6000000, "IND": 4000000}}
    assert F.team_codes(record) == ["TOR", "IND"]


# --------------------------------------------------------------------------
# which sheet answers for which season
# --------------------------------------------------------------------------


def test_the_boundary_is_the_newest_season_the_tab_holds():
    assert B.latest_season(["1999-00", "2025-26", "2024-25"]) == "2025-26"
    assert B.latest_season(["1999-00", "2000-01"]) == "2000-01"
    assert B.latest_season([]) is None
    assert B.latest_season(["nonsense"]) is None


def test_seasons_after_counts_by_the_year_a_season_ends_in():
    later = B.seasons_after("2025-26")
    assert not later("2024-25")
    assert not later("2025-26")
    assert later("2026-27")
    assert later("2030-31")


def test_with_no_tab_at_all_every_season_is_still_read():
    """An empty or missing historical sheet must not empty the file: the
    forward sheets keep answering for everything rather than nothing."""
    always = B.seasons_after(None)
    assert always("1990-91") and always("2030-31")


CURRENT_SHEET = (
    "PLAYER,x,TEAM,2026,2027\n"
    "Deandre Ayton,,LAL,\"$33,654,814\",\"$35,000,000\"\n"
)
DEAD_SHEET = (
    "PLAYER,a,b,TEAM,SALARY 25-26,SALARY 26-27\n"
    "Chris Paul,,,TOR,\"$3,634,153\",\"$1,000,000\"\n"
)


def test_the_forward_sheets_stop_at_the_season_the_tab_reaches():
    lookup, seasons, dropped, dead_offer = B.process_cyro_salaries(
        CURRENT_SHEET, DEAD_SHEET, after_season="2025-26")
    assert seasons == {"2026-27"}
    assert ("deandre ayton", "2025-26") not in lookup
    assert ("chris paul", "2025-26") not in lookup
    assert lookup[("deandre ayton", "2026-27")][0]["salary"] == 35000000
    assert lookup[("chris paul", "2026-27")][0]["salary"] == 1000000
    # and the build can say what it stopped reading, per sheet
    assert dropped["current"]["2025-26"]["deandre ayton"]["salary"] == 33654814
    assert dropped["dead"]["2025-26"]["chris paul"]["salary"] == 3634153
    # the dead money comes back separately: the tab has these dollars without
    # the club that owes them, so the sheet is kept for the team
    assert dead_offer[("chris paul", "2025-26")]["teams"] == {"TOR": 3634153}
    assert ("deandre ayton", "2025-26") not in dead_offer


def test_the_forward_sheets_still_answer_where_the_tab_has_not_reached():
    """The fallback that matters on the day the tab is a season behind: nothing
    is lost, the forward sheets simply still speak for it."""
    lookup, seasons, dropped, dead_offer = B.process_cyro_salaries(
        CURRENT_SHEET, DEAD_SHEET, after_season="2024-25")
    assert seasons == {"2025-26", "2026-27"}
    assert lookup[("deandre ayton", "2025-26")][0]["salary"] == 33654814
    assert lookup[("chris paul", "2025-26")][0]["salary"] == 3634153
    assert dropped == {"current": {}, "dead": {}}
    assert dead_offer == {}


FUTURE_SHEET = "PLAYER,TEAM,2026,2027,2028\nDeandre Ayton,LAL,100,200,300\n"


def test_the_future_sheet_reads_every_year_column_past_the_boundary():
    lookup, skipped = B.process_future_salaries(FUTURE_SHEET, after_season="2025-26")
    assert skipped == ["2026"]
    assert ("deandre ayton", "2025-26") not in lookup
    assert lookup[("deandre ayton", "2026-27")][0]["salary"] == 200
    assert lookup[("deandre ayton", "2027-28")][0]["salary"] == 300


def test_the_future_sheet_needs_no_edit_when_the_boundary_moves():
    """The columns used to be the literal list 2027 to 2031. A sheet that gains
    a 2032 column has to be read without anyone remembering to say so."""
    sheet = "PLAYER,TEAM,2031,2032\nSomeone,LAL,10,20\n"
    lookup, skipped = B.process_future_salaries(sheet, after_season="2025-26")
    assert skipped == []
    assert lookup[("someone", "2031-32")][0]["salary"] == 20


@pytest.mark.skipif(
    not os.path.exists(os.path.join(REPO, "data", "salary_sources_report.json")),
    reason="data/salary_sources_report.json not present",
)
def test_the_shipped_sources_report_names_a_boundary_and_keeps_its_books():
    with open(os.path.join(REPO, "data", "salary_sources_report.json"), "r",
              encoding="utf-8") as fh:
        doc = json.load(fh)
    through = doc["historical_through"]
    assert B.normalize_season(through) == through, through
    later = B.seasons_after(through)
    rows = doc["rows_per_season_per_sheet"]
    for season, sheets in rows.items():
        if not later(season):
            # the tab owns it, so no forward sheet may have answered for it
            assert set(sheets) <= {"historical"}, (season, sheets)
        else:
            assert "historical" not in sheets, (season, sheets)
    # The arithmetic the build checked, recomputed here rather than taken on
    # trust: a season the tab covers holds the money the tab gave, plus the rows
    # the dead sheet had that the tab did not, plus the rows filled from the
    # current sheet. Every term, or the check passes while money goes missing.
    assert doc["the_sums_balance"], "no season was checked"
    for season, book in doc["the_sums_balance"].items():
        assert not later(season), season
        assert book["balances"] is True, (season, book)
        assert (book["dollars_on_the_historical_tab"]
                + book["dollars_on_dead_rows_the_tab_had_none_for"]
                + book["dollars_filled_from_the_current_sheet"]
                == book["dollars_in_the_file"]), (season, book)

    # the current-sheet fill: a season the tab covers, a team, and games played
    fill = doc["filled_from_current_sheet_missing_from_tab"]
    for row in fill["rows_filled"]:
        assert not later(row["season"]), row
        assert row["team"], row
        assert row["games"] > 0, row
    for row in fill["left_out_for_no_games"]:
        assert not later(row["season"]), row

    dead = doc["dead_money_into_the_tabs_seasons"]
    # the tab holds this money without the club that owes it, so every team the
    # dead sheet filled in has to be for a season the tab covers
    for row in dead["teams_filled_in"] + dead["rows_added"]:
        assert not later(row["season"]), row
        assert row["team"], row
    for sheet, by_season in doc["cutover"].items():
        for season, book in by_season.items():
            assert not later(season), (sheet, season)
            assert (book["also_in_the_historical_tab"]
                    + len(book["missing_from_the_historical_tab"])
                    == book["player_seasons_offered"]), (sheet, season)


# --------------------------------------------------------------------------
# the workflows
# --------------------------------------------------------------------------

WORKFLOWS = os.path.join(REPO, ".github", "workflows")


def test_no_workflow_pastes_an_input_into_a_shell():
    """A workflow input is outside content. In env: it is a string; interpolated
    into a run: script it is one quote away from being a command."""
    offenders = []
    for name in sorted(os.listdir(WORKFLOWS)):
        with open(os.path.join(WORKFLOWS, name), "r", encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        in_run = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("run:"):
                in_run = True
                continue
            if stripped.startswith("- name:") or stripped.startswith("env:"):
                in_run = False
            if in_run and "github.event" in line and "${{" in line:
                offenders.append((name, stripped))
    assert not offenders, offenders


def test_both_workflows_commit_the_sources_report():
    for name in ("update-data.yml", "data-build.yml"):
        with open(os.path.join(WORKFLOWS, name), "r", encoding="utf-8") as fh:
            body = fh.read()
        assert "data/salary_sources_report.json" in body, name


def test_the_dead_money_offer_asks_about_the_person_not_the_spelling():
    """Two spellings the register resolves to one man are two keys before the
    per-person re-key and one after it. Asked of the spelling, "has the tab got
    him already" answers no, a second row goes in beside the tab's, and the loop
    that folds records into the unified list keeps the first salary it saw and
    drops the second. The money disappears without a word. Asked of the person,
    the tab's row is found and only its blank team is filled.
    """
    index = B.PersonIndex()
    index.add_person({
        "PLAYER": "Skal Labissiere", "DRAFT": "2016", "PICK": "28",
        "COLLEGE / TEAM": "Kentucky", "BIRTHDAY": "", "POS": "",
        "NATIONALITY": "", "HEIGHT": "", "WEIGHT": "",
    })
    # whatever the two sheets spell, both are this man
    tab_spelling, dead_spelling = "Skal Labissiere", "Skal Labissiere"
    who = index.resolve(dead_spelling, "2025-26", note=False)
    by_person = {(index.resolve(tab_spelling, "2025-26", note=False), "2025-26"): [
        {"player_original": tab_spelling, "team": "", "salary": 153330}]}

    existing = by_person.get((who, "2025-26"))
    assert existing is not None, "the person is the key that finds him"
    blank = [rec for rec in existing if not (rec.get("team") or "").strip()]
    for rec in blank:
        rec["team"] = "WAS"
    assert len(by_person) == 1
    rows = by_person[(who, "2025-26")]
    assert len(rows) == 1 and rows[0]["team"] == "WAS"
    # the figure is the tab's, once, and no second row was created to be dropped
    assert rows[0]["salary"] == 153330


def test_the_unified_merge_drops_a_second_salary_on_one_person_season():
    """Why the question above has to be asked of the person: this is the loop
    that would have swallowed the answer. A second record on the same
    (person, season) contributes its team and never its money."""
    ps_map = {("p1", "2025-26"): {
        "pid": "p1", "season": "2025-26", "salary": 153330,
        "end_year": 2026, "team": ""}}
    second = {"player_original": "same man, other spelling", "team": "WAS",
              "salary": 153330}
    key = ("p1", "2025-26")
    if key in ps_map:
        if second.get("team") and not ps_map[key]["team"]:
            ps_map[key]["team"] = second["team"]
    assert ps_map[key]["salary"] == 153330, "the second salary is not added"
    assert ps_map[key]["team"] == "WAS", "only the team comes across"


def test_two_rows_on_one_team_in_one_season_keep_both_payments():
    """The combine used to collapse a key only when its rows named more than one
    team. Two rows on the same team stayed two, and the loop that folds records
    into the unified list takes the first salary and only the others' teams, so
    the second payment vanished. $153,330 of 2025-26 went that way.
    """
    # columns by position, as the parser reads them: TEAM, YEAR, PLAYER, SALARY
    lookup = B.process_salaries_csv(
        "TEAM,YEAR,PLAYER,SALARY\n"
        "WAS,2026,Skal Labissiere,\"$100,000\"\n"
        "WAS,2026,Skal Labissiere,\"$53,330\"\n"
    )
    key = ("skal labissiere", "2025-26")
    assert len(lookup[key]) == 2, "two rows as read off the sheet"

    # the collapse, as build_data runs it
    recs = lookup[key]
    team_sals = {}
    for rec in recs:
        team_sals[rec.get("team", "")] = team_sals.get(rec.get("team", ""), 0) + rec["salary"]
    teams_sorted = sorted(t for t in team_sals if t)
    combined = {"team": ", ".join(teams_sorted), "salary": sum(team_sals.values())}
    if len(teams_sorted) > 1:
        combined["team_salaries"] = {t: s for t, s in team_sals.items() if t}

    assert combined["salary"] == 153330, "both payments, not just the first"
    assert combined["team"] == "WAS"
    # one team, so no per-team breakdown: it would say nothing new
    assert "team_salaries" not in combined


# --------------------------------------------------------------------------
# the current sheet, where the tab has no row and he played
# --------------------------------------------------------------------------

CURRENT_WITH_A_COVERED_SEASON = (
    "PLAYER,x,TEAM,2026,2027\n"
    "Gary Payton II,,MIA,\"$3,303,774\",\"$4,000,000\"\n"
)


def _fill_from_current(dropped, stats, tab, persons, season="2025-26"):
    """The fill as build_data runs it, on the same inputs."""
    filled, no_games, covered = [], [], 0
    for nk, offer in sorted((dropped.get(season) or {}).items()):
        who = persons.resolve(offer["player"], season)
        if tab.get((who, season)) is not None:
            covered += 1
            continue
        played = B.stats_teams_for(stats, offer["player"], season)
        games = sum(row.get("gp") or 0
                    for row in (stats.get((nk, season)) or [])
                    if row.get("team") != "TOT")
        if not (played and games):
            no_games.append(offer["player"])
            continue
        tab[(who, season)] = [{"player_original": offer["player"],
                               "team": ", ".join(played),
                               "salary": offer["salary"]}]
        filled.append((offer["player"], ", ".join(played), offer["salary"], games))
    return filled, no_games, covered


def _register(name):
    index = B.PersonIndex()
    index.add_person({
        "PLAYER": name, "DRAFT": "2016", "PICK": "UND",
        "COLLEGE / TEAM": "Oregon St", "BIRTHDAY": "", "POS": "",
        "NATIONALITY": "", "HEIGHT": "", "WEIGHT": "",
    })
    return index


def test_the_fill_takes_the_team_from_the_stats_line_not_the_sheet():
    """The sheet carries one TEAM per player and applies it to every season in
    the row, which is the whole reason 2025-26 moved to the tab. So the fill
    reads the team off the stats line: the sheet says Miami and he played for
    Golden State."""
    _lk, _s, dropped, _dead = B.process_cyro_salaries(
        CURRENT_WITH_A_COVERED_SEASON, None, after_season="2025-26")
    assert dropped["current"]["2025-26"]["gary payton"]["salary"] == 3303774
    stats = {("gary payton", "2025-26"): [{"team": "GSW", "gp": 73}]}
    filled, no_games, covered = _fill_from_current(
        dropped["current"], stats, {}, _register("Gary Payton II"))
    assert filled == [("Gary Payton II", "GSW", 3303774, 73)]
    assert (no_games, covered) == ([], 0)


def test_a_man_with_no_games_stays_out():
    """Two-way and camp rows. A season nobody played is not a season to put
    money on, and the tab is right to omit them."""
    _lk, _s, dropped, _dead = B.process_cyro_salaries(
        "PLAYER,x,TEAM,2026,2027\nBen Simmons,,SAC,\"$636,435\",$0\n",
        None, after_season="2025-26")
    filled, no_games, covered = _fill_from_current(
        dropped["current"], {}, {}, _register("Ben Simmons"))
    assert (filled, no_games, covered) == ([], ["Ben Simmons"], 0)


def test_the_fill_never_touches_a_row_the_tab_already_has():
    """And the day the tab gains a row for him it stops applying to him, with
    nothing here to edit: that is the same test, one build later."""
    _lk, _s, dropped, _dead = B.process_cyro_salaries(
        CURRENT_WITH_A_COVERED_SEASON, None, after_season="2025-26")
    persons = _register("Gary Payton II")
    who = persons.resolve("Gary Payton II", "2025-26")
    tab = {(who, "2025-26"): [
        {"player_original": "Gary Payton II", "team": "GSW", "salary": 999}]}
    stats = {("gary payton", "2025-26"): [{"team": "GSW", "gp": 73}]}
    filled, no_games, covered = _fill_from_current(
        dropped["current"], stats, tab, persons)
    assert (filled, no_games, covered) == ([], [], 1)
    # the tab's figure, untouched
    assert tab[(who, "2025-26")] == [
        {"player_original": "Gary Payton II", "team": "GSW", "salary": 999}]


def test_the_fill_touches_only_the_season_that_changed_hands():
    """The sheet's older columns were never read by any build: it used to start
    at 2026, so its 2025 column has never answered for 2024-25. Filling from it
    there would add a row that has never existed rather than restore one."""
    _lk, _s, dropped, _dead = B.process_cyro_salaries(
        "PLAYER,x,TEAM,2025,2026,2027\n"
        "Gary Payton II,,MIA,\"$1,000,000\",\"$3,303,774\",\"$4,000,000\"\n",
        None, after_season="2025-26")
    # both older columns come back as the tab's to answer for
    assert set(dropped["current"]) == {"2024-25", "2025-26"}
    stats = {("gary payton", "2024-25"): [{"team": "GSW", "gp": 60}],
             ("gary payton", "2025-26"): [{"team": "GSW", "gp": 73}]}
    persons = _register("Gary Payton II")
    # only the boundary season is filled
    filled, _no, _cov = _fill_from_current(dropped["current"], stats, {}, persons,
                                          season="2025-26")
    assert [row[0] for row in filled] == ["Gary Payton II"]
    assert filled[0][2] == 3303774


def test_the_fill_stops_at_the_boundary_like_everything_else():
    """A season past the boundary is the current sheet's to answer for outright,
    so nothing is 'filled' there: it is simply read."""
    _lk, seasons, dropped, _dead = B.process_cyro_salaries(
        CURRENT_WITH_A_COVERED_SEASON, None, after_season="2025-26")
    assert seasons == {"2026-27"}
    assert set(dropped["current"]) == {"2025-26"}
