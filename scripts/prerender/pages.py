"""One builder per page family. Each returns (title, description, body html)."""

from __future__ import annotations

import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import factoids as F  # noqa: E402

from . import config as C  # noqa: E402
from .render import (  # noqa: E402
    CONTRACTED_TAG, esc, facts_by_season, links_row, money, money_short,
    more_block, page_url, player_link, rank_table, related_chips, roll_call,
    scope_line, section, summary_block,
)
from .summary import cohort_summary  # noqa: E402


def _paid(idx, record):
    return not idx.is_contracted(record["season"])


def _rankable(idx, record):
    """A salary that can hold a place in a ranking.

    A record the engine flagged as impossible is a data fault, not a contract,
    so it never tops a table and never feeds a sentence. It stays on the
    player's own season table, which lists what the file holds rather than
    ranking anything.
    """
    return not idx.is_impossible(record)


def _top_paid(idx, records, limit):
    rows = [
        r for r in records
        if _paid(idx, r) and r.get("salary") and _rankable(idx, r)
    ]
    rows.sort(key=lambda r: (-(r.get("salary") or 0), r["player"], r["season"]))
    return rows[:limit]


def _owner_map(identities):
    """record id -> the identity it belongs to."""
    out = {}
    for ident in identities:
        for record in ident.records:
            out[id(record)] = ident
    return out


def _paid_entries(idx, records, owners, limit):
    """(record, identity) for the biggest paid seasons, biggest first."""
    out = []
    for record in _top_paid(idx, records, limit):
        ident = owners.get(id(record))
        if ident is not None:
            out.append((record, ident))
    return out


def _season_salary_rows(idx, records, owners, limit, media=None):
    rows = []
    for i, (record, ident) in enumerate(
        _paid_entries(idx, records, owners, limit), start=1
    ):
        rows.append([
            player_link(ident, rank=i, face=_face(media, ident),
                        tag=_flag(media, ident)),
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


def _face(media, ident):
    return media.face(ident) if media else ""


def _flag(media, ident):
    return media.player_flag(ident) if media else ""


def _career_entries(idx, identities):
    """Money already paid, biggest first, active players included.

    A list of finished careers only is wrong on its face: it called Elton
    Brand the highest-earning Duke player while Kyrie Irving, still playing and
    $200 million clear of him, was not on it. The figure is the running total
    through each man's last season that is not contracted, so nothing here
    counts money that has not been paid.

    What stays out is a total that is not one man's: a name covering two
    players, a running total that was already running when he arrived, and a
    page for one segment of a confirmed split, whose key sums two careers.
    """
    entries = []
    for ident in identities:
        data_key = ident.data_key
        if ident.extra.get("segment") is not None:
            continue
        if not idx.career_rankable(data_key):
            continue
        total, season = idx.paid_through(data_key)
        if total is None:
            continue
        last = next(
            (r for r in reversed(ident.records) if r["season"] == season), None
        )
        if last is None:
            continue
        entries.append((total, ident, last))
    entries.sort(key=lambda t: (-t[0], t[1].name, t[1].key))
    return entries


ACTIVE_TAG = '<span class="hm-active">active</span>'


def _career_rows(idx, entries, limit, media=None):
    rows = []
    for i, (total, ident, last) in enumerate(entries[:limit], start=1):
        active = not idx.career_complete(ident.data_key)
        # Both ends named, active or not: "to date" leaves a reader guessing
        # which season the total runs through.
        span = "{} to {}".format(
            esc(ident.records[0]["season"]), esc(last["season"]))
        played = sum(
            1 for r in ident.records if not idx.is_contracted(r["season"])
        )
        rows.append([
            player_link(ident, rank=i, face=_face(media, ident),
                        tag=_flag(media, ident) + (ACTIVE_TAG if active else "")),
            span,
            str(played),
            money(total),
        ])
    # The money is the column a reader came for, so on a phone it sits beside
    # the name; the span and the season count follow. Ordered in CSS, not in
    # the markup, so the desktop table is untouched and the header and the
    # body cannot fall out of step.
    return rank_table(
        [("Player", "hm-who"), ("Career", "hm-num hm-span"),
         ("Seasons", "hm-num hm-seasons"), ("Career earnings", "hm-money")],
        rows, table_class="hm-career-table",
    )


def _current_rows(idx, records, owners, limit, media=None):
    """On a roster now, and what is signed beyond it."""
    current = {}
    contracted = collections.defaultdict(list)
    for record in records:
        key = idx.canonical(record["player"])
        if not _rankable(idx, record):
            continue
        if record["season"] == idx.current_season:
            current[key] = record
        elif idx.is_contracted(record["season"]):
            contracted[key].append(record)
    rows = []
    ordered = sorted(
        current.values(), key=lambda r: (-(r.get("salary") or 0), r["player"])
    )
    entries = []
    for record in ordered[:limit]:
        ident = owners.get(id(record))
        if ident is None:
            continue
        entries.append((record, ident))
        future = sorted(
            contracted.get(idx.canonical(record["player"]), []),
            key=lambda r: F.season_key(r["season"]),
        )
        through = future[-1]["season"] if future else "-"
        rows.append([
            player_link(ident, face=_face(media, ident), tag=_flag(media, ident)),
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
    ), len(ordered), entries


# --------------------------------------------------------------------------
# cohort pages: college, country, draft class, pick, position, agent
# --------------------------------------------------------------------------

COHORT_HEADINGS = {
    # These three carry their own noun, which arrives on the entity because
    # lowercasing "Duke Guards" would take Duke with it.
    "region": "Highest-paid {noun} in the NBA",
    "pick_range": "Highest-paid {noun} in the NBA",
    "college_position": "Highest-paid {noun} in the NBA",
    "college": "Highest-paid {name} players in the NBA",
    "country": "Highest-paid NBA players from {name}",
    "draft": "Highest-paid players of the {name} NBA draft",
    "pick": "Highest-paid No. {name} picks in the NBA",
    "position": "Highest-paid NBA {name_lower}",
    "agent": "{name}: NBA clients and salaries",
}

COHORT_NOUN = {
    "region": "{noun}",
    "pick_range": "{noun}",
    "college_position": "{noun}",
    "college": "players out of {name}",
    "country": "players from {name}",
    "draft": "players from the {name} draft class",
    "pick": "No. {name} picks",
    "position": "{name_lower}",
    "agent": "{name}'s clients",
}


def cohort_page(idx, entity, identities, facts_by_cohort, media=None,
                linker=None, family_members=None, relatives=None):
    owners = _owner_map(identities)
    name = entity.name
    lower = name.lower()
    own_noun = entity.extra.get("noun") or lower

    if entity.family == "pick" and entity.key == "undrafted":
        heading = "Highest-paid undrafted players in the NBA"
        noun = "undrafted players"
        title = C.TITLES["pick_undrafted"][0]
    else:
        heading = COHORT_HEADINGS[entity.family].format(
            name=name, name_lower=lower, noun=own_noun)
        noun = COHORT_NOUN[entity.family].format(
            name=name, name_lower=lower, noun=own_noun)
        title = C.title_for(entity.family, name)

    top = _top_paid(idx, entity.records, 1)
    if top:
        leader = owners.get(id(top[0]))
        leader_name = leader.name if leader else top[0]["player"]
        description = (
            "Every NBA salary for {}. {} tops the list at {} in {}."
        ).format(noun, leader_name, money_short(top[0]["salary"]), top[0]["season"])
    else:
        description = "Every NBA salary for {}.".format(noun)

    paid_seasons = sum(1 for r in entity.records if _paid(idx, r) and r.get("salary"))
    career = _career_entries(idx, entity.players)
    career_html = _career_rows(idx, career, C.TABLE_ROWS, media)
    current_html, current_count, current = _current_rows(
        idx, entity.records, owners, C.TABLE_ROWS, media)
    paid = _paid_entries(idx, entity.records, owners, C.TABLE_ROWS)
    facts = facts_by_cohort.get((entity.family, entity.key), [])

    # The summary carries the page's own numbers, so the description's one fact
    # is not repeated in the body as well.
    sentences = cohort_summary(
        idx, entity.family, entity.key, name, career, paid, current, facts,
        slug=entity.slug, noun=entity.extra.get("noun"),
        noun_one=entity.extra.get("noun_one"),
        noun_record=entity.extra.get("noun_record"))

    body = [
        "<h1>{}</h1>".format(esc(heading)),
        summary_block(sentences, linker, entity.url)
        or '<p class="hm-lede">{}</p>'.format(esc(description)),
        scope_line(),
        section(
            "Highest career earnings",
            "Money already paid, {} players in all, active men included. "
            "Contracted seasons are left out, and so is any name the data "
            "cannot pin to one man.".format(len(career)),
            career_html,
        ),
        section(
            "Highest single-season salaries",
            "Top {} of {} paid seasons on file. Contracted future seasons are "
            "left out because that money has not been paid.".format(
                min(C.TABLE_ROWS, paid_seasons), paid_seasons),
            _season_salary_rows(idx, entity.records, owners, C.TABLE_ROWS, media),
        ),
        section(
            "On a roster in {}".format(idx.current_season),
            "{} players, with what is signed beyond this season.".format(current_count),
            current_html,
        ),
        section(
            "Every player",
            "{} in all.".format(len(entity.players)),
            roll_call([(p.name, p.slug, None) for p in entity.players], "player"),
        ),
    ]
    if relatives:
        body.append(section(
            "Related pages",
            "The same players, cut another way.",
            related_chips(relatives),
        ))
    if family_members:
        body.append(more_block(entity.family, family_members, entity.slug))
    return title, description, "\n".join(body)


# --------------------------------------------------------------------------
# player pages
# --------------------------------------------------------------------------

def player_page(idx, ident, season_table_html, facts, related, linker=None):
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
        body.append(section(
            "What the numbers say",
            "Season by season, newest first.",
            facts_by_season(facts, F.season_key(idx.current_season),
                            linker, ident.url),
        ))
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

def team_page(idx, entity, identities):
    owners = _owner_map(identities)
    title = C.TITLES["team"][0].format(name=entity.name)
    code = entity.extra["code"]

    current = [r for r in entity.records if r["season"] == idx.current_season]
    payroll = sum(
        amount for r in current for c, amount in F.team_amounts(r) if c == code
    )
    description = (
        "{} payroll and salary history, season by season. The {} roster is on "
        "{} for {}."
    ).format(entity.name, idx.current_season, money_short(payroll), entity.name)

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
            player_link(ident),
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
                [r for r in entity.records
                 if not F.is_split_season(r) and _rankable(idx, r)],
                owners, C.TABLE_ROWS,
            ),
        ),
        section(
            "Every player",
            "{} have appeared on a {} payroll.".format(len(entity.players), entity.name),
            roll_call([(p.name, p.slug, None) for p in entity.players], "player"),
        ),
    ]
    return title, description, "\n".join(body)


# --------------------------------------------------------------------------
# season pages
# --------------------------------------------------------------------------

def season_page(idx, entity, identities):
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
    ordered = sorted(
        [r for r in entity.records if _rankable(idx, r)],
        key=lambda r: -(r.get("salary") or 0),
    )
    for i, record in enumerate(ordered[:C.TABLE_ROWS], start=1):
        ident = owners.get(id(record))
        if ident is None:
            continue
        rows.append([
            player_link(ident, rank=i,
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
        ['<a href="{}">{}</a>'.format(
            esc(page_url("team", _team_slug(code))), esc(idx.franchises[code]["name"])),
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
            roll_call([(p.name, p.slug, None) for p in entity.players], "player"),
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

def hub_page(hub_slug, family, entries, lead=None, family_members=None):
    title = C.HUB_TITLES[hub_slug]
    heading = C.HUB_HEADINGS[hub_slug]
    description = (
        "Every {} page on HoopsMatic's NBA salary database, {} in all, each "
        "with the highest single-season salaries and career earnings."
    ).format(C.FAMILIES[family]["label_one"].lower(), len(entries))
    body = [
        "<h1>{}</h1>".format(esc(heading)),
        '<p class="hm-lede">{}</p>'.format(esc(description)),
        scope_line(),
    ]
    # The hub's own list is the "more" block for this family: printing it
    # twice would be the same chips under two headings. Past 40 pages it is
    # grouped, so a reader can find a letter or a decade without scrolling
    # through the lot.
    if family_members and len(family_members) > 40:
        body.append(more_block(
            family, family_members, None,
            heading="All {}".format(C.FAMILIES[family]["label"].lower())))
    else:
        body.append(section(
            "All {}".format(C.FAMILIES[family]["label"].lower()),
            "{} pages, each one a ranked table of salaries and career "
            "earnings.".format(len(entries)),
            roll_call(entries, family, lead=lead),
        ))
    return title, description, "\n".join(body)
