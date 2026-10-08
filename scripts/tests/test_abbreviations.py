"""Tests for the abbreviated-name step and the duplicate export in build_data.py.

HoopsHype prints a first name as its initial, "G. Antetokounmpo", and rows
copied from it carry that onto the historical tab. The register has no man of
that name, so without this step the row became a player of its own.
"""

from __future__ import annotations

import csv

import build_data as B


def person(name, draft=None):
    return {"PLAYER": name, "DRAFT": "" if draft is None else str(draft), "PICK": "",
            "COLLEGE / TEAM": "", "BIRTHDAY": "", "POS": "", "NATIONALITY": "",
            "HEIGHT": "", "WEIGHT": ""}


def register(*rows):
    index = B.PersonIndex()
    for row in rows:
        index.add_person(row)
    return index


def stats(*rows):
    """(name, season, team, games) -> the stats lookup the build keys on."""
    out = {}
    for name, season, team, gp in rows:
        out.setdefault((B.normalize_name(name), season), []).append(
            {"player_original": name, "team": team, "gp": gp})
    return out


BROTHERS = register(
    person("Giannis Antetokounmpo", 2013),
    person("Thanasis Antetokounmpo", 2014),
    person("Kostas Antetokounmpo", 2018),
    person("Alex Antetokounmpo"),
    person("Nickeil Alexander-Walker", 2019),
    person("CJ McCollum", 2013),
)


def test_an_initial_and_a_surname_read_as_the_one_man_they_fit():
    m = B.AbbreviationMatcher(BROTHERS, {})
    assert m.expand("G. Antetokounmpo", "2025-26") == ("Giannis Antetokounmpo", None)
    assert m.expand("T. Antetokounmpo", "2025-26") == ("Thanasis Antetokounmpo", None)


def test_a_hyphenated_surname_is_still_the_surname():
    m = B.AbbreviationMatcher(BROTHERS, {})
    assert m.expand("N. Alexander-Walker", "2025-26") == ("Nickeil Alexander-Walker", None)


def test_two_initials_are_one_first_name():
    m = B.AbbreviationMatcher(BROTHERS, {})
    assert m.expand("C. J. McCollum", "2025-26") == ("CJ McCollum", None)


def test_a_full_name_is_left_alone():
    m = B.AbbreviationMatcher(BROTHERS, {})
    assert m.expand("Giannis Antetokounmpo", "2025-26") == (None, None)
    assert m.expand("Nobody Atall", "2025-26") == (None, None)


def test_the_resolved_man_is_the_register_person():
    m = B.AbbreviationMatcher(BROTHERS, {})
    full, _ = m.expand("G. Antetokounmpo", "2025-26")
    assert BROTHERS.resolve(full, "2025-26") == BROTHERS.resolve(
        "Giannis Antetokounmpo", "2025-26")


def test_more_than_one_fit_is_settled_by_who_played_then_by_the_club():
    index = register(person("Jalen Williams", 2022), person("Jaylin Williams", 2022))
    both = stats(("Jalen Williams", "2025-26", "OKC", 60),
                 ("Jaylin Williams", "2025-26", "OKC", 60))
    m = B.AbbreviationMatcher(index, both)
    full, why = m.expand("J. Williams", "2025-26", {"OKC"})
    assert full is None and "more than one" in why

    one = stats(("Jalen Williams", "2025-26", "OKC", 60))
    m = B.AbbreviationMatcher(index, one)
    assert m.expand("J. Williams", "2025-26") == ("Jalen Williams", None)

    apart = stats(("Jalen Williams", "2025-26", "OKC", 60),
                  ("Jaylin Williams", "2025-26", "MEM", 60))
    m = B.AbbreviationMatcher(index, apart)
    assert m.expand("J. Williams", "2025-26", {"MEM"}) == ("Jaylin Williams", None)


def test_a_man_bio_csv_lacks_is_found_on_the_stats_sheet():
    m = B.AbbreviationMatcher(register(), stats(("Mohamed Diawara", "2025-26", "CHA", 40)))
    assert m.expand("M. Diawara", "2025-26") == ("Mohamed Diawara", None)


def test_a_spelling_that_fits_nobody_says_so():
    m = B.AbbreviationMatcher(BROTHERS, {})
    full, why = m.expand("Z. Antetokounmpo", "2025-26")
    assert full is None and why


TAB = (
    "TEAM,YEAR,PLAYER,SALARY\n"
    "MIL,2026,Damian Lillard,\"$22,516,603\"\n"       # row 2
    "POR,2026,Damian Lillard,\"$14,104,000\"\n"       # row 3
    "MIL,2026,Damian Lillard,\"$22,516,603\"\n"       # row 4
    ",,,\n"                                           # row 5
    "MIL,2026,G. Antetokounmpo,\"$54,126,450\"\n"     # row 6
    "MIL,2026,Giannis Antetokounmpo,\"$54,126,450\"\n"  # row 7
    "MIL,2025,Damian Lillard,\"$22,516,603\"\n"       # row 8
    "CHA,2026,Spencer Dinwiddie,\"$3,634,153\"\n"     # row 9
    "TOR,2026,Spencer Dinwiddie,\"$3,634,153\"\n"     # row 10
)


def test_the_tab_rows_carry_the_row_number_the_sheet_shows():
    rows = B.tab_rows_with_numbers(TAB)
    assert [r["row"] for r in rows] == [2, 3, 4, 6, 7, 8, 9, 10]


def test_duplicates_are_one_man_one_club_one_figure_in_the_season():
    index = register(person("Damian Lillard", 2012), person("Giannis Antetokounmpo", 2013),
                     person("Spencer Dinwiddie", 2014))
    m = B.AbbreviationMatcher(index, {})

    def who(name, season):
        full, _ = m.expand(name, season)
        return index.resolve(full or name, season, note=False)

    groups = B.tab_duplicate_groups(B.tab_rows_with_numbers(TAB), "2025-26", who)
    assert groups == [
        {"player": "Damian Lillard", "season": "2025-26", "team": "MIL",
         "amount": 22516603, "sheet_rows": [2, 4]},
        {"player": "G. Antetokounmpo / Giannis Antetokounmpo", "season": "2025-26",
         "team": "MIL", "amount": 54126450, "sheet_rows": [6, 7]},
    ]


def test_the_duplicates_file_has_the_columns_asked_for(tmp_path):
    path = tmp_path / "reports" / "dups.csv"
    B.write_tab_duplicates([{"player": "Damian Lillard", "season": "2025-26",
                             "team": "MIL", "amount": 22516603, "sheet_rows": [2, 4]}],
                           str(path))
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    assert rows == [["player", "season", "team", "amount", "sheet_row_numbers"],
                    ["Damian Lillard", "2025-26", "MIL", "22516603", "2; 4"]]


def test_the_existing_de_duplication_is_unchanged():
    """The export only reads the tab a second time. The build still reads the
    repeated row once and reports it in the same shape as before."""
    lookup, repeats = B.process_salaries_csv(TAB)
    assert [(r["team"], r["salary"]) for r in lookup[("damian lillard", "2025-26")]] == [
        ("MIL", 22516603), ("POR", 14104000)]
    assert repeats == [{"player": "Damian Lillard", "season": "2025-26",
                        "team": "MIL", "salary": 22516603}]


def test_a_spot_check_names_the_tab_rows_behind_a_miss():
    records = [{"player": "Elijah Harkless", "season": "2025-26", "team": "UTA",
                "salary": 1272869},
               {"player": "Gary Payton II", "season": "2025-26", "team": "GSW",
                "salary": 3303774}]
    tab = B.tab_rows_with_numbers(
        "TEAM,YEAR,PLAYER,SALARY\n"
        "UTA,2026,Elijah Harkless,\"$636,435\"\n"
        "UTA,2026,Elijah Harkless,\"$636,434\"\n")
    out = B.run_spot_checks(
        {"season": "2025-26",
         "amounts": {"Elijah Harkless": 636435, "Gary Payton II": 3303774},
         "appear_once": ["Elijah Harkless"]},
        records, tab)
    by_name = {e["player"]: e for e in out["amounts"]}
    assert by_name["Gary Payton II"]["result"] == "ok"
    assert by_name["Elijah Harkless"]["result"] == "mismatch"
    assert [r["row"] for r in by_name["Elijah Harkless"]["tab_rows"]] == [2, 3]
    assert out["appear_once"][0]["result"] == "ok"
