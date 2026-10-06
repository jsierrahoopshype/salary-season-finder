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
import re

import pytest

import build_data as B

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REAL_DATA = os.path.join(REPO, "data", "data.json")
REAL_BIO = os.path.join(REPO, "data_sources", "bio.csv")
REAL_OVERRIDES = os.path.join(REPO, "data", "bio_overrides.json")


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


# --------------------------------------------------------------------------
# stand-in bios
# --------------------------------------------------------------------------


def test_a_stand_in_bio_never_replaces_a_value_biocsv_carries():
    """The one rule of data/bio_overrides.json. An entry is a stand-in for a row
    that does not exist, so wherever bio.csv has a row the sheet wins field by
    field, including the fields it leaves blank."""
    index = register(person(
        "Marcus Thornton", draft=2009, pick=43, college="LSU",
        birthday="6/5/1987", pos="F-G", nat="United States"))
    index.add_override("Marcus Thornton", {
        "POS": "G", "HEIGHT": "6-4", "WEIGHT": "190",
        "NATIONALITY": "Canada", "COLLEGE / TEAM": "William & Mary",
        "DRAFT": "2015", "PICK": "45", "BIRTHDAY": "2/9/1993",
    })
    who = index.resolve("Marcus Thornton", "2010-11")
    bio = index.bio(who)
    assert bio["college"] == "LSU"
    assert (bio["draft_year"], bio["draft_pick"]) == (2009, 43)
    assert bio["pos"] == "F-G"
    assert bio["nationality"] == "United States"
    assert bio["birthday"] == "6/5/1987"
    # bio.csv leaves height and weight off this row, and they stay off: a
    # stand-in cannot fill a gap in a row that exists, only stand in for one
    # that does not.
    assert bio["height"] == ""
    assert bio["weight"] is None
    assert index.overrides_used == set()


def test_a_stand_in_bio_reaches_a_man_biocsv_has_no_row_for():
    """Same base name, no row for the suffix: the son resolves to a person of
    his own, and that is the man an entry is for."""
    index = register(person("Jameer Nelson", draft=2004, pick=20,
                            college="St. Joseph's (PA)"))
    index.add_override("Jameer Nelson Jr", {
        "POS": "G", "HEIGHT": "6-1", "WEIGHT": "190",
        "NATIONALITY": "United States", "COLLEGE / TEAM": "TCU",
        "DRAFT": "2024", "PICK": "UND", "BIRTHDAY": "8/7/2001",
    })
    son = index.resolve("Jameer Nelson Jr", "2026-27")
    father = index.resolve("Jameer Nelson", "2004-05")
    assert son != father
    assert index.bio(son)["college"] == "TCU"
    assert index.bio(son)["draft_year"] == 2024
    # the undrafted convention: the draft class is his, the pick is nobody's
    assert index.bio(son)["draft_pick"] is None
    assert index.bio(father)["college"] == "St. Joseph's (PA)"
    assert index.overrides_used == {("jameer nelson", "jr", "")}


def test_a_stand_in_bio_stops_applying_the_day_biocsv_gains_the_row():
    index = register(
        person("Jameer Nelson", draft=2004, pick=20, college="St. Joseph's (PA)"),
        person("Jameer Nelson Jr", draft=2024, college="TCU", pos="G"),
    )
    index.add_override("Jameer Nelson Jr", {
        "COLLEGE / TEAM": "Somewhere Else", "DRAFT": "2019", "PICK": "7",
    })
    son = index.resolve("Jameer Nelson Jr", "2026-27")
    assert index.bio(son)["college"] == "TCU"
    assert index.bio(son)["draft_year"] == 2024
    assert index.overrides_used == set()


def test_a_name_biocsv_has_never_heard_of_still_takes_its_stand_in():
    index = register(person("Someone Else", draft=2001))
    index.add_override("Nobody Known", {"COLLEGE / TEAM": "TCU", "DRAFT": "2024",
                                        "PICK": "UND"})
    who = index.resolve("Nobody Known", "2025-26")
    assert index.bio(who) == {
        "pos": "", "height": "", "weight": None, "nationality": "",
        "college": "TCU", "draft_year": 2024, "draft_pick": None, "birthday": "",
    }


def test_a_bio_block_reads_a_sheet_row_the_same_way_whichever_sheet_it_is_on():
    row = {
        "POS": " G ", "HEIGHT": "6-4", "WEIGHT": "190",
        "NATIONALITY": "United States", "COLLEGE / TEAM": " William & Mary ",
        "DRAFT": "2015", "PICK": "45", "BIRTHDAY": "2/9/1993",
    }
    assert B.bio_block(row) == {
        "pos": "G", "height": "6-4", "weight": 190,
        "nationality": "United States", "college": "William & Mary",
        "draft_year": 2015, "draft_pick": 45, "birthday": "2/9/1993",
    }
    assert B.bio_block({})["draft_pick"] is None


@pytest.mark.skipif(not os.path.exists(REAL_OVERRIDES),
                    reason="data/bio_overrides.json not present")
def test_the_shipped_stand_in_bios_name_their_sources_and_use_the_sheets_formats():
    entries = B.load_bio_overrides()
    assert entries, "data/bio_overrides.json has no entries"
    for name, entry in entries.items():
        assert entry.get("note"), name
        sources = entry.get("source")
        assert isinstance(sources, list) and sources, name
        for url in sources:
            assert url.startswith("https://"), (name, url)
        block = B.bio_block(entry)
        # the sheet's own formats, read back through the sheet's own parser
        assert block["nationality"], name
        assert re.fullmatch(r"\d-\d{1,2}", block["height"]), (name, block["height"])
        assert re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", block["birthday"]), name
        assert isinstance(block["weight"], int), name
        assert block["draft_year"], name
        # the undrafted convention: PICK is the literal UND, never blank, and
        # the draft class year stays on the row
        if entry.get("PICK") == "UND":
            assert block["draft_pick"] is None, name
        else:
            assert 1 <= block["draft_pick"] <= 60, name


@pytest.mark.skipif(not os.path.exists(REAL_OVERRIDES) or not os.path.exists(REAL_BIO),
                    reason="data/bio_overrides.json or data_sources/bio.csv not present")
def test_no_shipped_stand_in_bio_shadows_a_biocsv_row():
    """Read against the real sheet: every entry has to be for a man bio.csv has
    no row for, so none of them can be shadowing one."""
    with open(REAL_BIO, "r", encoding="utf-8-sig") as fh:
        index = B.PersonIndex()
        for row in B.parse_csv_string(fh.read()):
            index.add_person(row)
    for name in B.load_bio_overrides():
        base, suffix, marker = B.split_player_name(name)
        exact = [pid for pid in index.by_base.get(base, ())
                 if index.people[pid]["suffix"] == suffix
                 and index.people[pid]["marker"] == marker]
        assert not exact, (name, exact)


@pytest.mark.skipif(not os.path.exists(REAL_OVERRIDES),
                    reason="data/bio_overrides.json not present")
def test_marcus_thornton_ii_is_not_filed_as_anybodys_son():
    """The II in the salary sheet's spelling tells two unrelated men of one name
    apart; it is not a family suffix. This one is the William & Mary guard born
    in 1993 and taken 45th in 2015, and nothing may tie him to the 2009 LSU pick
    the sheets spell without the II: not a shared college, not a shared draft
    class, not a shared draft slot, and not an alias merging the two names.
    """
    entry = B.load_bio_overrides()["Marcus Thornton II"]
    block = B.bio_block(entry)
    assert block["college"] == "William & Mary"
    assert (block["draft_year"], block["draft_pick"]) == (2015, 45)
    assert block["birthday"] == "2/9/1993"
    assert "not a son" in entry["note"].lower()

    if not os.path.exists(REAL_BIO):
        pytest.skip("data_sources/bio.csv not present")
    with open(REAL_BIO, "r", encoding="utf-8-sig") as fh:
        rows = [r for r in B.parse_csv_string(fh.read())
                if r["PLAYER"].strip() == "Marcus Thornton"]
    assert len(rows) == 1, rows
    lsu = B.bio_block(rows[0])
    assert lsu["college"] == "LSU"
    # the three cohort keys that would put them in one another's company
    assert block["college"] != lsu["college"]
    assert block["draft_year"] != lsu["draft_year"]
    assert block["draft_pick"] != lsu["draft_pick"]
    assert block["birthday"] != lsu["birthday"]


@pytest.mark.skipif(not os.path.exists(REAL_DATA) or not os.path.exists(REAL_OVERRIDES),
                    reason="data/data.json or data/bio_overrides.json not present")
def test_every_shipped_stand_in_bio_reaches_a_player_in_the_data():
    """A stand-in that reaches nobody is silent damage: the man it was written
    for is back to carrying no bio and nothing says so."""
    with open(REAL_DATA, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    keys = {B.split_player_name(name)[:3] for name in B.load_bio_overrides()}
    seen = {B.split_player_name(name)[:3] for name in data["players"]}
    assert keys <= seen, sorted(keys - seen)


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
def test_career_earnings_runs_as_one_mans_total_from_first_season_to_last():
    """The first record opening on the player's own first salary is only half of
    it: a total that is right in 1990-91 and wrong in 1995-96 passes that check.
    Every season has to read the running sum of the salaries above it under the
    same name.

    Corey Brewer is the one key this cannot hold for, because it covers two men:
    bio.csv has a row for the 2007 Florida pick and none for whoever Miami paid
    $100,000 in 1999-00, so the build cannot give the second man a name of his
    own. identity_splits.json records the split, and factoids.py closes every
    career-level claim on the key.
    """
    with open(REAL_DATA, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    by_player = {}
    for record in data["seasons"]:
        by_player.setdefault(record["player"], []).append(record)
    offenders = {}
    for name, records in by_player.items():
        records.sort(key=lambda r: int(str(r["season"]).split("-")[0]))
        running = 0
        for record in records:
            running += record.get("salary") or 0
            if record.get("career_earnings") != running:
                offenders[name] = (record["season"],
                                   record.get("career_earnings"), running)
                break
    assert set(offenders) == {"Corey Brewer"}, offenders

    with open(os.path.join(REPO, "data", "identity_splits.json"), "r",
              encoding="utf-8") as fh:
        splits = json.load(fh)["entries"]
    entry = splits.get("Corey Brewer")
    assert entry and entry["split"] and entry["confirmed"], entry


@pytest.mark.skipif(not os.path.exists(REAL_DATA) or not os.path.exists(REAL_BIO),
                    reason="data/data.json or data_sources/bio.csv not present")
def test_each_man_of_a_shared_name_carries_his_own_bio_and_nobody_elses():
    """Where bio.csv holds more than one person under one base name, every
    data.json key built on that name has to carry one of those people's bio
    exactly, and it has to be a person whose career could hold the key's
    seasons.

    The draft-year and career-earnings checks above miss one shape of this. A
    son who carries his father's bio reads a draft year *earlier* than his own
    debut, which is what a late-starting career looks like, and his running
    total is his own if the father was never paid inside this window. Jim Paxson
    reading Jim Paxson Sr's 1956 draft would pass both and fail here.
    """
    with open(REAL_BIO, "r", encoding="utf-8-sig") as fh:
        rows = B.parse_csv_string(fh.read())
    people = {}
    for row in rows:
        base, _suffix, _marker = B.split_player_name((row.get("PLAYER") or "").strip())
        if base:
            people.setdefault(base, []).append(row)
    shared = {base: rows for base, rows in people.items() if len(rows) > 1}
    assert len(shared) > 30, "bio.csv should hold dozens of shared base names"

    # A man bio.csv has no row for carries his stand-in bio instead, so that
    # counts as one of the people a key on his name may match.
    for name, entry in B.load_bio_overrides().items():
        base, _suffix, _marker = B.split_player_name(name)
        if base in shared:
            shared[base] = shared[base] + [dict(entry, PLAYER=name)]

    with open(REAL_DATA, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    by_player = {}
    for record in data["seasons"]:
        by_player.setdefault(record["player"], []).append(record)

    checked = 0
    for name, records in sorted(by_player.items()):
        base, _suffix, _marker = B.split_player_name(name)
        if base not in shared:
            continue
        records.sort(key=lambda r: int(str(r["season"]).split("-")[0]))
        stamps = {
            (r.get("college") or "", r.get("draft_year"), r.get("draft_pick"),
             r.get("pos") or "", r.get("height") or "", r.get("weight"))
            for r in records
        }
        assert len(stamps) == 1, (name, sorted(stamps))
        stamp = stamps.pop()
        if stamp == ("", None, None, "", "", None):
            # bio.csv has never heard of this spelling, so the build leaves the
            # bio blank rather than borrowing a namesake's. Nothing to match.
            continue
        matches = [
            row for row in shared[base]
            if (row["COLLEGE / TEAM"].strip(), B.parse_int(row["DRAFT"]),
                B.parse_int(row["PICK"])) == (stamp[0], stamp[1], stamp[2])
        ]
        assert len(matches) == 1, (name, stamp, len(matches))
        drafted = B.parse_int(matches[0]["DRAFT"])
        if drafted is not None:
            first = int(str(records[0]["season"]).split("-")[0])
            last = int(str(records[-1]["season"]).split("-")[0])
            assert drafted <= first, (name, drafted, records[0]["season"])
            assert last <= drafted + B.MAX_CAREER_SPAN, (
                name, drafted, records[-1]["season"])
        checked += 1
    assert checked > 50, checked


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
