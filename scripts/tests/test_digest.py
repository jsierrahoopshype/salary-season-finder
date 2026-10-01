"""Tests for the daily salary digest.

Every test builds the two data.json payloads by hand, so a failure names a rule
of the digest rather than a day's worth of real salary churn.
"""

from __future__ import annotations

import datetime
import json
import os

import pytest

import salary_digest as D

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

WHEN = datetime.date(2026, 10, 1)
CURRENT = "2026-27"

#: 30 teams and 15 paid players each, so compute_current_season lands on 2026-27.
def payload(records, current=CURRENT):
    filler = [
        {"player": "Filler {}".format(i), "season": current, "team": "OKC",
         "salary": 1000000, "years_exp": 3}
        for i in range(450)
    ]
    return {
        "seasons": filler + records,
        "seasons_list": [current],
        "teams": ["OKC"],
        "players": sorted({r["player"] for r in filler + records}),
    }


def rec(player, season, salary, team="ATL", years=3):
    return {"player": player, "season": season, "team": team,
            "salary": salary, "years_exp": years}


SCALE = {
    "2026-27": {"0": 1366314, "3": 2553508, "10": 3900945},
    "2027-28": {"0": 1434000, "3": 2680000, "10": 4094000},
    "2028-29": {"0": 1506000, "3": 2814000, "10": 4299000},
}
TOLERANCE = 0.03

NAMES = {"ATL": "Atlanta", "MEM": "Memphis", "DAL": "Dallas", "DEN": "Denver"}


def digest(old, new, factoids=None, slugs=None):
    items = D.changes(old, new, CURRENT)
    return items, D.render(items, WHEN, SCALE, TOLERANCE,
                           factoids or {}, slugs or {}, NAMES)


# --------------------------------------------------------------------------
# what counts as a change
# --------------------------------------------------------------------------


def test_a_player_season_that_was_not_there_is_new():
    old = payload([])
    new = payload([rec("Signed Man", CURRENT, 9000000)])
    items, _ = digest(old, new)
    assert [(i["kind"], i["player"]) for i in items] == [("new", "Signed Man")]


def test_a_season_beyond_his_last_one_is_an_extension():
    old = payload([rec("Extended Man", CURRENT, 30000000)])
    new = payload([rec("Extended Man", CURRENT, 30000000),
                   rec("Extended Man", "2027-28", 32000000)])
    items, _ = digest(old, new)
    assert [(i["kind"], i["season"]) for i in items] == [("extension", "2027-28")]


def test_a_different_number_is_a_salary_change():
    old = payload([rec("Raised Man", CURRENT, 10000000)])
    new = payload([rec("Raised Man", CURRENT, 11000000)])
    items, _ = digest(old, new)
    assert items[0]["kind"] == "salary"
    assert items[0]["was"] == 10000000
    assert items[0]["salary"] == 11000000


def test_the_same_money_on_another_team_is_a_team_change():
    old = payload([rec("Traded Man", CURRENT, 10000000, team="ATL")])
    new = payload([rec("Traded Man", CURRENT, 10000000, team="MEM")])
    items, _ = digest(old, new)
    assert items[0]["kind"] == "team"
    assert (items[0]["was_team"], items[0]["team"]) == ("ATL", "MEM")


def test_a_player_season_that_has_gone_is_reported():
    old = payload([rec("Waived Man", CURRENT, 8000000)])
    new = payload([])
    items, _ = digest(old, new)
    assert [(i["kind"], i["player"]) for i in items] == [("gone", "Waived Man")]


def test_a_salary_and_a_team_moving_together_are_two_changes():
    old = payload([rec("Busy Man", CURRENT, 10000000, team="ATL")])
    new = payload([rec("Busy Man", CURRENT, 12000000, team="DEN")])
    items, _ = digest(old, new)
    assert sorted(i["kind"] for i in items) == ["salary", "team"]


def test_a_past_season_is_left_alone():
    old = payload([rec("Retired Man", "2019-20", 5000000)])
    new = payload([rec("Retired Man", "2019-20", 6000000)])
    items, _ = digest(old, new)
    assert items == []


def test_nothing_changing_is_no_changes():
    one = payload([rec("Steady Man", CURRENT, 10000000)])
    items, posts = digest(one, payload([rec("Steady Man", CURRENT, 10000000)]))
    assert items == []
    assert posts == ["Salary data changes, Oct. 1, 2026: none today."]


def test_the_biggest_money_of_each_kind_comes_first():
    old = payload([])
    new = payload([rec("Small Man", CURRENT, 9000000),
                   rec("Big Man", CURRENT, 40000000)])
    items, _ = digest(old, new)
    assert [i["player"] for i in items] == ["Big Man", "Small Man"]


# --------------------------------------------------------------------------
# how it reads
# --------------------------------------------------------------------------


def test_each_kind_has_its_own_sentence():
    old = payload([rec("Traded Man", CURRENT, 10000000, team="ATL"),
                   rec("Waived Man", CURRENT, 8000000)])
    new = payload([rec("Traded Man", CURRENT, 10000000, team="MEM"),
                   rec("Signed Man", CURRENT, 9000000, team="DAL")])
    items, posts = digest(old, new)
    text = posts[0]
    assert "Signed Man is on Dallas' books for $9 million in 2026-27." in text
    assert ("Traded Man's 2026-27 salary of $10 million is now on Memphis' "
            "books, not Atlanta's.") in text
    assert "Waived Man's 2026-27 salary of $8 million is off the books." in text


def test_money_follows_ap_style():
    old = payload([rec("Poor Man", CURRENT, 600000, years=0)])
    new = payload([rec("Rich Man", CURRENT, 47600000),
                   rec("Round Man", CURRENT, 50000000),
                   rec("Poor Man", CURRENT, 507336, years=0)])
    _items, posts = digest(old, new)
    assert "$47.6 million" in posts[0]
    assert "$50 million" in posts[0]
    # a changed number is never collapsed, so a small one is printed in full
    assert "from $600,000 to $507,336" in posts[0]


def test_a_two_team_season_names_both():
    old = payload([])
    new = payload([rec("Split Man", CURRENT, 9000000, team="DAL, DEN")])
    _items, posts = digest(old, new)
    assert "on Dallas and Denver's books" in posts[0]


def test_an_extension_names_the_season_it_adds():
    old = payload([rec("Extended Man", CURRENT, 30000000, team="MEM")])
    new = payload([rec("Extended Man", CURRENT, 30000000, team="MEM"),
                   rec("Extended Man", "2027-28", 32000000, team="MEM")])
    _items, posts = digest(old, new)
    assert ("Extended Man now has 2027-28 on his deal, $32 million with "
            "Memphis.") in posts[0]


def test_no_em_dash_reaches_the_digest():
    old = payload([rec("Traded Man", CURRENT, 10000000, team="ATL")])
    new = payload([rec("Traded Man", CURRENT, 11000000, team="MEM"),
                   rec("Signed Man", CURRENT, 9000000)])
    _items, posts = digest(old, new)
    for post in posts:
        assert "—" not in post and "--" not in post


def test_the_header_carries_the_date():
    old, new = payload([]), payload([rec("Signed Man", CURRENT, 9000000)])
    _items, posts = digest(old, new)
    assert posts[0].startswith("Salary data changes, Oct. 1, 2026\n")


def test_a_factoid_and_a_link_ride_with_the_change():
    old, new = payload([]), payload([rec("Famous Man", CURRENT, 40000000)])
    factoids = {
        "Famous Man|2026-27": [
            {"text": "First thing."}, {"text": "Second thing."},
            {"text": "Third thing."},
        ],
    }
    slugs = {"Famous Man": "famous-man"}
    _items, posts = digest(old, new, factoids=factoids, slugs=slugs)
    assert "First thing." in posts[0]
    assert "Second thing." in posts[0]
    assert "Third thing." not in posts[0]
    assert ("https://hoopsmatic.com/salary-season-finder/player/famous-man/"
            in posts[0])


def test_a_split_name_links_to_the_man_playing_now():
    slugs = {"Two Men#0": "two-men", "Two Men#1": "two-men-2"}
    assert D.player_url(slugs, "Two Men").endswith("/player/two-men-2/")
    assert D.player_url({}, "Nobody") == ""


# --------------------------------------------------------------------------
# minimum deals
# --------------------------------------------------------------------------


def test_minimum_deals_come_to_one_line():
    old = payload([])
    new = payload([rec("Min One", CURRENT, 2553508),
                   rec("Min Two", CURRENT, 2553508),
                   rec("Paid Man", CURRENT, 20000000)])
    _items, posts = digest(old, new)
    assert "Paid Man is on Atlanta's books for $20 million" in posts[0]
    assert ("Also: 2 players on minimum deals new on the books: Min One, "
            "Min Two.") in posts[0]
    assert "Min One is on" not in posts[0]


def test_a_minimum_deal_with_a_factoid_is_printed_in_full():
    old = payload([])
    new = payload([rec("Min Man", CURRENT, 2553508)])
    factoids = {"Min Man|2026-27": [{"text": "Worth saying."}]}
    _items, posts = digest(old, new, factoids=factoids)
    assert "Min Man is on Atlanta's books for $2.6 million" in posts[0]
    assert "Worth saying." in posts[0]
    assert "Also:" not in posts[0]


def test_a_scale_amount_is_the_minimum_whatever_the_service_says():
    """years_exp counts seasons in this file, so a man with service elsewhere
    reads as a rookie while being paid a later year's minimum."""
    item = {"season": CURRENT, "salary": 3900945,
            "record": {"years_exp": 0}}
    assert D.is_minimum(item, SCALE, TOLERANCE) is True
    item["salary"] = 3000000
    assert D.is_minimum(item, SCALE, TOLERANCE) is False


def test_a_salary_change_is_never_collapsed():
    old = payload([rec("Min Man", CURRENT, 2553508)])
    new = payload([rec("Min Man", CURRENT, 2500000)])
    _items, posts = digest(old, new)
    assert "Min Man's 2026-27 salary moved from $2.6 million to $2.5 million." \
        in posts[0]
    assert "Also:" not in posts[0]


def test_a_season_the_scale_does_not_cover_collapses_nothing():
    old = payload([])
    new = payload([rec("Future Man", "2031-32", 1200000)])
    items = D.changes(old, new, CURRENT)
    assert D.is_minimum(items[0], SCALE, TOLERANCE) is False


# --------------------------------------------------------------------------
# posting
# --------------------------------------------------------------------------


def test_a_long_day_splits_into_several_posts():
    old = payload([])
    new = payload([rec("Player Number {:02d}".format(i), CURRENT,
                       20000000 + i, team="ATL") for i in range(60)])
    _items, posts = digest(old, new)
    assert len(posts) > 1
    for i, post in enumerate(posts, 1):
        assert post.startswith("Salary data changes, Oct. 1, 2026 ({} of {})".format(
            i, len(posts)))
        assert len(post) <= D.POST_LIMIT


def test_a_change_is_never_split_across_two_posts():
    old = payload([])
    new = payload([rec("Player Number {:02d}".format(i), CURRENT,
                       20000000 + i, team="ATL") for i in range(60)])
    factoids = {
        "Player Number {:02d}|2026-27".format(i): [{"text": "A fact about him."}]
        for i in range(60)
    }
    slugs = {"Player Number {:02d}".format(i): "p{:02d}".format(i) for i in range(60)}
    _items, posts = digest(old, new, factoids=factoids, slugs=slugs)
    for i in range(60):
        name = "Player Number {:02d}".format(i)
        holding = [p for p in posts if name in p]
        assert len(holding) == 1, name
        assert "A fact about him." in holding[0]
        assert "/player/p{:02d}/".format(i) in holding[0]


# --------------------------------------------------------------------------
# the state file
# --------------------------------------------------------------------------


def test_the_state_file_round_trips(tmp_path):
    path = str(tmp_path / "digest_state.json")
    D.save_state("abc123", path=path, posted=7)
    state = D.load_state(path)
    assert state["last_commit"] == "abc123"
    assert state["changes_posted"] == 7
    assert state["readme"]
    assert D.load_state(str(tmp_path / "missing.json")) == {}


def test_the_shipped_state_file_names_a_commit():
    path = os.path.join(REPO, "data", "digest_state.json")
    if not os.path.exists(path):
        pytest.skip("data/digest_state.json not present")
    state = D.load_state(path)
    assert state.get("last_commit")


def test_the_shipped_minimum_scale_covers_the_seasons_with_contracts():
    scale, tolerance = D.load_min_scale()
    assert 0 < tolerance < 0.1
    with open(os.path.join(REPO, "data", "data.json"), "r", encoding="utf-8") as fh:
        data = json.load(fh)
    import factoids as F
    current = F.compute_current_season(data)
    contracted = {r["season"] for r in data["seasons"]
                  if F.season_key(r["season"]) >= F.season_key(current)}
    assert contracted <= set(scale), sorted(contracted - set(scale))
    for season, rungs in scale.items():
        assert sorted(rungs) == sorted(str(i) for i in range(11)), season
        values = [rungs[str(i)] for i in range(11)]
        assert values == sorted(values), season


def test_the_first_run_writes_the_baseline_and_says_nothing(tmp_path, capsys):
    """No state file means no previous build to compare against, and a baseline
    is not news."""
    path = str(tmp_path / "state.json")
    assert D.main(["--state", path]) == 0
    printed = capsys.readouterr().out
    assert "writing the baseline and saying nothing" in printed
    assert "Salary data changes" not in printed
    assert D.load_state(path)["last_commit"]


def test_a_dry_run_writes_no_state(tmp_path, capsys):
    path = str(tmp_path / "state.json")
    assert D.main(["--state", path, "--dry-run"]) == 0
    assert not os.path.exists(path)


def test_a_rerun_from_the_same_build_has_nothing_to_say(tmp_path, capsys):
    path = str(tmp_path / "state.json")
    D.save_state(D.head_commit(), path=path)
    assert D.main(["--state", path, "--dry-run"]) == 0
    printed = capsys.readouterr().out
    assert "0 change(s)" in printed
    assert "none today." in printed


# --------------------------------------------------------------------------
# several seasons of one player are one piece of news
# --------------------------------------------------------------------------


def test_a_contract_that_adds_three_seasons_is_one_sentence():
    old = payload([rec("Jamal Shead", CURRENT, 5000000, team="ATL")])
    new = payload([rec("Jamal Shead", CURRENT, 5000000, team="ATL")] + [
        rec("Jamal Shead", season, 8000000, team="MEM")
        for season in ("2027-28", "2028-29", "2029-30")
    ])
    items, posts = digest(old, new)
    assert [i["kind"] for i in items] == ["extension_run"]
    assert ("Jamal Shead's deal now runs through 2029-30 with Memphis, "
            "$8 million a year.") in posts[0]
    assert posts[0].count("Jamal Shead") == 1


def test_a_rising_run_gives_the_first_and_the_last():
    old = payload([rec("Climber", CURRENT, 5000000, team="MEM")])
    new = payload([rec("Climber", CURRENT, 5000000, team="MEM"),
                   rec("Climber", "2027-28", 8000000, team="MEM"),
                   rec("Climber", "2028-29", 8600000, team="MEM"),
                   rec("Climber", "2029-30", 9200000, team="MEM")])
    _items, posts = digest(old, new)
    assert ("Climber's deal now runs through 2029-30 with Memphis, rising from "
            "$8 million to $9.2 million.") in posts[0]


def test_a_run_across_two_teams_names_neither():
    old = payload([rec("Split Deal", CURRENT, 5000000, team="ATL")])
    new = payload([rec("Split Deal", CURRENT, 5000000, team="ATL"),
                   rec("Split Deal", "2027-28", 8000000, team="ATL"),
                   rec("Split Deal", "2028-29", 8000000, team="MEM")])
    _items, posts = digest(old, new)
    assert "Split Deal's deal now runs through 2028-29, $8 million a year." \
        in posts[0]


def test_several_salary_changes_to_one_player_are_one_sentence():
    old = payload([rec("Redrawn", "2027-28", 25800000),
                   rec("Redrawn", "2028-29", 28500000),
                   rec("Redrawn", "2029-30", 30000000)])
    new = payload([rec("Redrawn", "2027-28", 26700000),
                   rec("Redrawn", "2028-29", 28900000),
                   rec("Redrawn", "2029-30", 68400000)])
    items, posts = digest(old, new)
    assert [i["kind"] for i in items] == ["salary_run"]
    assert ("Redrawn's salary moved in 3 seasons, 2027-28 through 2029-30, now "
            "rising from $26.7 million to $68.4 million.") in posts[0]


def test_one_season_on_its_own_keeps_its_own_sentence():
    old = payload([rec("Single", CURRENT, 10000000)])
    new = payload([rec("Single", CURRENT, 11000000)])
    _items, posts = digest(old, new)
    assert "Single's 2026-27 salary moved from $10 million to $11 million." \
        in posts[0]


def test_a_run_of_minimum_seasons_collapses_with_the_single_ones():
    old = payload([rec("Min Run", CURRENT, 2553508),
                   rec("Min One", CURRENT, 2553508),
                   rec("Min Two", CURRENT, 2553508)])
    new = payload([
        rec("Min Run", CURRENT, 2553508),
        rec("Min Run", "2027-28", 2680000),
        rec("Min Run", "2028-29", 2814000),
        rec("Min One", CURRENT, 2553508),
        rec("Min One", "2027-28", 2680000),
        rec("Min Two", CURRENT, 2553508),
        rec("Min Two", "2027-28", 2680000),
    ])
    _items, posts = digest(old, new)
    assert ("Also: 3 players on minimum salaries added for a later season: "
            "Min One, Min Run, Min Two.") in posts[0]
    assert "Min Run's deal" not in posts[0]


def test_a_run_is_not_a_minimum_run_when_one_season_is_above_it():
    old = payload([rec("Mixed", CURRENT, 2553508)])
    new = payload([rec("Mixed", CURRENT, 2553508),
                   rec("Mixed", "2027-28", 2680000),
                   rec("Mixed", "2028-29", 20000000)])
    _items, posts = digest(old, new)
    assert "Mixed's deal now runs through 2028-29" in posts[0]
    assert "Also:" not in posts[0]


def test_a_run_takes_its_factoids_from_the_seasons_it_covers():
    old = payload([rec("Famous", CURRENT, 40000000)])
    new = payload([rec("Famous", CURRENT, 40000000),
                   rec("Famous", "2027-28", 42000000),
                   rec("Famous", "2028-29", 44000000)])
    factoids = {
        "Famous|2027-28": [{"text": "A 2027-28 thing."}],
        "Famous|2028-29": [{"text": "A 2028-29 thing."}, {"text": "A third thing."}],
    }
    _items, posts = digest(old, new, factoids=factoids)
    assert "A 2027-28 thing." in posts[0]
    assert "A 2028-29 thing." in posts[0]
    assert "A third thing." not in posts[0]


def test_a_signing_that_arrives_as_four_seasons_is_one_sentence():
    old = payload([])
    new = payload([rec("Rookie", CURRENT, 4200000, team="MEM"),
                   rec("Rookie", "2027-28", 4400000, team="MEM"),
                   rec("Rookie", "2028-29", 6800000, team="MEM")])
    items, posts = digest(old, new)
    assert [i["kind"] for i in items] == ["new_run"]
    assert ("Rookie is on Memphis' books through 2028-29, rising from "
            "$4.2 million to $6.8 million.") in posts[0]


def test_a_team_change_is_never_grouped():
    """Two seasons moving to two different teams are two things to say."""
    old = payload([rec("Moved", CURRENT, 10000000, team="ATL"),
                   rec("Moved", "2027-28", 11000000, team="ATL")])
    new = payload([rec("Moved", CURRENT, 10000000, team="MEM"),
                   rec("Moved", "2027-28", 11000000, team="DEN")])
    items, _posts = digest(old, new)
    assert [i["kind"] for i in items] == ["team", "team"]
