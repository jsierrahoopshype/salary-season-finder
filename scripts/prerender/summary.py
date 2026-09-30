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


#: The same group in the singular, for "more than any Duke player has earned".
SUMMARY_NOUN_ONE = {
    "college": "{name} player",
    "country": "player from {name}",
    "draft": "player from the {name} draft class",
    "pick": "No. {name} pick",
    "position": "{name_one}",
    "agent": "client of {name}",
}


def group_noun(family, key, name):
    if family == "pick" and key == "undrafted":
        return "undrafted players"
    return SUMMARY_NOUN[family].format(name=name, name_lower=name.lower())


def group_noun_one(family, key, name):
    if family == "pick" and key == "undrafted":
        return "undrafted player"
    lower = name.lower()
    return SUMMARY_NOUN_ONE[family].format(
        name=name, name_lower=lower,
        name_one=lower[:-1] if lower.endswith("s") else lower,
    )


class _CareerField(object):
    """The career table as a universe, so the engine's own pre-window guard
    can read it. One attribute is all ``pre_window_shadow`` touches."""

    def __init__(self, entries):
        self.entries = entries


def _as_universe(idx, career):
    return _CareerField([
        {"value": total, "pre_window": idx.pre_window_career(ident.data_key),
         "display": ident.name}
        for total, ident, _last in career
    ])


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
    one = group_noun_one(family, key, name)
    entries = drop_mirrors(list(facts))
    out = []
    scoped = False

    # ---- who has earned the most -----------------------------------------
    #
    # Money already paid, active men included. A career that began before the
    # data did is short of its first seasons, so where one of those sits close
    # enough to the leader to be wrong, the sentence is not written at all.
    shadowed = bool(career) and F.pre_window_shadow(
        _as_universe(idx, career), career[0][0])
    if career and not shadowed:
        total, ident, last = career[0]
        active = not idx.career_complete(ident.data_key)
        played = sum(1 for r in ident.records if not idx.is_contracted(r["season"]))
        clause = ""
        if len(career) > 1:
            clause = _ratio_clause(total, career[1][0])
            if clause:
                clause += "what any other {} has earned.".format(one)
        if active:
            out.append(
                "{} has earned more than any other {}, {} across {} seasons to date.{}".format(
                    ident.name, one, _money(total), played, clause
                )
            )
        else:
            out.append(
                "{} leads all {} with {} across {} seasons.{}".format(
                    ident.name, noun, _money(total), played, clause
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
            clause += "what any other {} has been paid for a season.".format(one)
        if scoped and career and record_ident is career[0][1]:
            out.append(
                "His {} in {} is also the biggest single season on the list.{}".format(
                    _money(record.get("salary")), record["season"], clause
                )
            )
        else:
            out.append(
                "The biggest single season belongs to {}, {} in {}.{}".format(
                    record_ident.name, _money(record.get("salary")),
                    record["season"], clause
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
                    "In {} he is still the best-paid {} in the league, at {}.".format(
                        idx.current_season, one, _money(top.get("salary"))
                    )
                )
            else:
                out.append(
                    "In {} the highest-paid {} is {} at {}.".format(
                        idx.current_season, one, top_ident.name,
                        _money(top.get("salary"))
                    )
                )

    # ---- what is still owed ----------------------------------------------
    if future:
        tail = ""
        if future["tops"]:
            tail = ", each more than any {} has earned in a season".format(one)
            if len(future["rows"]) == 1:
                tail = ", more than any {} has earned in a season".format(one)
        out.append(
            "{} contract would pay him {}{}.".format(
                _possessive(future["name"]), _merge_seasons(future["rows"]), tail
            )
        )

    # A cohort whose leader started before the data did gets no career
    # sentence, so it gets the reason instead: with one sentence left the page
    # reads as if there were nothing to say.
    if shadowed and idx.pre_window_career(career[0][1].data_key) and len(out) < 2:
        out.append(
            "The biggest careers here began before the salary data does, so no "
            "career total on this page is the whole of what the man earned."
        )
    return out[:C.SUMMARY_SENTENCES]


def _possessive(name):
    return name + "'" if name.endswith("s") else name + "'s"
