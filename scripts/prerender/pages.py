"""One builder per page family. Each returns (title, description, body html)."""

from __future__ import annotations

import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import factoids as F  # noqa: E402

from . import config as C  # noqa: E402
from .render import (  # noqa: E402
    CONTRACTED_TAG, cap_pct, esc, facts_summary, grouped_rank_table, links_row,
    money, money_short, more_block, page_url, plain_name, player_link,
    rank_table, related_chips, roll_call, scope_line, season_span, section,
    summary_block, timeline_list,
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
            cap_pct(record.get("salary_cap_pct")),
        ])
    return rank_table(
        [("Player", "hm-who"), ("Season", "hm-word"), ("Team", "hm-word"),
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
        # which season the total runs through. One season is not a span, so it
        # is printed once rather than as "2026-27 to 2026-27".
        span = season_span(ident.records[0]["season"], last["season"])
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
        [("Player", "hm-who"), ("Career", "hm-word hm-span"),
         ("Seasons", "hm-word hm-seasons"), ("Career earnings", "hm-money")],
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
        [("Player", "hm-who"), ("Team", "hm-word"),
         ("{} salary".format(idx.current_season), "hm-money"),
         ("Signed through", "hm-word"), ("Contracted after", "hm-money")],
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

def _career_may_predate_the_data(idx, ident):
    """Whether the edge of the window can have cut anything off this career.

    Two shapes of career can. One was already under way when the file opens: a
    man whose first season on file is the first season on file at all was
    almost certainly paid before it, and the page cannot say how much. The
    other was drafted before the oldest draft class the file can hold whole, so
    his early seasons are outside it whatever season he first appears in.

    Everything else is a career the window did not touch, and there the note
    answers a question nobody asked: the table holds every dollar he was paid.

    The season the file opens in is read off the data, not named here, so a file
    that gains an older season moves this test with it.
    """
    if not idx.seasons or not ident.records:
        return True
    opens = idx.seasons[0]
    if ident.records[0]["season"] == opens:
        return True
    first_draft_in_window = int(str(opens).split("-")[0])
    drafted = [r.get("draft_year") for r in ident.records if r.get("draft_year")]
    return bool(drafted) and min(drafted) < first_draft_in_window


def player_page(idx, ident, season_table_html, facts, related, linker=None,
                timeline=()):
    title = C.TITLES["player"][0].format(name=ident.name)
    first, last = ident.records[0], ident.records[-1]
    paid = [r for r in ident.records if _paid(idx, r)]
    best = max(paid, key=lambda r: r.get("salary") or 0) if paid else None
    one_season = len({r["season"] for r in ident.records}) == 1

    # The marker a name carries to tell two men apart belongs where a reader is
    # choosing between them, which is the heading, the title and the breadcrumb.
    # A sentence has already made the choice: "Josh Davis (1991) salary history"
    # reads as a filing reference rather than as prose.
    name = plain_name(ident.name)

    # One season is not a history to walk through, so it is named rather than
    # ranged over: "salary history in 2026-27", not "season by season, from
    # 2026-27 to 2026-27". The sentence is the page's lede and its meta
    # description both, so the fix reaches the search result as well as the page.
    if one_season:
        bits = ["{} salary history in {}.".format(name, first["season"])]
    else:
        bits = ["{} salary history, season by season, from {} to {}.".format(
            name, first["season"], last["season"])]
    # Nor is one season a career to pick a best from. "His biggest paid season
    # is $28,834 in 2013-14" on a page whose table holds that one row names a
    # winner of a field of one, in the same breath as the sentence before it.
    if best and best.get("salary") and not one_season:
        bits.append("His biggest paid season is {} in {}.".format(
            money_short(best["salary"]), best["season"]))
    description = " ".join(bits)

    body = [
        "<h1>{}</h1>".format(esc(ident.name)),
        '<p class="hm-lede">{}</p>'.format(esc(description)),
    ]
    # The note tells a reader what the file cannot show him. Under a career that
    # began well inside the window it shows him nothing: every dollar the man
    # was paid is in the table above it. It stays on every other page, where the
    # tables rank men whose careers the window did cut.
    if _career_may_predate_the_data(idx, ident):
        body.append(scope_line())
    body.append(section(
        "Season by season",
        "Salary, share of the cap, league and team rank, and what the "
        "contract carries beyond this season.",
        season_table_html,
    ))
    if facts:
        body.append(section(
            "What the numbers say",
            None,
            facts_summary(facts, linker, ident.url),
        ))
    if timeline:
        body.append(section(
            "Season by season, what changed",
            "Only the seasons that changed something, newest first.",
            timeline_list(timeline, linker, ident.url),
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

    # The money is what this team carries, so the share of the cap has to be
    # that money's share. Reading it off salary_cap_pct put a two-team man's
    # whole-season percentage beside one team's part of him: Kentavious
    # Caldwell-Pope's $3.9 million on these books read as 13% of the cap,
    # which is what his $21.6 million season is worth, not what Philadelphia
    # pays him.
    cap = ((idx.cap or {}).get(idx.current_season) or {}).get("cap") or 0
    current_rows = []
    for record in sorted(current, key=lambda r: -(r.get("salary") or 0)):
        ident = owners.get(id(record))
        if ident is None:
            continue
        share = next((a for c, a in F.team_amounts(record) if c == code), None)
        current_rows.append([
            player_link(ident),
            money(share),
            _share(share, cap),
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
            "Most career earnings with the {}".format(entity.name),
            _with_team_hint(idx),
            rank_table(
                [("Player", "hm-who"), ("Seasons", "hm-word"),
                 ("Earned with the team", "hm-money")],
                _with_team_rows(idx, entity, owners, code, C.TABLE_ROWS),
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
            cap_pct(record.get("salary_cap_pct")),
            str(record.get("age") or "-"),
        ])

    payroll_groups = _payroll_groups(idx, entity, owners, season, contracted)

    body = [
        "<h1>NBA salaries {}</h1>".format(esc(season)),
        '<p class="hm-lede">{}</p>'.format(esc(description)),
        scope_line(),
        section(
            "Biggest salaries",
            "Top {} of {} players on file for {}.".format(
                min(C.TABLE_ROWS, len(ordered)), len(ordered), season),
            rank_table(
                [("Player", "hm-who"), ("Team", "hm-word"), ("Salary", "hm-money"),
                 ("% of cap", "hm-num"), ("Age", "hm-num")],
                rows,
            ),
        ),
        section(
            "Team payrolls",
            "What each franchise carried in {}, ranked, and the roster inside "
            "it ranked by what each man was paid.".format(season),
            grouped_rank_table(
                [("Team", "hm-who"), ("Payroll", "hm-money"),
                 ("% of cap", "hm-num")],
                payroll_groups,
            ),
        ),
        section(
            "Every player",
            "{} on file for {}.".format(len(entity.players), season),
            roll_call([(p.name, p.slug, None) for p in entity.players], "player"),
        ),
    ]
    return title, description, "\n".join(body)


def _with_team_hint(idx):
    """What the career-earnings table counts, said before the table says it.

    The season being played is in these totals, which is the convention the
    career-earnings claims use, so the line says so rather than leaving a
    reader to work out why a man with one season on the books is in the top
    ten. The season is the one the data puts us in, never a year typed in here.
    """
    lead = ""
    if idx.current_season and idx.current_season_in_progress:
        lead = "Includes {}, the season in progress. ".format(
            idx.current_season)
    return lead + (
        "Money already paid, so a contracted season is never in it. Split "
        "seasons are left out: a salary spread over more than one team is a "
        "cap-sheet allocation, not money one franchise paid a player to play "
        "for it."
    )


def _with_team_rows(idx, entity, owners, code, limit):
    """Who has earned the most on one franchise's books, most first.

    The same money the career-earnings claims count: what has been paid, so a
    contracted season is never in it, and no split season either, because a
    salary spread over two teams is an allocation rather than money this one
    paid him. Ties share a rank and read alphabetically, which is what sorting
    on the name inside the total does.
    """
    earned, seasons, who = {}, {}, {}
    for record in entity.records:
        if idx.is_contracted(record["season"]) or F.is_split_season(record):
            continue
        ident = owners.get(id(record))
        if ident is None:
            continue
        for team, amount in F.team_amounts(record):
            if team != code:
                continue
            earned[ident.key] = earned.get(ident.key, 0) + (amount or 0)
            seasons.setdefault(ident.key, set()).add(record["season"])
            who[ident.key] = ident

    table = sorted(earned.items(), key=lambda kv: (-kv[1], who[kv[0]].name))
    rows, place = [], 0
    for i, (key, total) in enumerate(table[:limit]):
        if i == 0 or total != table[i - 1][1]:
            place = i + 1
        mine = sorted(seasons[key], key=F.season_key)
        # One season is a season, not a span: "1, 2026-27 to 2026-27" says it
        # twice and means it once.
        span = season_span(mine[0], mine[-1])
        rows.append([
            player_link(who[key], rank=place),
            "{}, {}".format(len(mine), span),
            money(total),
        ])
    return rows


def _payroll_groups(idx, entity, owners, season, contracted):
    """Every franchise that season, ranked, with its roster ranked inside it.

    A team's payroll is what its own rows add up to, so a split season counts
    on each team only for the part team_amounts gives it, and a season the
    build cannot divide counts on neither. The share of the cap is the same
    figure for a team as for a player: what it took of that season's cap.
    """
    cap = ((idx.cap or {}).get(season) or {}).get("cap") or 0
    payrolls = collections.defaultdict(float)
    rosters = collections.defaultdict(list)
    for record in entity.records:
        for code, amount in F.team_amounts(record):
            if code not in idx.franchises:
                continue
            payrolls[code] += amount or 0
            rosters[code].append((amount or 0, record))

    groups = []
    ranked = sorted(payrolls.items(), key=lambda kv: (-kv[1], kv[0]))
    for place, (code, total) in enumerate(ranked, start=1):
        lead = [
            '<span class="hm-rank">{}</span><a href="{}">{}</a>'.format(
                place, esc(page_url("team", _team_slug(code))),
                esc(idx.franchises[code]["name"])),
            money(total),
            _share(total, cap),
        ]
        members = []
        inside = sorted(rosters[code], key=lambda pair: -pair[0])
        for seat, (amount, record) in enumerate(inside, start=1):
            ident = owners.get(id(record))
            if ident is None:
                continue
            members.append([
                player_link(ident, rank=seat,
                            tag=CONTRACTED_TAG if contracted else ""),
                money(amount),
                _share(amount, cap),
            ])
        groups.append((lead, members))
    return groups


def _share(amount, cap):
    """What a figure took of a season's cap, where the cap is on file."""
    if not cap or not amount:
        return "-"
    return cap_pct(100.0 * amount / cap)


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

# --------------------------------------------------------------------------
# the award-drought pages
# --------------------------------------------------------------------------

def drought_page(idx, built, key, linker=None):
    """One list: who leads it now, and who has led it.

    The standings are the career-earnings convention the engine's own claims
    use: money already paid, the season being played included, a contracted
    season never in it.
    """
    lst = built[key]
    spec = lst["spec"]
    heading = spec.title_phrase
    title = "{} | HoopsMatic".format(heading)
    leader = lst["standing"][0][1] if lst["standing"] else ""
    description = (
        "{}, paid to date. {} leads on {}.".format(
            heading, leader, money_short(lst["standing"][0][0]))
        if leader else "{}.".format(heading)
    )

    rows = []
    place = 0
    for i, (paid, player) in enumerate(lst["standing"][:C.PAGE_DROUGHT_ROWS]):
        if i == 0 or paid != lst["standing"][i - 1][0]:
            place = i + 1
        seasons = sorted({r["season"] for r in idx.records
                          if idx.canonical(r["player"]) == player
                          and not idx.is_contracted(r["season"])},
                         key=F.season_key)
        here = idx.record(player, idx.current_season) is not None
        rows.append([
            _drought_name(idx, player, place),
            "{}, {}".format(len(seasons), season_span(seasons[0], seasons[-1])),
            money(paid),
            "active" if here else "",
        ])

    held = []
    for reign in lst["reigns"]:
        span = season_span(reign["from"], reign["to"])
        if reign["opening"]:
            why = "led when the count begins in {}".format(F.SCOPE_FIRST_SEASON)
        elif reign["why"] == "selected":
            why = "left on {} in {}".format(
                "his first MVP" if spec.won else "his first {}".format(spec.selection),
                reign["detail"])
        elif reign["why"] == "passed":
            why = "passed by {}".format(reign["detail"])
        else:
            why = "still leads"
        held.append([_drought_name(idx, reign["player"]), span,
                     money(reign["total"]), why])

    others = [other for other in C.DROUGHT_PAGES if other != key]
    body = [
        "<h1>{}</h1>".format(esc(heading)),
        summary_block([
            "{} has earned {}, more than any other player {}.".format(
                leader, money_short(lst["standing"][0][0]), spec.singular),
            "A player leaves the list from the season of his first selection, "
            "so these are careers counted while the award had not come.",
        ], linker=linker) if leader else "",
        section(
            "The list",
            "Money already paid, {} included. A contracted season is never "
            "in it.".format(idx.current_season),
            rank_table(
                [("Player", "hm-who"), ("Seasons", "hm-word"),
                 ("Career earnings", "hm-money"), ("", "hm-word")],
                rows,
            ),
        ),
        section(
            "Who held No. 1",
            None,
            rank_table(
                [("Player", "hm-who"), ("Seasons led", "hm-word"),
                 ("Earned by then", "hm-money"), ("How it ended", "hm-text")],
                held,
            ) + _drought_notes(),
        ),
        section("More", None, links_row([
            (C.DROUGHT_LABELS[other], "{}/{}/".format(C.TOOL_ROOT, built[other]["spec"].slug))
            for other in others
        ])),
        scope_line(),
    ]
    return title, description, "\n".join(b for b in body if b)


def _drought_notes():
    """The two things a reader would otherwise read as errors."""
    return (
        '<p class="hm-note">{}</p><p class="hm-note">{}</p>'.format(
            esc("Salary data starts in {}, so the earliest leaders reflect "
                "where the count begins.".format(F.SCOPE_FIRST_SEASON)),
            esc("When the leader leaves through a selection, the next man up "
                "can have earned less than he had."),
        )
    )


def _drought_name(idx, player, rank=None):
    """His name, linked to his page where he has one."""
    ident = _DROUGHT_IDENTS.get(player)
    if ident is not None:
        return player_link(ident, rank=rank)
    return "{}{}".format(rank or "", esc(player))


_DROUGHT_IDENTS = {}


def register_drought_idents(identities):
    _DROUGHT_IDENTS.clear()
    for ident in identities:
        if ident.slug:
            _DROUGHT_IDENTS.setdefault(ident.name, ident)


def drought_hub(built):
    title = "Most NBA Career Earnings Without an Award | HoopsMatic"
    lead = built["all-star"]["standing"]
    description = (
        "The NBA's biggest career earners among players who have not made "
        "an All-Star team, have not made an All-NBA team, or have not won "
        "MVP. {} leads on {}.".format(lead[0][1], money_short(lead[0][0]))
        if lead else
        "The NBA's biggest career earners among players an award has not "
        "come to."
    )
    rows = []
    for key in C.DROUGHT_PAGES:
        lst = built[key]
        leader = lst["standing"][0][1] if lst["standing"] else ""
        rows.append((C.DROUGHT_LABELS[key], lst["spec"].slug, leader,
                     lst["standing"][0][0] if lst["standing"] else 0))
    body = [
        "<h1>Most career earnings without an award</h1>",
        summary_block([
            "Three lists of career earnings among players an award has not "
            "come to. A player leaves a list from the season of his first "
            "selection.",
        ]),
        section("The lists", None, rank_table(
            [("List", "hm-who"), ("Leads it now", "hm-word"),
             ("On", "hm-money")],
            [['<a class="hm-inline-link" href="{}/{}/">{}</a>'.format(
                C.TOOL_ROOT, slug, esc(label)), esc(leader), money(paid)]
             for label, slug, leader, paid in rows],
        )),
        scope_line(),
    ]
    return title, description, "\n".join(body)
