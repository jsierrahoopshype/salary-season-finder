"""Tests for the player identity join in build_data.py.

The salary sheets carry no player id, so the build has to recognise a person by
name, and this league has had a Gary Payton and a Gary Payton II. Every test
below is one rule of that join, on a register small enough to read.

The last block runs over the real data/data.json and checks the two things the
join exists to get right: no record carries another man's bio, and
career_earnings is one man's running total.
"""

from __future__ import annotations

import json
import os

import pytest

import build_data as B

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REAL_DATA = os.path.join(REPO, "data", "data.json")


def person(name, draft=None, pick=None, college="", birthday="", pos="", nat=""):
    """One row of bio.csv, with only the columns a test cares about."""
    return {
        "PLAYER": name,
        "DRAFT": "" if draft is None else str(draft),
        "PICK": "" if pick is None else str(pick),
        "COLLEGE / TEAM": college,
        "BIRTHDAY": birthday,
        "POS": pos,
        "NATIONALITY": nat,
        "HEIGHT": "",
        "WEIGHT": "",
    }


def register(*rows):
    index = B.PersonIndex()
    for row in rows:
        index.add_person(row)
    return index


# --------------------------------------------------------------------------
# pulling a name apart
# --------------------------------------------------------------------------


def test_a_suffix_comes_off_the_base_without_being_thrown_away():
    assert B.split_player_name("Gary Payton II") == ("gary payton", "ii", "")
    assert B.split_player_name("Larry Nance, Jr.") == ("larry nance", "jr", "")
    assert B.split_player_name("Terrence Shannon Jr.") == ("terrence shannon", "jr", "")
    assert B.split_player_name("Gary Payton") == ("gary payton", "", "")


def test_a_birth_year_marker_is_kept_as_its_own_field():
    assert B.split_player_name("Charles Smith (1965)") == ("charles smith", "", "1965")
    assert B.split_player_name("Chris Smith") == ("chris smith", "", "")


def test_accents_punctuation_and_case_fold_away():
    assert B.split_player_name("Nikola Jokić")[0] == "nikola jokic"
    assert B.split_player_name("D'Angelo Russell")[0] == "dangelo russell"
    assert B.split_player_name("  Jaren  Jackson  ")[0] == "jaren jackson"


def test_a_known_alias_resolves_to_the_canonical_base():
    assert B.split_player_name("Metta World Peace")[0] == "ron artest"


def test_a_name_that_is_only_a_suffix_keeps_its_token():
    assert B.split_player_name("Jr")[0] == "jr"


# --------------------------------------------------------------------------
# who a spelling belongs to
# --------------------------------------------------------------------------


def test_a_father_and_a_son_are_told_apart_by_the_suffix():
    index = register(
        person("Gary Payton", draft=1990, pick=2, college="Oregon St"),
        person("Gary Payton II", draft=2016, college="Oregon St"),
    )
    father = index.resolve("Gary Payton", "1995-96")
    son = index.resolve("Gary Payton II", "2021-22")
    assert father != son
    assert index.bio(father)["draft_pick"] == 2
    assert index.bio(son)["draft_year"] == 2016


def test_the_season_decides_when_the_sheet_puts_the_sons_suffix_on_the_father():
    """Every Jaren Jackson salary row reads 'Jaren Jackson Jr', the father's
    included, so the suffix cannot be taken at face value."""
    index = register(
        person("Jaren Jackson", draft=1989, college="Georgetown"),
        person("Jaren Jackson Jr", draft=2018, college="Michigan St"),
    )
    father = index.resolve("Jaren Jackson Jr", "1996-97")
    son = index.resolve("Jaren Jackson Jr", "2022-23")
    assert father != son
    assert index.bio(father)["college"] == "Georgetown"
    assert index.bio(son)["college"] == "Michigan St"
    assert index.display(father) == "Jaren Jackson"
    assert index.display(son) == "Jaren Jackson Jr"


def test_a_birth_year_marker_the_sheets_leave_off_is_settled_by_the_season():
    index = register(
        person("Brandon Williams", draft=2021, college="Arizona"),
        person("Brandon Williams (1975)", draft=1996, college="Davidson"),
    )
    older = index.resolve("Brandon Williams", "1998-99")
    newer = index.resolve("Brandon Williams", "2022-23")
    assert index.bio(older)["college"] == "Davidson"
    assert index.bio(newer)["college"] == "Arizona"
    assert index.display(older) == "Brandon Williams (1975)"


def test_a_suffix_the_register_has_never_seen_is_a_man_it_does_not_know():
    """Jameer Nelson Jr is not his father, and bio.csv has never heard of him,
    so his record gets no bio rather than his father's."""
    index = register(person("Jameer Nelson", draft=2004, college="St. Joseph's (PA)"))
    father = index.resolve("Jameer Nelson", "2008-09")
    son = index.resolve("Jameer Nelson Jr", "2026-27")
    assert father != son
    assert index.bio(son) == {}
    assert index.display(son) == "Jameer Nelson Jr"


def test_a_suffix_a_stable_id_spells_belongs_to_the_man_who_lacks_it():
    """bio.csv writes 'Terrence Shannon'; the id columns beside the name in
    stats.csv write 'Terrence Shannon Jr.', which is the same man."""
    index = register(person("Terrence Shannon", draft=2024, college="Illinois"))
    index.add_suffix_hint("Terrence Shannon", "Terrence Shannon Jr.")
    bare = index.resolve("Terrence Shannon", "2026-27")
    suffixed = index.resolve("Terrence Shannon Jr.", "2024-25")
    assert bare == suffixed
    assert index.display(bare) == "Terrence Shannon Jr"


def test_a_stable_id_does_not_add_a_suffix_nobody_writes():
    """The hint is only read when a sheet actually spells the suffix, so a
    player whose rows all read 'Jimmy Butler' is not renamed."""
    index = register(person("Jimmy Butler", draft=2011, college="Marquette"))
    index.add_suffix_hint("Jimmy Butler", "Jimmy Butler III")
    assert index.display(index.resolve("Jimmy Butler", "2024-25")) == "Jimmy Butler"


def test_one_man_spelled_two_ways_is_one_person():
    index = register(person("Wendell Carter Jr", draft=2018, college="Duke"))
    with_suffix = index.resolve("Wendell Carter Jr", "2018-19")
    without = index.resolve("Wendell Carter", "2024-25")
    assert with_suffix == without
    assert index.display(without) == "Wendell Carter Jr"


def test_a_season_before_the_only_candidates_draft_is_another_man():
    """bio.csv has one Corey Brewer, drafted in 2007. The man paid in 1999-00 is
    somebody else, and the file cannot say who."""
    index = register(person("Corey Brewer", draft=2007, college="Florida"))
    known = index.resolve("Corey Brewer", "2007-08")
    other = index.resolve("Corey Brewer", "1999-00")
    assert known != other
    assert index.bio(other) == {}
    assert index.display(other) == "Corey Brewer"


def test_a_season_a_lifetime_after_the_draft_is_another_man():
    """A salary paid 29 years after a draft is not that draftee's: the John
    Lucas paid in 2005-06 is John Lucas III."""
    index = register(
        person("John Lucas", draft=1976, college="Maryland"),
        person("John Lucas III", draft=2005, college="Oklahoma St"),
    )
    who = index.resolve("John Lucas", "2005-06")
    assert index.bio(who)["college"] == "Oklahoma St"
    assert index.display(who) == "John Lucas III"


def test_the_last_man_drafted_takes_a_season_both_could_have_played():
    index = register(
        person("Ken Johnson (1962)", draft=1985),
        person("Ken Johnson (1978)", draft=2001),
    )
    assert index.display(index.resolve("Ken Johnson", "2002-03")) == "Ken Johnson (1978)"


def test_a_name_the_register_never_heard_keeps_its_spellings_together():
    """With no bio row there is no father and son to keep apart, so a suffix is
    only a fuller spelling of the same man."""
    index = register(person("Somebody Else", draft=2000))
    bare = index.resolve("Terrell Brown", "2026-27")
    suffixed = index.resolve("Terrell Brown Jr", "2026-27")
    assert bare == suffixed
    assert index.bio(bare) == {}


def test_the_printed_name_for_an_unknown_man_is_the_spelling_the_sheets_use():
    index = B.PersonIndex()
    index.resolve("Marcus Thornton II", "2017-18")
    index.resolve("Marcus Thornton II", "2018-19")
    index.resolve("Marcus Thornton 2", "2017-18", note=False)
    assert index.display(index.resolve("Marcus Thornton II", "2017-18")) \
        == "Marcus Thornton II"


def test_a_sheet_that_becomes_no_record_does_not_name_anybody():
    """The agent sheet is read with note=False, so its spelling never becomes
    the name data.json prints."""
    index = B.PersonIndex()
    pid = index.resolve("Scoot Henderson", "2024-25")
    index.resolve("Scoot Henderson Jr", note=False)
    assert index.display(pid) == "Scoot Henderson"


def test_without_a_season_only_an_unambiguous_answer_comes_back():
    index = register(
        person("Chris Wright (1988)", draft=2011),
        person("Chris Wright (1989)", draft=2011),
    )
    guessed = index.resolve("Chris Wright")
    assert index.bio(guessed) == {}


def test_an_empty_name_resolves_to_nobody():
    assert register().resolve("") is None
    assert register().resolve("   ") is None


# --------------------------------------------------------------------------
# the real data
# --------------------------------------------------------------------------


@pytest.mark.skipif(not os.path.exists(REAL_DATA), reason="data/data.json not present")
def test_no_record_carries_a_draft_that_postdates_the_career_it_is_on():
    """A draft year later than a player's first season is another man's."""
    with open(REAL_DATA, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    first = {}
    offenders = set()
    for record in data["seasons"]:
        start = int(str(record["season"]).split("-")[0])
        name = record["player"]
        first[name] = min(first.get(name, start), start)
    for record in data["seasons"]:
        drafted = record.get("draft_year")
        if drafted and drafted > first[record["player"]]:
            offenders.add(record["player"])
    # Corey Brewer is the one name left: two men, and bio.csv can name only one.
    assert offenders == {"Corey Brewer"}, sorted(offenders)


@pytest.mark.skipif(not os.path.exists(REAL_DATA), reason="data/data.json not present")
def test_career_earnings_opens_on_a_players_own_first_salary():
    with open(REAL_DATA, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    seasons = {}
    for record in data["seasons"]:
        name = record["player"]
        key = int(str(record["season"]).split("-")[0])
        if name not in seasons or key < seasons[name][0]:
            seasons[name] = (key, record)
    carried = {
        name: (rec["career_earnings"], rec["salary"])
        for name, (_k, rec) in seasons.items()
        if rec.get("career_earnings") is not None
        and rec["career_earnings"] > (rec.get("salary") or 0) + 1
    }
    assert carried == {}, carried


@pytest.mark.skipif(not os.path.exists(REAL_DATA), reason="data/data.json not present")
def test_one_person_never_appears_under_two_spellings_of_the_same_name():
    """Two display names that differ only by a suffix are a father and a son, so
    they have no season in common. One man spelled two ways would.

    Names told apart by a birth year ('Charles Smith (1965)') are grouped on
    that marker rather than here, because three men of one name did share
    seasons with each other.
    """
    with open(REAL_DATA, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    groups = {}
    for name in data["players"]:
        base, _suffix, marker = B.split_player_name(name)
        groups.setdefault((base, marker), set()).add(name)
    seasons = {}
    for record in data["seasons"]:
        seasons.setdefault(record["player"], set()).add(record["season"])
    for base, names in sorted(groups.items()):
        if len(names) < 2:
            continue
        for name in names:
            others = set()
            for other in names - {name}:
                others |= seasons.get(other, set())
            overlap = seasons.get(name, set()) & others
            assert not overlap, (base, name, sorted(overlap))


# --------------------------------------------------------------------------
# awards
# --------------------------------------------------------------------------

AWARD_HEADER = ",,AWARD,YEAR,RG CODE,PLAYER / COACH,TEAM,CAREER AWARDS,SEASON AWARDS"


def awards_csv(*rows):
    """The awards sheet, with only the three columns the build reads."""
    lines = [AWARD_HEADER]
    for player, year, awards in rows:
        lines.append(',,,{},,{},,,"{}"'.format(year, player, awards))
    return "\n".join(lines) + "\n"


def test_an_award_counts_for_the_man_the_row_names():
    index = register(
        person("Gary Payton", draft=1990, pick=2),
        person("Gary Payton II", draft=2016),
    )
    lookup, kinds = B.process_awards(awards_csv(
        ("Gary Payton", "1996", "All-Defensive First Team"),
        ("Gary Payton", "2021", "NBA Top-75"),
        ("Gary Payton II", "2022", "NBA Champion"),
    ), index)
    father = index.resolve("Gary Payton", "1995-96", note=False, span=False)
    son = index.resolve("Gary Payton II", "2021-22", note=False, span=False)
    assert lookup[(father, "1995-96")] == ["All-Defensive First Team"]
    # a retrospective honour is his too, thirty years after his draft
    assert lookup[(father, "2020-21")] == ["NBA Top-75"]
    assert (son, "2020-21") not in lookup
    assert lookup[(son, "2021-22")] == ["NBA Champion"]
    assert kinds == {"All-Defensive First Team", "NBA Top-75", "NBA Champion"}


def test_an_honour_decades_after_a_draft_is_still_his():
    """The career-span ceiling is for salaries. A salary is never paid 30 years
    after a draft; an honour can be handed to a man twenty years retired."""
    index = register(person("Old Timer", draft=1985))
    paid = index.resolve("Old Timer", "2021-22")
    honoured = index.resolve("Old Timer", "2021-22", span=False)
    assert index.bio(paid) == {}
    assert index.bio(honoured)["draft_year"] == 1985


def test_the_award_list_keeps_the_sheet_order_and_drops_repeats():
    index = register(person("Busy Man", draft=2016))
    lookup, _kinds = B.process_awards(awards_csv(
        ("Busy Man", "2020", "All-Star, Player of the Week"),
        ("Busy Man", "2020", "Player of the Week, Blocks Leader"),
    ), index)
    who = index.resolve("Busy Man", "2019-20", note=False, span=False)
    assert lookup[(who, "2019-20")] == [
        "All-Star", "Player of the Week", "Blocks Leader"]


def test_an_override_moves_a_row_the_sheet_files_under_the_wrong_name(tmp_path,
                                                                     monkeypatch):
    index = register(
        person("Jaren Jackson", draft=1989),
        person("Jaren Jackson Jr", draft=2018),
    )
    overrides = {
        "overrides": [{
            "season": "2021-22",
            "awards": ["Blocks Leader"],
            "filed_as": "Jaren Jackson",
            "belongs_to": "Jaren Jackson Jr",
            "evidence": "he led the league in blocks that season",
        }],
    }
    path = tmp_path / "data"
    path.mkdir()
    (path / "award_overrides.json").write_text(json.dumps(overrides), encoding="utf-8")
    monkeypatch.setattr(B, "BASE_DIR", str(tmp_path))

    lookup, _kinds = B.process_awards(awards_csv(
        ("Jaren Jackson", "2022", "Blocks Leader"),
        ("Jaren Jackson Jr", "2022", "All-Defensive First Team"),
    ), index)
    father = index.resolve("Jaren Jackson", "2021-22", note=False, span=False)
    son = index.resolve("Jaren Jackson Jr", "2021-22", note=False, span=False)
    assert (father, "2021-22") not in lookup
    assert lookup[(son, "2021-22")] == ["All-Defensive First Team", "Blocks Leader"]


def test_the_shipped_overrides_name_a_season_an_award_and_two_people():
    overrides = B.load_award_overrides()
    assert overrides, "data/award_overrides.json has no entries"
    for entry in overrides:
        assert entry["awards"], entry
        assert entry["filed_as"] and entry["belongs_to"], entry
        assert entry["filed_as"] != entry["belongs_to"], entry
        assert entry["evidence"], entry
        assert B.normalize_season(entry["season"]) == entry["season"], entry


@pytest.mark.skipif(not os.path.exists(REAL_DATA), reason="data/data.json not present")
def test_the_real_awards_sit_on_the_right_man():
    with open(REAL_DATA, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    awards = {(r["player"], r["season"]): set(r.get("awards") or [])
              for r in data["seasons"]}
    # the override holds these two in place
    assert "Blocks Leader" in awards[("Jaren Jackson Jr", "2021-22")]
    assert "Blocks Leader" in awards[("Jaren Jackson Jr", "2022-23")]
    # and the join keeps his father's honours off the son
    assert "NBA Top-75" not in awards.get(("Gary Payton II", "2020-21"), set())
    assert "HoopsHype Top-78" not in awards.get(("Gary Payton II", "2022-23"), set())
