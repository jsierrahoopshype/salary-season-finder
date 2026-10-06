#!/usr/bin/env python3
"""Step-0 audit for the factoid engine.

Every gate in scripts/factoids.py was set by one of these checks, so this script
is how the PR's audit numbers are reproduced and how a future data change gets
re-checked. It reads data only; it writes nothing.

    python scripts/audit_factoids.py
    python scripts/audit_factoids.py --section awards
"""

from __future__ import annotations

import argparse
import collections
import itertools
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from factoids import (  # noqa: E402
    ALL_NBA_AWARDS,
    ALL_NBA_COUNT_EXPECTED,
    ALL_STAR_AWARDS,
    ALL_STAR_COUNT_MAX,
    ALL_STAR_COUNT_MIN,
    FIRST_DRAFT_YEAR_IN_WINDOW,
    MAX_CAREER_GAP_SEASONS,
    MIN_UNEVIDENCED_GAP,
    SEASONS_WITHOUT_ALL_STAR_GAME,
    build_index,
    is_split_season,
    load_data,
    load_name_aliases,
    load_identity_splits,
    season_key,
    team_amounts,
    team_codes,
)


def rule(title):
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def audit_agents(data, idx):
    rule("(a) AGENT HISTORY")
    by_player = collections.defaultdict(list)
    for rec in idx.records:
        by_player[rec["player"]].append(rec)

    distinct = {p: {r.get("agent") for r in rs if r.get("agent")} for p, rs in by_player.items()}
    with_agent = {p: v for p, v in distinct.items() if v}
    multi = {p: v for p, v in with_agent.items() if len(v) > 1}

    print("players with an agent on any record : {}".format(len(with_agent)))
    print("players whose agent changes         : {}".format(len(multi)))
    print("-> the field is NOT a constant per player, so it is not simply the")
    print("   current agent back-filled.")

    revisits = []
    blips = 0
    for player, recs in by_player.items():
        recs = sorted(recs, key=lambda r: season_key(r["season"]))
        seq = [r.get("agent") for r in recs if r.get("agent")]
        runs = [(k, len(list(g))) for k, g in itertools.groupby(seq)]
        names = [k for k, _ in runs]
        if len(names) != len(set(names)):
            revisits.append((player, names))
        for i in range(1, len(runs) - 1):
            if runs[i][1] == 1 and runs[i - 1][0] == runs[i + 1][0]:
                blips += 1

    print("\nplayers whose agent sequence returns to an earlier agent: {}".format(len(revisits)))
    print("of those, one-season blips sandwiched by the same agent : {}".format(blips))
    print("\nexamples:")
    for player, names in sorted(revisits)[:8]:
        print("   {:24s} {}".format(player, " > ".join(names)))
    print("\n-> real representation does not flap like this. The variation is")
    print("   noise, not history.")

    print("\ncoverage by season:")
    for season in idx.seasons:
        have, total = idx.agent_coverage[season]
        pct = 100.0 * have / total if total else 0.0
        flag = "  <- trusted" if season in idx.agent_seasons_safe else ""
        print("   {}  {:4d}/{:4d}  {:5.1f}%{}".format(season, have, total, pct, flag))
    print("\n-> coverage is under half before 2007-08, so an all-time agent")
    print("   ranking is unsupportable on two counts.")
    print("VERDICT: agent factoids limited to current and contracted seasons,")
    print("         phrased as a current-clients claim.")
    fut = [p for p, v in (
        (p, {r.get("agent") for r in rs
             if r.get("agent") and r["season"] in idx.agent_seasons_safe})
        for p, rs in by_player.items()
    ) if len(v) > 1]
    print("         (players disagreeing with themselves inside that window: {})".format(len(fut)))


def audit_awards(data, idx):
    rule("(b) AWARDS COVERAGE")
    print("awards_list strings mapped to All-Star   : {}".format(sorted(ALL_STAR_AWARDS)))
    print("awards_list strings mapped to All-NBA    : {}".format(sorted(ALL_NBA_AWARDS)))
    print("nothing is pattern-matched; a string is in one of those sets or it is")
    print("not a selection.\n")
    print("plausibility bands: All-Star {}-{}, All-NBA exactly {}\n".format(
        ALL_STAR_COUNT_MIN, ALL_STAR_COUNT_MAX, ALL_NBA_COUNT_EXPECTED
    ))
    print("{:9s} {:>9s} {:>9s}   {}".format("season", "All-Star", "All-NBA", "verdict"))
    for season in idx.seasons:
        n_as = idx.all_star_counts[season]
        n_nba = idx.all_nba_counts[season]
        if season_key(season) >= idx.current_key:
            verdict = "unknown (not played yet)"
        else:
            flags = []
            if season in SEASONS_WITHOUT_ALL_STAR_GAME:
                flags.append("no game held (lockout), zero is correct")
            if season in idx.all_star_unsafe_seasons:
                flags.append("All-Star list UNSAFE")
            if season in idx.all_nba_unsafe_seasons:
                flags.append("All-NBA list UNSAFE")
            verdict = "; ".join(flags) if flags else "ok"
        print("{:9s} {:>9d} {:>9d}   {}".format(season, n_as, n_nba, verdict))
    print("\nAll-Star list not trusted: {}".format(sorted(idx.all_star_unsafe_seasons)))
    print("All-NBA list not trusted : {}".format(sorted(idx.all_nba_unsafe_seasons)))
    print("Flags are per award, not per season. A season with a sound All-Star")
    print("list and a short All-NBA one only blocks the All-NBA claim, so a")
    print("career that touched it can still prove a negative about All-Star.")
    print("awards known through: {}".format(idx.awards_known_through))
    print("\nexempt from the All-Star band: {}".format(sorted(SEASONS_WITHOUT_ALL_STAR_GAME)))
    print("   the 1999 All-Star Game was cancelled by the lockout, so no")
    print("   selections exist and a count of zero is the right answer rather")
    print("   than a scrape that came back empty. The All-NBA check still runs.")
    print("   1996-97 and 2006-07 carry 29 each and stay flagged: injury")
    print("   replacements plausibly explain the extra names, but the list has")
    print("   not been checked against the published rosters.")


def audit_truncated(data, idx):
    rule("(c) TRUNCATED CAREERS")
    print("Rule as briefed: draft_year < {} OR first season is {} with".format(
        FIRST_DRAFT_YEAR_IN_WINDOW, "1990-91"
    ))
    print("years_exp > 0.\n")
    exp_values = collections.Counter(
        r.get("years_exp") for r in idx.records if r["season"] == "1990-91"
    )
    print("years_exp values on 1990-91 records: {}".format(dict(exp_values)))
    print("-> years_exp counts seasons since the player's first season IN THIS")
    print("   FILE, so every 1990-91 record reads 0, Magic Johnson's included.")
    print("   The second half of the rule can never fire and is replaced by:")
    print("   first season is 1990-91 and draft_year is not {}.".format(FIRST_DRAFT_YEAR_IN_WINDOW))

    by_player = collections.defaultdict(list)
    for rec in idx.records:
        by_player[rec["player"]].append(rec)
    print("\ntruncated careers excluded from career and negative-space rankings: {} of {}".format(
        len(idx.truncated), len(by_player)
    ))
    print("examples: {}".format(", ".join(sorted(idx.truncated)[:8])))

    rule("(c2) MERGED IDENTITIES AND CORRUPTED DRAFT METADATA (not briefed, found in audit)")
    print("draft_year later than the player's first season, which means the")
    print("metadata belongs to another man under the same key: {}".format(
        len(idx.draft_meta_suspect)
    ))
    for player in sorted(idx.draft_meta_suspect):
        recs = sorted(by_player[player], key=lambda r: season_key(r["season"]))
        print("   {:20s} draft_year={} pick={} first season {}".format(
            player, recs[0].get("draft_year"), recs[0].get("draft_pick"), recs[0]["season"]
        ))
    print("-> excluded from every identity cohort: college, country and")
    print("   position come off the same player record as the draft fields.")
    restored = sorted(
        p for p in idx.draft_meta_suspect
        if any(k[0] == p for k in idx.segment_owns_metadata)
    )
    print("   Exception: a confirmed split names which segment the metadata")
    print("   describes, and that segment gets its cohorts back: {}".format(
        ", ".join(restored) or "none"
    ))
    print()
    print("career_earnings is a running total, so a first record should read")
    print("exactly the first salary. Where it reads more, the total was already")
    print("running under someone else's name: {} players".format(
        len(idx.career_total_carried_in)
    ))
    for player in sorted(idx.career_total_carried_in):
        first = sorted(idx.by_player[player], key=lambda r: season_key(r["season"]))[0]
        print("   {:22s} first {} salary {:>12,} career {:>13,}".format(
            player, first["season"], int(first.get("salary") or 0),
            int(first.get("career_earnings") or 0),
        ))
    print("-> excluded from every career-level claim, their own and anyone")
    print("   else's measured against them.\n")
    print("careers with a gap longer than {} seasons, which means two players".format(
        MAX_CAREER_GAP_SEASONS
    ))
    print("merged under one name: {}".format(len(idx.identity_suspect)))
    worst = []
    for player in idx.identity_suspect:
        recs = sorted(by_player[player], key=lambda r: season_key(r["season"]))
        keys = [season_key(r["season"]) for r in recs]
        gap = max(keys[i] - keys[i - 1] for i in range(1, len(keys)))
        worst.append((gap, player, recs[0]["season"], recs[-1]["season"]))
    for gap, player, first, last in sorted(worst, reverse=True)[:8]:
        print("   {:20s} {} to {}  (gap {})".format(player, first, last, gap))
    print("-> excluded from every career-level claim.")

    rule("(c4) GAPS NO RECORDED AGE VOUCHES FOR")
    print("A gap under one name is a man who went away and came back, or two")
    print("men filed together, and the only thing in this data that tells them")
    print("apart is the age each season recorded. Where one side of the gap")
    print("carries an age the build worked out from a birth date instead, that")
    print("test cannot be run: a computed age agrees with the birth date it came")
    print("from whichever man the season belonged to. Reported, never acted on.")
    print("")
    rows = idx.gaps_without_observed_age
    checked = load_identity_splits() or {}
    print("gaps of {}+ seasons with no recorded age across them: {} on {} names".format(
        MIN_UNEVIDENCED_GAP, len(rows), len({r[0] for r in rows})
    ))
    sizes = collections.Counter(r[3] for r in rows)
    print("   by gap: {}".format(
        ", ".join("{} seasons: {}".format(g, n) for g, n in sorted(sizes.items()))))
    settled = [r for r in rows if r[0] in checked]
    print("\nalready carrying an entry in identity_splits.json: {}".format(len(settled)))
    print("their entry says one man, and the age it says so on is a computed one:")
    for player, early, late, gap in settled:
        entry = checked[player]
        print("   {:22s} {} -> {}  (gap {})  confirmed={} split={}".format(
            player, early, late, gap, bool(entry.get("confirmed")), bool(entry.get("split"))
        ))
    print("\nno entry yet, so nobody has looked: {}".format(len(rows) - len(settled)))
    for player, early, late, gap in rows:
        if player in checked:
            continue
        a = idx.record(player, early) or {}
        b = idx.record(player, late) or {}
        print("   {:22s} {} {:>4} {:>12,}  ->  {} {:>4} {:>12,}  (gap {})".format(
            player, early, a.get("team") or "-", int(a.get("salary") or 0),
            late, b.get("team") or "-", int(b.get("salary") or 0), gap,
        ))
    print("-> nothing here changes a factoid. It is the queue for checking.")

    rule("(c3) IDENTITY SPLITS: which gaps are two men and which are one")
    splits = load_identity_splits()
    if not splits:
        print("data/identity_splits.json not present.")
        return
    print("data.json carries an explicit age per record, so a gap where the")
    print("later age continues the earlier one is one man coming back, and a")
    print("gap where it jumps is two men filed under one name. An age no NBA")
    print("player can have is the same finding by another route.\n")
    two = {k: v for k, v in splits.items() if v.get("split")}
    one = {k: v for k, v in splits.items() if not v.get("split")}
    print("names covering two people : {}".format(len(two)))
    for key in sorted(two):
        entry = two[key]
        print("   {}".format(key))
        for person in entry["people"]:
            name = person.get("display_name") or "(name unknown)"
            print("      {:24s} {} to {}  draft {} pick {} college {}".format(
                name, person["first_season"], person["last_season"],
                person.get("draft_year") or "-", person.get("draft_pick") or "-",
                person.get("college") or "-",
            ))
            if person.get("note"):
                print("         {}".format(person["note"]))
        print("      evidence: {}".format(entry["evidence"]))
        print("      confirmed: {}".format(entry.get("confirmed", False)))
    print("\none man with a gap (a spell abroad): {}".format(len(one)))
    print("   {}".format(", ".join(sorted(one))))
    print("   confirmed: {} of {}".format(
        sum(1 for v in one.values() if v.get("confirmed")), len(one)
    ))
    print("   an unconfirmed gap keeps the career-level exclusion it triggered:")
    print("   a long gap under one name is not proof of one person.")
    unconfirmed = sorted(k for k, v in two.items() if not v.get("confirmed"))
    print("\nunconfirmed splits, whose earlier segments are held back: {}".format(
        len(unconfirmed)
    ))
    print("seasons held back: {}".format(len(idx.split_suppressed)))
    for pair in sorted(idx.split_suppressed):
        print("   {} {}".format(*pair))
    renamed = {k: v for k, v in idx.segment_display.items() if v != k[0]}
    print("\nseasons a confirmed split renames: {}".format(len(renamed)))
    for pair in sorted(renamed):
        print("   {} {} prints as {}".format(pair[0], pair[1], renamed[pair]))


def audit_splits(data, idx):
    rule("(g) SPLIT SEASONS")
    print("team_salaries is a cap-sheet allocation, not money paid while on a")
    print("roster: Russell Westbrook's 2022-23 reads $46.3 million against UTA,")
    print("a team he never played a game for, while the Lakers paid most of")
    print("that season. DeMar DeRozan's 2026-27 is split DEN/SAC before a game")
    print("has been played.\n")
    split_recs = [r for r in idx.records if is_split_season(r)]
    print("records with more than one team in team_salaries: {} of {}".format(
        len(split_recs), len(idx.records)
    ))
    by_season = collections.Counter(r["season"] for r in split_recs)
    print("busiest seasons: {}".format(
        ", ".join("{} ({})".format(s, n) for s, n in by_season.most_common(5))
    ))
    for player, season in (("Russell Westbrook", "2022-23"), ("DeMar DeRozan", "2026-27")):
        rec = idx.record(player, season)
        if rec:
            print("   {} {}: team={} salary={} splits={}".format(
                player, season, rec.get("team"), rec.get("salary"), rec.get("team_salaries")
            ))
    print("\n-> a split record emits no franchise factoid (gate split_season)")
    print("   and is kept out of every franchise comparison set, so it can")
    print("   neither hold a franchise record nor be quoted as a previous")
    print("   holder. Season, cohort, cap and career claims use the whole")
    print("   season salary and are unaffected.")


def audit_name_aliases(data, idx):
    rule("(h) NAME-VARIANT DUPLICATES")
    print("data.json can file one career under two spellings. Wendell Carter's")
    print("2024-25 sits under \"Wendell Carter\" while the rest of the same")
    print("career is \"Wendell Carter Jr\", so apart, one half looks finished and")
    print("the other starts mid-career with the first half's running total.\n")
    aliases = load_name_aliases()
    if not aliases:
        print("data/name_aliases.json not present.")
        return
    print("merged pairs: {}".format(len(aliases)))
    for alias in sorted(aliases):
        canonical = aliases[alias]
        recs = idx.by_player.get(canonical) or []
        print("   {:24s} <- {:24s}  merged run {} to {}, {} seasons".format(
            canonical, alias,
            recs[0]["season"] if recs else "-",
            recs[-1]["season"] if recs else "-", len(recs),
        ))
    print("\n-> the rule: same name once the suffix, full stops and apostrophes")
    print("   are stripped, same draft year or one missing, no season in common,")
    print("   and seasons that run continuously together. A shared season means")
    print("   two men; a hole in the run is a career gap, which identity_splits")
    print("   handles instead. Every genuine father and son in this data fails")
    print("   the rule on the gap: Gary Payton to Gary Payton II is 10 seasons,")
    print("   Larry Nance to Larry Nance Jr is 22.")


def audit_franchises(data, idx):
    rule("(d) FRANCHISE MAP")
    codes = collections.defaultdict(list)
    for rec in idx.records:
        for code in team_codes(rec):
            codes[code].append(season_key(rec["season"]))
    print("{} team codes in data.json, {} in data/franchises.json\n".format(
        len(codes), len(idx.franchises)
    ))
    print("{:5s} {:>6s}  {:24s} {}".format("code", "recs", "franchise", "eras inside the window"))
    for code in sorted(codes):
        franchise = idx.franchises.get(code)
        if franchise is None:
            print("{:5s} {:>6d}  {:24s} UNMAPPED -> no franchise factoids".format(
                code, len(codes[code]), "-"
            ))
            continue
        eras = "; ".join(
            "{} {}-{}".format(e["name"], e["from"], e["to"] or "now")
            for e in franchise["eras"]
        )
        print("{:5s} {:>6d}  {:24s} {}".format(
            code, len(codes[code]), franchise["full_name"], eras
        ))
    unmapped = sorted(set(codes) - set(idx.franchises))
    print("\nunmapped codes: {}".format(unmapped or "none"))
    cha = sorted({s for s in codes.get("CHA", ())})
    missing = [y for y in range(min(cha), max(cha) + 1) if y not in cha] if cha else []
    print("CHA seasons missing from the data (the Bobcats gap): {}".format(
        ["{}-{}".format(y - 1, str(y)[-2:]) for y in missing]
    ))
    print("NOP first season: {}".format(
        min(("{}-{}".format(y - 1, str(y)[-2:]) for y in codes.get("NOP", ())), default="-")
    ))
    print("-> the 1988-2002 Charlotte history sits under CHA, not NOP, which is")
    print("   what the brief requires.")


def audit_current_and_caps(data, idx):
    rule("(e) CURRENT SEASON  /  (f) FUTURE CAPS")
    team_count = len(data.get("teams") or []) or 30
    counts = collections.Counter(r["season"] for r in idx.records if r.get("salary"))
    print("front-end rule: newest season with at least {} x 15 = {} paid players".format(
        team_count, team_count * 15
    ))
    for season in sorted(idx.seasons, key=season_key, reverse=True)[:8]:
        mark = "  <- current" if season == idx.current_season else ""
        print("   {}  {:4d} paid records{}".format(season, counts[season], mark))
    print("\nCURRENT_SEASON = {} (derived, never hardcoded)".format(idx.current_season))
    print("contracted seasons: {}".format(
        [s for s in idx.seasons if season_key(s) > idx.current_key]
    ))
    print("\nsalary cap coverage:")
    missing = [s for s in idx.seasons if not (idx.cap.get(s) or {}).get("cap")]
    print("   seasons with no cap on file: {}".format(missing or "none"))
    print("   -> the missing-cap gate is live in code but never fires on today's data.")


SECTIONS = {
    "agents": audit_agents,
    "awards": audit_awards,
    "careers": audit_truncated,
    "franchises": audit_franchises,
    "seasons": audit_current_and_caps,
    "splits": audit_splits,
    "aliases": audit_name_aliases,
}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", default=None)
    parser.add_argument("--section", choices=sorted(SECTIONS), default=None)
    args = parser.parse_args(argv)

    data = load_data(args.data)
    idx = build_index(data)
    print("data/data.json: {} season records, {} seasons, {} players".format(
        len(idx.records), len(idx.seasons), len(idx.by_player)
    ))
    sections = [SECTIONS[args.section]] if args.section else [
        audit_agents, audit_awards, audit_truncated, audit_name_aliases,
        audit_splits, audit_franchises, audit_current_and_caps,
    ]
    for section in sections:
        section(data, idx)
    return 0


if __name__ == "__main__":
    sys.exit(main())
