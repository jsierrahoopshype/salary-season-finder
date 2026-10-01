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
import subprocess
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import factoids as F  # noqa: E402

DATA_PATH = os.path.join("data", "data.json")
STATE_PATH = os.path.join(REPO, "data", "digest_state.json")
MIN_SCALE_PATH = os.path.join(REPO, "data", "min_scale.json")
FACTOIDS_PATH = os.path.join(REPO, "data", "factoids.json")
SLUGS_PATH = os.path.join(REPO, "data", "slugs.json")
FRANCHISES_PATH = os.path.join(REPO, "data", "franchises.json")

TOOL_ROOT = "https://hoopsmatic.com/salary-season-finder"

#: Slack renders a post this long comfortably; past it the thread is better.
POST_LIMIT = 3500

#: At most this many engine factoids ride along with one change.
FACTOIDS_PER_CHANGE = 2

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


def save_state(commit, path=STATE_PATH, posted=0):
    payload = {
        "readme": (
            "The build scripts/salary_digest.py last read. Committed so that a "
            "rerun of the workflow compares against the same place and never "
            "posts a day's changes twice."
        ),
        "last_commit": commit,
        "last_run": datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
        "changes_posted": posted,
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


def load_team_names(path=FRANCHISES_PATH):
    """Team code -> the city a sentence names, from data/franchises.json."""
    if not os.path.exists(path):
        return {}
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


def player_url(slugs, name):
    """The page for this man, or nothing if he has none yet.

    A split key files its segments as "Name#0" and "Name#1"; the later segment
    is the man playing now, which is the one a digest is ever about.
    """
    slug = slugs.get(name)
    if not slug:
        parts = sorted(k for k in slugs if k.rsplit("#", 1)[0] == name)
        if not parts:
            return ""
        slug = slugs[parts[-1]]
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


def _money_shape(item):
    """What a run of seasons is worth: one figure, or the two ends of a climb."""
    first, last = item.get("first_salary"), item.get("last_salary")
    if first == last:
        return "{} a year".format(F.fmt_money(first))
    return "rising from {} to {}".format(F.fmt_money(first), F.fmt_money(last))


def facts_for(item, factoids):
    """Up to two factoids, from the seasons this change is about."""
    seasons = [m["season"] for m in item.get("members") or []] or [item["season"]]
    out = []
    for season in seasons:
        for fact in factoids.get("{}|{}".format(item["player"], season)) or []:
            text = fact.get("text")
            if text and text not in out:
                out.append(text)
                if len(out) >= FACTOIDS_PER_CHANGE:
                    return out
    return out


def block(item, factoids, slugs, names=None):
    """One change, as the lines it takes up."""
    lines = [sentence(item, names)]
    lines.extend(facts_for(item, factoids))
    url = player_url(slugs, item["player"])
    if url:
        lines.append(url)
    return "\n".join(lines)


def collapsed_line(kind, players):
    """The one line a day's minimum deals of one kind come to."""
    who = sorted(players)
    return "Also: {} player{} {}: {}.".format(
        len(who), "" if len(who) == 1 else "s",
        COLLAPSE_LABEL[kind], ", ".join(who))


def render(items, when, scale, tolerance, factoids, slugs, names=None):
    """The digest, as the posts it takes."""
    header = "Salary data changes, {}".format(ap_date(when))
    if not items:
        return ["{}: none today.".format(header)]

    blocks = []
    collapsed = collections.defaultdict(set)
    for item in items:
        facts = facts_for(item, factoids)
        # a run of seasons collapses on the same line as a single one of its kind
        base = item["kind"].replace("_run", "")
        if facts or base not in COLLAPSE_LABEL \
                or not is_minimum(item, scale, tolerance):
            blocks.append(block(item, factoids, slugs, names))
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
    body = json.dumps({"text": text}).encode("utf-8")
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

    scale, tolerance = load_min_scale()
    posts = render(items, datetime.date.today(), scale, tolerance,
                   load_factoids(), load_player_slugs(), load_team_names())

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
        save_state(head, path=args.state, posted=len(items))
        return 0

    for text in posts:
        try:
            post(webhook, text)
        except (urllib.error.URLError, OSError) as problem:
            print("could not post to Slack: {}".format(problem))
            return 1
    print("\nposted {} message(s)".format(len(posts)))
    save_state(head, path=args.state, posted=len(items))
    return 0


if __name__ == "__main__":
    sys.exit(main())
