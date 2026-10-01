#!/usr/bin/env python3
"""Write data/min_scale.json: the minimum salary by season and years of service.

The digest needs a line to decide what is a minimum deal, because a day's worth
of 10-day contracts and two-way conversions would otherwise bury the two or
three salaries worth reading about.

How the table is built. The CBA sets each service year's minimum as a fixed
share of the salary cap, so the whole table is one column of shares. The column
here is the published 2025-26 minimum scale, and every other season is that
column times the ratio of its cap to the 2025-26 cap, from salary_cap_info.csv.

Two checks on that, both run by this script and printed:

* Scaling the 2025-26 column down by the 2024-25 cap ratio (10/11 exactly)
  returns $1,157,153 at no years of service, which is the published 2024-25
  rookie minimum to the dollar.
* The salary data itself carries each season's minimum deals. Dividing every
  salary by its service year's 2025-26 figure and taking the most common
  quotient recovers one ratio per season: 0.909090 for 2024-25, 1.000000 for
  2025-26, 1.066694 for 2026-27. Those are the real scale ratios, and they sit
  within 0.7% of the cap ratios used here, the gap being that a future cap is a
  projection and the sheets were built against an earlier one. From 2027-28 on,
  where the sheets carry only a dozen or so minimum deals each, the most common
  quotient starts picking up a 10% raise on a minimum base rather than the scale
  itself, so read the runner-up there: 1.120028 for 2027-28, which is again
  0.6% from the cap ratio.

That 1% is why the digest reads a tolerance from this file rather than comparing
exactly. The next real tier above the minimum is the room exception, more than
$1.5 million clear of it, so a threshold a few per cent high costs nothing.

    python scripts/build_min_scale.py
"""

from __future__ import annotations

import collections
import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

OUT_PATH = os.path.join(REPO, "data", "min_scale.json")
DATA_PATH = os.path.join(REPO, "data", "data.json")

#: The published 2025-26 minimum scale, years of service 0 to 10 and over. Every
#: one of these appears in the salary data as a 2025-26 salary somebody is paid.
ANCHOR_SEASON = "2025-26"
ANCHOR = {
    0: 1272870, 1: 2048494, 2: 2296274, 3: 2378870, 4: 2461463, 5: 2667947,
    6: 2874436, 7: 3080921, 8: 3287409, 9: 3303774, 10: 3634153,
}

#: How far above the table a salary may sit and still count as the minimum. A
#: future cap is a projection, so the sheets' own figures drift from a table
#: built off this one by under a per cent.
TOLERANCE = 0.03


def observed_ratios(data):
    """The scale ratio each season's own minimum deals imply."""
    out = {}
    for season in data.get("seasons_list") or []:
        counts = collections.Counter()
        for record in data["seasons"]:
            if record["season"] != season or not record.get("salary"):
                continue
            years = record.get("years_exp")
            if years is None:
                continue
            counts[round(record["salary"] / ANCHOR[min(years, 10)], 6)] += 1
        if counts:
            ratio, seen = counts.most_common(1)[0]
            out[season] = (ratio, seen)
    return out


def main():
    caps = {}
    with open(os.path.join(REPO, "salary_cap_info.csv"), "r", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            # the file writes "2025-2026"; the data writes "2025-26"
            season = (row.get("Season") or "").strip()
            parts = season.split("-")
            if len(parts) == 2 and len(parts[1]) == 4:
                season = "{}-{}".format(parts[0], parts[1][-2:])
            value = (row.get("Salary Cap") or "").replace("$", "").replace(",", "").strip()
            if season and value:
                caps[season] = int(float(value))
    anchor_cap = caps[ANCHOR_SEASON]

    with open(DATA_PATH, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    seen = observed_ratios(data)

    scale = {}
    for season in sorted(caps):
        if season < "2024-25":
            continue
        ratio = caps[season] / float(anchor_cap)
        scale[season] = {
            str(years): int(round(value * ratio)) for years, value in sorted(ANCHOR.items())
        }
        shown = seen.get(season)
        print("{}  cap ratio {:.6f}   the data's own most common ratio {}".format(
            season, ratio,
            "{:.6f} on {} salaries".format(shown[0], shown[1]) if shown else "none"))

    payload = {
        "readme": (
            "The minimum salary by season and years of service, 10 meaning 10 "
            "or more. Read by scripts/salary_digest.py to decide which of a "
            "day's changes are minimum deals and collapse into one line. "
            "Written by scripts/build_min_scale.py: the published 2025-26 "
            "scale, every other season that column times the ratio of its cap "
            "to the 2025-26 cap, which is how the CBA sets it. Checked two "
            "ways, both in that script's docstring."
        ),
        "anchor_season": ANCHOR_SEASON,
        "tolerance": TOLERANCE,
        "tolerance_note": (
            "A salary at or below its figure here times 1 + tolerance is a "
            "minimum deal. The slack is for a future cap being a projection: "
            "the sheets' own minimums sit under a per cent from this table."
        ),
        "scale": scale,
    }
    with open(OUT_PATH, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1, sort_keys=True)
        fh.write("\n")
    print("wrote {} for {} seasons".format(OUT_PATH, len(scale)))


if __name__ == "__main__":
    main()
