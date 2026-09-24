#!/usr/bin/env python3
"""Build data/factoids.json from data/data.json.

Every record from the current season onward is evaluated. Past seasons are then
added, newest first, for as long as the file stays inside the size budget, so
the daily diff engine has history to compare against without the file becoming
another 10 MB payload.

The output is deterministic: keys are sorted, floats are rounded the same way
every run and nothing carries a timestamp. An unchanged data.json therefore
produces a byte-identical factoids.json and no diff, which is what keeps the
daily commit quiet.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from factoids import (  # noqa: E402
    AGENT_MIN_CLIENTS,
    APPROACH_MAX_RANK,
    APPROACH_WITHIN_PCT,
    COHORT_MINIMUMS,
    SCOPE_FIRST_SEASON,
    build_index,
    factoids_for,
    load_data,
    season_key,
)

#: Roughly 2 MB, the ceiling the brief sets for the committed file.
DEFAULT_MAX_BYTES = 2 * 1024 * 1024

OUT_PATH = os.path.join("data", "factoids.json")


def repo_path(*parts):
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(here), *parts)


def _round_floats(obj):
    """Round every float to 4 dp so the same input always serialises the same."""
    if isinstance(obj, float):
        return round(obj, 4)
    if isinstance(obj, dict):
        return {k: _round_floats(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_round_floats(v) for v in obj]
    return obj


def build_for_seasons(data, index, seasons):
    """Evaluate every record in ``seasons``. Returns {"player|season": [...]}. """
    wanted = set(seasons)
    out = {}
    for record in data.get("seasons") or ():
        if record["season"] not in wanted:
            continue
        facts = factoids_for(
            data, record["player"], record["season"], index=index
        )
        if facts:
            out["{}|{}".format(record["player"], record["season"])] = _round_floats(facts)
    return out


def serialise(payload):
    """One canonical serialisation, used for both sizing and writing."""
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=1) + "\n"


def make_payload(index, factoids, seasons_covered):
    families = sorted({f["family"] for facts in factoids.values() for f in facts})
    return {
        "meta": {
            "scope_first_season": SCOPE_FIRST_SEASON,
            "current_season": index.current_season,
            "seasons_covered": sorted(seasons_covered, key=season_key),
            "record_count": len(factoids),
            "factoid_count": sum(len(v) for v in factoids.values()),
            "families": families,
            "gates": {
                "approach_max_rank": APPROACH_MAX_RANK,
                "approach_within_pct": APPROACH_WITHIN_PCT,
                "agent_min_clients": AGENT_MIN_CLIENTS,
                "cohort_minimums": dict(sorted(COHORT_MINIMUMS.items())),
                "awards_unsafe_seasons": sorted(index.awards_unsafe_seasons),
                "agent_seasons": sorted(index.agent_seasons_safe, key=season_key),
                "truncated_careers": len(index.truncated),
                "merged_identities": len(index.identity_suspect),
                "draft_metadata_suspect": sorted(index.draft_meta_suspect),
            },
        },
        "factoids": factoids,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", default=None, help="Path to data.json")
    parser.add_argument("--out", default=None, help="Path to factoids.json")
    parser.add_argument(
        "--max-bytes", type=int, default=DEFAULT_MAX_BYTES,
        help="Size budget; past seasons are added only while the file fits (default 2 MiB)",
    )
    parser.add_argument(
        "--no-past", action="store_true",
        help="Current season onward only, skip the back-fill",
    )
    parser.add_argument("--dry-run", action="store_true", help="Report the size, write nothing")
    args = parser.parse_args(argv)

    data_path = args.data or repo_path("data", "data.json")
    out_path = args.out or repo_path(OUT_PATH)

    data = load_data(data_path)
    index = build_index(data)
    all_seasons = sorted(index.seasons, key=season_key)
    current = index.current_season

    forward = [s for s in all_seasons if season_key(s) >= season_key(current)]
    past = [s for s in all_seasons if season_key(s) < season_key(current)]
    past.reverse()  # newest first

    covered = list(forward)
    factoids = build_for_seasons(data, index, covered)
    body = serialise(make_payload(index, factoids, covered))
    print("current season {}".format(current))
    print("{} season(s) from the current one onward: {} records, {} bytes".format(
        len(forward), len(factoids), len(body.encode("utf-8"))
    ))

    if len(body.encode("utf-8")) > args.max_bytes:
        print(
            "WARNING: the current and contracted seasons alone are {} bytes, over the "
            "{} byte budget. Writing them anyway: they are the part the diff engine "
            "needs.".format(len(body.encode("utf-8")), args.max_bytes)
        )
    elif not args.no_past:
        # Size each past season by its own serialised weight rather than
        # re-serialising the whole payload once per candidate, which turns the
        # back-fill from quadratic into a single pass.
        running = len(body.encode("utf-8"))
        for season in past:
            season_facts = build_for_seasons(data, index, [season])
            if not season_facts:
                covered.append(season)
                continue
            weight = len(
                json.dumps(season_facts, sort_keys=True, ensure_ascii=False, indent=1).encode("utf-8")
            )
            if running + weight > args.max_bytes:
                print("stopping back-fill at {}: adding it would reach about {} bytes".format(
                    season, running + weight
                ))
                break
            covered.append(season)
            factoids.update(season_facts)
            running += weight
        body = serialise(make_payload(index, factoids, covered))
        # The per-season weights are an estimate; drop seasons until the real
        # serialisation fits.
        while len(body.encode("utf-8")) > args.max_bytes and len(covered) > len(forward):
            dropped = min(covered, key=season_key)
            covered.remove(dropped)
            for key in [k for k in factoids if k.endswith("|" + dropped)]:
                del factoids[key]
            print("trimmed {} to stay inside the budget".format(dropped))
            body = serialise(make_payload(index, factoids, covered))
        print("back-filled to {}".format(min(covered, key=season_key)))

    final_size = len(body.encode("utf-8"))
    print("seasons covered: {} to {}".format(
        min(covered, key=season_key), max(covered, key=season_key)
    ))
    print("records with at least one factoid: {}".format(len(factoids)))
    print("factoids: {}".format(sum(len(v) for v in factoids.values())))
    print("final size: {} bytes ({:.2f} MiB)".format(final_size, final_size / 1048576.0))

    if args.dry_run:
        return 0

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(body)
    print("wrote {}".format(out_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
