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
    "added_now": "minimum salaries added for {season}",
    "added_later": "minimum salaries added for later seasons",
    "moved": "minimum salaries moved to another team",
    "removed": "minimum salaries removed",
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
    """Team code -> the city a sentence names, from data/franchises.json.

    With codes, the map the link pass wants instead: code -> page slug.
    """
    if not os.path.exists(path):
        return {}
    if codes:
        return (load_cohort_slugs(SLUGS_PATH).get("team") or {})
    with open(path, "r", encoding="utf-8") as fh:
        payload = (json.load(fh) or {}).get("franchises") or {}
    out = {}
    for code, entry in payload.items():
        full = (entry.get("full_name") or "").strip()
        nickname = (entry.get("name") or "").strip()
        if full and nickname and full.endswith(nickname):
            out[code] = full[: -len(nickname)].strip() or full
        elif full:
            out[code] = full
    return out


def load_player_slugs(path=SLUGS_PATH):
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        return ((json.load(fh) or {}).get("slugs") or {}).get("player") or {}


def link(text, url):
    """Slack mrkdwn. A link is the only place the digest spends a URL."""
    if not url:
        return text
    return "<{}|{}>".format(url, text)


def apply_links(text, links):
    """Turn each named entity in a sentence into a link, once.

    Longest surface first, so linking a player never cuts a team name in half,
    and on a word boundary, so "Arizona St" cannot swallow the first half of
    "Arizona State". A surface already inside a link is left alone.
    """
    done = []
    for surface, url in sorted(links, key=lambda pair: -len(pair[0])):
        if not surface or not url:
            continue
        if any(surface in seen for seen in done):
            continue
        pattern = r"(?<![\w<|/-]){}(?![\w-])".format(re.escape(surface))
        found = re.search(pattern, text)
        if not found:
            continue
        text = text[:found.start()] + link(surface, url) + text[found.end():]
        done.append(surface)
    return text


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
    """What a sentence calls a team, including a two-team season."""
    names = names or {}
    parts = [p.strip() for p in (code or "").split(",") if p.strip()]
    if not parts:
        return "nobody"
    spelled = [names.get(p, p) for p in parts]
    if len(spelled) == 1:
        return spelled[0]
    return "{} and {}".format(", ".join(spelled[:-1]), spelled[-1])


def possessive(name):
    """AP style: Dallas' books, Memphis' books, Atlanta's books."""
    return name + ("'" if name.endswith("s") else "'s")


def lead(item, names=None, teams=None, slugs=None):
    """The first sentence, with every entity it names linked.

    A day with several clauses can name several teams, so every team any of
    them puts him on is linked, not only the one the item leads with.
    """
    text = sentence(item, names)
    links = [(item["player"], player_url(slugs or {}, item["player"]))]
    sources = [item] + list(item.get("members") or [])
    seen = set()
    for source in sources:
        for code in (source.get("team") or "").split(","):
            code = code.strip()
            if code and code not in seen:
                seen.add(code)
                spelled = (names or {}).get(code, code)
                links.append((spelled, team_url(teams, code)))
    # was_team is deliberately absent from every sentence, so it gets no link.
    return apply_links(text, links)


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
    for place, (kind, members) in enumerate(groups):
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
    # A clause that could not say it all leaves the rest as its own sentence.
    after = " ".join(said.get("after") or ())
    after = " " + after if after else ""
    if len(clauses) == 1:
        return clauses[0] + "." + after
    # A clause carrying an "and" of its own, or two clauses of its own, cannot
    # take another: the semicolon keeps the day to one sentence without three
    # ands inside it, and without an "and" a reader has to re-read.
    if any(_crowded(text) for text in clauses):
        return "; ".join(clauses) + "." + after
    return "{}, and {}.".format(", ".join(clauses[:-1]), clauses[-1]) + after


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
            where = "{} has him at".format(team) if team else "he is due"
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
        return changed_salary(item, whose, said)

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
    """A number that moved: the first season it moved in, old to new.

    One number a reader can hold, in the season nearest to now, and then what
    the whole move is worth: the money added to or taken off what he is owed,
    and the season it runs to. "His next four seasons move with it" said that
    they moved and never said by how much, which is the only part worth a line.

    Where the money only moved about inside the deal, the shape is the story
    and the clause says so instead.
    """
    if whose is None:
        whose = possessive(item["player"])
    first = item.get("first_season") or item["season"]
    was = item.get("first_was")
    if was is None:
        was = item.get("was")
    now = item.get("first_salary")
    if now is None:
        now = item.get("salary")

    reshaped = _reshaped(item)
    if reshaped:
        if said is not None:
            owed = _owed_sentence(item)
            if owed:
                said.setdefault("after", []).append(owed)
        return "{} salary is {}: {} in {}, {} from {}, with his later seasons " \
            "{} by {}".format(
                whose, reshaped["shape"], F.fmt_money(now), first,
                "up" if (now or 0) > (was or 0) else "down", F.fmt_money(was),
                reshaped["way"], reshaped["much"])

    verb = step_verb(now or 0, was or 0)
    tail = _rest_of_run(item)
    if not verb:
        return "{} {} salary is {}{}".format(
            whose, first, F.fmt_money(now), tail)
    return "{} {} salary {} to {} from {}{}".format(
        whose, first, verb, F.fmt_money(now), F.fmt_money(was), tail)


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


def _owed_sentence(item):
    """"He's still owed $200 million through 2030-31." """
    total, through = item.get("owed") or (0, "")
    if not total or not through:
        return ""
    return "He's still owed {} through {}.".format(F.fmt_money(total), through)


#: A net move smaller than this is not worth a figure of its own: the seasons
#: behind the first one changed, and saying so is all the line can carry.
NET_FLOOR = 0.05


def _rest_of_run(item):
    """What the seasons behind the first one are worth, as a clause.

    The money the day put on or took off what he is owed, and the season it
    runs to, whichever way the seasons inside it went: what he is owed through
    the last of them went up or down by this much. Where the net is too small
    for a figure the clause says only that the later seasons moved.
    """
    members = item.get("members") or []
    rest = members[1:]
    if not rest:
        return ""
    now = sum((m.get("salary") or 0) for m in members)
    was = sum((m.get("was") or 0) for m in members)
    net = now - was
    if not net or not was or abs(net) < was * NET_FLOOR:
        # Nothing was added or taken off worth a figure, and the shape is not
        # one _reshaped would claim either: all a line can say is that the
        # seasons behind the first one moved.
        return ", and his later seasons change too"
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
    """The second sentence: the strongest thing the money means, linked.

    One nugget, never two. An item is the lead and this, and a reader carries
    away one fact about a man and not a stat list.
    """
    if not nuggets:
        return ""
    used = nuggets[:1]
    text = used[0]["opener"].rstrip(".") + "."

    links = []
    for nugget in used:
        for kind, key in nugget.get("entities") or []:
            if kind == "player":
                links.append((key, player_url(slugs or {}, key)))
            else:
                links.append((cohort_label(context_idx, kind, key),
                              cohort_url(slugs or {}, kind, key)))
        spec = nugget.get("peer_link")
        if spec and nugget.get("link_phrase"):
            links.append((nugget["link_phrase"], peer_url(spec)))
    return apply_links(text, links)


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
    amounts = [
        (m.get("salary") or 0) for m in (item.get("members") or [item])
        if m["kind"].replace("_run", "") in RAISE_KINDS
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


def attach_nuggets(items, idx, data, factoids, raises, opened):
    """Give every item the two nuggets it prints, its raise and its context."""
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
        item["nuggets"] = N.nuggets_for(
            item, idx, data, factoids, pool, F.fmt_money, opened,
            limit=NUGGETS_PER_CHANGE)
    return pool


def block(item, context):
    """One change, as at most two sentences with their links inside them."""
    names, teams, slugs = context["names"], context["teams"], context["slugs"]
    out = [lead(item, names, teams, slugs)]
    second = nugget_sentence(
        item.get("nuggets") or [], item, names, teams, slugs,
        context_idx=context.get("idx"))
    if second:
        out.append(second)
    return " ".join(out)


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


def collapsed_line(bucket, players, season=""):
    """The one line a day's minimum salaries of one shape come to."""
    who = sorted(players)
    return "Also: {}: {}.".format(
        COLLAPSE_LABEL[bucket].format(season=season), ", ".join(who))


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


def record_is_news(item, context):
    """Whether the day made the record the item would print, or moved him in it.

    A record he already held, at the rank he already held it at, is not what
    today did: the number behind it moved a little, and the sentence about it
    would have read the same yesterday. So the fact the item would print is
    looked up in the world before the change, and only one that was not there,
    or was there at another rank, is news.
    """
    record = next((n for n in item.get("nuggets") or []
                   if n["kind"] == "record"), None)
    if record is None:
        return False
    detail = record.get("detail") or {}
    key, rank, season = (detail.get("key"), detail.get("rank"),
                         detail.get("season"))
    old, old_idx = context.get("old"), context.get("old_idx")
    if not key or not season or old is None or old_idx is None:
        # Nothing to compare against, so nothing can be shown to be new.
        return False
    try:
        before = F.factoids_for(old, item["player"], season, index=old_idx)
    except (KeyError, TypeError, ValueError, IndexError):
        return False
    for fact in before or ():
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
    """
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
    """The one line the day's quiet items come to, with every name linked.

    One entry a man, however many of his seasons were quiet: his name twice in
    the same line is the thing this line exists to avoid.
    """
    names, teams, slugs = context["names"], context["teams"], context["slugs"]
    by_player = collections.OrderedDict()
    for item in sorted(items, key=lambda i: (-(i.get("salary") or 0), i["player"])):
        by_player.setdefault(item["player"], []).append(item)

    parts, links = [], []
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
        links.append((player, player_url(slugs or {}, player)))
        for item in mine:
            for code in (item.get("team") or "").split(","):
                code = code.strip()
                if code:
                    links.append(((names or {}).get(code, code),
                                  team_url(teams, code)))
    return apply_links("Also on the books: {}.".format(", ".join(parts)), links)


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
    """The committed-money lines a day earned, each with its names linked."""
    out = []
    names, teams, slugs = context["names"], context["teams"], context["slugs"]
    for entry in context.get("commitments") or ():
        name = team_name(entry["team"], names)
        text = T.line(entry, name, F.fmt_money)
        links = [(name, team_url(teams, entry["team"]))]
        links += [(player, player_url(slugs, player))
                  for player, _amount in entry["top"]]
        out.append(apply_links(text, links))
    return out


def render(items, when, scale, tolerance, context):
    """The digest, as the posts it takes."""
    header = "Salary data changes, {}".format(ap_date(when))
    commitments = team_lines(context)
    if not items and not commitments:
        return ["{}: none today.".format(header)]

    idx = context.get("idx")
    blocks = []
    collapsed = collections.defaultdict(set)
    quiet = []
    for item in items:
        # A record is what rescues a day from any of these lines: every other
        # nugget has something to say about every deal, so letting any of them
        # rescue one would collapse nothing. A day that only nudged his numbers
        # has to have made that record, or moved him inside it, to be posted at
        # all: holding one he already held is not what today did.
        rescued = any(n["kind"] == "record" for n in item.get("nuggets") or [])
        if tiny_change(item):
            if not record_is_news(item, context):
                continue
        elif not rescued and is_minimum(item, scale, tolerance):
            bucket = collapse_bucket(item, idx)
            if bucket:
                collapsed[bucket].add(item["player"])
                continue
        if item["kind"].replace("_run", "") in ALSO_KINDS \
                and not has_story(item):
            quiet.append(item)
            continue
        blocks.append(block(item, context))
    for bucket in COLLAPSE_ORDER:
        if collapsed.get(bucket):
            blocks.append(collapsed_line(
                bucket, collapsed[bucket],
                getattr(idx, "current_season", "")))
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

    ``mrkdwn`` is what makes Slack parse <url|text>; without it the post
    arrives with the angle brackets in it. Markdown is not an option on either
    setting, which is why every link in this file is built by link() and why
    no_markdown_links() refuses a post that carries one.

    Slack unfurls every link in a post by default, which on a digest of ten
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


def post(url, text):
    body = json.dumps(payload(no_markdown_links(text))).encode("utf-8")
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
    pool = attach_nuggets(items, idx, new, load_factoids(), raises, opened)

    # What the day did to what the teams it touched have committed. The old
    # build is measured on its own index, because a salary the guard flags in
    # one build may not be flagged in the other. The same index answers whether
    # a record a nudge would print was already his yesterday.
    old_idx = F.build_index(old)
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
    # Checked before a word of it goes out, and in a dry run too, so a
    # Markdown link is caught where it is cheap rather than in Slack.
    for text in posts:
        no_markdown_links(text)

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
