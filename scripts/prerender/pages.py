"""One builder per page family. Each returns (title, description, body html)."""

from __future__ import annotations

import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import factoids as F  # noqa: E402

from . import config as C  # noqa: E402
from .render import (  # noqa: E402
    CONTRACTED_TAG, esc, facts_list, links_row, money, money_short, player_link,
    rank_table, roll_call, scope_line, section, up,
)


def _paid(idx, record):
    return not idx.is_contracted(record["season"])


def _top_paid(idx, records, limit):
    rows = [r for r in records if _paid(idx, r) and r.get("salary")]
    rows.sort(key=lambda r: (-(r.get("salary") or 0), r["player"], r["season"]))
    return rows[:limit]


def _owner_map(identities):
    """record id -> the identity it belongs to."""
    out = {}
    for ident in identities:
        for record in ident.records:
            out[id(record)] = ident
    return out


def _season_salary_rows(idx, records, owners, depth, limit):
    rows = []
    for i, record in enumerate(_top_paid(idx, records, limit), start=1):
        ident = owners.get(id(record))
        if ident is None:
            continue
        rows.append([
            player_link(ident, depth, rank=i),
            esc(record["season"]),
            esc(record.get("team") or "-"),
            money(record.get("salary")),
            "{:.1f}%".format(record["salary_cap_pct"]) if record.get("salary_cap_pct") is not None else "-",
        ])
    return rank_table(
        [("Player", "hm-who"), ("Season", "hm-num"), ("Team", "hm-num"),
         ("Salary", "hm-money"), ("% of cap", "hm-num")],
        rows,
    )


def _career_rows(idx, identities, depth, limit):
    """Completed careers only, by the engine's own rule.

    A career total is claimed for a man whose career is finished, whose whole
    career is inside the window, whose name covers one player, and whose running
    total started at his own first salary. Everything else is left out rather
    than printed with a figure that is not his.
    """
    entries = []
    for ident in identities:
        data_key = ident.data_key
        if not ident.extra.get("career_trustworthy"):
            continue
        if not idx.career_complete(data_key):
            continue
        last = ident.records[-1]
        if not idx.is_final_season(data_key, last["season"]):
            continue
        total = last.get("career_earnings")
        if total is None:
            continue
        entries.append((total, ident, last))
    entries.sort(key=lambda t: (-t[0], t[1].name, t[1].key))
    rows = []
    for i, (total, ident, last) in enumerate(entries[:limit], start=1):
        rows.append([
            player_link(ident, depth, rank=i),
            "{} to {}".format(esc(ident.records[0]["season"]), esc(last["season"])),
            str(len(ident.records)),
            money(total),
        ])
    return rank_table(
        [("Player", "hm-who"), ("Career", "hm-num"), ("Seasons", "hm-num"),
         ("Career earnings", "hm-money")],
        rows,
    ), len(entries)


def _current_rows(idx, records, owners, depth, limit):
    """On a roster now, and what is signed beyond it."""
    current = {}
    contracted = collections.defaultdict(list)
    for record in records:
        key = idx.canonical(record["player"])
        if record["season"] == idx.current_season:
            current[key] = record
        elif idx.is_contracted(record["season"]):
            contracted[key].append(record)
    rows = []
    ordered = sorted(
        current.values(), key=lambda r: (-(r.get("salary") or 0), r["player"])
    )
    for record in ordered[:limit]:
        ident = owners.get(id(record))
        if ident is None:
            continue
        future = sorted(
            contracted.get(idx.canonical(record["player"]), []),
            key=lambda r: F.season_key(r["season"]),
        )
        through = future[-1]["season"] if future else "-"
        rows.append([
            player_link(ident, depth),
            esc(record.get("team") or "-"),
            money(record.get("salary")),
            esc(through),
            money(sum(r.get("salary") or 0 for r in future)) if future else "-",
        ])
    return rank_table(
        [("Player", "hm-who"), ("Team", "hm-num"),
         ("{} salary".format(idx.current_season), "hm-money"),
         ("Signed through", "hm-num"), ("Contracted after", "hm-money")],
        rows,
    ), len(ordered)


# --------------------------------------------------------------------------
# cohort pages: college, country, draft class, pick, position, agent
# --------------------------------------------------------------------------

COHORT_HEADINGS = {
    "college": "Highest-paid {name} players in the NBA",
    "country": "Highest-paid NBA players from {name}",
    "draft": "Highest-paid players of the {name} NBA draft",
    "pick": "Highest-paid No. {name} picks in the NBA",
    "position": "Highest-paid NBA {name_lower}",
    "agent": "{name}: NBA clients and salaries",
}

COHORT_NOUN = {
    "college": "players out of {name}",
    "country": "players from {name}",
    "draft": "players from the {name} draft class",
    "pick": "No. {name} picks",
    "position": "{name_lower}",
    "agent": "{name}'s clients",
}


def cohort_page(idx, entity, identities, facts_by_cohort, depth=2):
    owners = _owner_map(identities)
    name = entity.name
    lower = name.lower()

    if entity.family == "pick" and entity.key == "undrafted":
        heading = "Highest-paid undrafted players in the NBA"
        noun = "undrafted players"
        title = C.TITLES["pick_undrafted"][0]
    else:
        heading = COHORT_HEADINGS[entity.family].format(name=name, name_lower=lower)
        noun = COHORT_NOUN[entity.family].format(name=name, name_lower=lower)
        title = C.title_for(entity.family, name)

    top = _top_paid(idx, entity.records, 1)
    if top:
        leader = owners.get(id(top[0]))
        leader_name = leader.name if leader else top[0]["player"]
        description = (
            "Every NBA salary for {} since {}. {} tops the list at {} in {}."
        ).format(noun, F.SCOPE_FIRST_SEASON, leader_name,
                 money_short(top[0]["salary"]), top[0]["season"])
    else:
        description = "Every NBA salary for {} since {}.".format(noun, F.SCOPE_FIRST_SEASON)

    paid_seasons = sum(1 for r in entity.records if _paid(idx, r) and r.get("salary"))
    career_html, career_count = _career_rows(idx, entity.players, depth, C.TABLE_ROWS)
    current_html, current_count = _current_rows(idx, entity.records, owners, depth, C.TABLE_ROWS)
    facts = facts_by_cohort.get((entity.family, entity.key), [])

    body = [
        "<h1>{}</h1>".format(esc(heading)),
        '<p class="hm-lede">{}</p>'.format(esc(description)),
        scope_line(),
        section(
            "Highest single-season salaries",
            "Top {} of {} paid seasons on file. Contracted future seasons are "
            "left out because that money has not been paid.".format(
                min(C.TABLE_ROWS, paid_seasons), paid_seasons),
            _season_salary_rows(idx, entity.records, owners, depth, C.TABLE_ROWS),
        ),
        section(
            "Highest career earnings",
            "Completed careers only, {} of them. A career counts once the player "
            "has been absent two seasons, his whole career is inside the window, "
            "his name covers one player and his running total starts at his own "
            "first salary.".format(career_count),
            career_html,
        ),
        section(
            "On a roster in {}".format(idx.current_season),
            "{} of them, with what is signed beyond this season.".format(current_count),
            current_html,
        ),
    ]
    if facts:
        body.append(section("What the numbers say", None, facts_list(facts)))
    body.append(section(
        "Every player",
        "{} in all.".format(len(entity.players)),
        roll_call([(p.name, p.slug, None) for p in entity.players], depth, "player"),
    ))
    return title, description, "\n".join(body)


# --------------------------------------------------------------------------
# player pages
# --------------------------------------------------------------------------

def player_page(idx, ident, season_table_html, facts, related, depth=2):
    title = C.TITLES["player"][0].format(name=ident.name)
    first, last = ident.records[0], ident.records[-1]
    paid = [r for r in ident.records if _paid(idx, r)]
    best = max(paid, key=lambda r: r.get("salary") or 0) if paid else None

    bits = ["{} salary history, season by season, from {} to {}.".format(
        ident.name, first["season"], last["season"])]
    if best and best.get("salary"):
        bits.append("His biggest paid season is {} in {}.".format(
            money_short(best["salary"]), best["season"]))
    description = " ".join(bits)

    body = [
        "<h1>{}</h1>".format(esc(ident.name)),
        '<p class="hm-lede">{}</p>'.format(esc(description)),
        scope_line(),
        section(
            "Season by season",
            "Salary, share of the cap, league and team rank, and what the "
            "contract carries beyond this season.",
            season_table_html,
        ),
    ]
    if facts:
        body.append(section("What the numbers say", None, facts_list(facts)))
    elif not ident.extra.get("factoids_allowed"):
        body.append(section(
            "What the numbers say",
            None,
            '<p class="hm-empty">This name covers more than one player in the '
            'data and the split has not been checked, so nothing here is '
            'claimed about him.</p>',
        ))
    body.append(section("Related", None, links_row(related)))
    return title, description, "\n".join(body)


# --------------------------------------------------------------------------
# team pages
# --------------------------------------------------------------------------

def team_page(idx, entity, identities, depth=2):
    owners = _owner_map(identities)
    title = C.TITLES["team"][0].format(name=entity.name)
    code = entity.extra["code"]

    current = [r for r in entity.records if r["season"] == idx.current_season]
    payroll = sum(
        amount for r in current for c, amount in F.team_amounts(r) if c == code
    )
    description = (
        "{} payroll and salary history since {}. The {} roster is on {} for {}."
    ).format(entity.name, F.SCOPE_FIRST_SEASON, idx.current_season,
             money_short(payroll), entity.name)

    # contracted totals per future season
    future = collections.defaultdict(float)
    for record in entity.records:
        if not idx.is_contracted(record["season"]):
            continue
        for c, amount in F.team_amounts(record):
            if c == code:
                future[record["season"]] += amount or 0
    future_rows = [
        [esc(season), str(sum(
            1 for r in entity.records if r["season"] == season
        )), money(total)]
        for season, total in sorted(future.items(), key=lambda kv: F.season_key(kv[0]))
    ]

    current_rows = []
    for record in sorted(current, key=lambda r: -(r.get("salary") or 0)):
        ident = owners.get(id(record))
        if ident is None:
            continue
        share = next((a for c, a in F.team_amounts(record) if c == code), None)
        current_rows.append([
            player_link(ident, depth),
            money(share),
            "{:.1f}%".format(record["salary_cap_pct"]) if record.get("salary_cap_pct") is not None else "-",
            str(record.get("age") or "-"),
            str(record.get("years_exp") if record.get("years_exp") is not None else "-"),
        ])

    body = [
        "<h1>{} payroll and salary history</h1>".format(esc(entity.name)),
        '<p class="hm-lede">{}</p>'.format(esc(description)),
        scope_line(),
        section(
            "{} roster".format(idx.current_season),
            "{} players on {}.".format(len(current_rows), money_short(payroll)),
            rank_table(
                [("Player", "hm-who"), ("Salary", "hm-money"), ("% of cap", "hm-num"),
                 ("Age", "hm-num"), ("Exp", "hm-num")],
                current_rows,
            ),
        ),
        section(
            "Contracted beyond {}".format(idx.current_season),
            "Money on signed deals, not money already paid.",
            rank_table(
                [("Season", "hm-who"), ("Players", "hm-num"), ("Committed", "hm-money")],
                future_rows,
            ),
        ),
        section(
            "Biggest salaries in franchise history",
            "Split seasons are left out: a salary spread over more than one team "
            "is a cap-sheet allocation, not money one franchise paid a player to "
            "play for it.",
            _season_salary_rows(
                idx,
                [r for r in entity.records if not F.is_split_season(r)],
                owners, depth, C.TABLE_ROWS,
            ),
        ),
        section(
            "Every player",
            "{} have appeared on a {} payroll.".format(len(entity.players), entity.name),
            roll_call([(p.name, p.slug, None) for p in entity.players], depth, "player"),
        ),
    ]
    return title, description, "\n".join(body)


# --------------------------------------------------------------------------
# season pages
# --------------------------------------------------------------------------

def season_page(idx, entity, identities, depth=2):
    owners = _owner_map(identities)
    season = entity.key
    title = C.TITLES["season"][0].format(name=season)
    contracted = idx.is_contracted(season)

    total = sum(r.get("salary") or 0 for r in entity.records)
    top = _top_paid(idx, entity.records, 1) if not contracted else sorted(
        entity.records, key=lambda r: -(r.get("salary") or 0))[:1]
    if top:
        leader = owners.get(id(top[0]))
        leader_name = leader.name if leader else top[0]["player"]
        description = (
            "Every NBA salary for {}. {} players on {} in all, led by {} on {}."
        ).format(season, len(entity.players), money_short(total), leader_name,
                 money_short(top[0].get("salary")))
    else:
        description = "Every NBA salary for {}.".format(season)

    rows = []
    ordered = sorted(entity.records, key=lambda r: -(r.get("salary") or 0))
    for i, record in enumerate(ordered[:C.TABLE_ROWS], start=1):
        ident = owners.get(id(record))
        if ident is None:
            continue
        rows.append([
            player_link(ident, depth, rank=i,
                        tag=CONTRACTED_TAG if contracted else ""),
            esc(record.get("team") or "-"),
            money(record.get("salary")),
            "{:.1f}%".format(record["salary_cap_pct"]) if record.get("salary_cap_pct") is not None else "-",
            str(record.get("age") or "-"),
        ])

    payrolls = collections.defaultdict(float)
    for record in entity.records:
        for code, amount in F.team_amounts(record):
            if code in idx.franchises:
                payrolls[code] += amount or 0
    payroll_rows = [
        ['<a href="{}team/{}/">{}</a>'.format(up(depth), esc(_team_slug(code)), esc(idx.franchises[code]["name"])),
         money(amount)]
        for code, amount in sorted(payrolls.items(), key=lambda kv: -kv[1])
    ]

    body = [
        "<h1>NBA salaries {}</h1>".format(esc(season)),
        '<p class="hm-lede">{}</p>'.format(esc(description)),
        scope_line(),
        section(
            "Biggest salaries",
            "Top {} of {} players on file for {}.".format(
                min(C.TABLE_ROWS, len(ordered)), len(ordered), season),
            rank_table(
                [("Player", "hm-who"), ("Team", "hm-num"), ("Salary", "hm-money"),
                 ("% of cap", "hm-num"), ("Age", "hm-num")],
                rows,
            ),
        ),
        section(
            "Team payrolls",
            "What each franchise carried in {}.".format(season),
            rank_table([("Team", "hm-who"), ("Payroll", "hm-money")], payroll_rows),
        ),
        section(
            "Every player",
            "{} on file for {}.".format(len(entity.players), season),
            roll_call([(p.name, p.slug, None) for p in entity.players], depth, "player"),
        ),
    ]
    return title, description, "\n".join(body)


_TEAM_SLUGS = {}


def register_team_slugs(teams):
    _TEAM_SLUGS.clear()
    for team in teams:
        _TEAM_SLUGS[team.key] = team.slug


def _team_slug(code):
    return _TEAM_SLUGS.get(code, code.lower())


# --------------------------------------------------------------------------
# hubs
# --------------------------------------------------------------------------

def hub_page(hub_slug, family, entries, depth=1):
    title = C.HUB_TITLES[hub_slug]
    heading = C.HUB_HEADINGS[hub_slug]
    description = (
        "Every {} page on HoopsMatic's NBA salary database, {} in all, "
        "each with the highest single-season salaries and career earnings "
        "since {}."
    ).format(C.FAMILIES[family]["label"].lower().rstrip("s"), len(entries),
             F.SCOPE_FIRST_SEASON)
    body = [
        "<h1>{}</h1>".format(esc(heading)),
        '<p class="hm-lede">{}</p>'.format(esc(description)),
        scope_line(),
        section(
            "All {}".format(C.FAMILIES[family]["label"].lower()),
            "{} pages, each one a ranked table of salaries and career "
            "earnings.".format(len(entries)),
            roll_call(entries, depth, family),
        ),
    ]
    return title, description, "\n".join(body)
