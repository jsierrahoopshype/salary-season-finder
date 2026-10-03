"""Tests for a player page's summary paragraph.

Each test hands the writer a set of claims and reads the paragraph back, so a
failure names a rule of the prose rather than a quirk of the data.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import factoids as F  # noqa: E402
from prerender import seasons as S  # noqa: E402

CURRENT = "2026-27"
FUTURE = "2027-28"


class FakeIndex(object):
    """Just what the writer asks an index for."""

    def __init__(self, current=CURRENT, in_progress=True, paid=None):
        self.current_season = current
        self.current_key = F.season_key(current)
        self.current_season_in_progress = in_progress
        self.franchises = {"DAL": {"name": "Mavericks"},
                           "MIA": {"name": "Heat"}}
        self._paid = paid or ("Subject", 100000000, "2026-27")

    def is_contracted(self, season):
        return F.season_key(season) > self.current_key

    def college_display(self, key):
        return {"Duke": "Duke", "Arizona St": "Arizona State"}.get(key, key)

    def career_rankable(self, _player):
        return True

    def paid_through(self, _player):
        return self._paid[1], self._paid[2]


def fact(key, kind="sets", value=1000000, rank=1, size=400, **extra):
    out = {"key": key, "type": kind, "value": value, "rank": rank,
           "comparison_size": size, "text": "Engine wrote this."}
    out.update(extra)
    return out


def season_fact(field, ckey, season=CURRENT, **kw):
    return fact("cohort_season|{}|{}|Subject|{}".format(field, ckey, season), **kw)


def career_fact(field, ckey, season=CURRENT, **kw):
    return fact("cohort_career|{}|{}|Subject|{}".format(field, ckey, season), **kw)


def write(rows, name="Test Man", idx=None, player="Subject"):
    return S.summary(idx or FakeIndex(), name, rows, player=player)


def run(field, ckey, seasons, **kw):
    return [(s, season_fact(field, ckey, s, **kw)) for s in seasons]


# --------------------------------------------------------------------------
# a record held across seasons is said once, with its span
# --------------------------------------------------------------------------


def test_a_run_of_seasons_becomes_one_span_sentence():
    rows = run("draft_class", "2013",
               ["2021-22", "2022-23", "2023-24", "2024-25", "2025-26", CURRENT])
    out = write(rows, name="Giannis")
    assert out[0] == ("Giannis has been the highest-paid player from the 2013 "
                      "draft class every season since 2021-22.")


def test_a_span_that_has_ended_reads_in_the_past():
    rows = run("college", "Duke", ["2019-20", "2020-21", "2021-22"])
    out = write(rows, name="Kyrie")
    assert out[0] == ("Kyrie was the highest-paid Duke player in every season "
                      "from 2019-20 to 2021-22.")


def test_a_record_is_never_repeated_season_by_season():
    rows = run("draft_class", "2013", ["2024-25", "2025-26", CURRENT])
    text = " ".join(write(rows))
    assert text.count("2013 draft class") == 1
    assert text.count("highest-paid") == 1


def test_a_gap_breaks_a_span():
    rows = run("college", "Duke", ["2018-19", "2019-20", "2022-23", "2023-24",
                                   "2024-25"])
    out = write(rows, name="Man")
    assert "from 2022-23 to 2024-25" in out[0]


def test_one_season_alone_is_not_a_span():
    rows = run("college", "Duke", ["2021-22"])
    out = write(rows, name="Man")
    assert "highest-paid Duke player" not in (out[0] if out else "")


def test_a_contracted_season_is_not_part_of_a_span():
    """Nobody has been paid it, so it is not a season he "has been" anything in."""
    rows = run("draft_class", "2013", ["2025-26", CURRENT, FUTURE])
    out = write(rows, name="Man")
    assert "every season since 2025-26" in out[0]
    assert FUTURE not in out[0]


def test_a_franchise_span_names_the_team():
    rows = [(s, fact("franchise|MIA|Subject|{}".format(s)))
            for s in ("2025-26", CURRENT)]
    out = write(rows, name="Giannis")
    assert out[0] == ("Giannis has been the Heat's highest-paid player every "
                      "season since 2025-26.")


def test_a_tie_still_counts_as_holding_the_record():
    rows = run("college", "Duke", ["2024-25", "2025-26", CURRENT], kind="ties")
    assert "every season since 2024-25" in write(rows)[0]


# --------------------------------------------------------------------------
# which record leads
# --------------------------------------------------------------------------


def test_a_wider_field_leads_over_a_longer_run_in_a_narrow_one():
    """Ten seasons against 62 players is a smaller fact than six against 470."""
    rows = (run("nationality", "Greece",
                ["2017-18", "2018-19", "2019-20", "2020-21", "2021-22",
                 "2022-23", "2023-24", "2024-25", "2025-26", CURRENT], size=62)
            + run("draft_class", "2013",
                  ["2021-22", "2022-23", "2023-24", "2024-25", "2025-26",
                   CURRENT], size=470))
    assert "2013 draft class" in write(rows)[0]


def test_a_run_still_going_leads_over_a_longer_one_that_ended():
    rows = (run("college", "Duke",
                ["2015-16", "2016-17", "2017-18", "2018-19"], size=500)
            + run("position", "G", ["2025-26", CURRENT], size=450))
    assert "guard" in write(rows)[0]


def test_a_longer_run_leads_inside_the_same_field_size():
    rows = (run("draft_class", "2013", ["2024-25", "2025-26", CURRENT], size=470)
            + run("college", "Duke",
                  ["2021-22", "2022-23", "2023-24", "2024-25", "2025-26",
                   CURRENT], size=450))
    assert "Duke player" in write(rows)[0]


# --------------------------------------------------------------------------
# the career total
# --------------------------------------------------------------------------


def test_the_season_being_played_is_not_earned_yet():
    rows = [(CURRENT, career_fact("college", "Duke"))]
    idx = FakeIndex(paid=("Subject", 391900000, CURRENT))
    out = write(rows, name="Kyrie", idx=idx)
    assert out[0].startswith("By the end of 2026-27 Kyrie will have earned "
                             "$391.9 million")


def test_a_finished_season_has_earned_its_money():
    rows = [(CURRENT, career_fact("college", "Duke"))]
    idx = FakeIndex(in_progress=False, paid=("Subject", 391900000, CURRENT))
    assert "has earned $391.9 million through 2026-27" in write(rows, idx=idx)[0]


def test_the_career_total_carries_the_widest_field_it_leads():
    rows = [(CURRENT, career_fact("college", "Duke", size=200)),
            (CURRENT, career_fact("region", "international", size=600))]
    idx = FakeIndex(paid=("Subject", 397500000, CURRENT))
    assert "more than any other international player" in \
        " ".join(write(rows, idx=idx))


def test_a_near_miss_is_given_as_a_rank():
    rows = [(CURRENT, career_fact("draft_class", "2011", kind="approaches",
                                  rank=3))]
    idx = FakeIndex(paid=("Subject", 391900000, CURRENT))
    assert "the third-most of any player from the 2011 draft class" in \
        " ".join(write(rows, idx=idx))


def test_a_career_total_outside_the_top_five_is_given_alone():
    rows = [(CURRENT, career_fact("college", "Duke", kind="approaches",
                                  rank=40))]
    idx = FakeIndex(paid=("Subject", 1000000, CURRENT))
    assert write(rows, idx=idx) == \
        ["By the end of 2026-27 Test Man will have earned $1 million."]


def test_a_man_with_no_claims_still_gets_his_total():
    idx = FakeIndex(paid=("Subject", 6100000, CURRENT))
    assert write([], name="Jamal Shead", idx=idx) == \
        ["By the end of 2026-27 Jamal Shead will have earned $6.1 million."]


def test_a_career_the_engine_will_not_rank_gets_no_total():
    class Unrankable(FakeIndex):
        def career_rankable(self, _player):
            return False

    assert write([], idx=Unrankable()) == []


# --------------------------------------------------------------------------
# what is still to come
# --------------------------------------------------------------------------


def test_a_record_still_to_come_is_conditional():
    rows = [(FUTURE, season_fact("position", "F", FUTURE, value=62800000))]
    idx = FakeIndex(paid=("Subject", 1, CURRENT))
    text = " ".join(write(rows, idx=idx))
    assert ("$62.8 million in 2027-28 would be the biggest single-season "
            "salary of any forward") in text


def test_the_biggest_field_wins_the_record_to_come():
    rows = [(FUTURE, season_fact("college", "Duke", FUTURE, size=100)),
            (FUTURE, season_fact("position", "F", FUTURE, size=7000))]
    idx = FakeIndex(paid=("Subject", 1, CURRENT))
    assert "of any forward" in " ".join(write(rows, idx=idx))


def test_a_field_is_named_once_in_the_paragraph():
    """The span, the career total and the record to come take three fields."""
    rows = (run("college", "Duke", ["2019-20", "2020-21", "2021-22"])
            + [(CURRENT, career_fact("college", "Duke")),
               (CURRENT, career_fact("draft_class", "2011", kind="approaches",
                                     rank=3)),
               (FUTURE, season_fact("college_position", "Duke|G", FUTURE))])
    idx = FakeIndex(paid=("Subject", 391900000, CURRENT))
    out = write(rows, name="Kyrie", idx=idx)
    assert len(out) == 3
    assert "Duke player" in out[0]
    assert "2011 draft class" in out[1]
    assert "Duke guard" in out[2]


# --------------------------------------------------------------------------
# how it reads
# --------------------------------------------------------------------------


def test_he_is_named_once():
    rows = (run("college", "Duke", ["2019-20", "2020-21"])
            + [(CURRENT, career_fact("position", "G")),
               (FUTURE, season_fact("draft_class", "2011", FUTURE))])
    idx = FakeIndex(paid=("Subject", 391900000, CURRENT))
    text = " ".join(write(rows, name="Kyrie Irving", idx=idx))
    assert text.count("Kyrie Irving") == 1
    assert " he'll " in text or " he " in text or text.count("His ") >= 1


def test_no_more_than_four_sentences():
    rows = (run("college", "Duke", ["2019-20", "2020-21"])
            + run("position", "G", ["2024-25", "2025-26", CURRENT])
            + run("draft_class", "2011", ["2021-22", "2022-23"])
            + [(CURRENT, career_fact("region", "international")),
               (FUTURE, season_fact("draft_slot", "1", FUTURE))])
    idx = FakeIndex(paid=("Subject", 391900000, CURRENT))
    assert len(write(rows, idx=idx)) <= 4


def test_the_same_claims_always_write_the_same_paragraph():
    rows = run("college", "Duke", ["2019-20", "2020-21", "2021-22"])
    idx = FakeIndex(paid=("Subject", 1000000, CURRENT))
    assert write(rows, idx=idx) == write(rows, idx=idx)


def test_every_sentence_ends_in_a_full_stop():
    rows = (run("college", "Duke", ["2019-20", "2020-21"])
            + [(FUTURE, season_fact("position", "G", FUTURE))])
    idx = FakeIndex(paid=("Subject", 1000000, CURRENT))
    for sentence in write(rows, idx=idx):
        assert sentence.endswith("."), sentence
        assert sentence[0].isupper(), sentence


# --------------------------------------------------------------------------
# naming a field
# --------------------------------------------------------------------------


def test_every_field_has_a_noun():
    idx = FakeIndex()
    for kind, key, expect in (
        ("college", "Duke", "Duke player"),
        ("college_position", "Duke|G", "Duke guard"),
        ("draft_class", "2011", "player from the 2011 draft class"),
        ("draft_slot", "1", "No. 1 pick"),
        ("draft_slot", "undrafted", "undrafted player"),
        ("position", "C", "center"),
        ("nationality", "France", "player from France"),
        ("region", "europe", "European player"),
        ("pick_range", "lottery", "lottery pick"),
        ("franchise", "DAL", "Mavericks"),
    ):
        assert S._noun(idx, kind, key) == expect, (kind, key)


def test_a_college_name_is_spelled_out():
    assert S._noun(FakeIndex(), "college", "Arizona St") == "Arizona State player"


def test_a_college_position_key_keeps_its_position():
    """"Duke|G" carries a separator of its own, so the key cannot be read
    positionally from the left."""
    assert S._cohort(
        {"key": "cohort_season|college_position|Duke|G|Subject|2026-27"}
    ) == ("college_position", "Duke|G")


# --------------------------------------------------------------------------
# how far clear a leader is, and who is ahead of him
# --------------------------------------------------------------------------


def test_a_dominant_leader_says_how_far_clear_he_is():
    rows = [(CURRENT, career_fact(
        "nationality", "Greece",
        **{"lead": "more than every other player from Greece combined"}))]
    idx = FakeIndex(paid=("Subject", 397500000, CURRENT))
    assert " ".join(write(rows, idx=idx)) == (
        "By the end of 2026-27 Test Man will have earned $397.5 million, "
        "more than every other player from Greece combined.")


def test_a_leader_with_nothing_stronger_to_say_keeps_the_plain_form():
    rows = [(CURRENT, career_fact("college", "Duke"))]
    idx = FakeIndex(paid=("Subject", 391900000, CURRENT))
    assert "more than any other Duke player" in " ".join(write(rows, idx=idx))


def test_a_third_place_names_everyone_ahead():
    rows = [(CURRENT, career_fact("college", "Stanford", kind="approaches",
                                  rank=3, **{"ahead": "Brook Lopez and Robin Lopez"}))]
    idx = FakeIndex(paid=("Subject", 87700000, CURRENT))
    assert " ".join(write(rows, idx=idx)) == (
        "By the end of 2026-27 Test Man will have earned $87.7 million, "
        "the third-most of any Stanford player, behind Brook Lopez and "
        "Robin Lopez.")


def test_a_place_with_nobody_listed_ahead_says_only_the_rank():
    rows = [(CURRENT, career_fact("college", "Duke", kind="approaches", rank=5))]
    idx = FakeIndex(paid=("Subject", 1000000, CURRENT))
    assert " ".join(write(rows, idx=idx)).endswith(
        "the fifth-most of any Duke player.")
