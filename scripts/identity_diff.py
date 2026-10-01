#!/usr/bin/env python3
"""Compare two data.json builds record by record.

Written for the pull request that fixed the player identity join: it reads the
file the old join produced and the file the new one produced and prints every
difference, grouped by the person it belongs to, so a reviewer can check that
only the names the change is about moved.

    python scripts/identity_diff.py old.json new.json [--max-people N]

Exits 0 whatever it finds. Reading the report is the point.
"""

from __future__ import annotations

import argparse
import collections
import json
import sys

# Fields that say who a person is, as opposed to what a season was worth.
IDENTITY_FIELDS = (
    "pos", "nationality", "college", "draft_year", "draft_pick",
    "height", "weight", "age", "agent", "career_earnings", "years_exp",
)


def load(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def by_key(payload):
    out = {}
    for record in payload["seasons"]:
        out[(record["player"], record["season"])] = record
    return out


def money(value):
    if value is None:
        return "none"
    return "${:,}".format(value)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("old")
    parser.add_argument("new")
    parser.add_argument("--max-people", type=int, default=0,
                        help="stop listing after this many people (0 = all)")
    args = parser.parse_args(argv)

    old, new = load(args.old), load(args.new)
    old_recs, new_recs = by_key(old), by_key(new)

    print("## Record counts")
    print("")
    print("| | old | new |")
    print("|---|---|---|")
    print("| records | {} | {} |".format(
        len(old["seasons"]), len(new["seasons"])))
    print("| players | {} | {} |".format(
        len(old["players"]), len(new["players"])))
    print("| agents | {} | {} |".format(len(old["agents"]), len(new["agents"])))
    print("| teams | {} | {} |".format(len(old["teams"]), len(new["teams"])))
    print("| seasons | {} | {} |".format(
        len(old["seasons_list"]), len(new["seasons_list"])))
    print("")

    for name, payload in (("seasons_list", "seasons_list"), ("teams", "teams"),
                          ("awards_list", "awards_list")):
        gone = sorted(set(old[payload]) - set(new[payload]))
        came = sorted(set(new[payload]) - set(old[payload]))
        if gone or came:
            print("`{}` changed: -{} +{}".format(name, gone, came))
            print("")

    gone_names = sorted(set(old["players"]) - set(new["players"]))
    new_names = sorted(set(new["players"]) - set(old["players"]))
    print("## Names")
    print("")
    print("{} display names gone, {} new.".format(
        len(gone_names), len(new_names)))
    print("")
    if gone_names:
        print("Gone: " + ", ".join(gone_names))
        print("")
    if new_names:
        print("New: " + ", ".join(new_names))
        print("")

    # Group every difference under the person it is about. A record that moved
    # from one display name to another is reported under both.
    people = collections.defaultdict(lambda: {
        "dropped": [], "added": [], "changed": collections.defaultdict(list),
    })
    for key in sorted(set(old_recs) - set(new_recs)):
        people[key[0]]["dropped"].append(key[1])
    for key in sorted(set(new_recs) - set(old_recs)):
        people[key[0]]["added"].append(key[1])
    for key in sorted(set(old_recs) & set(new_recs)):
        was, now = old_recs[key], new_recs[key]
        for field in sorted(set(was) | set(now)):
            if was.get(field) != now.get(field):
                people[key[0]]["changed"][field].append(
                    (key[1], was.get(field), now.get(field)))

    print("## {} people touched".format(len(people)))
    print("")
    shown = 0
    for person in sorted(people):
        entry = people[person]
        shown += 1
        if args.max_people and shown > args.max_people:
            print("...and {} more.".format(len(people) - args.max_people))
            break
        print("### {}".format(person))
        print("")
        if entry["dropped"]:
            print("- seasons no longer under this name: {}".format(
                ", ".join(entry["dropped"])))
        if entry["added"]:
            print("- seasons now under this name: {}".format(
                ", ".join(entry["added"])))
        for field in sorted(entry["changed"]):
            moves = entry["changed"][field]
            if field in ("career_earnings",):
                sample = "; ".join(
                    "{} {} -> {}".format(s, money(a), money(b))
                    for s, a, b in moves[:4])
            else:
                sample = "; ".join(
                    "{} {!r} -> {!r}".format(s, a, b) for s, a, b in moves[:4])
            more = "" if len(moves) <= 4 else " (+{} more seasons)".format(
                len(moves) - 4)
            print("- {}: {}{}".format(field, sample, more))
        print("")

    identity_only = True
    for entry in people.values():
        for field in entry["changed"]:
            if field not in IDENTITY_FIELDS:
                identity_only = False
    print("Every changed field is an identity field: {}".format(identity_only))
    return 0


if __name__ == "__main__":
    sys.exit(main())
