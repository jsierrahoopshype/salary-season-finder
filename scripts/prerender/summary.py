"""The written summary at the top of a cohort page.

The engine's sentences are built one claim at a time, which is right for a
Slack digest and wrong for a page: eight of them in a list repeat each other
and bury the three things a reader came for. This writer takes the same
numbers, picks those three, and writes them as prose.

Order is fixed: who has earned the most, whose single season is the biggest,
and what the money still owed would do. A claim appears once, from one side
only, and a season that has not been played is always conditional.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import factoids as F  # noqa: E402

from . import config as C  # noqa: E402
from .phrasing import drop_mirrors  # noqa: E402

#: How the group is named inside a sentence. The page heading's noun reads
#: badly after "leads all", so each family gets a plural that does.
SUMMARY_NOUN = {
    "college": "{name} players",
    "country": "players from {name}",
    "draft": "players from the {name} draft class",
    "pick": "No. {name} picks",
    "position": "{name_lower}",
    "agent": "clients of {name}",
}

#: A ratio is worth printing only when it is this lopsided.
RATIO_MIN = 2.0


def group_noun(family, key, name):
    if family == "pick" and key == "undrafted":
        return "undrafted players"
    return SUMMARY_NOUN[family].format(name=name, name_lower=name.lower())


def _money(value):
    return F.fmt_money(value)


def _ratio_clause(top, rival):
    """Only where the gap tells the reader something on its own."""
    if not rival or not top or rival <= 0:
        return ""
    factor = top / float(rival)
    if factor >= 3:
        return " That is more than three times "
    if factor >= RATIO_MIN:
        return " That is more than double "
    return ""


def _merge_seasons(rows):
    """"$18.1 million in 2027-28 and $19 million in 2028-29"."""
    parts = ["{} in {}".format(_money(value), season) for season, value in rows]
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return "{} and {}".format(*parts)
    return "{} and {}".format(", ".join(parts[:-1]), parts[-1])


def _future_claims(idx, entries, limit=3):
    """Contracted single-season claims, one player, newest contract first.

    The engine decides what counts as notable: a season only has a claim here
    if it ranks inside the cohort's own list. The figures are the engine's.
    """
    by_subject = {}
    for season, fact in entries:
        if not fact.get("contracted"):
            continue
        if not fact["key"].startswith("cohort_season|"):
            continue
        subject = fact["key"].split("|")[-2]
        by_subject.setdefault(subject, []).append((season, fact))
    if not by_subject:
        return None
    subject = max(
        sorted(by_subject),
        key=lambda s: max(f.get("value") or 0 for _season, f in by_subject[s]),
    )
    rows = sorted(
        {(season, fact.get("value")) for season, fact in by_subject[subject]},
        key=lambda row: F.season_key(row[0]),
    )[:limit]
    tops = any(fact["type"] == "sets" for _season, fact in by_subject[subject])
    name = idx.display_name(subject, rows[0][0]) if rows else subject
    return {"subject": subject, "name": name, "rows": rows, "tops": tops}


def cohort_summary(idx, family, key, name, career, paid, current, facts):
    """Two to four sentences about one cohort.

    ``career`` is [(total, identity, last record)], ``paid`` and ``current``
    are [(record, identity)], all sorted biggest first. ``facts`` is
    [(season, fact)] for this cohort straight from factoids.json.
    """
    noun = group_noun(family, key, name)
    entries = drop_mirrors(list(facts))
    out = []
    scoped = False

    # ---- who has earned the most -----------------------------------------
    if career:
        total, ident, last = career[0]
        clause = ""
        if len(career) > 1:
            clause = _ratio_clause(total, career[1][0])
            if clause:
                clause += "any other finished career in the group."
        out.append(
            "{} leads all {} in career earnings since {}, {} across {} seasons.{}".format(
                ident.name, noun, F.SCOPE_FIRST_SEASON, _money(total),
                len(ident.records), clause
            )
        )
        scoped = True

    # ---- the biggest single season ---------------------------------------
    record_ident = None
    if paid:
        record, record_ident = paid[0]
        rival = next(
            (r.get("salary") for r, i in paid if i is not record_ident), None
        )
        clause = _ratio_clause(record.get("salary"), rival)
        if clause:
            clause += "any other member has been paid for a season."
        since = "" if scoped else " since {}".format(F.SCOPE_FIRST_SEASON)
        if career and record_ident is career[0][1]:
            out.append(
                "His {} in {} is also the biggest single season on the list{}.{}".format(
                    _money(record.get("salary")), record["season"], since, clause
                )
            )
        else:
            out.append(
                "The biggest single season belongs to {}, {} in {}{}.{}".format(
                    record_ident.name, _money(record.get("salary")),
                    record["season"], since, clause
                )
            )

    # ---- who is on the payroll now ---------------------------------------
    future = _future_claims(idx, entries)
    if current:
        top, top_ident = current[0]
        same = (
            record_ident is top_ident
            and paid and paid[0][0]["season"] == top["season"]
        )
        if not same:
            if top_ident is record_ident:
                # already named a sentence ago, so he is "he" here
                out.append(
                    "In {} he is still the best-paid member of the group, at {}.".format(
                        idx.current_season, _money(top.get("salary"))
                    )
                )
            else:
                out.append(
                    "In {} the highest-paid member is {} at {}.".format(
                        idx.current_season, top_ident.name, _money(top.get("salary"))
                    )
                )

    # ---- what is still owed ----------------------------------------------
    if future:
        tail = ""
        if future["tops"]:
            tail = (
                ", each one above anything the group has been paid"
                if len(future["rows"]) > 1
                else ", more than anyone in the group has been paid"
            )
        out.append(
            "{} contract would pay him {}{}.".format(
                _possessive(future["name"]), _merge_seasons(future["rows"]), tail
            )
        )

    return out[:C.SUMMARY_SENTENCES]


def _possessive(name):
    return name + "'" if name.endswith("s") else name + "'s"
