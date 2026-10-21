"""A season salary under build_data.MIN_SALARY is a sheet's placeholder.

The build leaves such a season out of data.json (no page row, no rank, no
career total), lists it in meta.skipped_salaries, and the digest never reports
one coming or going. The real-data test warns with the list, so it can go to
whoever keeps the sheet.
"""

from __future__ import annotations

import json
import os
import warnings

import pytest

import build_data as B
import salary_digest as D

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REAL_DATA = os.path.join(REPO, "data", "data.json")


def season(pid, year, salary, team="LAC"):
    return {"pid": pid, "season": year, "salary": salary, "team": team,
            "end_year": B.season_to_year(year)}


def test_a_salary_under_a_thousand_is_left_out_and_listed():
    kept, skipped = B.drop_token_salaries([
        season("Bradley Beal", "2030-31", 7),
        season("Bradley Beal", "2029-30", 57000000),
        season("Some One", "1999-00", 999, team=""),
        season("Min Man", "1999-00", 1000),
        season("No Figure", "2028-29", None),
    ])
    assert [(r["pid"], r["season"]) for r in kept] == [
        ("Bradley Beal", "2029-30"), ("Min Man", "1999-00"),
        ("No Figure", "2028-29")]
    assert skipped == [
        {"player": "Some One", "season": "1999-00", "team": "", "salary": 999},
        {"player": "Bradley Beal", "season": "2030-31", "team": "LAC",
         "salary": 7},
    ]


def test_the_listed_name_is_the_one_data_json_prints():
    _kept, skipped = B.drop_token_salaries(
        [season("pid-17", "2030-31", 7)], display=lambda pid: "Bradley Beal")
    assert skipped[0]["player"] == "Bradley Beal"


def test_the_digest_never_reports_a_placeholder_coming_or_going():
    def payload(records):
        return {"seasons": records}
    old = payload([{"player": "Bradley Beal", "season": "2030-31",
                    "team": "LAC", "salary": 7}])
    new = payload([])
    assert D.by_key(old) == {}
    assert D.changes(old, new, "2026-27") == []
    # a real salary that replaces it later is news like any other
    fixed = payload([{"player": "Bradley Beal", "season": "2030-31",
                      "team": "LAC", "salary": 60000000}])
    assert [i["kind"] for i in D.changes(new, fixed, "2026-27")] != []


@pytest.mark.skipif(not os.path.exists(REAL_DATA),
                    reason="data/data.json not present")
def test_data_json_holds_no_placeholder_salary_and_lists_the_ones_it_left_out():
    with open(REAL_DATA, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    low = sorted("{} {} ${:,}".format(r["player"], r["season"], r["salary"])
                 for r in data["seasons"]
                 if r.get("salary") is not None and r["salary"] < B.MIN_SALARY)
    assert low == [], low

    skipped = data["meta"]["skipped_salaries"]
    assert skipped["min_salary"] == B.MIN_SALARY
    held = {(r["player"], r["season"]) for r in data["seasons"]}
    for rec in skipped["records"]:
        assert rec["salary"] < B.MIN_SALARY, rec
        assert (rec["player"], rec["season"]) not in held, rec
    if skipped["records"]:
        warnings.warn(
            "{} season(s) left out of data/data.json for a salary under ${:,}: "
            "{}".format(
                len(skipped["records"]), B.MIN_SALARY,
                "; ".join("{} {} {} ${:,}".format(
                    r["player"], r["season"], r["team"] or "(no team)",
                    r["salary"]) for r in skipped["records"])),
            UserWarning,
        )
