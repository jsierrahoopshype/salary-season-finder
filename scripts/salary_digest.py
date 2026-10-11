#!/usr/bin/env python3
"""The daily salary digest: what moved in data/data.json since the last build.

Runs after the daily data build, compares the data.json that was just committed
against the one the last digest saw, and posts a sentence per change to Slack.
The commit it last read is kept in data/digest_state.json and committed with the
rest, so a rerun of the workflow finds nothing to say rather than posting twice.
The first run writes that file and posts nothing: there is no previous build to
compare against, and a baseline is not news.

What counts as a change, for the current season and every season after it:

new         a player-season that was not on the books at all
extension   a season beyond the furthest one that player had
salary      a different number in the same player-season
team        the same money on a different team's books
gone        a player-season that is no longer there

Over-reporting is the brief: a correction and a signing look the same in a
salary sheet, so every one of the five goes in the digest and the sentence says
what the data did rather than guessing at the transaction behind it. The one
thing collapsed is minimum deals, which on a busy day would otherwise bury the
two or three salaries worth reading about. data/min_scale.json draws that line,
and a minimum deal that carries a factoid is printed in full anyway.

    python scripts/salary_digest.py                  # post, then save the state
    python scripts/salary_digest.py --dry-run        # print, touch nothing
    python scripts/salary_digest.py --since <sha>    # compare against that build
    python scripts/salary_digest.py --summary FILE   # also append to FILE
    python scripts/salary_digest.py --state FILE     # read and write FILE
"""

from __future__ import annotations

import argparse
import collections
import datetime
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import digest_teams as T  # noqa: E402
import factoids as F  # noqa: E402
import digest_nuggets as N  # noqa: E402

DATA_PATH = os.path.join("data", "data.json")
STATE_PATH = os.path.join(REPO, "data", "digest_state.json")
MIN_SCALE_PATH = os.path.join(REPO, "data", "min_scale.json")
FACTOIDS_PATH = os.path.join(REPO, "data", "factoids.json")
SLUGS_PATH = os.path.join(REPO, "data", "slugs.json")
FRANCHISES_PATH = os.path.join(REPO, "data", "franchises.json")

TOOL_ROOT = "https://hoopsmatic.com/salary-season-finder"

#: Slack renders a post this long comfortably; past it the thread is better.
POST_LIMIT = 3500

#: An item is the lead and one nugget: two sentences in all. Where several
#: nuggets qualify, digest_nuggets keeps the strongest, which is record, then
#: career milestone, then raise rank, then peers, then where the money ends.
NUGGETS_PER_CHANGE = 1

#: Cohort kind -> the directory its page lives in. Slugs for the first three
#: are derived rather than stored, the same way the prerender derives them.
COHORT_DIRS = {
    "draft_class": "draft", "draft_slot": "pick", "position": "position",
    "region": "region", "pick_range": "pick-range",
    "college": "college", "nationality": "country",
    "college_position": "college-position",
}

POSITION_SLUGS = {"G": "guard", "F": "forward", "C": "center"}

#: How near a scale amount a salary has to be to be read as that amount. The
#: rungs of a season's scale are 4% apart at the narrowest, so this cannot reach
#: from one to the next; it is wide enough for the drift between a projected cap
#: and the one the sheets were built against.
SCALE_MATCH = 0.015

#: The order changes are read in: the ones a reader wants first, first.
KIND_ORDER = ("new", "extension", "salary", "team", "gone")

#: What a day's minimum salaries come to, by what happened to them. A changed
#: number is in none of them: it is one short line, and a correction to a
#: minimum salary is as worth reading as any other.
COLLAPSE_LABEL = {
    "added_now": "New at the minimum for {season}",
    "added_later": "New at the minimum for {season}",
    "moved": "On a new team's books at the minimum for {season}",
    "removed": "Off the {season} books",
}

#: The order those lines are read in: this season, then the ones after it, then
#: the money that only changed hands, then the money that has gone.
COLLAPSE_ORDER = ("added_now", "added_later", "moved", "removed")

MONTHS = ("Jan.", "Feb.", "March", "April", "May", "June", "July", "Aug.",
          "Sept.", "Oct.", "Nov.", "Dec.")


def ap_date(when):
    """AP style: Oct. 1, 2026."""
    return "{} {}, {}".format(MONTHS[when.month - 1], when.day, when.year)


# ── git ────────────────────────────────────────────────────────────────
def git(*args):
    return subprocess.run(
        ["git"] + list(args), cwd=REPO, capture_output=True, text=True)


def head_commit():
    out = git("rev-parse", "HEAD")
    return out.stdout.strip() if out.returncode == 0 else ""


def data_at(commit):
    """data/data.json as of that commit, or None if it cannot be read."""
    out = git("show", "{}:{}".format(commit, DATA_PATH))
    if out.returncode != 0 or not out.stdout.strip():
        return None
    try:
        return json.loads(out.stdout)
    except ValueError:
        return None


# ── the files beside it ────────────────────────────────────────────────
def load_state(path=STATE_PATH):
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def save_state(commit, path=STATE_PATH, posted=0, raises=None, opened=None):
    stamp = datetime.datetime.now(datetime.timezone.utc)
    kept = []
    for entry in raises or []:
        entry = dict(entry)
        entry.setdefault("date", stamp.date().isoformat())
        if opened is None or entry["date"] >= opened.isoformat():
            kept.append(entry)
    kept.sort(key=lambda entry: (-(entry.get("amount") or 0), entry["player"]))
    payload = {
        "readme": (
            "The build scripts/salary_digest.py last read. Committed so that a "
            "rerun of the workflow compares against the same place and never "
            "posts a day's changes twice. raises holds every raise the digest "
            "has seen since the league year opened on July 1, which is what a "
            "raise is ranked against; entries before that date are dropped."
        ),
        "last_commit": commit,
        "last_run": stamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "changes_posted": posted,
        "league_year_opened": opened.isoformat() if opened else "",
        "raises": kept,
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1, sort_keys=True)
        fh.write("\n")


def load_min_scale(path=MIN_SCALE_PATH):
    if not os.path.exists(path):
        return {}, 0.0
    with open(path, "r", encoding="utf-8") as fh:
        payload = json.load(fh)
    return payload.get("scale") or {}, float(payload.get("tolerance") or 0.0)


def load_factoids(path=FACTOIDS_PATH):
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        return (json.load(fh) or {}).get("factoids") or {}


def load_cohort_slugs(path=SLUGS_PATH):
    """Every family's slug map except the players', for the link pass."""
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        families = (json.load(fh) or {}).get("slugs") or {}
    return {name: table for name, table in families.items() if name != "player"}


def load_team_names(path=FRANCHISES_PATH, codes=False):
    """Team code -> the nickname a sentence names, from data/franchises.json.

    The nickname and never the city: "Los Angeles" is two teams, and a line
    pasted onto the Rumors page reads "the Clippers" the way the rest of it
    does. team_name() puts the article on.

    With codes, the map the page URLs want instead: code -> page slug.
    """
    if not os.path.exists(path):
        return {}
    if codes:
        return (load_cohort_slugs(SLUGS_PATH).get("team") or {})
    with open(path, "r", encoding="utf-8") as fh:
        payload = (json.load(fh) or {}).get("franchises") or {}
    out = {}
    for code, entry in payload.items():
        nickname = (entry.get("name") or "").strip()
        full = (entry.get("full_name") or "").strip()
        if nickname or full:
            out[code] = nickname or full
    return out


def load_player_slugs(path=SLUGS_PATH):
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        return ((json.load(fh) or {}).get("slugs") or {}).get("player") or {}


# ── the pages under each item ─────────────────────────────────────────
# The sentences are plain text: no name in them is a link. The page an item is
# about goes on a line of its own under it, as a bare URL, so that the block
# (text and URLs) can be copied onto the HoopsHype Rumors page as one unit.
# Only a page the built site has is ever printed; a URL nobody can open is
# worse than none.


def page_exists(url, site_root=REPO):
    """Whether the built site has the page this URL names.

    The site is the prerendered tree in this repository, served under
    TOOL_ROOT, so /player/johni-broome/ is player/johni-broome/index.html.
    """
    if not url or not url.startswith(TOOL_ROOT):
        return False
    rest = url[len(TOOL_ROOT):].split("#", 1)[0].split("?", 1)[0].strip("/")
    parts = [p for p in rest.split("/") if p]
    if any(p in (".", "..") for p in parts):
        return False
    return os.path.isfile(os.path.join(site_root, *(parts + ["index.html"])))


def season_url(season):
    return "{}/season/{}/".format(TOOL_ROOT, season) if season else ""


def existing(urls, context):
    """The URLs whose pages exist, in order, each once."""
    exists = (context or {}).get("page_exists") or page_exists
    out = []
    for url in urls:
        if url and url not in out and exists(url):
            out.append(url)
    return out


def first_existing(urls, context):
    """The first of these with a page, as a one-item list, or nothing."""
    found = existing(urls, context)
    return found[:1]


def fallback_urls(seasons, context):
    """Where an item points when its own page is missing: the season, else the
    tool itself. Every item gets at least one URL a reader can open."""
    return first_existing(
        [season_url(s) for s in seasons] + [TOOL_ROOT + "/"], context)


def item_urls(item, context):
    """The page under a player item: his, or failing that the nearest that is."""
    slugs, teams = context.get("slugs") or {}, context.get("teams") or {}
    mine = first_existing([player_url(slugs, item["player"])], context)
    if mine:
        return mine
    codes = []
    for source in [item] + list(item.get("members") or []):
        for code in (source.get("team") or "").split(","):
            if code.strip() and code.strip() not in codes:
                codes.append(code.strip())
    return (first_existing([team_url(teams, c) for c in codes], context)
            or fallback_urls([item.get("season")], context))


def players_urls(players, seasons, context):
    """One URL per player with a page, in the order the line names them."""
    slugs = context.get("slugs") or {}
    out = []
    for player in players:
        for url in existing([player_url(slugs, player)], context):
            if url not in out:
                out.append(url)
    return out or fallback_urls(seasons, context)


def with_urls(text, urls):
    """A block: the text, then each URL on its own line."""
    return "\n".join([text] + list(urls))


def team_url(teams, code):
    slug = (teams or {}).get((code or "").strip())
    return "{}/team/{}/".format(TOOL_ROOT, slug) if slug else ""


def cohort_label(idx, kind, key):
    """What a sentence calls this cohort, which is what a link has to cover."""
    if kind == "college":
        return idx.college_display(key) if idx else key
    if kind == "draft_slot":
        return "No. {}".format(key) if key != "undrafted" else "undrafted"
    if kind == "draft_class":
        return "{} draft class".format(key)
    if kind == "position":
        return {"G": "guard", "F": "forward", "C": "center"}.get(key, key)
    if kind == "region":
        return (F.REGION_PHRASES.get(key) or {}).get("one", key)
    return key


def cohort_url(slugs, kind, key):
    """The page for one cohort, or nothing where that cohort has none."""
    directory = COHORT_DIRS.get(kind)
    if not directory:
        return ""
    if kind == "position":
        slug = POSITION_SLUGS.get(key)
    elif kind in ("draft_class", "draft_slot", "region", "pick_range"):
        slug = str(key)
    else:
        slug = ((slugs or {}).get(kind.replace("nationality", "country")) or {}).get(key)
    return "{}/{}/{}/".format(TOOL_ROOT, directory, slug) if slug else ""


def peer_url(spec):
    """The tool, filtered to exactly the peer group a nugget counted.

    Every one of these is a filter the tool already has; the games floor only
    needed carrying in the hash, which js/app.js now does.
    """
    low_p, high_p = spec["ppg"]
    second = spec["second_stat"]
    low_o, high_o = spec[second]
    params = [
        ("from", spec["season"]), ("to", spec["season"]),
        ("pos", spec["pos"]),
        ("gp_min", spec["gp_min"]),
        ("ppg_min", low_p), ("ppg_max", high_p),
        ("{}_min".format(second[:3]), low_o),
        ("{}_max".format(second[:3]), high_o),
    ]
    return "{}#{}".format(TOOL_ROOT, "&".join(
        "{}={}".format(key, value) for key, value in params))


def player_url(slugs, name):
    """The page for this man, or nothing if he has none yet.

    ``slugs`` is either the player table or the whole families map. A split key
    files its segments as "Name#0" and "Name#1"; the later segment is the man
    playing now, which is the one a digest is ever about.
    """
    table = slugs.get("player") if isinstance(slugs.get("player"), dict) else slugs
    slug = table.get(name)
    if not slug:
        parts = sorted(k for k in table if k.rsplit("#", 1)[0] == name)
        if not parts:
            return ""
        slug = table[parts[-1]]
    return "{}/player/{}/".format(TOOL_ROOT, slug)


# ── comparing two builds ───────────────────────────────────────────────
def by_key(data):
    return {(r["player"], r["season"]): r for r in data.get("seasons") or []}


def last_season(records, player):
    seasons = [s for (p, s) in records if p == player]
    return max(seasons, key=F.season_key) if seasons else None


def changes(old, new, from_season):
    """Every change worth a sentence, newest money first."""
    was, now = by_key(old), by_key(new)
    floor = F.season_key(from_season)
    items = []

    for key in sorted(set(now) - set(was)):
        player, season = key
        if F.season_key(season) < floor:
            continue
        previous_last = last_season(was, player)
        kind = "new"
        if previous_last and F.season_key(season) > F.season_key(previous_last):
            kind = "extension"
        items.append({
            "kind": kind, "player": player, "season": season,
            "record": now[key], "salary": now[key].get("salary"),
            "team": now[key].get("team") or "",
        })

    for key in sorted(set(was) - set(now)):
        player, season = key
        if F.season_key(season) < floor:
            continue
        items.append({
            "kind": "gone", "player": player, "season": season,
            "record": was[key], "salary": was[key].get("salary"),
            "team": was[key].get("team") or "",
        })

    for key in sorted(set(was) & set(now)):
        player, season = key
        if F.season_key(season) < floor:
            continue
        before, after = was[key], now[key]
        if (before.get("salary") or 0) != (after.get("salary") or 0):
            items.append({
                "kind": "salary", "player": player, "season": season,
                "record": after, "salary": after.get("salary"),
                "was": before.get("salary"), "team": after.get("team") or "",
            })
        if (before.get("team") or "") != (after.get("team") or ""):
            items.append({
                "kind": "team", "player": player, "season": season,
                "record": after, "salary": after.get("salary"),
                "was_team": before.get("team") or "", "team": after.get("team") or "",
            })

    items = drop_rescales(items)
    items.sort(key=lambda i: (
        KIND_ORDER.index(i["kind"]), -(i.get("salary") or 0), i["player"]))
    return group_by_player(items)


#: How many salaries have to move by the same ratio before the day reads as the
#: whole sheet being rebuilt against a new cap projection rather than as news.
RESCALE_MIN = 10

#: Ratios are rounded to this many places before they are counted as the same.
RESCALE_PLACES = 3


def drop_rescales(items):
    """Salary changes that are one sheet-wide rescale, not one piece of news.

    When a cap projection moves, every future salary built off it moves by the
    same ratio on the same day. Fifty players did not each get a raise; one
    number upstream changed. A run of changes sharing a ratio is dropped
    wholesale rather than filling the digest.
    """
    counts = collections.Counter()
    for item in items:
        ratio = _ratio(item)
        if ratio is not None:
            counts[ratio] += 1
    rescaled = {r for r, n in counts.items() if n >= RESCALE_MIN}
    if not rescaled:
        return items
    return [i for i in items if _ratio(i) not in rescaled]


def _ratio(item):
    """What a salary was multiplied by, for a change that is only a number."""
    if item.get("kind") != "salary":
        return None
    was, now = item.get("was") or 0, item.get("salary") or 0
    if not was or not now:
        return None
    return round(now / float(was), RESCALE_PLACES)


def drop_impossible(items, idx):
    """A salary no contract can pay is a data fault, not a change to report.

    The engine flags these on the index; the digest honours the same flag, so a
    projection that cannot be real never becomes an item, a raise or part of a
    team's committed total.
    """
    out = []
    for item in items:
        player = idx.canonical(item["player"])
        members = item.get("members")
        if members:
            # A run is re-measured on the seasons that survive, not dropped
            # because the flagged one happened to be its last.
            kept = [m for m in members
                    if (player, m["season"]) not in idx.impossible]
            if not kept:
                continue
            if len(kept) != len(members):
                item = _regroup(item, kept)
        elif (player, item["season"]) in idx.impossible:
            continue
        out.append(item)
    return out


def _regroup(item, members):
    """A merged item with some of its seasons flagged away, built on the rest.

    The members are the plain changes the day found, so the rest are regrouped
    the same way the day grouped them in the first place.
    """
    fallback = {"player": item["player"], "kind": item["kind"].replace("_run", "")}
    return group_by_player([dict(fallback, **m) for m in members])[0]


def group_by_player(items):
    """Every change one man had on one day, as one item.

    A day's sheet can move three of his seasons, put a fourth on another team's
    books and drop a fifth. That is one piece of news about one man, and five
    entries for it is a digest nobody finishes. The members are kept whole, so
    the money, the factoids and the minimum test all still read the seasons
    themselves, and ``groups`` holds them by kind, in the order a reader wants
    them, so the sentence can say each in a clause of its own.
    """
    by_player = collections.OrderedDict()
    for item in items:
        by_player.setdefault(item["player"], []).append(item)

    out = []
    for _player, mine in by_player.items():
        by_kind = collections.OrderedDict()
        for one in mine:
            by_kind.setdefault(_group_key(one), []).append(one)
        groups = sorted(by_kind.items(),
                        key=lambda kv: (KIND_ORDER.index(kv[0][0]), kv[0][1]))
        for _key, members in groups:
            members.sort(key=lambda i: F.season_key(i["season"]))
        everything = [m for _key, members in groups for m in members]
        groups = [(key[0], members) for key, members in groups]

        item = _group_item(groups[0][0], groups[0][1])
        item["groups"] = groups
        item["members"] = everything
        item["salary"] = max((m.get("salary") or 0) for m in everything)
        item["peak_salary"] = item["salary"]
        out.append(item)

    out.sort(key=lambda i: (
        KIND_ORDER.index(i["kind"].replace("_run", "")),
        -(i.get("salary") or 0), i["player"]))
    return out


def _group_key(item):
    """What makes two of his changes one clause of the sentence.

    The kind, and for the two kinds that are about a team, the team: two
    seasons moving to two different teams are two things to say, and a clause
    that named neither would say nothing.
    """
    kind = item["kind"]
    if kind in ("team", "gone"):
        return kind, (item.get("team") or item.get("was_team") or "")
    return kind, ""


def _group_item(kind, members, parent=None):
    """One kind of change over one or more of his seasons, as one item.

    This is what a clause of the sentence is written from, and what the leading
    group lends the merged item: its kind, its first and last season, and the
    money at either end of it.
    """
    teams = {m["team"] for m in members if m.get("team")}
    item = dict(members[0])
    item.update({
        "kind": kind + "_run" if len(members) > 1 else kind,
        "season": members[-1]["season"],
        "first_season": members[0]["season"],
        "record": members[-1].get("record"),
        "salary": max((m.get("salary") or 0) for m in members),
        "first_salary": members[0].get("salary"),
        "last_salary": members[-1].get("salary"),
        "first_was": members[0].get("was"),
        "peak_salary": max((m.get("salary") or 0) for m in members),
        "team": list(teams)[0] if len(teams) == 1 else "",
        "members": members,
    })
    if parent is not None:
        item["current_salary"] = parent.get("current_salary") or 0
        item["owed"] = parent.get("owed")
    return item


def is_minimum(item, scale, tolerance):
    """Whether this is a minimum deal, as data/min_scale.json draws the line.

    A run of seasons is one only where every season in it is.
    """
    members = item.get("members")
    if members:
        return all(is_minimum(m, scale, tolerance) for m in members)
    salary = item.get("salary")
    if not salary:
        return True
    season = (scale.get(item["season"]) or {})
    if not season:
        return False
    years = item["record"].get("years_exp")
    years = 0 if years is None else min(int(years), 10)
    ceiling = season.get(str(years))
    if ceiling and salary <= ceiling * (1.0 + tolerance):
        return True
    # years_exp counts seasons in this file, so a man with service elsewhere can
    # be paid a later year's minimum while the file reads him as a rookie. A
    # salary that is one of the season's scale amounts is a minimum deal
    # whichever row of the scale it comes from.
    return any(abs(salary - amount) <= amount * SCALE_MATCH
               for amount in season.values())


# ── writing it ─────────────────────────────────────────────────────────
def team_name(code, names=None):
    """What a sentence calls a team, including a two-team season.

    "the Bucks", "the Clippers and the Bucks": the nickname with its article,
    never the city.
    """
    names = names or {}
    parts = [p.strip() for p in (code or "").split(",") if p.strip()]
    if not parts:
        return "nobody"
    spelled = ["the " + names.get(p, p) for p in parts]
    if len(spelled) == 1:
        return spelled[0]
    return "{} and {}".format(", ".join(spelled[:-1]), spelled[-1])


#: Nicknames that take a singular verb: "the Magic has", "the Bucks have".
SINGULAR_NICKNAMES = ("Heat", "Jazz", "Magic", "Thunder")


def has_verb(name):
    """"has" or "have", to agree with what team_name() returned."""
    if " and " in name:
        return "have"
    return "has" if name.split()[-1] in SINGULAR_NICKNAMES else "have"


def capitalized(text):
    return text[:1].upper() + text[1:]


def possessive(name):
    """AP style: the Bucks' books, the Jazz's books, Johni Broome's books.

    Two teams each take their own: the Mavericks' and the Nuggets' books.
    """
    if name.startswith("the ") and " and the " in name:
        parts = re.split(r"(, | and )", name)
        return "".join(part if part in (", ", " and ") else possessive(part)
                       for part in parts)
    return name + ("'" if name.endswith("s") else "'s")


def lead(item, names=None, teams=None, slugs=None):
    """The first sentence, as plain text.

    No name in it is a link: the page it is about goes under the block.
    """
    return sentence(item, names)


#: How a salary moved, in a verb a reader feels. The multiples first, because
#: "nearly quadruples" says more than "gets a 294% raise"; then the percentage,
#: which is how a raise inside a multiple is spoken about.
#: Under this much the move is not the news, and "rises 4%" is a line nobody
#: would file. The money itself leads instead.
MOVEMENT_FLOOR = 0.15


#: A multiple is only used where the number is close enough to it to be read
#: that way. 2.6 times is not "triples", and 3.5 times is not "triples" either;
#: both are "more than" the multiple below them. Outside these bands the
#: percentage is the honest form.
MULTIPLES = ((2.9, 3.1, "triples", "more than triples"),
             (1.9, 2.1, "doubles", "more than doubles"))


def movement(now, before):
    """The verb phrase for a salary going from ``before`` to ``now``."""
    if not before or not now:
        return ""
    ratio = now / float(before)
    for low, high, exact, over in MULTIPLES:
        if ratio > high:
            return over
        if ratio >= low:
            return exact
    if ratio >= 1 + MOVEMENT_FLOOR:
        return "rises {:.0f}%".format((ratio - 1) * 100)
    if ratio <= 1 - MOVEMENT_FLOOR:
        return "falls {:.0f}%".format((1 - ratio) * 100)
    return ""


def over_seasons(item):
    """"$24 million over three seasons", for a run that carries one figure.

    One figure a season: a day that moved the same season twice moved one
    salary, not two, so the seasons are counted and not the changes.
    """
    by_season = _by_season(item.get("members") or [])
    total = sum(by_season.values())
    if len(by_season) < 2 or not total:
        return ""
    return "{} over {} seasons".format(F.fmt_money(total), _spell(len(by_season)))


def _by_season(members):
    """{season: the salary it ends the day on}, in season order."""
    out = collections.OrderedDict()
    seasons = {m.get("season") for m in members if m.get("season")}
    for season in sorted(seasons, key=F.season_key):
        salaries = [m.get("salary") or 0 for m in members
                    if m["season"] == season]
        out[season] = max(salaries) if salaries else 0
    return out


NUMBER_WORDS = ("zero", "one", "two", "three", "four", "five", "six", "seven",
                "eight", "nine", "ten")


def _spell(n):
    return NUMBER_WORDS[n] if 0 <= n < len(NUMBER_WORDS) else str(n)


def sentence(item, names=None):
    """One publishable line about what the data did to one man.

    The news first, in a verb that carries it, then the money. Never a contract
    word: this data holds a salary against a season, and whether it came from a
    signing, an option or a correction is not in it.

    A day that moved his seasons in more than one way says each of them in a
    clause of the same sentence. The first clause names him and the rest say
    "he", so the line reads as one piece of news rather than a list of them.
    """
    groups = item.get("groups") or [(item["kind"].replace("_run", ""),
                                     item.get("members") or [item])]
    clauses = []
    said = {"seasons": set(), "teams": set()}
    # A season whose money now sits on two teams' books is said first and on
    # its own: each team's figure, then what the season comes to. Read as a
    # changed number it was a summed "raise", which it is not.
    split = split_seasons(item)
    for season, members in split.items():
        clauses.append(_split_clause(
            season, members, names, item["player"] if not clauses else "he"))
        said["seasons"].add(season)
        said["teams"].update(
            (members[-1].get("record") or {}).get("team_salaries") or {})
    groups = [(kind, [m for m in members if m["season"] not in split])
              for kind, members in groups]
    groups = [(kind, members) for kind, members in groups if members]
    for kind, members in groups:
        place = len(clauses)
        group = _group_item(kind, members, item)
        text = _clause(group, names,
                       item["player"] if not place else "he",
                       possessive(item["player"]) if not place else "his",
                       said)
        if text:
            clauses.append(text)
        # Only a clause of the same shape can lend its team to the next one:
        # "$9.7 million for 2026-27" reads as books money after "is on
        # Charlotte's books at $10.1 million for 2027-28" and as nothing at all
        # after a sentence about a raise.
        said["shape"] = ON_BOOKS if ON_BOOKS in (text or "") else ""
        said["seasons"].update(m["season"] for m in members)
        said["teams"].update(
            code.strip() for code in (group.get("team") or "").split(",")
            if code.strip())
    if not clauses:
        return ""
    if len(clauses) == 1:
        return clauses[0] + "."
    # A clause carrying an "and" of its own, or two clauses of its own, cannot
    # take another: the semicolon keeps the day to one sentence without three
    # ands inside it, and without an "and" a reader has to re-read.
    if any(_crowded(text) for text in clauses):
        return "; ".join(clauses) + "."
    return "{}, and {}.".format(", ".join(clauses[:-1]), clauses[-1])


def split_seasons(item):
    """{season: his changes in it} for each season whose money now sits on
    more than one team's books, as the record's team_salaries says."""
    out = collections.OrderedDict()
    for member in item.get("members") or [item]:
        if member["kind"].replace("_run", "") == "gone":
            continue
        record = member.get("record") or {}
        paid = [code for code, amount in (record.get("team_salaries") or {}).items()
                if amount]
        if len(paid) > 1:
            out.setdefault(member["season"], []).append(member)
    return out


def _split_clause(season, members, names, who):
    """"Johni Broome now has 2026-27 money on two teams' books, $2.2 million
    with the Clippers and $2.2 million with the Bucks, and his total for the
    season rises to $4.3 million from $2.2 million".

    Each team's figure on its own; the total only as what the season comes to.
    The team that already held the money is named first, without saying it was
    his team: the sentence is about whose books the money is on now.
    """
    record = members[-1].get("record") or {}
    paid = {code: amount for code, amount
            in (record.get("team_salaries") or {}).items() if amount}
    held = []
    for member in members:
        for code in (member.get("was_team") or "").split(","):
            code = code.strip()
            if code in paid and code not in held:
                held.append(code)
    order = held + sorted((c for c in paid if c not in held),
                          key=lambda c: (-paid[c], c))
    amounts = _join_and(["{} with {}".format(F.fmt_money(paid[c]),
                                             team_name(c, names))
                         for c in order])
    total = record.get("salary") or sum(paid.values())
    text = "{} now has {} money on {} teams' books, {}".format(
        who, season, _spell(len(order)), amounts)
    was = next((m.get("was") for m in members if m.get("was")), None)
    if was and was != total:
        return "{}, and his total for the season {} to {} from {}".format(
            text, "rises" if total > was else "falls",
            F.fmt_money(total), F.fmt_money(was))
    if not any(m["kind"] == "team" for m in members):
        return "{}, {} in all".format(text, F.fmt_money(total))
    return text


def _crowded(text):
    """Whether a clause is already too busy to take another by an "and"."""
    if ", and " in text:
        return True
    # The commas inside $678,882 are not clauses.
    return re.sub(r"(?<=\d),(?=\d)", "", text).count(",") >= 2


def _clause(item, names, who, whose, said=None):
    """One kind of change, as a clause with no full stop on it.

    ``who`` and ``whose`` are the player's name in the first clause and a
    pronoun in every one after it. ``said`` is the seasons and teams the
    clauses before it already named, so this one says only what is new: a
    season restated with the same figure, or a team named twice, is the noise
    a merged sentence would otherwise read as.
    """
    said = said or {"seasons": set(), "teams": set()}
    repeats_team = _repeats_team(item, said) and said.get("shape") == ON_BOOKS
    season = item["season"]
    money = F.fmt_money(item.get("salary"))
    team = team_name(item["team"], names) if item.get("team") else ""
    kind, now = item["kind"], item.get("current_salary") or 0
    biggest = item.get("peak_salary") or item.get("salary") or 0
    verb = movement(biggest, now)
    through = len(item.get("members") or []) > 1

    if kind in ("new", "new_run", "extension", "extension_run"):
        if verb and now:
            # The clause has named him already, so the money takes a pronoun.
            where = "{} {} him at".format(capitalized(team), has_verb(team)) \
                if team else "he is due"
            lead = "{} salary {} in {}".format(
                whose, verb, item.get("first_season") or season)
            body = "{} {}".format(where, _run_money(item))
            if through:
                body += " through {}".format(season)
            # "up from $4.2 million now" says nothing when the run opens on that
            # same figure: the climb inside it is the whole story.
            if item.get("first_salary") == now:
                return "{}: {}".format(lead, body)
            return "{}: {}, up from {} now".format(lead, body, F.fmt_money(now))
        if through:
            return "{} is due {} through {}{}".format(
                who, _run_money(item), season,
                " on {} books".format(possessive(team)) if team else "")
        if team:
            if repeats_team:
                return "{} for {}".format(money, season)
            return "{} is on {} books at {} for {}".format(
                who, possessive(team), money, season)
        return "{} is due {} in {}".format(who, money, season)

    if kind in ("salary", "salary_run"):
        return changed_salary(item, whose)

    if kind in ("team", "team_run"):
        # Where the money used to sit is not the news and dates the line: what
        # a reader wants is whose books it is on now.
        if {m["season"] for m in item["members"]} <= said["seasons"]:
            # A clause before this one already gave the season and the figure.
            return "it is now on {} books".format(possessive(team))
        if repeats_team and not through:
            return "{} for {}".format(money, season)
        if through:
            return "{} is on {} books for {} through {}, {} in all".format(
                who, possessive(team), item["first_season"], season,
                F.fmt_money(_total(item)))
        return "{} is on {} books at {} for {}".format(
            who, possessive(team), money, season)

    left = possessive(team_name(item.get("was_team") or item.get("team"), names))
    if through:
        return "{} is no longer on {} books for {} through {}, {} in all".format(
            who, left, item["first_season"], season, F.fmt_money(_total(item)))
    return "{} is no longer on {} {} books, a {} salary".format(
        who, left, season, money)


#: The one clause shape that can lend its team to the clause after it.
ON_BOOKS = " books at "


def _repeats_team(item, said):
    """Whether every team this clause names was named by one before it."""
    codes = {code.strip() for code in (item.get("team") or "").split(",")
             if code.strip()}
    return bool(codes) and codes <= said["teams"]


def _total(item):
    """Every season of a group, added up: one figure a season."""
    return sum(_by_season(item.get("members") or []).values())


#: Where a one-season move stops being a rise and reads as a jump. Under it
#: "rises" is the honest verb; a fifth of a salary in one season is not.
STEP_JUMP = 0.20


def step_verb(now, before):
    """The verb a changed number takes, with no percentage in it.

    Both figures are in the sentence, so a reader can see the size of the move
    without being told it; what the verb is for is the direction, and the
    multiple where the move is big enough to be read as one.
    """
    if not before or not now:
        return ""
    ratio = now / float(before)
    for low, high, exact, over in MULTIPLES:
        if ratio > high:
            return over
        if ratio >= low:
            return exact
    if ratio >= 1 + STEP_JUMP:
        return "jumps"
    if ratio <= 1 - STEP_JUMP:
        return "drops"
    if now > before:
        return "rises"
    if now < before:
        return "falls"
    return ""


def changed_salary(item, whose=None, said=None):
    """A number that moved: the season it moved most in, old to new.

    One number a reader can hold, and then what the whole move is worth: the
    money added to or taken off what he is owed, and the season it runs to.
    "His next four seasons move with it" said that they moved and never said by
    how much, which is the only part worth a line.

    Where the money only moved about inside the deal, the shape is the story
    and the clause says so instead, with what is still owed folded into it so
    the item stays at two sentences.
    """
    if whose is None:
        whose = possessive(item["player"])
    lead, leads = _lead_season(item)
    season = lead["season"]
    was, now = lead.get("was") or 0, lead.get("salary") or 0

    reshaped = _reshaped(item) if leads else None
    if reshaped:
        return "{} salary is {}: {} in {}, {} from {}, with his later seasons " \
            "{} by {}{}".format(
                whose, reshaped["shape"], F.fmt_money(now), season,
                "up" if now > was else "down", F.fmt_money(was),
                reshaped["way"], reshaped["much"], _owed_clause(item))

    verb = step_verb(now, was)
    tail = _rest_of_run(item, leads)
    if not verb:
        return "{} {} salary is {}{}".format(
            whose, season, F.fmt_money(now), tail)
    return "{} {} salary {} to {} from {}{}".format(
        whose, season, verb, F.fmt_money(now), F.fmt_money(was), tail)


def _lead_season(item):
    """(the season the line leads on, whether it is the first one that changed).

    The first changed season, which is the one nearest to now, unless it barely
    moved while a season behind it moved properly: a line that opens on a
    number that did not really change buries the one that did. The biggest move
    is the biggest in money, which is what every other figure in the digest is.
    """
    members = item.get("members") or []
    if not members:
        return item, True
    first = members[0]
    if _clears_floor(first) or not any(_clears_floor(m) for m in members[1:]):
        return first, True
    biggest = max(members, key=lambda m: (
        abs((m.get("salary") or 0) - (m.get("was") or 0)),
        -F.season_key(m["season"])))
    return biggest, biggest is first


def _clears_floor(member):
    """Whether one season moved enough to be worth leading a line with."""
    was = member.get("was") or 0
    now = member.get("salary") or 0
    return bool(was) and abs(now - was) >= was * TINY_CHANGE


def _reshaped(item):
    """A day that moved money about inside a deal without changing the total.

    The seasons went different ways and the total barely moved, so the figure
    a reader wants is not what was added: it is which end of the deal grew and
    which paid for it. Returns the words that say so, or None.
    """
    members = item.get("members") or []
    if len(members) < 2:
        return None
    now = sum((m.get("salary") or 0) for m in members)
    was = sum((m.get("was") or 0) for m in members)
    net = now - was
    ways = {_way((m.get("salary") or 0) - (m.get("was") or 0)) for m in members}
    if len(ways) < 2 or (was and abs(net) >= was * NET_FLOOR):
        return None
    first_now = members[0].get("salary") or 0
    first_was = members[0].get("was") or 0
    led = _way(first_now - first_was)
    if not led:
        return None
    # "Front-loaded" is a claim about the front of the deal, so the season at
    # the front has to have moved enough to carry it. $56.1 million up from
    # $56 million is not a deal being loaded anywhere.
    if not first_was or abs(first_now - first_was) < first_was * TINY_CHANGE:
        return None
    return {
        "shape": "front-loaded" if led > 0 else "back-loaded",
        "way": "coming down" if led > 0 else "going up",
        # Only an exactly flat total is "as much"; a little off and it is not.
        "much": "as much" if not net else "almost as much",
    }


def _owed_clause(item):
    """"and $200 million still owed through 2030-31", inside the same sentence.

    A reshaped deal is two facts, the shape and the total, and a sentence each
    would make the item three with its nugget. Folded together it stays at two.
    """
    total, through = item.get("owed") or (0, "")
    if not total or not through:
        return ""
    return " and {} still owed through {}".format(F.fmt_money(total), through)


#: A net move smaller than this is not worth a figure of its own: the seasons
#: behind the first one changed, and saying so is all the line can carry.
NET_FLOOR = 0.05


def _rest_of_run(item, leads=True):
    """What the seasons behind the first one are worth, as a clause.

    The money the day put on or took off what he is owed, and the season it
    runs to, whichever way the seasons inside it went: what he is owed through
    the last of them went up or down by this much. Where the net is too small
    for a figure the clause says only that the later seasons moved.
    """
    members = item.get("members") or []
    if len(members) < 2:
        return ""
    now = sum((m.get("salary") or 0) for m in members)
    was = sum((m.get("was") or 0) for m in members)
    net = now - was
    if not net or not was or abs(net) < was * NET_FLOOR:
        # Nothing was added or taken off worth a figure, and the shape is not
        # one _reshaped would claim either: all a line can say is that the
        # other seasons moved. "Later" only where the line led on the first of
        # them; where it led on a season further out, some of the rest are
        # earlier than it.
        return ", and his {} seasons change too".format(
            "later" if leads else "other")
    moved = "adding {} to".format(F.fmt_money(net)) if net > 0 \
        else "cutting {} from".format(F.fmt_money(-net))
    return ", {} what he's owed through {}".format(moved, members[-1]["season"])


def _way(difference):
    """Which way a number moved: up, down or not at all."""
    if difference > 0:
        return 1
    return -1 if difference < 0 else 0


def _run_money(item):
    """What a run of seasons is worth: one figure a year, or the climb."""
    first, last = item.get("first_salary"), item.get("last_salary")
    if first is None:
        return F.fmt_money(item.get("salary"))
    if len(item.get("members") or []) < 2:
        return F.fmt_money(first)
    if first == last:
        return "{} a year".format(F.fmt_money(first))
    return "{}, rising to {}".format(F.fmt_money(first), F.fmt_money(last))


def nugget_sentence(nuggets, item, names=None, teams=None, slugs=None,
                    context_idx=None):
    """The second sentence: the strongest thing the money means, plain text.

    One nugget, never two. An item is the lead and this, and a reader carries
    away one fact about a man and not a stat list.
    """
    if not nuggets:
        return ""
    return nuggets[0]["opener"].rstrip(".") + "."


#: Kinds where new money arrives, which is the only place a raise can be.
RAISE_KINDS = ("new", "new_run", "extension", "extension_run", "salary",
               "salary_run")


def raise_amount(idx, item):
    """New money against what he is paid now, which is what a raise is here.

    Not the difference between two future figures: a reader measures a raise
    against the salary on this season's books. A salary coming off the books is
    not a raise, and nor is the same money moving to another team.
    """
    now = idx.record(item["player"], idx.current_season) or {}
    current = now.get("salary") or 0
    # One item now carries every kind of change he had, so the raise is read off
    # the seasons that brought new money and not off the ones that moved team.
    # A season split across two teams' books is not a raise either: what
    # changed is that a second team owes him, and the sum is no one's salary.
    split = split_seasons(item)
    amounts = [
        (m.get("salary") or 0) for m in (item.get("members") or [item])
        if m["kind"].replace("_run", "") in RAISE_KINDS
        and m["season"] not in split
    ]
    if not amounts:
        return 0
    return max(0, max(amounts) - current)


def owed_over(idx, item):
    """(what he is owed across the seasons a change touched, the last of them).

    Every season in the range, not only the ones that moved: "owed $200 million
    through 2030-31" is a figure about a span, and a season the day left alone
    is still money inside it.
    """
    seasons = [m["season"] for m in item.get("members") or [item]]
    if not seasons:
        return 0, ""
    first, last = min(seasons, key=F.season_key), max(seasons, key=F.season_key)
    low, high = F.season_key(first), F.season_key(last)
    total = 0
    for record in idx.by_player.get(idx.canonical(item["player"])) or ():
        if low <= F.season_key(record["season"]) <= high:
            total += record.get("salary") or 0
    return total, last


def attach_nuggets(items, idx, data, factoids, raises, opened, before=None):
    """Give every item the nugget it prints, its raise and its context.

    ``before`` is {"old", "old_idx"} for the build this day is measured against,
    which is what says whether a record the item could print is one the day
    made. That decision is taken once and kept on the item: the nugget order
    reads it, and so does the rule on whether a day of nudges is posted at all.
    """
    for item in items:
        # What he is paid this season is what every movement is measured
        # against: a reader hears a raise against today's money, not against
        # one future figure versus another.
        now = idx.record(item["player"], idx.current_season) or {}
        item["current_salary"] = now.get("salary") or 0
        item.setdefault("peak_salary", item.get("salary") or 0)
        item["raise_amount"] = raise_amount(idx, item)
        item["owed"] = owed_over(idx, item)
    pool = list(raises) + [
        {"player": item["player"], "season": item["season"],
         "amount": item["raise_amount"]}
        for item in items if item["raise_amount"] > 0
    ]
    for item in items:
        record = N.record_nugget(idx, item, factoids)
        item["has_record"] = record is not None
        item["record_news"] = record is not None and record_is_news(
            item["player"], record, before)
        # Every nugget it has, not only the one it prints: which of them wins
        # the slot is a question about wording, and whether the day had a
        # record or a comparison in it is a question about whether the item is
        # worth an entry at all. Reading the second off the first is what put
        # a day with a record on the closing line once the career milestone
        # started outranking it.
        found = N.nuggets_for(
            item, idx, data, factoids, pool, F.fmt_money, opened,
            limit=len(N.NUGGET_ORDER), record_news=item["record_news"])
        item["has_peers"] = any(n["kind"] == "peers" for n in found)
        item["nuggets"] = found[:NUGGETS_PER_CHANGE]
    return pool


def block(item, context):
    """One change: at most two sentences, then the page it is about."""
    names, teams, slugs = context["names"], context["teams"], context["slugs"]
    out = [lead(item, names, teams, slugs)]
    second = nugget_sentence(
        item.get("nuggets") or [], item, names, teams, slugs,
        context_idx=context.get("idx"))
    if second:
        out.append(second)
    return with_urls(" ".join(out), item_urls(item, context))


def collapse_season(item):
    """The season a closing line names for this item: the first it touched."""
    return item.get("first_season") or item["season"]


def collapse_bucket(item, idx):
    """Which closing line a minimum item belongs on, or None for no line.

    Read off the leading change of the day he had: money added for the season
    being played, money added for one after it, money that only changed hands,
    or money that has gone.
    """
    # A changed number is on none of these lines, wherever it sits in his day:
    # it is one short line, and a correction to a minimum salary is as worth
    # reading as any other.
    if any(m["kind"].replace("_run", "") == "salary"
           for m in item.get("members") or [item]):
        return None
    kind = item["kind"].replace("_run", "")
    if kind in ("new", "extension"):
        season = item.get("first_season") or item["season"]
        current = getattr(idx, "current_season", "")
        return "added_now" if season == current else "added_later"
    if kind == "team":
        return "moved"
    if kind == "gone":
        return "removed"
    return None


def collapsed_line(bucket, players, season="", context=None):
    """The one line a day's minimum salaries of one shape and season come to,
    with one URL per player under it, in the order the line names them."""
    who = sorted(players)
    text = "{}: {}.".format(
        COLLAPSE_LABEL[bucket].format(season=season), _join_and(who))
    if context is None:
        return text
    return with_urls(text, players_urls(who, [season], context))


#: How far a number has to move to be worth an item of its own. Under this the
#: sheet has been nudged, not rewritten, and a line saying so is a line nobody
#: would file. A record it sets is the one thing that still makes it news.
TINY_CHANGE = 0.05

#: How far money has to move to be a story by itself: a raise or a cut a reader
#: would repeat. Under it an item needs something else to say.
STORY_MOVE = 0.25

#: Kinds that can go on the "also on the books" line. A season coming off the
#: books is not on them, so "gone" keeps its own entry however quiet it is.
ALSO_KINDS = ("new", "extension", "salary", "team")


def record_is_news(player, record, before):
    """Whether the day made this record, or moved him inside it.

    A record he already held, at the rank he already held it at, is not what
    today did: the number behind it moved a little, and the sentence about it
    would have read the same yesterday. So the fact is looked up in the world
    before the change, and only one that was not there, or was there at another
    rank, is news.
    """
    detail = (record or {}).get("detail") or {}
    key, rank, season = (detail.get("key"), detail.get("rank"),
                         detail.get("season"))
    old = (before or {}).get("old")
    old_idx = (before or {}).get("old_idx")
    if not key or not season or old is None or old_idx is None:
        # Nothing to compare against, so nothing can be shown to be new.
        return False
    try:
        was = F.factoids_for(old, player, season, index=old_idx)
    except (KeyError, TypeError, ValueError, IndexError):
        return False
    for fact in was or ():
        if fact.get("key") == key:
            return fact.get("rank") != rank
    return True


def _steps(item):
    """Every ratio a change moved a number by, one per season it touched."""
    members = item.get("members") or []
    pairs = [(m.get("salary") or 0, m.get("was") or 0) for m in members]
    if not pairs:
        pairs = [(item.get("salary") or 0, item.get("was") or 0)]
    return [now / float(was) for now, was in pairs if was and now]


def tiny_change(item):
    """A day that only nudged his numbers: nothing else, and nothing far.

    Every change he had has to be a changed number, and every one of them has
    to have moved under the floor. A season added or a season moved to another
    team is something else happening, whatever the numbers did.
    """
    members = item.get("members") or [item]
    if any(m["kind"].replace("_run", "") != "salary" for m in members):
        return False
    steps = _steps(item)
    if not steps:
        return False
    return all(abs(step - 1) < TINY_CHANGE for step in steps)


def has_story(item):
    """Whether an item has anything of its own to say.

    A raise or a cut a reader would repeat, a record, or the company he keeps.
    Everything else is a figure on a sheet, which the closing line carries.
    A record counts whether or not it is the nugget that prints.
    """
    if item.get("has_record") or item.get("has_peers"):
        return True
    # Money landing on a second team's books is news by itself, and its
    # summed figure is not a move to be measured.
    if split_seasons(item):
        return True
    for nugget in item.get("nuggets") or []:
        if nugget["kind"] in ("record", "peers"):
            return True
    steps = _steps(item)
    if steps and max(abs(step - 1) for step in steps) >= STORY_MOVE:
        return True
    # New money has no "before" of its own: what it is measured against is the
    # salary he is on now, which is how a reader hears a raise. Money moving to
    # another team's books is not new money and is measured against nothing.
    if item["kind"] not in RAISE_KINDS:
        return False
    now = item.get("current_salary") or 0
    biggest = item.get("peak_salary") or item.get("salary") or 0
    if now and biggest and abs(biggest / float(now) - 1) >= STORY_MOVE:
        return True
    return False


def also_line(items, context):
    """The one line the day's quiet items come to, one URL per man under it.

    One entry a man, however many of his seasons were quiet: his name twice in
    the same line is the thing this line exists to avoid.
    """
    names, teams, slugs = context["names"], context["teams"], context["slugs"]
    by_player = collections.OrderedDict()
    for item in sorted(items, key=lambda i: (-(i.get("salary") or 0), i["player"])):
        by_player.setdefault(item["player"], []).append(item)

    parts = []
    for player, mine in by_player.items():
        mine.sort(key=lambda i: F.season_key(i["season"]))
        spelled = {team_name(i["team"], names) for i in mine if i.get("team")}
        if len(spelled) == 1:
            inside = "{}, {}".format(
                spelled.pop(), _join_and([_also_money(i) for i in mine]))
        elif spelled:
            inside = _join_and([
                "{}, {}".format(team_name(i["team"], names), _also_money(i))
                for i in mine])
        else:
            inside = _join_and([_also_money(i) for i in mine])
        parts.append("{} ({})".format(player, inside))
    seasons = [i["season"] for i in items]
    return with_urls("Also on the books: {}.".format(", ".join(parts)),
                     players_urls(list(by_player), seasons, context))


def _also_money(item):
    """What one quiet item is worth, in the fewest words that are true.

    Money that has left the books is not on them, so a season he lost is not
    counted into the figure this line carries.
    """
    members = [m for m in (item.get("members") or [item])
               if m["kind"].replace("_run", "") != "gone"] \
        or (item.get("members") or [item])
    by_season = _by_season(members)
    seasons = list(by_season)
    if len(seasons) > 1:
        return "{} over {} seasons".format(
            F.fmt_money(sum(by_season.values())), _spell(len(seasons)))
    return "{} in {}".format(F.fmt_money(by_season[seasons[0]]), seasons[0])


def _join_and(parts):
    if len(parts) < 2:
        return "".join(parts)
    return "{} and {}".format(", ".join(parts[:-1]), parts[-1])


def team_lines(context):
    """The committed-money lines a day earned, each with the team's page."""
    out = []
    names, teams = context["names"], context["teams"]
    for entry in context.get("commitments") or ():
        name = team_name(entry["team"], names)
        text = T.line(entry, capitalized(name), F.fmt_money, verb=has_verb(name))
        urls = (first_existing([team_url(teams, entry["team"])], context)
                or fallback_urls([entry.get("season")], context))
        out.append(with_urls(text, urls))
    return out


def render(items, when, scale, tolerance, context):
    """The digest, as the posts it takes."""
    header = "Salary changes, {}".format(ap_date(when))
    commitments = team_lines(context)
    if not items and not commitments:
        return ["{}: none today.".format(header)]

    idx = context.get("idx")
    blocks = []
    #: (bucket, season) -> the players on that line
    collapsed = collections.defaultdict(set)
    quiet = []
    for item in items:
        # A record is what rescues a day from any of these lines: every other
        # nugget has something to say about every deal, so letting any of them
        # rescue one would collapse nothing. A day that only nudged his numbers
        # has to have made that record, or moved him inside it, to be posted at
        # all: holding one he already held is not what today did.
        # A record rescues a deal whether or not it is the nugget that prints:
        # the career milestone can outrank it and the deal is still a record.
        rescued = item.get("has_record") or any(
            n["kind"] == "record" for n in item.get("nuggets") or [])
        if tiny_change(item):
            if not item.get("record_news"):
                continue
        elif not rescued and is_minimum(item, scale, tolerance):
            bucket = collapse_bucket(item, idx)
            if bucket:
                collapsed[(bucket, collapse_season(item))].add(item["player"])
                continue
        if item["kind"].replace("_run", "") in ALSO_KINDS \
                and not has_story(item):
            quiet.append(item)
            continue
        blocks.append(block(item, context))
    for (bucket, season) in sorted(collapsed, key=lambda key: (
            COLLAPSE_ORDER.index(key[0]), F.season_key(key[1]))):
        blocks.append(collapsed_line(
            bucket, collapsed[(bucket, season)], season, context))
    if quiet:
        blocks.append(also_line(quiet, context))
    blocks.extend(commitments)
    if not blocks:
        # Everything the day held was a nudge or a figure already on the books.
        return ["{}: none today.".format(header)]

    # Pack into posts without ever splitting one change across two.
    posts, current = [], []
    length = len(header) + 1
    for text in blocks:
        if current and length + len(text) + 2 > POST_LIMIT:
            posts.append(current)
            current, length = [], len(header) + 1
        current.append(text)
        length += len(text) + 2
    if current:
        posts.append(current)

    out = []
    for i, group in enumerate(posts, 1):
        head = header if len(posts) == 1 else "{} ({} of {})".format(
            header, i, len(posts))
        out.append(head + "\n\n" + "\n\n".join(group))
    return out


# ── posting it ─────────────────────────────────────────────────────────
def payload(text):
    """What goes on the wire, as the dict Slack reads it from.

    The sentences carry no links; each page is a bare URL on its own line,
    which Slack makes clickable by itself. check_post() refuses a post with
    link markup in a sentence or an item with no URL under it.

    Slack unfurls every URL in a post by default, which on a digest of ten
    items is ten preview cards nobody asked for.
    """
    return {
        "text": text, "mrkdwn": True,
        "unfurl_links": False, "unfurl_media": False,
    }


#: What a Markdown link looks like, which is what Slack prints verbatim.
MARKDOWN_LINK = "]("


def no_markdown_links(text):
    """Raise rather than post a line Slack would print the brackets of."""
    if MARKDOWN_LINK in text:
        where = text.index(MARKDOWN_LINK)
        raise ValueError(
            "a Markdown link reached the digest, which Slack prints verbatim: "
            "...{}...".format(text[max(0, where - 60):where + 20]))
    return text


#: Link markup of any kind inside a sentence: Slack's <url|text>, a bare URL
#: or a Markdown link. The URLs belong on their own lines.
SENTENCE_LINK = re.compile(r"<https?:|https?://|\]\(")


def check_post(text):
    """Raise rather than post a digest that breaks the format.

    Every block after the header is one item: its text, then at least one line
    that is nothing but a hoopsmatic.com URL. No sentence carries a link.
    """
    no_markdown_links(text)
    chunks = text.split("\n\n")
    for chunk in chunks[1:]:
        lines = chunk.split("\n")
        urls = [line for line in lines if line.startswith(TOOL_ROOT)]
        sentences = [line for line in lines if not line.startswith(TOOL_ROOT)]
        if not urls:
            raise ValueError("an item has no URL under it: {}".format(chunk[:120]))
        if any(" " in url for url in urls):
            raise ValueError("a URL line carries more than a URL: {}".format(urls))
        for line in sentences:
            if SENTENCE_LINK.search(line):
                raise ValueError("a link inside a sentence: {}".format(line[:120]))
        if lines.index(urls[0]) != len(sentences):
            raise ValueError("text after a URL line: {}".format(chunk[:120]))
    return text


def post(url, text):
    body = json.dumps(payload(check_post(text))).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.status


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="print the digest, post nothing, save nothing")
    parser.add_argument("--since", default="",
                        help="the build to compare against, instead of the state file")
    parser.add_argument("--summary", default="",
                        help="append the digest to this file as well")
    parser.add_argument("--state", default=STATE_PATH,
                        help="where the last build read is recorded")
    parser.add_argument("--webhook-env", default="SLACK_SIGNINGS_WEBHOOK",
                        help="the environment variable holding the Slack webhook")
    args = parser.parse_args(argv)

    head = head_commit()
    with open(os.path.join(REPO, DATA_PATH), "r", encoding="utf-8") as fh:
        new = json.load(fh)

    since = args.since or (load_state(args.state).get("last_commit") or "")
    if not since:
        print("no previous build on record: writing the baseline and saying nothing")
        if not args.dry_run:
            save_state(head, path=args.state)
        return 0

    old = data_at(since)
    if old is None:
        print("cannot read data.json at {}: writing the baseline instead".format(since))
        if not args.dry_run:
            save_state(head, path=args.state)
        return 0

    from_season = F.compute_current_season(new)
    idx = F.build_index(new)
    items = drop_impossible(changes(old, new, from_season), idx)
    print("comparing {}..{} from {} onward: {} change(s)".format(
        since[:8], head[:8] or "working tree", from_season, len(items)))

    today = datetime.date.today()
    opened = N.league_year_start(today)
    state = load_state(args.state)
    raises = [
        entry for entry in (state.get("raises") or [])
        if (entry.get("date") or "") >= opened.isoformat()
    ]
    # The build this day is measured against, which the record test reads. It
    # is built before the nuggets now, because the nugget order depends on it.
    old_idx = F.build_index(old)
    pool = attach_nuggets(items, idx, new, load_factoids(), raises, opened,
                          before={"old": old, "old_idx": old_idx})

    # What the day did to what the teams it touched have committed. The old
    # build is measured on its own index, because a salary the guard flags in
    # one build may not be flagged in the other.
    commitments = T.crossings(
        T.commitments(old, old_idx), T.commitments(new, idx), idx,
        T.touched_teams(items, idx))

    scale, tolerance = load_min_scale()
    context = {
        "names": load_team_names(),
        "teams": load_team_names(codes=True),
        "slugs": load_player_slugs(),
        "idx": idx,
        "old": old,
        "old_idx": old_idx,
        "commitments": commitments,
    }
    context["slugs"] = {"player": context["slugs"]}
    context["slugs"].update(load_cohort_slugs())
    posts = render(items, today, scale, tolerance, context)
    # Checked before a word of it goes out, and in a dry run too, so a link in
    # a sentence or an item without a page is caught where it is cheap rather
    # than in Slack.
    for text in posts:
        check_post(text)

    for text in posts:
        print("\n" + "-" * 60)
        print(text)

    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as fh:
            for text in posts:
                fh.write("```\n" + text + "\n```\n\n")

    webhook = os.environ.get(args.webhook_env or "", "")
    if args.dry_run:
        print("\ndry run: nothing posted, state file untouched")
        return 0
    if not webhook:
        print("\n{} is not set: the digest is in the job summary only".format(
            args.webhook_env))
        save_state(head, path=args.state, posted=len(items), raises=pool,
                   opened=opened)
        return 0

    for text in posts:
        try:
            post(webhook, text)
        except (urllib.error.URLError, OSError) as problem:
            print("could not post to Slack: {}".format(problem))
            return 1
    print("\nposted {} message(s)".format(len(posts)))
    save_state(head, path=args.state, posted=len(items), raises=pool,
               opened=opened)
    return 0


if __name__ == "__main__":
    sys.exit(main())
