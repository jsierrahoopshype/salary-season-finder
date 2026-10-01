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

#: At most this many nuggets ride along with one change.
NUGGETS_PER_CHANGE = 2

#: Cohort kind -> the directory its page lives in. Slugs for the first three
#: are derived rather than stored, the same way the prerender derives them.
COHORT_DIRS = {
    "draft_class": "draft", "draft_slot": "pick", "position": "position",
    "region": "region", "pick_range": "pick-range",
    "college": "college", "nationality": "country",
    "college_position": "college-position",
}

POSITION_SLUGS = {"G": "guard", "F": "forward", "C": "center"}

#: The second sentence runs to this at most; past it the second nugget goes.
SENTENCE_LIMIT = 260

#: How near a scale amount a salary has to be to be read as that amount. The
#: rungs of a season's scale are 4% apart at the narrowest, so this cannot reach
#: from one to the next; it is wide enough for the drift between a projected cap
#: and the one the sheets were built against.
SCALE_MATCH = 0.015

#: The order changes are read in: the ones a reader wants first, first.
KIND_ORDER = ("new", "extension", "salary", "team", "gone")

#: Kinds where several seasons of one player are one piece of news. Four rows
#: appearing at once is one contract, not four signings, and four numbers
#: redrawn at once is one deal rewritten. A team change is not in here: two
#: seasons moving to different teams are two different things to say.
RUN_KINDS = ("new", "extension", "salary")

#: Which kinds collapse when the money is the minimum. A changed number never
#: does: it is one short line, and a correction to a minimum salary is as worth
#: reading as any other.
COLLAPSE_LABEL = {
    "new": "on minimum deals new on the books",
    "extension": "on minimum salaries added for a later season",
    "team": "on minimum deals now on another team's books",
    "gone": "on minimum deals off the books",
}

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

    items.sort(key=lambda i: (
        KIND_ORDER.index(i["kind"]), -(i.get("salary") or 0), i["player"]))
    return group_runs(items)


def group_runs(items):
    """Several seasons of one player, in one kind, as one piece of news.

    A contract that adds 2027-28, 2028-29 and 2029-30 at once is one deal, and
    a digest that says so three times buries the rest of the day. The members
    are kept, so the money, the factoids and the minimum test all still read
    the seasons themselves.
    """
    runs = collections.OrderedDict()
    out = []
    for item in items:
        if item["kind"] not in RUN_KINDS:
            out.append(item)
            continue
        runs.setdefault((item["kind"], item["player"]), []).append(item)

    for (kind, player), members in runs.items():
        if len(members) == 1:
            out.append(members[0])
            continue
        members.sort(key=lambda i: F.season_key(i["season"]))
        teams = {m["team"] for m in members if m["team"]}
        out.append({
            "kind": kind + "_run",
            "player": player,
            "season": members[-1]["season"],
            "first_season": members[0]["season"],
            "record": members[-1]["record"],
            "salary": max((m.get("salary") or 0) for m in members),
            "first_salary": members[0].get("salary"),
            "last_salary": members[-1].get("salary"),
            "team": list(teams)[0] if len(teams) == 1 else "",
            "members": members,
        })

    out.sort(key=lambda i: (
        KIND_ORDER.index(i["kind"].replace("_run", "")),
        -(i.get("salary") or 0), i["player"]))
    return out


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
    """The first sentence, with every entity it names linked."""
    text = sentence(item, names)
    links = [(item["player"], player_url(slugs or {}, item["player"]))]
    for code in (item.get("team") or "").split(","):
        code = code.strip()
        if code:
            spelled = (names or {}).get(code, code)
            links.append((spelled, team_url(teams, code)))
    for code in (item.get("was_team") or "").split(","):
        code = code.strip()
        if code:
            spelled = (names or {}).get(code, code)
            links.append((spelled, team_url(teams, code)))
    return apply_links(text, links)


def sentence(item, names=None):
    """One publishable sentence about what the data did."""
    player, season = item["player"], item["season"]
    money = F.fmt_money(item.get("salary"))
    kind = item["kind"]
    if kind == "new":
        if item["team"]:
            return "{} is on {} books for {} in {}.".format(
                player, possessive(team_name(item["team"], names)), money, season)
        return "{} is on the books for {} in {}.".format(player, money, season)
    if kind == "extension":
        tail = " with {}".format(team_name(item["team"], names)) if item["team"] else ""
        return "{} now has {} on his deal, {}{}.".format(
            player, season, money, tail)
    if kind == "new_run":
        if item["team"]:
            return "{} is on {} books through {}, {}.".format(
                player, possessive(team_name(item["team"], names)), season,
                _money_shape(item))
        return "{} is on the books through {}, {}.".format(
            player, season, _money_shape(item))
    if kind == "extension_run":
        tail = " with {}".format(team_name(item["team"], names)) if item["team"] else ""
        return "{}'s deal now runs through {}{}, {}.".format(
            player, season, tail, _money_shape(item))
    if kind == "salary_run":
        return "{}'s salary moved in {} seasons, {} through {}, now {}.".format(
            player, len(item["members"]), item["first_season"], season,
            _money_shape(item))
    if kind == "salary":
        return "{}'s {} salary moved from {} to {}.".format(
            player, season, F.fmt_money(item.get("was")), money)
    if kind == "team":
        return "{}'s {} salary of {} is now on {} books, not {}.".format(
            player, season, money,
            possessive(team_name(item["team"], names)),
            possessive(team_name(item.get("was_team"), names)))
    return "{}'s {} salary of {} is off the books.".format(player, season, money)


def nugget_sentence(nuggets, item, names=None, teams=None, slugs=None,
                    context_idx=None):
    """The second sentence: at most two nuggets, linked, inside the limit."""
    if not nuggets:
        return ""
    text = nuggets[0]["opener"]
    used = nuggets[:1]
    # A nugget that already carries an "and" of its own takes no second one:
    # three clauses joined by two ands is not a sentence anybody reads.
    follower = nuggets[1].get("tail") if len(nuggets) > 1 else ""
    compound = ", and " in text or ", and " in (follower or "")
    if follower and not compound \
            and len(text) + len(follower) + 7 <= SENTENCE_LIMIT:
        text = "{}, and {}".format(text, follower)
        used = nuggets[:2]
    text = text.rstrip(".") + "."

    links = []
    for nugget in used:
        for kind, key in nugget.get("entities") or []:
            if kind == "player":
                links.append((key, player_url(slugs or {}, key)))
            else:
                links.append((cohort_label(context_idx, kind, key),
                              cohort_url(slugs or {}, kind, key)))
        spec = nugget.get("peer_link")
        if spec:
            phrase = nugget["opener"].split("among the ", 1)
            if len(phrase) == 2:
                links.append((phrase[1], peer_url(spec)))
    return apply_links(text, links)


def _money_shape(item):
    """What a run of seasons is worth: one figure, or the two ends of a climb."""
    first, last = item.get("first_salary"), item.get("last_salary")
    if first == last:
        return "{} a year".format(F.fmt_money(first))
    return "rising from {} to {}".format(F.fmt_money(first), F.fmt_money(last))


#: Kinds where new money arrives, which is the only place a raise can be.
RAISE_KINDS = ("new", "new_run", "extension", "extension_run", "salary",
               "salary_run")


def raise_amount(idx, item):
    """New money against what he is paid now, which is what a raise is here.

    Not the difference between two future figures: a reader measures a raise
    against the salary on this season's books. A salary coming off the books is
    not a raise, and nor is the same money moving to another team.
    """
    if item["kind"] not in RAISE_KINDS:
        return 0
    now = idx.record(item["player"], idx.current_season) or {}
    current = now.get("salary") or 0
    biggest = max(
        [(m.get("salary") or 0) for m in item.get("members") or []]
        or [item.get("salary") or 0])
    return max(0, biggest - current)


def attach_nuggets(items, idx, data, factoids, raises, opened):
    """Give every item the two nuggets it prints, and its raise."""
    for item in items:
        item["raise_amount"] = raise_amount(idx, item)
    pool = list(raises) + [
        {"player": item["player"], "season": item["season"],
         "amount": item["raise_amount"]}
        for item in items if item["raise_amount"] > 0
    ]
    for item in items:
        item["nuggets"] = N.nuggets_for(
            item, idx, data, factoids, pool, F.fmt_money, fmt_pct, opened,
            limit=NUGGETS_PER_CHANGE)
    return pool


def fmt_pct(value):
    text = "{:.1f}".format(value or 0.0)
    if text.endswith(".0"):
        text = text[:-2]
    return text + "%"


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


def collapsed_line(kind, players):
    """The one line a day's minimum deals of one kind come to."""
    who = sorted(players)
    return "Also: {} player{} {}: {}.".format(
        len(who), "" if len(who) == 1 else "s",
        COLLAPSE_LABEL[kind], ", ".join(who))


def render(items, when, scale, tolerance, context):
    """The digest, as the posts it takes."""
    header = "Salary data changes, {}".format(ap_date(when))
    if not items:
        return ["{}: none today.".format(header)]

    blocks = []
    collapsed = collections.defaultdict(set)
    for item in items:
        # A run of seasons collapses on the same line as a single one of its
        # kind. A record is what rescues a minimum deal from that line: every
        # other nugget has something to say about every deal, so letting any of
        # them rescue one would collapse nothing.
        base = item["kind"].replace("_run", "")
        rescued = any(n["kind"] == "record" for n in item.get("nuggets") or [])
        if rescued or base not in COLLAPSE_LABEL \
                or not is_minimum(item, scale, tolerance):
            blocks.append(block(item, context))
        else:
            collapsed[base].add(item["player"])
    for kind in KIND_ORDER:
        if collapsed.get(kind):
            blocks.append(collapsed_line(kind, collapsed[kind]))

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
def post(url, text):
    # Slack unfurls every link in a post by default, which on a digest of ten
    # items is ten preview cards nobody asked for.
    body = json.dumps({
        "text": text, "mrkdwn": True,
        "unfurl_links": False, "unfurl_media": False,
    }).encode("utf-8")
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
    items = changes(old, new, from_season)
    print("comparing {}..{} from {} onward: {} change(s)".format(
        since[:8], head[:8] or "working tree", from_season, len(items)))

    today = datetime.date.today()
    opened = N.league_year_start(today)
    state = load_state(args.state)
    raises = [
        entry for entry in (state.get("raises") or [])
        if (entry.get("date") or "") >= opened.isoformat()
    ]
    idx = F.build_index(new)
    pool = attach_nuggets(items, idx, new, load_factoids(), raises, opened)

    scale, tolerance = load_min_scale()
    context = {
        "names": load_team_names(),
        "teams": load_team_names(codes=True),
        "slugs": load_player_slugs(),
        "idx": idx,
    }
    context["slugs"] = {"player": context["slugs"]}
    context["slugs"].update(load_cohort_slugs())
    posts = render(items, today, scale, tolerance, context)

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
