"""A player page's season paragraphs: what the numbers say, in prose.

The engine writes one sentence per claim, which is right for a digest and wrong
for a page: a man's 2026-27 can carry five claims about the same career total,
and five sentences all opening with his name is a list, not a paragraph.

So this module reads the structured side of each claim (its value, rank, field
and the holder it measures against) and writes the season back out:

- the three most notable claims, records first, then milestones, then where he
  moved in a ranking;
- claims that share a figure said once, with their comparisons listed;
- a claim about a college position dropped where the same claim holds for the
  whole college, because "the most of any Duke guard" says nothing once he is
  the most of any Duke player;
- his name once, at the top, and "he" after that.

Everything here is derived from the claims the engine already allowed. Nothing
new is asserted: a comparison this module cannot phrase is left as the sentence
the engine wrote.
"""

from __future__ import annotations

import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import factoids as F  # noqa: E402

#: Claims per season. Three is a paragraph; the fourth is a list.
FACTS_PER_SEASON = 3

#: Most notable first. A record is what happened; a milestone is what it
#: reached; a rank shift is where it put him.
TYPE_ORDER = {"sets": 0, "ties": 1, "milestone": 2, "rank_shift": 3,
              "approaches": 4}

#: The two measures a claim can be about. Facts only merge inside one of them:
#: a season salary and a career total are different numbers even when they are
#: equal.
SEASON_SALARY = "season"
CAREER_TOTAL = "career"


def paragraphs(idx, name, rows, linker=None, url=None):
    """[(season, season_key, [sentence, ...])], newest season first.

    ``rows`` is [(season, fact)] for one man, already filtered for mirrors and
    for what an older season is still worth saying.
    """
    by_season = collections.defaultdict(list)
    for season, fact in rows:
        by_season[season].append(fact)

    out, openings = [], _Openings()
    for season in sorted(by_season, key=F.season_key, reverse=True):
        facts = _choose(idx, by_season[season])
        if not facts:
            continue
        sentences = _write(idx, name, season, facts, openings)
        if sentences:
            out.append((season, F.season_key(season), sentences))
    return out


# -- choosing ---------------------------------------------------------------

def _choose(idx, facts):
    """The three most notable claims of one season, redundancy removed."""
    kept = _drop_covered_college_positions(facts)
    kept.sort(key=_notability)
    return kept[:FACTS_PER_SEASON]


def _notability(fact):
    """Records, then milestones, then rank shifts; inside a tier, the widest
    field first, because being first of 400 beats being first of 15."""
    return (
        TYPE_ORDER.get(fact.get("type"), 9),
        -(fact.get("comparison_size") or 0),
        fact.get("rank") or 99,
        fact.get("key") or "",
    )


#: Claims where one field contains another, so holding the wider one says the
#: narrower one too: every Duke guard is a Duke player, every top-10 pick is a
#: lottery pick, and every European player is an international one. Only for a
#: record: being fifth among lottery picks says nothing about the top-10 list.
def _wider_of(kind, key):
    """The (kind, key) whose record would already cover this one, or None."""
    if kind == "college_position":
        return "college", (key or "").split("|")[0]
    if kind == "pick_range" and key == "top-10":
        return "pick_range", "lottery"
    if kind == "region" and key != "international":
        return "region", "international"
    return None


def _drop_covered_college_positions(facts):
    """"The most of any Duke guard" goes when he is the most of any Duke player.

    The wider claim contains the narrower one, so the narrower one is only
    worth a page's space where the wider one was not made.
    """
    held = {
        (_measure(f), f.get("type"), _cohort(f))
        for f in facts if f.get("type") in ("sets", "ties")
    }
    out = []
    for fact in facts:
        wider = _wider_of(*_cohort(fact))
        if wider and fact.get("type") in ("sets", "ties") \
                and (_measure(fact), fact.get("type"), wider) in held:
            continue
        out.append(fact)
    return out


def _cohort(fact):
    """(kind, key) for a cohort claim, ("franchise", code) for a franchise one,
    (None, None) for anything else."""
    parts = (fact.get("key") or "").split("|")
    if parts and parts[0] in ("cohort_season", "cohort_career") and len(parts) > 4:
        # The last two segments are the player and the season; everything
        # between the kind and them is the cohort key, which for a college
        # position is "Duke|G" and so carries a separator of its own.
        return parts[1], "|".join(parts[2:-2])
    if parts and parts[0] == "franchise" and len(parts) > 1:
        return "franchise", parts[1]
    return None, None


def _measure(fact):
    """Which number a claim is about."""
    key = (fact.get("key") or "")
    if key.startswith("cohort_career") or key.startswith("career_rank"):
        return CAREER_TOTAL
    if key.startswith("career_milestone") or key.startswith("milestone"):
        return CAREER_TOTAL
    return SEASON_SALARY


# -- writing ----------------------------------------------------------------

def _write(idx, name, season, facts, openings):
    """One season's paragraph: merged sentences, his name once, no repeats."""
    groups = _group_by_number(facts)
    sentences, named = [], False
    for measure, value, members in groups:
        subject = name if not named else "he"
        text = _sentence(idx, name, subject, season, measure, value, members,
                         openings, first=not named)
        if not text:
            continue
        named = True
        text = _cap(text)
        openings.seen.add(_opening(text))
        sentences.append(text)
    return sentences


def _group_by_number(facts):
    """[(measure, value, [fact, ...])] in notability order.

    Two claims merge when they are about the same measure at the same figure,
    which is what makes "the most of any Duke player and third in the 2011
    draft class" one sentence instead of two.
    """
    order, groups = [], {}
    for fact in facts:
        key = (_measure(fact), _round(fact.get("value")))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(fact)
    return [(measure, value, groups[(measure, value)])
            for measure, value in order]


def _round(value):
    return round(value or 0, 2)


def _sentence(idx, name, subject, season, measure, value, members, openings,
              first):
    """One sentence for one figure, carrying every comparison made about it."""
    clauses = [c for c in (_clause(idx, f) for f in members) if c]
    if not clauses:
        # Nothing this module can phrase: the engine's own sentence stands,
        # with his name cut to "he" where he has already been named.
        return _fallback(name, members[0], first)
    body = _join(clauses)
    if measure == CAREER_TOTAL:
        return _career_lead(idx, subject, season, value, openings, first) + \
            ", " + body + "."
    return _salary_lead(idx, subject, season, value, openings, first) + \
        ", " + body + "."


def _career_lead(idx, subject, season, value, openings, first):
    """"Kyrie Irving will have earned $391.9 million by the end of 2026-27"."""
    money = F.fmt_money(value)
    tense = F.season_tense(idx, season)
    if tense == F.PAST:
        shapes = [
            "{s} had earned {m} through {season}",
            "Through {season} {s} had earned {m}",
            "By the end of {season} {s} had earned {m}",
            "{p} career earnings stood at {m} through {season}",
        ]
    elif tense == F.CURRENT and idx.current_season_in_progress:
        shapes = [
            "{s} will have earned {m} by the end of {season}",
            "By the end of {season} {s} will have earned {m}",
            "{season} takes {o} to {m} in career earnings",
        ]
    else:
        shapes = [
            "{s} has earned {m} through {season}",
            "Through {season} {s} has earned {m}",
        ]
    return _pick_shape(shapes, openings, group="career|" + tense,
                       **_cases(subject, money, season))


def _salary_lead(idx, subject, season, value, openings, first):
    """"Kyrie Irving was paid $38 million in 2023-24"."""
    money = F.fmt_money(value)
    tense = F.season_tense(idx, season)
    if tense == F.PAST:
        shapes = [
            "{s} was paid {m} in {season}",
            "In {season} {s} was paid {m}",
            "{p} {season} salary was {m}",
            "{s} drew {m} in {season}",
        ]
    elif tense == F.FUTURE:
        shapes = [
            "{s} is due {m} in {season}",
            "In {season} {s} is due {m}",
            "{season} carries {m} for {o}",
            "{s} is on the books for {m} in {season}",
        ]
    else:
        shapes = [
            "{s} is on {m} this season",
            "{s} is paid {m} in {season}",
            "{p} {season} salary is {m}",
        ]
    return _pick_shape(shapes, openings, group="salary|" + tense,
                       **_cases(subject, money, season))


class _Openings(object):
    """What a page has already said, so the next sentence says it differently.

    Two rules, because two things repeat. The literal opening of a sentence is
    held once: no page says "He is on" twice. And the shape is taken in turn
    rather than greedily, because eight seasons all reading "In 2019-20 he was
    paid ..." differ in their first two words and repeat all the same.
    """

    __slots__ = ("seen", "turns")

    def __init__(self):
        self.seen = set()
        self.turns = {}

    def next_turn(self, group, count):
        n = self.turns.get(group, 0)
        self.turns[group] = n + 1
        return n % count


def _pick_shape(shapes, openings, group="", **fields):
    """The shape whose turn it is, moved on if the page has used its opening."""
    start = openings.next_turn(group, len(shapes))
    for step in range(len(shapes)):
        shape = shapes[(start + step) % len(shapes)]
        text = shape.format(**fields)
        if _opening(_cap(text)) not in openings.seen:
            return text
    return shapes[start].format(**fields)


def _opening(text):
    return " ".join(text.split()[:2]).strip(",.").lower()


def _cases(subject, money, season):
    """The three forms a shape may need him in: subject, object, possessive."""
    if subject == "he":
        return {"s": "he", "o": "him", "p": "his", "m": money, "season": season}
    possessive = subject + ("'" if subject.endswith("s") else "'s")
    return {"s": subject, "o": subject, "p": possessive,
            "m": money, "season": season}


def _cap(text):
    """A sentence opening with "he" is still a sentence opening."""
    return text[:1].upper() + text[1:] if text else text


def _fallback(name, fact, first):
    """The engine's sentence, with the repeated name taken out.

    The engine opens most claims with the man's name, either bare ("Kyrie
    Irving passed $250 million") or possessive ("Kyrie Irving's $38 million").
    Both become "he" once the paragraph has named him.
    """
    text = fact.get("text") or ""
    if first or not text or not name:
        return text
    if text.startswith(name + "'s "):
        return "His " + text[len(name) + 3:]
    if text.startswith(name + "' "):
        return "His " + text[len(name) + 2:]
    if text.startswith(name + " "):
        return "He " + text[len(name) + 1:]
    return text


# -- the comparison clauses -------------------------------------------------

def _clause(idx, fact):
    """"the most of any Duke player", "third in the 2011 draft class behind X"."""
    kind, key = _cohort(fact)
    where_one, where_many = _where(idx, kind, key)
    if where_one is None:
        return None
    rank, kind_of = fact.get("rank"), fact.get("type")
    holder = fact.get("previous_holder") or {}
    who = holder.get("player")
    if kind_of == "sets" or rank == 1:
        return "the most {}".format(where_one)
    if kind_of == "ties":
        return "level with the most {}".format(where_one)
    if not rank:
        return None
    text = "{} {}".format(F.ordinal(rank), where_many)
    if who:
        text += " behind {}".format(who)
    return text


def _where(idx, kind, key):
    """(the "of any X" form, the "among Xs" form) for one comparison field."""
    if kind == "college":
        college = idx.college_display(key)
        return "of any {} player".format(college), "among {} players".format(college)
    if kind == "college_position":
        college, position = (key.split("|") + [""])[:2]
        noun = F.position_group(position)[1] or "player"
        college = idx.college_display(college)
        return ("of any {} {}".format(college, noun),
                "among {} {}s".format(college, noun))
    if kind == "draft_class":
        return ("in the {} draft class".format(key),
                "in the {} draft class".format(key))
    if kind == "draft_slot":
        if key == "undrafted":
            return "of any undrafted player", "among undrafted players"
        return "of any No. {} pick".format(key), "among No. {} picks".format(key)
    if kind == "position":
        noun = F.position_group(key)[1]
        if not noun:
            return None, None
        return "of any {}".format(noun), "among {}s".format(noun)
    if kind == "nationality":
        article = "the " if key in F.NATIONALITY_TAKES_THE else ""
        return ("of any player from {}{}".format(article, key),
                "among players from {}{}".format(article, key))
    if kind == "region":
        phrases = F.REGION_PHRASES.get(key)
        if not phrases:
            return None, None
        return ("of any {}".format(phrases["one"]),
                "among {}".format(phrases["many"]))
    if kind == "pick_range":
        label = {"top-10": "top-10 pick", "lottery": "lottery pick",
                 "second-round": "second-round pick"}.get(key)
        if not label:
            return None, None
        return "of any {}".format(label), "among {}s".format(label)
    if kind == "franchise":
        team = (idx.franchises.get(key) or {}).get("name")
        if not team:
            return None, None
        return ("in {} history".format(team), "in {} history".format(team))
    return None, None


def _join(clauses):
    if len(clauses) == 1:
        return clauses[0]
    return "{} and {}".format(", ".join(clauses[:-1]), clauses[-1])
