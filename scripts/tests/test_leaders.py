"""Group-leader lines: the player page says so when he is No. 1 in a group."""

from __future__ import annotations

import os

import pytest

import factoids as F
from prerender import config as C
from prerender import entities as E
from prerender import leaders as L
from prerender import slugs as S

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def lead(family, key, noun, measure, value, season, passer=None, when=None):
    return L.Lead(family, key, noun, measure, value, season, passer, when)


# --------------------------------------------------------------------------
# the wording
# --------------------------------------------------------------------------


def test_both_ways_is_one_sentence_and_gives_the_total():
    lines, total = L.sentences("Fred VanVleet", [
        lead("pick", "undrafted", "undrafted player", L.SINGLE, 42846615,
             "2024-25", "Austin Reaves", "2027-28"),
        lead("pick", "undrafted", "undrafted player", L.CAREER, 215700000,
             "2026-27"),
    ])
    assert lines == [
        "Fred VanVleet is the highest-paid undrafted player ever, both in a "
        "single season ($42.8 million in 2024-25) and in career earnings "
        "($215.7 million through 2026-27).",
        "Austin Reaves is contracted to pass the single-season mark in 2027-28.",
    ]
    assert total is True


def test_one_way_only_names_the_measure():
    lines, total = L.sentences("Michael Jordan", [
        lead("draft", "1984", "player from the 1984 draft class", L.SINGLE,
             33140000, "1997-98"),
        lead("college", "North Carolina", "North Carolina player", L.SINGLE,
             33140000, "1997-98"),
    ])
    assert lines == [
        "Michael Jordan's $33.1 million in 1997-98 is the highest single-season "
        "salary ever paid to a player from the 1984 draft class or a North "
        "Carolina player."]
    assert total is False
    lines, total = L.sentences("Isaiah Thomas", [
        lead("pick", "60", "No. 60 pick", L.CAREER, 32700000, "2023-24")])
    assert lines == ["Isaiah Thomas' $32.7 million in career earnings through "
                     "2023-24 is the most ever by a No. 60 pick."]
    assert total is True


def test_highest_paid_never_stands_without_its_measure():
    lines, _ = L.sentences("Someone", [
        lead("college", "Duke", "Duke player", L.SINGLE, 30000000, "2020-21")])
    for line in lines:
        if "highest-paid" in line:
            assert "single season" in line and "career earnings" in line


def test_articles_follow_the_sound():
    assert L._a("undrafted player") == "an undrafted player"
    assert L._a("international player") == "an international player"
    assert L._a("No. 41 pick") == "a No. 41 pick"
    assert L._a("UCLA player") == "a UCLA player"
    assert L._a("LSU player") == "an LSU player"
    assert L._a("Utah player") == "a Utah player"
    assert L._a("center") == "a center"


# --------------------------------------------------------------------------
# the rule, on the real data
# --------------------------------------------------------------------------

DATA = os.path.join(REPO, "data", "data.json")


@pytest.fixture(scope="module")
def built():
    idx = F.build_index(F.load_data())
    everything = E.build_all(idx, S.load(os.path.join(REPO, C.SLUGS_PATH)))
    leads = L.build(idx, everything["cohorts"], everything["players"])
    names = {p.key: p for p in everything["players"]}
    return idx, everything, leads, names


def _of(built, name):
    _idx, _everything, leads, names = built
    for key, found in leads.items():
        if names[key].name == name:
            return {(lead.family, lead.key, lead.measure) for lead in found}
    return set()


@pytest.mark.skipif(not os.path.exists(DATA), reason="data/data.json not present")
def test_fred_vanvleet_leads_the_undrafted_both_ways(built):
    found = _of(built, "Fred VanVleet")
    assert ("pick", "undrafted", L.SINGLE) in found
    assert ("pick", "undrafted", L.CAREER) in found


@pytest.mark.skipif(not os.path.exists(DATA), reason="data/data.json not present")
def test_south_sudan_is_left_out(built):
    _idx, _everything, leads, _names = built
    assert not any(lead.family == "country" and lead.key == "South Sudan"
                   for found in leads.values() for lead in found)


@pytest.mark.skipif(not os.path.exists(DATA), reason="data/data.json not present")
def test_every_career_line_has_four_paid_seasons_and_a_whole_career(built):
    idx, _everything, leads, names = built
    for key, found in leads.items():
        ident = names[key]
        for lead in found:
            if lead.measure != L.CAREER:
                continue
            paid = {r["season"] for r in ident.records
                    if not idx.is_contracted(r["season"])}
            assert len(paid) >= L.CAREER_MIN_SEASONS, ident.name
            assert not idx.pre_window_career(ident.data_key), ident.name
            assert lead.value >= F.PRE_WINDOW_MARGIN, ident.name


@pytest.mark.skipif(not os.path.exists(DATA), reason="data/data.json not present")
def test_every_single_season_line_beats_anything_before_the_data(built):
    idx, _everything, leads, _names = built
    ceiling = L._pre_window_ceiling(idx)
    assert ceiling > 0
    for found in leads.values():
        for lead in found:
            if lead.measure == L.SINGLE:
                assert lead.value > ceiling
                assert not idx.is_contracted(lead.season)


@pytest.mark.skipif(not os.path.exists(DATA), reason="data/data.json not present")
def test_a_country_he_leads_inside_a_region_he_leads_is_not_said(built):
    idx, _everything, leads, _names = built
    for found in leads.values():
        held = {(lead.family, lead.key, lead.measure) for lead in found}
        for lead in found:
            if lead.family == "country":
                region = idx.continents.get(lead.key)
                assert ("region", region, lead.measure) not in held
                assert ("region", "international", lead.measure) not in held
