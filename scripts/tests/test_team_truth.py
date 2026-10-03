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


def test_a_season_he_was_traded_in_is_split_by_games():
    records = league(CURRENT) + [
        rec("Traded", "2023-24", "IND", salary=10000000),
        rec("Traded", CURRENT, "IND"),
    ]
    B.correct_past_season_teams(records, stats([
        ("Traded", "2023-24", "TOR", 30), ("Traded", "2023-24", "IND", 20)]))
    past = [r for r in records if r["season"] == "2023-24"][0]
    assert past["team"] == "TOR, IND"
    assert past["team_salaries"] == {"TOR": 6000000, "IND": 4000000}
    assert sum(past["team_salaries"].values()) == past["salary"]


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
    with open(REPORT, encoding="utf-8") as fh:
        report = json.load(fh)
    with open(os.path.join(REPO, "data", "data.json"), encoding="utf-8") as fh:
        rows = {(r["player"], r["season"]): r for r in json.load(fh)["seasons"]}
    for row in report["corrections"]:
        rec = rows.get((row["player"], row["season"]))
        assert rec is not None, row
        assert rec["team"] == row["corrected_team"], row
