"""data/roster_status.json: each current-season man's team, contract and birth date.

Salary Ladder reads it beside data.json (section 25 of its spec): who he plays
for now, on what kind of contract, what that team pays him, what former teams
still owe him, and when he was born. The file has to agree with data.json to
the dollar, and where it does not the build says so and writes it anyway.

The last block runs over the committed file when there is one. The two Cyro
sheets are not tracked, so no checkout can build it locally; the first daily
build after this lands is what writes it.
"""

from __future__ import annotations

import json
import os
import re

import pytest

import build_data as B

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REAL_DATA = os.path.join(REPO, "data", "data.json")
REAL_ROSTER = os.path.join(REPO, "data", "roster_status.json")

SEASON = "2026-27"

# The shape of Cyro's sheets as process_cyro_salaries reads them: a 4-digit year
# column per season on the current sheet, "SALARY 26-27" on the dead sheet.
CURRENT_CSV = """PLAYER,POS,TEAM,2027,CAP HOLD,ST 26-27,2028
Jalen Johnson,F,Atlanta,"$30,000,000",,GUARANTEED,"$30,000,000"
Camp Guy,G,Boston,"$1,272,870",,NOT G,
Two Way Guy,G,Chicago,"$678,882",,TWO-WAY,
No Status Guy,C,Denver,"$2,000,000",,,
Moved Guy,F,Miami,"$5,600,000",,,
Teamless Guy,F,,"$900,000",,,
"""

DEAD_CSV = """PLAYER,STATUS,X,TEAM,SALARY 25-26,CAP HOLD 25-26,STATUS 25-26,SALARY 26-27,CAP HOLD 26-27,STATUS 26-27
Moved Guy,Waived,,Golden State,"$1,000,000",,Waived,"$2,000,000",,Waived
Waived Guy,Waived,,Utah,,,,"$3,000,000",,Waived
Waived Guy,Waived,,Phoenix,,,,"$500,000",,Waived
"""

# The future sheet heads both its 2025-26 and its 2026-27 status "ST 26-27";
# the one that belongs to 2027 is the one after it.
FUTURE_CSV = """PLAYER,TEAM,,CH 26-27,ST 26-27,2027,CH 26-27,ST 26-27,2028
No Status Guy,Denver,"$1,900,000",,NOT G,"$2,000,000",,PARTIAL G,
Moved Guy,Sacramento,,,,"$5,600,000",,GUARANTEED,
"""


# --------------------------------------------------------------------------
# reading the sheets
# --------------------------------------------------------------------------


def test_the_sheets_status_words_map_onto_the_files_contract_types():
    assert B.contract_from_status("GUARANTEED") == "guaranteed"
    assert B.contract_from_status(" not g ") == "non_guaranteed"
    assert B.contract_from_status("NOT") == "non_guaranteed"
    assert B.contract_from_status("PARTIAL G") == "partial"
    assert B.contract_from_status("Two-Way") == "two_way"
    assert B.contract_from_status("10-Day") == "ten_day"
    for raw in ("", None, "Waived", "Something new"):
        assert B.contract_from_status(raw) is None
    assert set(B.CONTRACT_STATUSES.values()) < set(B.ROSTER_CONTRACTS)


def test_the_status_column_is_the_one_inside_the_seasons_own_block():
    header = ["PLAYER", "TEAM", "", "CH 26-27", "ST 26-27", "2027",
              "CH 26-27", "ST 26-27", "2028", "CH 27-28", "ST 27-28"]
    assert B.status_column_after(header, 5) == 7
    assert B.status_column_after(header, 8) == 10
    # no status before the next season's salary means none for this season
    assert B.status_column_after(["PLAYER", "2027", "2028", "ST 27-28"], 1) is None


def test_both_cyro_sheets_are_read_for_the_season_and_kept_apart():
    parts, headers = B.read_roster_sheets(CURRENT_CSV, DEAD_CSV, SEASON)
    assert headers["current"][:4] == ["PLAYER", "POS", "TEAM", "2027"]
    assert parts["jalen johnson"]["current"] == {"ATL": 30000000}
    assert parts["jalen johnson"]["dead"] == {}
    assert parts["jalen johnson"]["status"] == {"ATL": "GUARANTEED"}
    # paid by one club, owed by another: two figures, not one
    assert parts["moved guy"]["current"] == {"MIA": 5600000}
    assert parts["moved guy"]["dead"] == {"GSW": 2000000}
    # the 2025-26 dead money is another season's
    assert parts["waived guy"]["current"] == {}
    assert parts["waived guy"]["dead"] == {"UTA": 3000000, "PHX": 500000}
    # kept, under no team, so the check can name him
    assert parts["teamless guy"]["current"] == {"": 900000}


def test_the_future_sheet_status_is_keyed_on_the_team_it_was_written_for():
    statuses = B.read_future_statuses(FUTURE_CSV, SEASON)
    assert statuses == {("no status guy", "DEN"): "PARTIAL G",
                        ("moved guy", "SAC"): "GUARANTEED"}


# --------------------------------------------------------------------------
# birth dates
# --------------------------------------------------------------------------


def test_a_birth_date_is_written_the_iso_way_or_not_at_all():
    assert B.iso_birth_date("5/5/2004") == "2004-05-05"
    assert B.iso_birth_date("12/25/2005") == "2005-12-25"
    assert B.iso_birth_date("2004-05-05") == "2004-05-05"
    for raw in ("", None, "2004", "May 2004", "13/40/2004", "#N/A"):
        assert B.iso_birth_date(raw) is None


def register():
    persons = B.PersonIndex()
    persons.add_person({"PLAYER": "Jalen Johnson", "BIRTHDAY": "12/18/2001"})
    persons.add_person({"PLAYER": "Both Sources", "BIRTHDAY": "1/1/2000"})
    persons.add_person({"PLAYER": "Blank Override", "BIRTHDAY": "3/3/2003"})
    persons.add_person({"PLAYER": "Nobody Knows", "BIRTHDAY": ""})
    persons.add_override("Both Sources", {"BIRTHDAY": "2/2/2002"})
    persons.add_override("Blank Override", {"BIRTHDAY": ""})
    persons.add_override("Stand In", {"BIRTHDAY": "4/4/2004"})
    return persons


def test_the_birth_date_comes_from_biocsv():
    persons = register()
    pid = persons.resolve("Jalen Johnson", SEASON)
    assert B.roster_birth_date(persons, pid) == "2001-12-18"


def test_an_override_wins_over_biocsv():
    persons = register()
    pid = persons.resolve("Both Sources", SEASON)
    assert B.roster_birth_date(persons, pid) == "2002-02-02"


def test_an_override_with_no_date_leaves_biocsvs_in_place():
    persons = register()
    pid = persons.resolve("Blank Override", SEASON)
    assert B.roster_birth_date(persons, pid) == "2003-03-03"


def test_a_man_only_an_override_knows_gets_its_date():
    persons = register()
    pid = persons.resolve("Stand In", SEASON)
    assert pid[0] == "new"
    assert B.roster_birth_date(persons, pid) == "2004-04-04"


def test_no_date_anywhere_is_left_blank_never_guessed():
    persons = register()
    assert B.roster_birth_date(persons, persons.resolve("Nobody Knows", SEASON)) is None
    assert B.roster_birth_date(persons, persons.resolve("Never Heard Of", SEASON)) is None
    assert B.roster_birth_date(persons, None) is None


def test_asking_for_a_birth_date_does_not_mark_a_stand_in_bio_used():
    persons = register()
    B.roster_birth_date(persons, persons.resolve("Stand In", SEASON))
    assert persons.overrides_used == set()


# --------------------------------------------------------------------------
# one entry
# --------------------------------------------------------------------------


def entry_for(name, salary, current_csv=CURRENT_CSV, dead_csv=DEAD_CSV, birth=None):
    parts, _ = B.read_roster_sheets(current_csv, dead_csv, SEASON)
    future = B.read_future_statuses(FUTURE_CSV, SEASON)
    nk = B.normalize_name(name)
    mine = parts.get(nk)
    fut = {team: raw for (fnk, team), raw in future.items() if fnk == nk}
    return B.roster_entry(name, salary, mine, fut, birth)


def test_a_guaranteed_man_on_one_team():
    e = entry_for("Jalen Johnson", 30000000, birth="2001-12-18")
    assert e == {"player": "Jalen Johnson", "team": "ATL", "contract": "guaranteed",
                 "current_salary": 30000000, "dead_money": [], "total": 30000000,
                 "birth_date": "2001-12-18"}


def test_the_current_sheets_own_status_is_read_first():
    assert entry_for("Camp Guy", 1272870)["contract"] == "non_guaranteed"
    assert entry_for("Two Way Guy", 678882)["contract"] == "two_way"


def test_where_cyro_has_no_status_the_future_sheets_is_read_for_the_same_team():
    assert entry_for("No Status Guy", 2000000)["contract"] == "partial"


def test_a_status_written_for_another_club_is_not_his():
    e = entry_for("Moved Guy", 7600000)
    assert e["team"] == "MIA"
    assert e["contract"] == "unknown"
    assert e["current_salary"] == 5600000
    assert e["dead_money"] == [{"team": "GSW", "amount": 2000000}]
    assert e["total"] == 7600000


def test_a_man_paid_only_dead_money_has_no_team():
    e = entry_for("Waived Guy", 3500000)
    assert e["team"] is None
    assert e["contract"] == "dead_money_only"
    assert e["current_salary"] == 0
    assert e["dead_money"] == [{"team": "PHX", "amount": 500000},
                               {"team": "UTA", "amount": 3000000}]


def test_a_current_row_with_no_team_gives_no_team():
    e = entry_for("Teamless Guy", 0)
    assert e["team"] is None
    assert e["contract"] == "unknown"
    assert e["current_salary"] == 900000


def test_two_current_teams_are_not_settled_by_guessing():
    current = CURRENT_CSV + 'Jalen Johnson,F,Boston,"$1,000,000",,GUARANTEED,\n'
    e = entry_for("Jalen Johnson", 31000000, current_csv=current)
    assert e["team"] is None
    assert e["contract"] == "unknown"
    assert e["current_salary"] == 31000000


def test_a_man_the_sheets_never_mention_is_still_listed():
    e = B.roster_entry("Ghost", 1000000, None, {}, None)
    assert e["team"] is None and e["contract"] == "unknown"
    assert e["current_salary"] == 0 and e["dead_money"] == [] and e["total"] == 1000000


# --------------------------------------------------------------------------
# the check
# --------------------------------------------------------------------------


def test_a_balanced_file_has_no_problems():
    entries = [entry_for("Jalen Johnson", 30000000), entry_for("Moved Guy", 7600000)]
    assert B.roster_status_problems(
        entries, {"Jalen Johnson": 30000000, "Moved Guy": 7600000}) == []


def test_parts_that_do_not_add_up_to_the_total_are_named():
    e = entry_for("Moved Guy", 7000000)
    problems = B.roster_status_problems([e], {"Moved Guy": 7000000})
    assert len(problems) == 1
    assert problems[0].startswith("Moved Guy: current 5,600,000 + dead 2,000,000 = 7,600,000")


def test_a_total_data_json_disagrees_with_is_named():
    e = entry_for("Jalen Johnson", 30000000)
    assert len(B.roster_status_problems([e], {"Jalen Johnson": 29000000})) == 1
    assert "has no salary" in B.roster_status_problems([e], {})[0]


def test_a_data_json_man_missing_from_the_file_is_named():
    e = entry_for("Jalen Johnson", 30000000)
    problems = B.roster_status_problems([e], {"Jalen Johnson": 30000000, "Ghost": 5})
    assert problems == ["Ghost: in data.json, not in roster_status.json"]


# --------------------------------------------------------------------------
# the file
# --------------------------------------------------------------------------


def build_fixture(tmp_path, salaries):
    """write_roster_status over the fixture sheets, as the build calls it."""
    persons = register()
    pid_of_key, player_season_list = {}, []
    for name, salary in salaries.items():
        pid = persons.resolve(name, SEASON)
        pid_of_key[(B.normalize_name(name), SEASON)] = pid
        player_season_list.append({"pid": pid, "season": SEASON, "salary": salary})
    # another season's record is not in the file
    player_season_list.append({"pid": persons.resolve("Jalen Johnson", "2025-26"),
                               "season": "2025-26", "salary": 1})
    output = {"meta": {"built": "2026-10-12T06:01:02.345678"}}
    path = tmp_path / "roster_status.json"
    result = B.write_roster_status(
        output, player_season_list, persons, pid_of_key, {SEASON, "2027-28"},
        CURRENT_CSV, DEAD_CSV, FUTURE_CSV, path=str(path))
    return json.loads(path.read_text(encoding="utf-8")), result


def test_the_file_carries_data_jsons_build_time_and_the_season(tmp_path):
    doc, _ = build_fixture(tmp_path, {"Jalen Johnson": 30000000})
    assert doc["built"] == "2026-10-12T06:01:02.345678"
    assert doc["season"] == SEASON
    assert list(doc) == ["built", "season", "players"]


def test_one_entry_per_data_json_man_under_his_data_json_name(tmp_path):
    doc, (_, problems) = build_fixture(
        tmp_path, {"Jalen Johnson": 30000000, "Moved Guy": 7600000,
                   "Waived Guy": 3500000})
    assert [p["player"] for p in doc["players"]] == [
        "Jalen Johnson", "Moved Guy", "Waived Guy"]
    assert problems == []
    jalen = doc["players"][0]
    assert jalen["birth_date"] == "2001-12-18"
    assert set(jalen) == {"player", "team", "contract", "current_salary",
                          "dead_money", "total", "birth_date"}


def test_a_mismatch_warns_and_the_file_is_still_written(tmp_path, capsys):
    doc, (_, problems) = build_fixture(tmp_path, {"Moved Guy": 1})
    assert len(problems) == 1
    assert doc["players"][0]["total"] == 1
    assert "::warning::roster_status.json: 1 players" in capsys.readouterr().out


def test_no_season_from_cyro_writes_nothing_and_raises_nothing(tmp_path):
    path = tmp_path / "roster_status.json"
    assert B.write_roster_status({"meta": {"built": "x"}}, [], B.PersonIndex(), {},
                                 set(), None, None, None, path=str(path)) is None
    assert not path.exists()


def test_the_daily_workflow_commits_the_file():
    with open(os.path.join(REPO, ".github", "workflows", "update-data.yml"),
              encoding="utf-8") as fh:
        assert "git add data/roster_status.json" in fh.read()


# --------------------------------------------------------------------------
# the committed file, once the daily build has written one
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def real():
    if not os.path.exists(REAL_ROSTER):
        pytest.skip("data/roster_status.json has not been built yet")
    with open(REAL_ROSTER, encoding="utf-8") as fh:
        roster = json.load(fh)
    with open(REAL_DATA, encoding="utf-8") as fh:
        data = json.load(fh)
    return roster, data


def test_the_committed_file_is_from_the_same_build_as_data_json(real):
    roster, data = real
    assert roster["built"] == data["meta"]["built"]


def test_the_committed_file_names_exactly_data_jsons_men_for_the_season(real):
    roster, data = real
    names = [p["player"] for p in roster["players"]]
    assert len(names) == len(set(names))
    assert set(names) == {r["player"] for r in data["seasons"]
                          if r["season"] == roster["season"]}


def test_the_committed_file_keeps_to_its_shape(real):
    roster, _ = real
    for p in roster["players"]:
        assert p["contract"] in B.ROSTER_CONTRACTS
        assert p["team"] is None or re.fullmatch(r"[A-Z]{3}", p["team"])
        assert p["birth_date"] is None or re.fullmatch(r"\d{4}-\d{2}-\d{2}", p["birth_date"])
        assert isinstance(p["current_salary"], int)
        assert all(isinstance(d["amount"], int) for d in p["dead_money"])
        if p["contract"] == "dead_money_only":
            assert p["team"] is None and p["current_salary"] == 0
