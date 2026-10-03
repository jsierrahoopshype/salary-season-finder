"""Tests for the daily salary digest.

Every test builds the two data.json payloads by hand, so a failure names a rule
of the digest rather than a day's worth of real salary churn.
"""

from __future__ import annotations

import datetime
import json
import os
import subprocess

import pytest

import digest_nuggets as N
import digest_teams as T
import factoids as F
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


OPENED = datetime.date(2026, 7, 1)


def context_for(new, slugs=None, teams=None):
    """The bundle render() reads, built the way main() builds it."""
    families = {"player": dict(slugs or {})}
    families.update(teams or {})
    return {"names": NAMES, "teams": (teams or {}).get("team") or {},
            "slugs": families, "idx": F.build_index(new)}


def digest(old, new, factoids=None, slugs=None, raises=None, teams=None):
    items = D.changes(old, new, CURRENT)
    context = context_for(new, slugs, teams)
    D.attach_nuggets(items, context["idx"], new, factoids or {},
                     raises or [], OPENED)
    return items, D.render(items, WHEN, SCALE, TOLERANCE, context)


def spoken(old, new, factoids=None, slugs=None, raises=None, teams=None):
    """Every change as the block it reads as, whatever the day does with it.

    How a change is worded and whether it earns an entry of its own are two
    rules, and most of these fixtures hold changes with nothing else to say,
    which a day now collects on one line.
    """
    items = D.changes(old, new, CURRENT)
    context = context_for(new, slugs, teams)
    D.attach_nuggets(items, context["idx"], new, factoids or {},
                     raises or [], OPENED)
    return "\n".join(D.block(item, context) for item in items)


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
    text = spoken(old, new)
    assert "Signed Man is on Dallas' books at $9 million for 2026-27." in text
    assert "Traded Man is on Memphis' books at $10 million for 2026-27." in text
    assert ("Waived Man is no longer on Atlanta's 2026-27 books, a $8 million "
            "salary.") in text


def test_money_follows_ap_style():
    old = payload([rec("Poor Man", CURRENT, 600000, years=0)])
    new = payload([rec("Rich Man", CURRENT, 47600000),
                   rec("Round Man", CURRENT, 50000000),
                   rec("Poor Man", CURRENT, 507336, years=0)])
    text = spoken(old, new)
    assert "$47.6 million" in text
    assert "$50 million" in text
    assert "to $507,336 from $600,000" in text


def test_a_two_team_season_names_both():
    old = payload([])
    new = payload([rec("Split Man", CURRENT, 9000000, team="DAL, DEN")])
    assert "on Dallas and Denver's books" in spoken(old, new)


def test_an_extension_names_the_season_it_adds():
    old = payload([rec("Extended Man", CURRENT, 30000000, team="MEM")])
    new = payload([rec("Extended Man", CURRENT, 30000000, team="MEM"),
                   rec("Extended Man", "2027-28", 32000000, team="MEM")])
    assert "Extended Man is on Memphis' books at $32 million for 2027-28." \
        in spoken(old, new)


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


def test_a_record_and_a_link_ride_with_the_change():
    """One record per change, and the man's name is the link to his page."""
    old, new = payload([]), payload([rec("Famous Man", CURRENT, 40000000)])
    factoids = {
        "Famous Man|2026-27": [
            {"text": "First thing.", "rank": 1},
            {"text": "Second thing.", "rank": 1},
        ],
    }
    slugs = {"Famous Man": "famous-man"}
    _items, posts = digest(old, new, factoids=factoids, slugs=slugs)
    assert "First thing." in posts[0]
    assert "Second thing." not in posts[0]
    assert ("<https://hoopsmatic.com/salary-season-finder/player/famous-man/"
            "|Famous Man>") in posts[0]


def test_a_factoid_outside_the_top_three_is_not_a_record():
    old, new = payload([]), payload([rec("Ordinary Man", CURRENT, 40000000)])
    factoids = {"Ordinary Man|2026-27": [{"text": "Eleventh best.", "rank": 11}]}
    items, posts = digest(old, new, factoids=factoids)
    assert "Eleventh best." not in posts[0]
    assert [n["kind"] for n in items[0]["nuggets"]] == []


def test_a_contracted_season_carries_no_record():
    """Nobody has been paid a future salary, so it ranks against nothing."""
    old = payload([rec("Future Man", CURRENT, 40000000)])
    new = payload([rec("Future Man", CURRENT, 40000000),
                   rec("Future Man", "2028-29", 50000000)])
    factoids = {"Future Man|2028-29": [{"text": "A future record.", "rank": 1}]}
    _items, posts = digest(old, new, factoids=factoids)
    assert "A future record." not in posts[0]


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
    assert ("Also: 2 players on minimum deals new on the books: Min One, "
            "Min Two.") in posts[0]
    assert "Min One is on" not in posts[0]
    assert "Paid Man" in posts[0]
    assert "Paid Man is on Atlanta's books at $20 million" in spoken(old, new)


def test_a_minimum_deal_with_a_factoid_is_printed_in_full():
    old = payload([])
    new = payload([rec("Min Man", CURRENT, 2553508)])
    factoids = {"Min Man|2026-27": [{"text": "Worth saying.", "rank": 1}]}
    _items, posts = digest(old, new, factoids=factoids)
    assert "Min Man is on Atlanta's books at $2.6 million" in posts[0]
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
    """A number that moved is never a minimum deal on the collapsed line."""
    old = payload([rec("Min Man", CURRENT, 2553508)])
    new = payload([rec("Min Man", CURRENT, 1800000)])
    _items, posts = digest(old, new)
    assert "Min Man's 2026-27 salary falls to $1.8 million from $2.6 million." \
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
    # each by a different ratio, so the day does not read as one rescale
    old = payload([rec("Player Number {:02d}".format(i), CURRENT,
                       5000000 + i * 37000, team="ATL") for i in range(60)])
    new = payload([rec("Player Number {:02d}".format(i), CURRENT,
                       20000000 + i * 1000, team="ATL") for i in range(60)])
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
        "Player Number {:02d}|2026-27".format(i): [
            {"text": "A fact about him.", "rank": 1}]
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


def _data_matches_the_commit():
    """Whether data/data.json on disk is the one HEAD carries.

    The data-build workflow rebuilds it from the live sheets before running the
    tests, and the sheets move every day, so there the file on disk is a
    different build from the committed one and comparing them is supposed to
    find changes.
    """
    try:
        done = subprocess.run(
            ["git", "status", "--porcelain", "--", "data/data.json"],
            cwd=REPO, capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return False
    return not done.stdout.strip()


@pytest.mark.skipif(not _data_matches_the_commit(),
                    reason="data.json on disk is a different build from HEAD")
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
    assert ("Jamal Shead's salary rises 60% in 2027-28: Memphis has him at "
            "$8 million a year through 2029-30, up from $5 million now.") in posts[0]
    assert posts[0].count("Jamal Shead") == 1


def test_a_rising_run_gives_the_first_and_the_last():
    old = payload([rec("Climber", CURRENT, 5000000, team="MEM")])
    new = payload([rec("Climber", CURRENT, 5000000, team="MEM"),
                   rec("Climber", "2027-28", 8000000, team="MEM"),
                   rec("Climber", "2028-29", 8600000, team="MEM"),
                   rec("Climber", "2029-30", 9200000, team="MEM")])
    _items, posts = digest(old, new)
    assert ("Climber's salary rises 84% in 2027-28: Memphis has him at "
            "$8 million, rising to $9.2 million through 2029-30, up from "
            "$5 million now.") in posts[0]


def test_a_run_across_two_teams_names_neither():
    old = payload([rec("Split Deal", CURRENT, 5000000, team="ATL")])
    new = payload([rec("Split Deal", CURRENT, 5000000, team="ATL"),
                   rec("Split Deal", "2027-28", 8000000, team="ATL"),
                   rec("Split Deal", "2028-29", 8000000, team="MEM")])
    _items, posts = digest(old, new)
    assert ("Split Deal's salary rises 60% in 2027-28: he is due $8 million a year "
            "through 2028-29, up from $5 million now.") \
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
    assert ("Redrawn's 2027-28 salary rises to $26.7 million from $25.8 "
            "million, and his next two seasons go up with it.") in posts[0]
    assert "redrawn" not in posts[0].lower().replace("redrawn's", "")


def test_one_season_on_its_own_keeps_its_own_sentence():
    old = payload([rec("Single", CURRENT, 10000000)])
    new = payload([rec("Single", CURRENT, 14000000)])
    _items, posts = digest(old, new)
    assert "Single's 2026-27 salary rises to $14 million from $10 million." \
        in posts[0]
    assert "with it" not in posts[0]


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
    assert "Mixed's salary more than triples in 2027-28" in posts[0]
    assert "Also:" not in posts[0]


def test_a_run_takes_its_record_from_the_paid_season_it_covers():
    """A run of changed numbers reaches every season it covers, but only the
    one already played out has a rank in it."""
    old = payload([rec("Famous", CURRENT, 40000000),
                   rec("Famous", "2027-28", 42000000)])
    new = payload([rec("Famous", CURRENT, 41000000),
                   rec("Famous", "2027-28", 43000000)])
    factoids = {
        "Famous|2026-27": [{"text": "A 2026-27 thing.", "rank": 1}],
        "Famous|2027-28": [{"text": "A 2027-28 thing.", "rank": 1}],
    }
    _items, posts = digest(old, new, factoids=factoids)
    assert "A 2026-27 thing." in posts[0]
    assert "A 2027-28 thing." not in posts[0]


def test_a_signing_that_arrives_as_four_seasons_is_one_sentence():
    old = payload([])
    new = payload([rec("Rookie", CURRENT, 4200000, team="MEM"),
                   rec("Rookie", "2027-28", 4400000, team="MEM"),
                   rec("Rookie", "2028-29", 6800000, team="MEM")])
    items, posts = digest(old, new)
    assert [i["kind"] for i in items] == ["new_run"]
    assert ("Rookie's salary rises 62% in 2026-27: Memphis has him at "
            "$4.2 million, rising to $6.8 million through 2028-29.") in posts[0]


def test_a_team_change_is_never_grouped():
    """Two seasons moving to two different teams are two things to say."""
    old = payload([rec("Moved", CURRENT, 10000000, team="ATL"),
                   rec("Moved", "2027-28", 11000000, team="ATL")])
    new = payload([rec("Moved", CURRENT, 10000000, team="MEM"),
                   rec("Moved", "2027-28", 11000000, team="DEN")])
    items, _posts = digest(old, new)
    assert [i["kind"] for i in items] == ["team", "team"]


# --------------------------------------------------------------------------
# peers: the band, the widening, and the link that has to return the group
# --------------------------------------------------------------------------

PAST = "2025-26"


def peer_payload(mine, others, salary=25000000, now_salaries=None):
    """A finished season full of guards, and what each is paid this season.

    ``mine`` and ``others`` are (ppg, apg) pairs; everyone played 60 games, so
    the games floor is clear and only the band decides the group.
    """
    records, names = [], []
    for i, (ppg, apg) in enumerate([mine] + list(others)):
        name = "Peer Man" if i == 0 else "Guard {:02d}".format(i)
        names.append(name)
        records.append({
            "player": name, "season": PAST, "team": "ATL", "salary": 5000000,
            "years_exp": 4, "pos": "G", "gp": 60, "ppg": ppg, "apg": apg,
            "rpg": 3.0, "age": 26,
        })
    now = now_salaries or [1000000 * (i + 1) for i in range(len(names) - 1)]
    for name, paid in zip(names[1:], now):
        records.append({
            "player": name, "season": CURRENT, "team": "ATL", "salary": paid,
            "years_exp": 5, "pos": "G", "gp": 0, "age": 27,
        })
    out = payload(records)
    out["seasons"].append({
        "player": "Peer Man", "season": CURRENT, "team": "ATL",
        "salary": salary, "years_exp": 5, "pos": "G", "gp": 0, "age": 27,
    })
    out["seasons_list"] = [PAST, CURRENT]
    out["players"] = sorted({r["player"] for r in out["seasons"]})
    return out


def peer_nugget_for(mine, others, **kwargs):
    data = peer_payload(mine, others, **kwargs)
    idx = F.build_index(data)
    item = {"player": "Peer Man", "season": CURRENT, "kind": "salary",
            "salary": kwargs.get("salary", 25000000),
            "record": idx.record("Peer Man", CURRENT), "members": []}
    return N.peer_nugget(idx, item, data, F.fmt_money), data


def test_a_band_is_the_whole_numbers_around_each_figure():
    assert N._band(7.4) == (7, 8)
    assert N._band(5.2) == (5, 6)
    assert N._band(0.4) == (0, 1)


def test_the_band_is_never_widened():
    """A band stretched until it finds him company is no longer a description
    of how he played."""
    nugget, _data = peer_nugget_for((7.4, 5.2), [(7.1, 5.0), (7.9, 5.9),
                                                 (6.5, 4.5), (8.5, 6.5)])
    assert nugget is None


def test_five_other_players_are_needed():
    assert peer_nugget_for(
        (7.4, 5.2), [(7.1, 5.0), (7.9, 5.9), (7.2, 5.5)])[0] is None
    assert peer_nugget_for(
        (7.4, 5.2), [(7.1, 5.0), (7.9, 5.9), (7.2, 5.5), (7.6, 5.1),
                     (7.3, 5.7)])[0] is not None


def test_the_sentence_states_the_band_it_used():
    nugget, _data = peer_nugget_for(
        (7.4, 5.2), [(7.1, 5.0), (7.9, 5.9), (7.2, 5.5), (7.6, 5.1), (7.3, 5.7)])
    assert nugget["opener"].startswith(
        "Guards who averaged 7 to 8 points and 5 to 6 assists last season")


def test_the_comparison_is_real_money_not_a_share_of_the_cap():
    nugget, _data = peer_nugget_for(
        (7.4, 5.2), [(7.1, 5.0), (7.9, 5.9), (7.2, 5.5), (7.6, 5.1), (7.3, 5.7)],
        now_salaries=[1000000, 2000000, 2900000, 4000000, 5100000])
    assert "of the cap" not in nugget["opener"]
    assert "make a median $2.9 million this season" in nugget["opener"]
    assert "the best-paid of them, Guard 05, is on $5.1 million" in nugget["opener"]


def test_the_peers_are_paid_at_this_season_not_the_one_they_played():
    """They put the numbers up last season; what they make now is the news."""
    nugget, _data = peer_nugget_for(
        (7.4, 5.2), [(7.1, 5.0), (7.9, 5.9), (7.2, 5.5), (7.6, 5.1), (7.3, 5.7)],
        now_salaries=[9000000] * 5)
    assert "median $9 million" in nugget["opener"]
    assert "$5 million" not in nugget["opener"]


def test_no_head_count_is_printed():
    nugget, _data = peer_nugget_for(
        (7.4, 5.2), [(7.1, 5.0), (7.9, 5.9), (7.2, 5.5), (7.6, 5.1), (7.3, 5.7)])
    group = nugget["opener"].split(" make ")[0]
    assert group == ("Guards who averaged 7 to 8 points and 5 to 6 assists "
                     "last season")
    assert "other guards" not in nugget["opener"]


def test_a_peer_with_no_salary_this_season_is_not_counted():
    data = peer_payload((7.4, 5.2), [(7.1, 5.0), (7.9, 5.9), (7.2, 5.5),
                                     (7.6, 5.1), (7.3, 5.7)])
    data["seasons"] = [r for r in data["seasons"]
                       if not (r["player"] == "Guard 01" and r["season"] == CURRENT)]
    idx = F.build_index(data)
    item = {"player": "Peer Man", "season": CURRENT, "kind": "salary",
            "salary": 25000000, "record": idx.record("Peer Man", CURRENT),
            "members": []}
    assert N.peer_nugget(idx, item, data, F.fmt_money) is None


def test_the_best_paid_peer_is_linked():
    nugget, _data = peer_nugget_for(
        (7.4, 5.2), [(7.1, 5.0), (7.9, 5.9), (7.2, 5.5), (7.6, 5.1), (7.3, 5.7)])
    assert ("player", "Guard 05") in nugget["entities"]


def test_a_forward_is_read_by_rebounds():
    data = peer_payload((7.4, 5.2), [(7.1, 5.0), (7.9, 5.9), (7.2, 5.5),
                                     (7.6, 5.1), (7.3, 5.7)])
    for record in data["seasons"]:
        record["pos"] = "F"
        record["rpg"] = 6.3
    idx = F.build_index(data)
    item = {"player": "Peer Man", "season": CURRENT, "kind": "salary",
            "salary": 25000000, "record": idx.record("Peer Man", CURRENT),
            "members": []}
    nugget = N.peer_nugget(idx, item, data, F.fmt_money)
    assert nugget is not None
    assert nugget["opener"].startswith(
        "Forwards who averaged 7 to 8 points and 6 to 7 rebounds last season")


def test_the_peer_link_returns_exactly_the_band():
    nugget, data = peer_nugget_for(
        (7.4, 5.2), [(7.1, 5.0), (7.9, 5.9), (7.2, 5.5), (7.6, 5.1), (7.3, 5.7)])
    spec = nugget["peer_link"]
    url = D.peer_url(spec)
    params = dict(part.split("=", 1) for part in url.split("#", 1)[1].split("&"))
    found = [
        record for record in data["seasons"]
        if record["season"] == params["from"]
        and (record.get("gp") or 0) >= int(params["gp_min"])
        and float(params["ppg_min"]) <= (record.get("ppg") or -1) <= float(params["ppg_max"])
        and float(params["apg_min"]) <= (record.get("apg") or -1) <= float(params["apg_max"])
    ]
    assert len(found) == spec["count"]


def test_the_peer_phrase_is_the_link_in_the_sentence():
    nugget, _data = peer_nugget_for(
        (7.4, 5.2), [(7.1, 5.0), (7.9, 5.9), (7.2, 5.5), (7.6, 5.1), (7.3, 5.7)])
    item = {"player": "Peer Man", "season": CURRENT, "kind": "salary"}
    text = D.nugget_sentence([nugget], item, NAMES, {}, {"player": {}})
    assert "<https://hoopsmatic.com/salary-season-finder#from=2025-26" in text
    assert "|Guards who averaged 7 to 8 points and 5 to 6 assists last season>" \
        in text


# --------------------------------------------------------------------------
# the career nugget
# --------------------------------------------------------------------------


def career_payload(paid_seasons, future, player="Rich Man", legends=()):
    """A career already paid, a season still to come, and retired men between.

    ``legends`` are (name, total, awards) for careers that finished inside the
    window, which is what makes them passable.
    """
    records, running = [], 0
    for season, salary in paid_seasons:
        running += salary
        records.append({"player": player, "season": season, "team": "ATL",
                        "salary": salary, "years_exp": 4, "age": 26,
                        "career_earnings": running})
    for season, salary in future:
        records.append({"player": player, "season": season, "team": "ATL",
                        "salary": salary, "years_exp": 8, "age": 30})
    for name, total, awards in legends:
        records.append({"player": name, "season": "2015-16", "team": "MEM",
                        "salary": total, "years_exp": 4, "awards": list(awards),
                        "career_earnings": total})
    out = payload(records)
    out["seasons_list"] = sorted(
        {r["season"] for r in out["seasons"]}, key=F.season_key)
    out["players"] = sorted({r["player"] for r in out["seasons"]})
    return out


def career_nugget_for(**kwargs):
    data = career_payload(**kwargs)
    idx = F.build_index(data)
    player = kwargs.get("player", "Rich Man")
    season = kwargs["future"][-1][0]
    item = {"player": player, "season": season, "kind": "extension",
            "salary": kwargs["future"][-1][1], "members": []}
    return N.career_nugget(idx, item, F.fmt_money), idx


def test_the_career_nugget_names_the_milestone_and_the_season():
    nugget, _idx = career_nugget_for(
        paid_seasons=[(CURRENT, 45000000)], future=[("2027-28", 48000000)])
    assert nugget is not None
    assert nugget["opener"] == ("He's already first in career earnings and "
                                "would pass $50 million in 2027-28")
    assert nugget["detail"]["milestone"] == 50000000
    assert nugget["detail"]["crosses_in"] == "2027-28"


def test_the_career_nugget_never_says_guaranteed():
    nugget, _idx = career_nugget_for(
        paid_seasons=[(CURRENT, 45000000)], future=[("2027-28", 48000000)])
    assert "guarantee" not in nugget["opener"]


def test_the_most_decorated_retired_men_passed_are_named():
    nugget, _idx = career_nugget_for(
        paid_seasons=[(CURRENT, 45000000)],
        future=[("2027-28", 48000000)],
        legends=[
            ("Mvp Man", 48000000, ["Most Valuable Player"]),
            ("All Nba Man", 47000000, ["All-NBA First Team"]),
            ("All Star Man", 46000000, ["All-Star"]),
            ("Plain Man", 49000000, []),
        ])
    # Only an all-time-team man qualifies, and with none here the All-NBA First
    # Team season is what the fallback takes. An MVP alone is not on the list.
    names = [entry["name"] for entry in nugget["detail"]["legends"]]
    assert names == ["All Nba Man"]
    assert "Plain Man" not in nugget["opener"]
    assert "Mvp Man" not in nugget["opener"]
    assert "going past All Nba Man on the way" in nugget["opener"]


def test_a_career_short_of_the_next_milestone_has_no_career_nugget():
    nugget, _idx = career_nugget_for(
        paid_seasons=[(CURRENT, 1000000)], future=[("2027-28", 1200000)])
    assert nugget is None


def test_a_standing_past_the_hundredth_name_is_not_printed():
    assert N._standing(461, 300) == ""
    assert N._standing(5, None) == "fifth in career earnings"
    assert N._standing(144, None) == ""
    assert "among players on a roster" in N._standing(400, 52)
    # past the words, the digit is how a rank is read
    assert N._standing(12, None) == "12th in career earnings"


# --------------------------------------------------------------------------
# the raise nugget
# --------------------------------------------------------------------------


def test_a_raise_is_measured_against_what_he_is_paid_now():
    data = payload([rec("Raised Man", CURRENT, 5000000),
                    rec("Raised Man", "2027-28", 40000000)])
    idx = F.build_index(data)
    item = {"player": "Raised Man", "season": "2027-28", "kind": "extension",
            "salary": 40000000, "members": []}
    assert D.raise_amount(idx, item) == 35000000


def test_money_leaving_the_books_is_not_a_raise():
    data = payload([rec("Gone Man", CURRENT, 5000000)])
    idx = F.build_index(data)
    for kind in ("gone", "team"):
        item = {"player": "Gone Man", "season": CURRENT, "kind": kind,
                "salary": 40000000, "members": []}
        assert D.raise_amount(idx, item) == 0


def test_the_biggest_raise_of_the_league_year_says_so():
    item = {"raise_amount": 50000000}
    raises = [{"amount": 10000000}, {"amount": 20000000}, {"amount": 50000000}]
    nugget = N.raise_nugget(item, raises, F.fmt_money, OPENED)
    assert nugget["opener"] == \
        "No team has taken on a bigger raise since July 1"
    assert nugget["detail"]["rank"] == 1


def test_a_raise_outside_the_ten_biggest_is_not_news():
    item = {"raise_amount": 1000000}
    raises = [{"amount": 10000000 + i} for i in range(20)]
    assert N.raise_nugget(item, raises, F.fmt_money, OPENED) is None


def test_the_raise_rank_is_spelled_as_an_ordinal():
    item = {"raise_amount": 10000000}
    raises = [{"amount": 30000000}, {"amount": 20000000}, {"amount": 10000000}]
    nugget = N.raise_nugget(item, raises, F.fmt_money, OPENED)
    assert "Only two raises since July 1 are bigger" in nugget["opener"]


def test_a_raise_on_its_own_has_nothing_to_rank_against():
    assert N.raise_nugget({"raise_amount": 500}, [], F.fmt_money, OPENED) is None


# --------------------------------------------------------------------------
# where the money ends
# --------------------------------------------------------------------------


def test_the_horizon_is_the_age_the_money_runs_to():
    data = payload([
        {"player": "Long Man", "season": CURRENT, "team": "ATL",
         "salary": 30000000, "years_exp": 5, "age": 27},
    ] + [rec("Long Man", s, 30000000) for s in ("2027-28", "2028-29", "2029-30")])
    idx = F.build_index(data)
    item = {"player": "Long Man", "season": "2029-30", "kind": "extension_run",
            "salary": 30000000,
            "members": [{"season": s} for s in ("2027-28", "2028-29", "2029-30")]}
    nugget = N.horizon_nugget(idx, item)
    assert nugget["opener"] == "The money runs through his age-30 season"
    assert nugget["detail"]["age"] == 30


def test_a_run_shorter_than_three_seasons_has_no_horizon():
    data = payload([rec("Short Man", CURRENT, 30000000)])
    idx = F.build_index(data)
    item = {"player": "Short Man", "season": "2027-28", "kind": "extension_run",
            "salary": 30000000,
            "members": [{"season": "2027-28"}, {"season": "2028-29"}]}
    assert N.horizon_nugget(idx, item) is None


# --------------------------------------------------------------------------
# choosing and joining
# --------------------------------------------------------------------------


def test_at_most_two_nuggets_ride_with_one_change():
    found = N.nuggets_for(
        {"player": "Nobody", "season": CURRENT, "kind": "new", "salary": 1,
         "members": [], "raise_amount": 0},
        F.build_index(payload([])), payload([]), {}, [], F.fmt_money,
        OPENED, limit=2)
    assert len(found) <= 2


def test_the_record_comes_before_the_career_total():
    data = payload([rec("Both Man", CURRENT, 45000000),
                    rec("Both Man", "2027-28", 48000000)])
    idx = F.build_index(data)
    item = {"player": "Both Man", "season": "2027-28", "kind": "extension",
            "salary": 48000000, "members": [{"season": CURRENT}],
            "raise_amount": 3000000}
    factoids = {"Both Man|2026-27": [{"text": "A record.", "rank": 1}]}
    found = N.nuggets_for(item, idx, data, factoids, [], F.fmt_money,
                          OPENED)
    assert [n["kind"] for n in found][0] == "record"


def test_two_nuggets_are_joined_with_one_and():
    item = {"player": "Joined Man", "season": CURRENT, "kind": "new"}
    nuggets = [
        {"kind": "raise", "opener": "That is the biggest raise",
         "tail": "it is the biggest raise", "entities": []},
        {"kind": "horizon", "opener": "The money runs through his age-30 season",
         "tail": "the money runs through his age-30 season", "entities": []},
    ]
    text = D.nugget_sentence(nuggets, item)
    assert text == ("That is the biggest raise, and the money runs through "
                    "his age-30 season.")
    assert text.count(", and ") == 1


def test_a_nugget_that_already_has_an_and_takes_no_second_one():
    item = {"player": "Busy Man", "season": CURRENT, "kind": "new"}
    nuggets = [
        {"kind": "peers", "opener": "Guards who averaged 19 to 20 points make "
                                    "a median $8 million, and the best-paid "
                                    "of them is on $30 million",
         "tail": "guards who averaged", "entities": []},
        {"kind": "horizon", "opener": "The money runs through his age-30 season",
         "tail": "the money runs through his age-30 season", "entities": []},
    ]
    text = D.nugget_sentence(nuggets, item)
    assert text.count(", and ") == 1
    assert "age-30" not in text


def test_a_career_nugget_takes_a_sentence_of_its_own():
    """Where he stands and what he would pass is a sentence, not a clause."""
    item = {"player": "Busy Man", "season": CURRENT, "kind": "new"}
    nuggets = [
        {"kind": "record", "opener": "He will have earned $444.5 million by "
                                     "the end of 2026-27, more than every "
                                     "other player out of Arizona State "
                                     "combined",
         "tail": "he will have earned", "entities": []},
        {"kind": "career",
         "opener": "He's already fifth in career earnings and would pass "
                   "$500 million in 2028-29",
         "tail": "he's already fifth", "entities": []},
    ]
    text = D.nugget_sentence(nuggets, item)
    assert text == ("He will have earned $444.5 million by the end of 2026-27, "
                    "more than every other player out of Arizona State "
                    "combined. He's already fifth in career earnings and would "
                    "pass $500 million in 2028-29.")
    assert "combined, and" not in text
    # two sentences, and never a third
    assert text.count(". ") == 1


def test_a_career_nugget_leading_still_keeps_its_own_sentence():
    item = {"player": "Busy Man", "season": CURRENT, "kind": "new"}
    nuggets = [
        {"kind": "career", "opener": "He's already fifth in career earnings "
                                     "and would pass $500 million in 2028-29",
         "tail": "he's already fifth", "entities": []},
        {"kind": "horizon", "opener": "The money runs through his age-30 season",
         "tail": "the money runs through his age-30 season", "entities": []},
    ]
    text = D.nugget_sentence(nuggets, item)
    assert text == ("He's already fifth in career earnings and would pass "
                    "$500 million in 2028-29. The money runs through his "
                    "age-30 season.")


def test_a_second_nugget_past_the_sentence_limit_is_dropped():
    item = {"player": "Wordy Man", "season": CURRENT, "kind": "new"}
    nuggets = [
        {"kind": "peers", "opener": "That is " + "x" * D.SENTENCE_LIMIT,
         "tail": "", "entities": []},
        {"kind": "horizon", "opener": "The money runs through his age-30 season",
         "tail": "the money runs through his age-30 season", "entities": []},
    ]
    assert "age-30" not in D.nugget_sentence(nuggets, item)


# --------------------------------------------------------------------------
# links
# --------------------------------------------------------------------------


def test_a_link_is_slack_mrkdwn():
    assert D.link("Atlanta", "https://x/") == "<https://x/|Atlanta>"
    assert D.link("Atlanta", "") == "Atlanta"


def test_a_surface_is_linked_once():
    text = D.apply_links("Atlanta beat Atlanta.", [("Atlanta", "https://x/")])
    assert text == "<https://x/|Atlanta> beat Atlanta."


def test_a_link_stops_at_a_word_boundary():
    """"Arizona St" must not swallow the first half of "Arizona State"."""
    text = D.apply_links(
        "the most of any Arizona State player",
        [("Arizona St", "https://x/"), ("Arizona State", "https://y/")])
    assert text == "the most of any <https://y/|Arizona State> player"


def test_the_longer_surface_wins():
    text = D.apply_links(
        "Jaren Jackson Jr is paid.",
        [("Jaren Jackson", "https://x/"), ("Jaren Jackson Jr", "https://y/")])
    assert text == "<https://y/|Jaren Jackson Jr> is paid."


def test_every_team_the_first_sentence_names_is_linked():
    item = {"player": "Moved Man", "season": CURRENT, "kind": "team",
            "salary": 20000000, "team": "MEM", "was_team": "DAL",
            "members": []}
    teams = {"MEM": "memphis-grizzlies", "DAL": "dallas-mavericks"}
    text = D.lead(item, NAMES, teams, {"player": {"Moved Man": "moved-man"}})
    assert "<https://hoopsmatic.com/salary-season-finder/player/moved-man/" \
        "|Moved Man>" in text
    assert "/team/memphis-grizzlies/|Memphis>" in text


def test_a_team_change_never_names_the_team_it_left():
    """Where the money used to sit is not the news and dates the line."""
    old = payload([rec("Moved Man", CURRENT, 20000000, team="DAL")])
    new = payload([rec("Moved Man", CURRENT, 20000000, team="MEM")])
    text = spoken(old, new)
    assert "Moved Man is on Memphis' books at $20 million for 2026-27." in text
    assert "Dallas" not in text
    _items, posts = digest(old, new)
    assert "Dallas" not in posts[0]


def test_a_cohort_a_record_names_is_linked_to_its_page():
    item = {"player": "College Man", "season": CURRENT, "kind": "new"}
    nuggets = [{
        "kind": "record",
        "opener": "the most any Arizona State player has been paid in a season",
        "tail": "", "entities": [("college", "Arizona State")],
    }]
    slugs = {"player": {}, "college": {"Arizona State": "arizona-state"}}
    text = D.nugget_sentence(nuggets, item, NAMES, {}, slugs)
    assert "/college/arizona-state/|Arizona State>" in text


def test_a_legend_named_in_a_career_nugget_is_linked():
    item = {"player": "Rich Man", "season": CURRENT, "kind": "new"}
    nuggets = [{
        "kind": "career",
        "opener": "That would push his career salary past $100 million by "
                  "2028-29, past Ben Wallace on the way",
        "tail": "", "entities": [("player", "Ben Wallace")],
    }]
    slugs = {"player": {"Ben Wallace": "ben-wallace"}}
    text = D.nugget_sentence(nuggets, item, NAMES, {}, slugs)
    assert "/player/ben-wallace/|Ben Wallace>" in text


def test_each_cohort_kind_knows_its_directory():
    slugs = {"college": {"Duke": "duke"}, "country": {"France": "france"},
             "college_position": {"Duke|G": "duke-guards"}}
    assert D.cohort_url(slugs, "draft_class", 2024).endswith("/draft/2024/")
    assert D.cohort_url(slugs, "draft_slot", 1).endswith("/pick/1/")
    assert D.cohort_url(slugs, "position", "G").endswith("/position/guard/")
    assert D.cohort_url(slugs, "region", "europe").endswith("/region/europe/")
    assert D.cohort_url(slugs, "pick_range", "lottery").endswith(
        "/pick-range/lottery/")
    assert D.cohort_url(slugs, "college", "Duke").endswith("/college/duke/")
    assert D.cohort_url(slugs, "nationality", "France").endswith(
        "/country/france/")
    assert D.cohort_url(slugs, "college_position", "Duke|G").endswith(
        "/college-position/duke-guards/")
    assert D.cohort_url(slugs, "nothing", "x") == ""


def test_a_cohort_with_no_page_gets_no_link():
    assert D.cohort_url({"college": {}}, "college", "Nowhere State") == ""


def test_the_digest_spends_no_bare_urls():
    old, new = payload([]), payload([rec("Linked Man", CURRENT, 40000000)])
    slugs = {"Linked Man": "linked-man"}
    _items, posts = digest(old, new, slugs=slugs)
    for post in posts:
        for word in post.split():
            if word.startswith("http"):
                assert word.startswith("<http"), word


def test_slack_is_told_not_to_unfurl(monkeypatch):
    sent = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_open(request, timeout=None):
        sent["body"] = json.loads(request.data.decode("utf-8"))
        return Response()

    monkeypatch.setattr(D.urllib.request, "urlopen", fake_open)
    D.post("https://hooks.example/x", "a digest")
    assert sent["body"]["unfurl_links"] is False
    assert sent["body"]["unfurl_media"] is False
    assert sent["body"]["mrkdwn"] is True


# --------------------------------------------------------------------------
# the hash the peer link relies on
# --------------------------------------------------------------------------


def test_the_tool_carries_the_games_filter_in_its_hash():
    """peer_url writes gp_min, so js/app.js has to read and write it."""
    app = open(os.path.join(REPO, "js", "app.js"), encoding="utf-8").read()
    assert "params.gp_min = f.gpMin" in app
    assert 'params.gp_min' in app and 'getElementById("gpMin").value = params.gp_min' in app


# --------------------------------------------------------------------------
# A1/A2: how a line reads
# --------------------------------------------------------------------------


def test_no_contract_word_reaches_the_digest():
    """The data holds a salary against a season, not the deal behind it."""
    old = payload([rec("Kept Man", CURRENT, 2300000, team="MEM")])
    new = payload([rec("Kept Man", CURRENT, 2300000, team="MEM")] + [
        rec("Kept Man", s, 8000000, team="MEM")
        for s in ("2027-28", "2028-29", "2029-30")
    ] + [rec("Moved Man", CURRENT, 9000000, team="DAL"),
         rec("Cut Man", "2027-28", 4000000, team="ATL")])
    _items, posts = digest(old, new)
    for banned in ("contract", "deal", "extension", "signed", "guarantee"):
        for post in posts:
            assert banned not in post.lower(), (banned, post)


def test_the_verb_carries_the_news():
    old = payload([rec("Shead", CURRENT, 2000000, team="MEM")])
    new = payload([rec("Shead", CURRENT, 2000000, team="MEM"),
                   rec("Shead", "2027-28", 8000000, team="MEM")])
    _items, posts = digest(old, new)
    assert "Shead's salary more than triples in 2027-28" in posts[0]


def test_a_multiple_is_only_used_where_the_number_is_one():
    """2.6 times is not "triples", and 3.5 times is not "triples" either."""
    assert D.movement(1850000, 1000000) == "rises 85%"
    assert D.movement(1900000, 1000000) == "doubles"
    assert D.movement(2100000, 1000000) == "doubles"
    assert D.movement(2150000, 1000000) == "more than doubles"
    assert D.movement(2600000, 1000000) == "more than doubles"
    assert D.movement(2900000, 1000000) == "triples"
    assert D.movement(3100000, 1000000) == "triples"
    assert D.movement(3200000, 1000000) == "more than triples"
    assert D.movement(5400000, 1000000) == "more than triples"
    assert D.movement(700000, 1000000) == "falls 30%"


def test_a_move_too_small_to_be_news_gets_no_verb():
    assert D.movement(2100000, 2000000) == ""
    assert D.movement(1950000, 2000000) == ""


def test_no_second_sentence_opens_with_that_is():
    item = {"player": "Man", "season": CURRENT, "kind": "new"}
    nuggets = [
        {"kind": "raise", "opener": "No team has taken on a bigger raise",
         "tail": "no team has", "entities": []},
    ]
    text = D.nugget_sentence(nuggets, item)
    assert not text.startswith("That is")
    assert not text.startswith("That ")


def test_a_run_of_seasons_gives_its_total_over_the_seasons():
    item = {"members": [{"salary": 8000000}, {"salary": 8000000},
                        {"salary": 8000000}]}
    assert D.over_seasons(item) == "$24 million over three seasons"


# --------------------------------------------------------------------------
# A3: a future season is never ranked inside its own books
# --------------------------------------------------------------------------


def test_a_rank_inside_a_future_season_is_not_a_nugget():
    old = payload([rec("Future Man", CURRENT, 10000000)])
    new = payload([rec("Future Man", CURRENT, 10000000),
                   rec("Future Man", "2028-29", 60000000)])
    factoids = {"Future Man|2028-29": [{
        "text": "His $60 million would take up 33% of the 2028-29 cap, the "
                "largest share of the cap in 2028-29.",
        "rank": 1, "key": "cap_pct_season|Future Man|2028-29",
        "previous_holder": {"player": "Other", "season": "2028-29", "value": 32},
    }]}
    _items, posts = digest(old, new, factoids=factoids)
    assert "largest share of the cap in 2028-29" not in posts[0]


def test_a_rank_inside_a_season_already_paid_is_fine():
    old = payload([])
    new = payload([rec("Paid Man", CURRENT, 60000000)])
    factoids = {"Paid Man|2026-27": [{
        "text": "His $60 million is the biggest salary of 2026-27.",
        "rank": 1, "key": "cohort_season|position|G|Paid Man|2026-27",
        "previous_holder": {"player": "Other", "season": "2025-26", "value": 32},
    }]}
    _items, posts = digest(old, new, factoids=factoids)
    assert "the biggest salary of 2026-27" in posts[0]


def test_the_digest_never_talks_in_cap_shares():
    old = payload([])
    new = payload([rec("Paid Man", CURRENT, 60000000)])
    factoids = {"Paid Man|2026-27": [{
        "text": "His $60 million is the largest share of the cap in 2026-27.",
        "rank": 1, "key": "cap_pct_season|Paid Man|2026-27",
        "previous_holder": {"player": "Other", "season": "2025-26", "value": 32},
    }]}
    _items, posts = digest(old, new, factoids=factoids)
    for post in posts:
        assert "of the cap" not in post


# --------------------------------------------------------------------------
# A5: who is worth naming
# --------------------------------------------------------------------------


def test_an_all_time_team_man_is_named_over_an_all_nba_one():
    nugget, _idx = career_nugget_for(
        paid_seasons=[(CURRENT, 45000000)],
        future=[("2027-28", 48000000)],
        legends=[("Top 75 Man", 48000000, ["NBA Top-75"]),
                 ("All Nba Man", 47000000, ["All-NBA First Team"])])
    assert [e["name"] for e in nugget["detail"]["legends"]] == ["Top 75 Man"]


def test_nobody_decorated_enough_means_no_names():
    nugget, _idx = career_nugget_for(
        paid_seasons=[(CURRENT, 45000000)],
        future=[("2027-28", 48000000)],
        legends=[("All Star Man", 48000000, ["All-Star"]),
                 ("Plain Man", 47000000, [])])
    assert nugget["detail"]["legends"] == []
    assert "on the way" not in nugget["opener"]


def test_a_career_nugget_gives_the_standing_or_the_names_not_both():
    nugget, _idx = career_nugget_for(
        paid_seasons=[(CURRENT, 45000000)],
        future=[("2027-28", 48000000)],
        legends=[("Top 75 Man", 48000000, ["NBA Top-75"])])
    assert "going past" in nugget["opener"]
    assert "already stands" not in nugget["opener"]


# --------------------------------------------------------------------------
# A7: a record about him opens with "He"
# --------------------------------------------------------------------------


def test_a_record_about_the_item_player_says_he():
    old, new = payload([]), payload([rec("Famous Man", CURRENT, 40000000)])
    factoids = {"Famous Man|2026-27": [
        {"text": "Famous Man has earned the most of any Duke player.",
         "rank": 1}]}
    _items, posts = digest(old, new, factoids=factoids)
    assert "He has earned the most of any Duke player." in posts[0]
    assert posts[0].count("Famous Man") == 1


def test_a_possessive_record_becomes_his():
    assert N._his_own("Famous Man's $40 million is a record.", "Famous Man") == \
        "His $40 million is a record."


def test_a_rivals_figure_is_dropped_but_his_name_stays():
    trimmed = N._trim_rival(
        "He has earned $87.7 million, the third-most among players out of "
        "Stanford, behind Brook Lopez's $238.7 million (2026-27).")
    assert trimmed.endswith("behind Brook Lopez.")
    assert "$238.7 million" not in trimmed


# --------------------------------------------------------------------------
# A8/A9: what never becomes an item
# --------------------------------------------------------------------------


def test_a_salary_the_guard_flagged_is_not_a_change():
    data = payload([rec("Leaper", CURRENT, 40000000),
                    rec("Leaper", "2027-28", 70000000)])
    data["salary_cap"] = {s: {"cap": 200000000}
                          for s in (CURRENT, "2027-28", "2028-29")}
    idx = F.build_index(data)
    assert ("Leaper", "2027-28") in idx.impossible
    items = [{"kind": "extension", "player": "Leaper", "season": "2027-28",
              "salary": 70000000, "team": "ATL", "members": []}]
    assert D.drop_impossible(items, idx) == []


def test_a_run_keeps_the_seasons_the_guard_left_alone():
    # 2028-29 is a 73% jump over 2027-28, past the 60% the guard allows
    data = payload([rec("Leaper", CURRENT, 40000000),
                    rec("Leaper", "2027-28", 44000000),
                    rec("Leaper", "2028-29", 76000000)])
    data["salary_cap"] = {s: {"cap": 200000000}
                          for s in (CURRENT, "2027-28", "2028-29")}
    idx = F.build_index(data)
    item = {"kind": "extension_run", "player": "Leaper", "season": "2028-29",
            "first_season": "2027-28", "salary": 76000000, "team": "ATL",
            "members": [{"season": "2027-28", "salary": 44000000},
                        {"season": "2028-29", "salary": 76000000}]}
    kept = D.drop_impossible([item], idx)
    assert len(kept) == 1
    # one season left, so it stops being a run and becomes a single change
    assert kept[0]["season"] == "2027-28"
    assert kept[0]["salary"] == 44000000
    assert kept[0]["kind"] == "extension"


def test_one_ratio_moving_the_whole_sheet_is_not_news():
    """A new cap projection moves every future salary by the same factor."""
    old = payload([rec("Man {:02d}".format(i), "2027-28", 10000000 + i * 1000)
                   for i in range(20)])
    new = payload([rec("Man {:02d}".format(i), "2027-28",
                       int((10000000 + i * 1000) * 1.05)) for i in range(20)])
    items = D.changes(old, new, CURRENT)
    assert items == []


def test_a_handful_of_moves_sharing_a_ratio_is_still_news():
    old = payload([rec("Man {:02d}".format(i), "2027-28", 10000000)
                   for i in range(3)])
    new = payload([rec("Man {:02d}".format(i), "2027-28", 10500000)
                   for i in range(3)])
    assert len(D.changes(old, new, CURRENT)) == 3


# --------------------------------------------------------------------------
# A6: what a team has committed
# --------------------------------------------------------------------------


def team_payload(rows, season="2027-28"):
    records = [
        {"player": player, "season": season, "team": code, "salary": salary,
         "years_exp": 4}
        for code, player, salary in rows
    ]
    out = payload(records)
    out["salary_cap"] = {CURRENT: {"cap": 166000000},
                         season: {"cap": 100000000}}
    out["seasons_list"] = [CURRENT, season]
    return out


def test_a_team_is_measured_on_its_three_biggest_salaries():
    # No salary towers over the rest, so the guard leaves all four alone.
    data = team_payload([("ATL", "One", 40000000), ("ATL", "Two", 35000000),
                         ("ATL", "Three", 30000000), ("ATL", "Four", 25000000)])
    idx = F.build_index(data)
    total, top = T.commitments(data, idx)[("ATL", "2027-28")]
    assert total == 105000000
    assert [player for player, _amount in top] == ["One", "Two", "Three"]


def test_crossing_half_the_cap_earns_a_line():
    before = {("ATL", "2027-28"): (40000000, [])}
    after = {("ATL", "2027-28"): (60000000, [("One", 60000000)])}
    idx = F.build_index(team_payload([("ATL", "One", 60000000)]))
    out = T.crossings(before, after, idx, {("ATL", "2027-28")})
    assert len(out) == 1
    assert out[0]["reason"] == "cap_share"
    assert out[0]["detail"]["share"] == 0.5


def test_a_team_already_past_the_line_earns_nothing():
    before = {("ATL", "2027-28"): (60000000, [])}
    after = {("ATL", "2027-28"): (70000000, [("One", 70000000)])}
    idx = F.build_index(team_payload([("ATL", "One", 70000000)]))
    assert T.crossings(before, after, idx, {("ATL", "2027-28")}) == []


def test_becoming_the_league_high_earns_a_line():
    before = {("ATL", "2027-28"): (10000000, []),
              ("MEM", "2027-28"): (20000000, [])}
    after = {("ATL", "2027-28"): (30000000, [("One", 30000000)]),
             ("MEM", "2027-28"): (20000000, [])}
    idx = F.build_index(team_payload([("ATL", "One", 30000000)]))
    out = T.crossings(before, after, idx, {("ATL", "2027-28")})
    assert out[0]["reason"] == "league_high"


def test_the_team_line_says_so_far():
    entry = {"team": "ATL", "season": "2027-28", "total": 142000000,
             "was": 90000000, "cap": 174300000, "reason": "league_high",
             "detail": {}, "top": [("A", 1), ("B", 1), ("C", 1)]}
    text = T.line(entry, "Detroit", F.fmt_money)
    assert text == ("Detroit now has $142 million committed to A, B and C for "
                    "2027-28, the most any team has tied up in three players "
                    "for that season so far.")


def test_a_team_line_never_counts_a_flagged_salary():
    data = team_payload([("ATL", "Leaper", 70000000)])
    data["seasons"].append({"player": "Leaper", "season": CURRENT,
                            "team": "ATL", "salary": 40000000, "years_exp": 4})
    data["salary_cap"][CURRENT] = {"cap": 200000000}
    data["salary_cap"]["2027-28"] = {"cap": 200000000}
    idx = F.build_index(data)
    assert ("Leaper", "2027-28") in idx.impossible
    assert ("ATL", "2027-28") not in T.commitments(data, idx)


# --------------------------------------------------------------------------
# a man is not one of his own peers
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# a career nugget that names a legend stands alone
# --------------------------------------------------------------------------


def test_a_named_legend_leaves_no_room_for_a_second_nugget():
    item = {"player": "Man", "season": CURRENT, "kind": "new"}
    nuggets = [
        {"kind": "career",
         "opener": "He would reach $100 million in career earnings by 2028-29, "
                   "going past Ben Wallace on the way",
         "tail": "x", "entities": [],
         "detail": {"legends": [{"name": "Ben Wallace"}]}},
        {"kind": "raise", "opener": "No team has taken on a bigger raise",
         "tail": "no team has taken on a bigger raise", "entities": []},
    ]
    text = D.nugget_sentence(nuggets, item)
    assert "Ben Wallace" in text
    assert "bigger raise" not in text


def test_a_career_nugget_with_no_legend_still_takes_one():
    item = {"player": "Man", "season": CURRENT, "kind": "new"}
    nuggets = [
        {"kind": "career",
         "opener": "He would reach $50 million in career earnings by 2027-28",
         "tail": "x", "entities": [], "detail": {"legends": []}},
        {"kind": "raise", "opener": "No team has taken on a bigger raise",
         "tail": "no team has taken on a bigger raise", "entities": []},
    ]
    assert "bigger raise" in D.nugget_sentence(nuggets, item)


def test_every_man_named_ahead_of_him_is_linked():
    old, new = payload([]), payload([rec("Third Man", CURRENT, 4000000)])
    factoids = {"Third Man|2026-27": [{
        "text": "Third Man has earned the third-most among players out of "
                "Stanford, behind Brook Lopez and Robin Lopez.",
        "rank": 3, "key": "cohort_career|college|Stanford|Third Man|2026-27",
        "ahead": "Brook Lopez and Robin Lopez",
        "previous_holder": {"player": "Brook Lopez", "season": CURRENT,
                            "value": 1},
    }]}
    slugs = {"player": {"Brook Lopez": "brook-lopez",
                        "Robin Lopez": "robin-lopez",
                        "Third Man": "third-man"},
             "college": {"Stanford": "stanford"}}
    items = D.changes(old, new, CURRENT)
    context = context_for(new, slugs["player"], {"college": slugs["college"]})
    D.attach_nuggets(items, context["idx"], new, factoids, [], OPENED)
    text = D.block(items[0], context)
    assert "/player/brook-lopez/|Brook Lopez>" in text
    assert "/player/robin-lopez/|Robin Lopez>" in text


# --------------------------------------------------------------------------
# a changed number: the first season it moved in, old to new
# --------------------------------------------------------------------------


def test_a_changed_number_is_never_called_redrawn():
    """"Redrawn" says a sheet changed without saying what it now says."""
    old = payload([rec("Harden Man", CURRENT, 28100000),
                   rec("Harden Man", "2027-28", 29000000),
                   rec("Harden Man", "2028-29", 30000000)])
    new = payload([rec("Harden Man", CURRENT, 30600000),
                   rec("Harden Man", "2027-28", 32000000),
                   rec("Harden Man", "2028-29", 33700000)])
    text = spoken(old, new)
    assert text.startswith(
        "Harden Man's 2026-27 salary rises to $30.6 million from $28.1 "
        "million, and his next two seasons go up with it.")
    assert "redrawn" not in text.lower()


def test_a_run_coming_down_comes_down_with_it():
    old = payload([rec("Cut Man", CURRENT, 30000000),
                   rec("Cut Man", "2027-28", 31000000)])
    new = payload([rec("Cut Man", CURRENT, 20000000),
                   rec("Cut Man", "2027-28", 21000000)])
    assert ("Cut Man's 2026-27 salary falls to $20 million from $30 million, "
            "and his next season comes down with it.") in spoken(old, new)


def test_a_run_moving_both_ways_moves_with_it():
    old = payload([rec("Mixed Man", CURRENT, 30000000),
                   rec("Mixed Man", "2027-28", 31000000),
                   rec("Mixed Man", "2028-29", 20000000)])
    new = payload([rec("Mixed Man", CURRENT, 40000000),
                   rec("Mixed Man", "2027-28", 25000000),
                   rec("Mixed Man", "2028-29", 30000000)])
    assert "and his next two seasons move with it." in spoken(old, new)


def test_a_multiple_is_still_the_verb_where_one_fits():
    old = payload([rec("Doubled Man", CURRENT, 10000000)])
    new = payload([rec("Doubled Man", CURRENT, 20000000)])
    assert ("Doubled Man's 2026-27 salary doubles to $20 million from "
            "$10 million.") in spoken(old, new)


def test_a_number_that_barely_moved_is_not_posted():
    """Under 5% in every season the sheet was nudged, not rewritten."""
    old = payload([rec("Nudged Man", CURRENT, 30000000),
                   rec("Nudged Man", "2027-28", 31000000)])
    new = payload([rec("Nudged Man", CURRENT, 30900000),
                   rec("Nudged Man", "2027-28", 32000000)])
    items, posts = digest(old, new)
    assert [i["kind"] for i in items] == ["salary_run"]
    assert "Nudged Man" not in posts[0]


def test_one_season_moving_enough_carries_the_whole_run():
    old = payload([rec("Moved Man", CURRENT, 30000000),
                   rec("Moved Man", "2027-28", 31000000)])
    new = payload([rec("Moved Man", CURRENT, 30900000),
                   rec("Moved Man", "2027-28", 45000000)])
    _items, posts = digest(old, new)
    assert "Moved Man's 2026-27 salary rises to $30.9 million" in posts[0]


def test_a_nudge_that_sets_a_record_is_posted():
    old = payload([rec("Record Man", CURRENT, 30000000)])
    new = payload([rec("Record Man", CURRENT, 30900000)])
    factoids = {"Record Man|2026-27": [{"text": "Worth saying.", "rank": 1}]}
    _items, posts = digest(old, new, factoids=factoids)
    assert "Record Man's 2026-27 salary rises to $30.9 million" in posts[0]
    assert "Worth saying." in posts[0]


# --------------------------------------------------------------------------
# items with nothing of their own to say
# --------------------------------------------------------------------------


def test_items_with_no_story_come_to_one_line():
    old = payload([])
    new = payload([rec("Quiet Man", "2027-28", 8800000, team="MEM"),
                   rec("Other Man", "2027-28", 4000000, team="DAL")])
    _items, posts = digest(old, new)
    assert ("Also on the books: Quiet Man (Memphis, $8.8 million in 2027-28), "
            "Other Man (Dallas, $4 million in 2027-28).") in posts[0]
    assert "Quiet Man is on" not in posts[0]


def test_a_quiet_run_is_named_by_what_it_is_worth():
    old = payload([])
    new = payload([rec("Quiet Man", "2027-28", 8800000, team="MEM"),
                   rec("Quiet Man", "2028-29", 9200000, team="MEM")])
    _items, posts = digest(old, new)
    assert ("Also on the books: Quiet Man (Memphis, $18 million over two "
            "seasons).") in posts[0]


def test_a_raise_of_a_quarter_keeps_its_own_entry():
    old = payload([rec("Raised Man", CURRENT, 10000000, team="MEM")])
    new = payload([rec("Raised Man", CURRENT, 10000000, team="MEM"),
                   rec("Raised Man", "2027-28", 14000000, team="MEM")])
    _items, posts = digest(old, new)
    assert ("Raised Man's salary rises 40% in 2027-28: Memphis has him at "
            "$14 million, up from $10 million now.") in posts[0]
    assert "Also on the books" not in posts[0]


def test_a_record_keeps_a_quiet_item_out_of_the_line():
    old = payload([])
    new = payload([rec("Quiet Man", CURRENT, 8800000, team="MEM")])
    factoids = {"Quiet Man|2026-27": [{"text": "Worth saying.", "rank": 1}]}
    _items, posts = digest(old, new, factoids=factoids)
    assert "Quiet Man is on Memphis' books at $8.8 million for 2026-27." \
        in posts[0]
    assert "Worth saying." in posts[0]
    assert "Also on the books" not in posts[0]


def test_a_season_off_the_books_keeps_its_own_entry():
    """"Also on the books" is the wrong line for money that has left them."""
    old = payload([rec("Waived Man", CURRENT, 8000000)])
    new = payload([])
    _items, posts = digest(old, new)
    assert "Waived Man is no longer on Atlanta's 2026-27 books" in posts[0]
    assert "Also on the books" not in posts[0]


def test_the_quiet_line_links_every_name_in_it():
    old = payload([])
    new = payload([rec("Quiet Man", "2027-28", 8800000, team="MEM")])
    _items, posts = digest(
        old, new, slugs={"Quiet Man": "quiet-man"},
        teams={"team": {"MEM": "memphis-grizzlies"}})
    assert "/player/quiet-man/|Quiet Man>" in posts[0]
    assert "/team/memphis-grizzlies/|Memphis>" in posts[0]


# --------------------------------------------------------------------------
# which season a comparison reads
# --------------------------------------------------------------------------


def peers_playing_now(mine, others, gp=12, **kwargs):
    """The same guards, with the season under way holding their numbers."""
    data = peer_payload(mine, others, **kwargs)
    pairs = {"Peer Man": mine}
    for i, figures in enumerate(others, 1):
        pairs["Guard {:02d}".format(i)] = figures
    for record in data["seasons"]:
        if record["season"] == CURRENT and record["player"] in pairs:
            ppg, apg = pairs[record["player"]]
            record.update({"gp": gp, "ppg": ppg, "apg": apg, "rpg": 3.0})
    return data


FIVE_PEERS = [(7.1, 5.0), (7.9, 5.9), (7.2, 5.5), (7.6, 5.1), (7.3, 5.7)]


def peer_nugget_now(mine, others, gp=12, **kwargs):
    data = peers_playing_now(mine, others, gp=gp, **kwargs)
    idx = F.build_index(data)
    item = {"player": "Peer Man", "season": CURRENT, "kind": "salary",
            "salary": 25000000, "record": idx.record("Peer Man", CURRENT),
            "members": []}
    return N.peer_nugget(idx, item, data, F.fmt_money), data


def test_ten_games_this_season_switches_the_comparison_to_it():
    nugget, _data = peer_nugget_now((7.4, 5.2), FIVE_PEERS, gp=10)
    assert nugget["opener"].startswith(
        "Guards who have averaged 7 to 8 points and 5 to 6 assists this season")
    assert nugget["detail"]["season"] == CURRENT
    assert "last season" not in nugget["opener"]


def test_under_ten_games_the_comparison_is_the_season_played_out():
    nugget, _data = peer_nugget_now((7.4, 5.2), FIVE_PEERS, gp=9)
    assert nugget["opener"].startswith(
        "Guards who averaged 7 to 8 points and 5 to 6 assists last season")
    assert nugget["detail"]["season"] == PAST


def test_this_season_is_said_once():
    nugget, _data = peer_nugget_now((7.4, 5.2), FIVE_PEERS)
    assert nugget["opener"].count("this season") == 1


def test_the_whole_comparison_reads_one_season():
    """His numbers and theirs are never read off different seasons."""
    nugget, _data = peer_nugget_now((7.4, 5.2), FIVE_PEERS)
    assert nugget["peer_link"]["season"] == CURRENT
    assert nugget["peer_link"]["gp_min"] == N.PEER_CURRENT_MIN_GAMES


def test_a_season_under_way_keeps_the_ten_game_floor_for_everybody():
    """Nobody has 40 games in October, so the floor that opened the window is
    the floor the group clears."""
    data = peers_playing_now((7.4, 5.2), FIVE_PEERS, gp=12)
    for record in data["seasons"]:
        if record["season"] == CURRENT and record["player"] == "Guard 01":
            record["gp"] = 11
    idx = F.build_index(data)
    item = {"player": "Peer Man", "season": CURRENT, "kind": "salary",
            "salary": 25000000, "record": idx.record("Peer Man", CURRENT),
            "members": []}
    nugget = N.peer_nugget(idx, item, data, F.fmt_money)
    assert nugget is not None
    assert nugget["detail"]["others"] == 5


def test_a_man_with_no_line_in_the_window_has_no_comparison():
    data = peer_payload((7.4, 5.2), FIVE_PEERS)
    data["seasons"] = [r for r in data["seasons"]
                       if not (r["player"] == "Peer Man" and r["season"] == PAST)]
    idx = F.build_index(data)
    item = {"player": "Peer Man", "season": CURRENT, "kind": "salary",
            "salary": 25000000, "record": idx.record("Peer Man", CURRENT),
            "members": []}
    assert N.peer_nugget(idx, item, data, F.fmt_money) is None


def test_the_quiet_line_names_a_man_once():
    # no season on this year's books, so there is no raise to measure
    old = payload([])
    new = payload([rec("Quiet Man", "2027-28", 6900000, team="MEM"),
                   rec("Quiet Man", "2028-29", 8800000, team="MEM")])
    _items, posts = digest(old, new)
    assert ("Also on the books: Quiet Man (Memphis, $15.7 million over two "
            "seasons).") in posts[0]


def test_two_quiet_team_changes_for_one_man_are_one_entry():
    old = payload([rec("Moved Man", CURRENT, 6900000, team="DAL"),
                   rec("Moved Man", "2027-28", 8800000, team="DAL")])
    new = payload([rec("Moved Man", CURRENT, 6900000, team="MEM"),
                   rec("Moved Man", "2027-28", 8800000, team="MEM")])
    _items, posts = digest(old, new)
    assert ("Also on the books: Moved Man (Memphis, $6.9 million in 2026-27 "
            "and $8.8 million in 2027-28).") in posts[0]
    assert posts[0].count("Moved Man") == 1
