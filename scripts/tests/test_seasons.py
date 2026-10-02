"""Tests for the player page's season paragraphs.

Each test hands the writer a season's worth of claims and reads the paragraph
back, so a failure names a rule of the prose rather than a quirk of the data.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import factoids as F  # noqa: E402
from prerender import seasons as S  # noqa: E402

CURRENT = "2026-27"
PAST = "2024-25"
FUTURE = "2027-28"


class FakeIndex(object):
    """Just the three things the writer asks an index for."""

    def __init__(self, current=CURRENT, in_progress=True):
        self.current_season = current
        self.current_key = F.season_key(current)
        self.current_season_in_progress = in_progress
        self.franchises = {"DAL": {"name": "Mavericks"}}

    def college_display(self, key):
        return {"Duke": "Duke", "Arizona St": "Arizona State"}.get(key, key)


def fact(key, kind="sets", value=1000000, rank=1, size=40, holder=None,
         text="Engine wrote this."):
    return {
        "key": key, "type": kind, "value": value, "rank": rank,
        "comparison_size": size, "text": text,
        "previous_holder": {"player": holder, "season": PAST, "value": 1}
        if holder else None,
    }


def cohort(field, ckey, measure="cohort_season", **kw):
    """A cohort claim. ``field`` is the cohort kind; ``kind`` is the claim type."""
    return fact("{}|{}|{}|Subject|{}".format(measure, field, ckey, CURRENT), **kw)


def write(rows, name="Test Man", idx=None):
    return S.paragraphs(idx or FakeIndex(), name, rows)


# --------------------------------------------------------------------------
# one paragraph per season, newest first
# --------------------------------------------------------------------------


def test_seasons_come_newest_first():
    rows = [(PAST, cohort("college", "Duke")),
            (CURRENT, cohort("college", "Duke")),
            (FUTURE, cohort("college", "Duke"))]
    assert [season for season, _key, _text in write(rows)] == \
        [FUTURE, CURRENT, PAST]


def test_a_season_is_one_paragraph():
    out = write([(CURRENT, cohort("college", "Duke"))])
    assert len(out) == 1
    assert len(out[0][2]) == 1


# --------------------------------------------------------------------------
# at most three claims, most notable first
# --------------------------------------------------------------------------


def test_only_three_claims_survive_a_crowded_season():
    rows = [
        (CURRENT, cohort("college", "Duke", value=1 + i, kind="approaches",
                         rank=2 + i, size=100 - i))
        for i in range(6)
    ]
    assert len(write(rows)[0][2]) == 3


def test_a_record_outranks_a_milestone_and_a_rank_shift():
    rows = [
        (CURRENT, fact("rank_league_first|Subject|" + CURRENT, kind="rank_shift",
                       value=5)),
        (CURRENT, fact("career_milestone|Subject|" + CURRENT, kind="milestone",
                       value=6)),
        (CURRENT, cohort("college", "Duke", value=7)),
    ]
    first = write(rows)[0][2][0]
    assert "Duke" in first


def test_a_wider_field_comes_before_a_narrower_one():
    rows = [
        (CURRENT, cohort("college", "Duke", kind="approaches", rank=3, size=20)),
        (CURRENT, cohort("position", "G", kind="approaches", rank=3, size=400)),
    ]
    assert "guard" in write(rows)[0][2][0]


# --------------------------------------------------------------------------
# claims about one number become one sentence
# --------------------------------------------------------------------------


def test_claims_about_the_same_figure_are_one_sentence():
    rows = [
        (CURRENT, cohort("college", "Duke", value=391900000,
                         measure="cohort_career")),
        (CURRENT, cohort("position", "G", value=391900000, kind="approaches",
                         rank=5, holder="Stephen Curry",
                         measure="cohort_career")),
    ]
    out = write(rows)[0][2]
    assert len(out) == 1
    assert "the most of any Duke player" in out[0]
    assert "fifth among guards behind Stephen Curry" in out[0]


def test_two_different_figures_stay_two_sentences():
    rows = [
        (CURRENT, cohort("college", "Duke", value=391900000,
                         measure="cohort_career")),
        (CURRENT, cohort("college", "Duke", value=39500000)),
    ]
    assert len(write(rows)[0][2]) == 2


# --------------------------------------------------------------------------
# a claim a wider field already made
# --------------------------------------------------------------------------


def test_a_college_position_record_goes_when_the_college_record_holds():
    rows = [
        (CURRENT, cohort("college", "Duke")),
        (CURRENT, cohort("college_position", "Duke|G")),
    ]
    out = write(rows)[0][2]
    assert "Duke player" in out[0]
    assert "Duke guard" not in out[0]


def test_a_college_position_record_stays_when_the_college_record_does_not():
    rows = [(CURRENT, cohort("college_position", "Duke|G"))]
    assert "Duke guard" in write(rows)[0][2][0]


def test_a_top_10_record_goes_when_the_lottery_record_holds():
    rows = [
        (CURRENT, cohort("pick_range", "lottery")),
        (CURRENT, cohort("pick_range", "top-10")),
    ]
    out = write(rows)[0][2][0]
    assert "lottery pick" in out
    assert "top-10" not in out


def test_a_regional_record_goes_when_the_international_record_holds():
    rows = [
        (CURRENT, cohort("region", "international")),
        (CURRENT, cohort("region", "europe")),
    ]
    out = write(rows)[0][2][0]
    assert "international player" in out
    assert "European" not in out


def test_containment_only_applies_to_a_record():
    """Fifth among lottery picks says nothing about the top-10 list."""
    rows = [
        (CURRENT, cohort("pick_range", "lottery", kind="approaches", rank=5,
                         holder="Someone")),
        (CURRENT, cohort("pick_range", "top-10", kind="approaches", rank=3,
                         holder="Someone")),
    ]
    out = write(rows)[0][2][0]
    assert "lottery" in out and "top-10" in out


# --------------------------------------------------------------------------
# his name once, then "he"
# --------------------------------------------------------------------------


def test_he_is_named_once_a_paragraph():
    rows = [
        (CURRENT, cohort("college", "Duke", value=391900000,
                         measure="cohort_career")),
        (CURRENT, cohort("college", "Duke", value=39500000)),
    ]
    paragraph = " ".join(write(rows, name="Kyrie Irving")[0][2])
    assert paragraph.count("Kyrie Irving") == 1


def test_the_engine_sentence_loses_the_repeated_name_too():
    rows = [
        (CURRENT, cohort("college", "Duke", value=39500000)),
        (CURRENT, fact("career_milestone|Subject|" + CURRENT, kind="milestone",
                       value=400000000,
                       text="Kyrie Irving passed $400 million in career earnings.")),
    ]
    paragraph = write(rows, name="Kyrie Irving")[0][2]
    assert paragraph[1].startswith("He passed $400 million")


def test_a_possessive_engine_sentence_becomes_his():
    rows = [
        (CURRENT, cohort("college", "Duke", value=39500000)),
        (CURRENT, fact("rank_league_first|Subject|" + CURRENT, kind="rank_shift",
                       value=1,
                       text="Kyrie Irving's rank moved to first.")),
    ]
    paragraph = write(rows, name="Kyrie Irving")[0][2]
    assert paragraph[1] == "His rank moved to first."


# --------------------------------------------------------------------------
# tense
# --------------------------------------------------------------------------


def test_a_past_season_reads_in_the_past():
    rows = [(PAST, cohort("college", "Duke", value=41000000))]
    assert "was paid $41 million in 2024-25" in write(rows)[0][2][0]


def test_the_season_under_way_is_not_earned_yet():
    rows = [(CURRENT, cohort("college", "Duke", value=391900000,
                             measure="cohort_career"))]
    assert "will have earned $391.9 million by the end of 2026-27" in \
        write(rows)[0][2][0]


def test_a_finished_current_season_has_earned_its_money():
    rows = [(CURRENT, cohort("college", "Duke", value=391900000,
                             measure="cohort_career"))]
    idx = FakeIndex(in_progress=False)
    assert "has earned $391.9 million through 2026-27" in \
        write(rows, idx=idx)[0][2][0]


def test_a_contracted_season_is_money_he_is_due():
    rows = [(FUTURE, cohort("college", "Duke", value=42400000))]
    assert "is due $42.4 million in 2027-28" in write(rows)[0][2][0]


# --------------------------------------------------------------------------
# variety
# --------------------------------------------------------------------------


def test_a_page_does_not_open_eight_seasons_the_same_way():
    rows = [
        (season, cohort("college", "Duke", value=1000000 + i))
        for i, season in enumerate(
            ("2018-19", "2019-20", "2020-21", "2021-22", "2022-23", "2023-24"))
    ]
    openings = [S._opening(group[2][0]) for group in write(rows)]
    assert len(set(openings)) > 1
    assert openings[0] != openings[1]


def test_the_same_claims_always_write_the_same_page():
    rows = [(season, cohort("college", "Duke", value=1000000 + i))
            for i, season in enumerate(("2021-22", "2022-23", "2023-24"))]
    assert write(rows) == write(rows)


def test_no_sentence_opens_with_a_lower_case_pronoun():
    rows = [
        (CURRENT, cohort("college", "Duke", value=391900000,
                         measure="cohort_career")),
        (CURRENT, cohort("college", "Duke", value=39500000)),
    ]
    for sentence in write(rows)[0][2]:
        assert sentence[0].isupper(), sentence


# --------------------------------------------------------------------------
# the comparison clauses
# --------------------------------------------------------------------------


def test_every_cohort_kind_has_a_phrase():
    idx = FakeIndex()
    for kind, key, expect in (
        ("college", "Duke", "Duke player"),
        ("college_position", "Duke|G", "Duke guard"),
        ("draft_class", "2011", "2011 draft class"),
        ("draft_slot", "1", "No. 1 pick"),
        ("draft_slot", "undrafted", "undrafted player"),
        ("position", "C", "center"),
        ("nationality", "France", "player from France"),
        ("region", "europe", "European player"),
        ("pick_range", "lottery", "lottery pick"),
        ("franchise", "DAL", "Mavericks history"),
    ):
        one, many = S._where(idx, kind, key)
        assert one and many, (kind, key)
        assert expect in one, (kind, key, one)


def test_a_college_name_is_spelled_out():
    assert "Arizona State" in S._where(FakeIndex(), "college", "Arizona St")[0]


def test_an_unknown_field_writes_no_clause():
    assert S._where(FakeIndex(), "nothing", "x") == (None, None)
