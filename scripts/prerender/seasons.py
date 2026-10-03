"""A player page's "What the numbers say": one paragraph about his whole career.

The engine writes one sentence per claim, per season, which is right for a
digest and wrong for a page. A man who has been the highest-paid player of his
draft class for six straight seasons gets six near-identical sentences out of
it; a reader wants that said once, with its span, and then wants to know what
he has earned and what is still to come.

So this module reads the structured side of every claim he has and writes two
to four sentences:

1. the record he has held longest, with the seasons it covers;
2. what he will have earned by the end of the season being played, and the
   widest field that total leads;
3. the biggest record still ahead of him, stated conditionally because nobody
   has been paid a contracted salary.

A milestone he passed in a single past season is dropped: the table below
carries the year-by-year detail, and "passed $150 million in 2020-21" is a row
of it rather than a line of prose.

Nothing new is asserted. Every sentence is built from claims the engine already
allowed, and a comparison this module cannot phrase is left out rather than
guessed at.
"""

from __future__ import annotations

import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import factoids as F  # noqa: E402

#: Sentences in a summary. Two is a paragraph, five is the list this replaced.
MIN_SENTENCES = 2
MAX_SENTENCES = 4

#: Seasons a record has to be held for before it is worth a span of its own.
#: One season is a record, not a run.
MIN_SPAN = 2

#: Claims that count as holding a record. A tie is a shared record, which is
#: still being the highest paid.
HOLDING = ("sets", "ties")


def summary(idx, name, rows, player=None):
    """Two to four sentences about one man, or [] where he has nothing.

    ``rows`` is [(season, fact)] for him, already filtered for mirrors, and
    ``player`` is the key the engine files him under, so a man with no claims at
    all still gets the one sentence his career total is worth.
    """
    held = _by_field(idx, rows)
    spans = _spans(idx, held)
    sentences, used = [], set()

    best = _pick_span(spans)
    if best:
        used.add(best["field"])
        sentences.append(_span_sentence(idx, name, best))

    # Each field is named once in the paragraph: "the highest-paid Duke player
    # every season" followed by "more than any other Duke player" is one fact
    # said twice. The record still to come claims its field before the career
    # total does, because a record is the better use of it; the career total
    # then takes the widest field left.
    ahead_text, ahead_field = _future_sentence(idx, rows, used)
    if ahead_field:
        used.add(ahead_field)

    subject = "he" if sentences else name
    total, field = _career_sentence(idx, rows, subject, player, used)
    if total:
        sentences.append(total)
        if field:
            used.add(field)
        subject = "he"

    if ahead_text:
        sentences.append(_future_wording(ahead_text, subject))
        subject = "he"

    # A page with only one thing to say gets a second from the next-widest
    # record he holds, so the section is a paragraph rather than a line.
    while len(sentences) < MIN_SENTENCES:
        nxt = _pick_span(spans, skip=used)
        if not nxt:
            break
        used.add(nxt["field"])
        sentences.append(_span_sentence(idx, name if not sentences else "he", nxt))
    return sentences[:MAX_SENTENCES]


# -- the inventory ----------------------------------------------------------

def _by_field(idx, rows):
    """{(kind, key): {season: fact}} for single-season records he holds."""
    out = collections.defaultdict(dict)
    for season, fact in rows:
        if fact.get("type") not in HOLDING:
            continue
        if _measure(fact) != "season":
            continue
        field = _cohort(fact)
        if field[0] is None:
            continue
        out[field][season] = fact
    return out


def _spans(idx, held):
    """The longest unbroken run of paid seasons in each field.

    Paid seasons only: a contracted season is money nobody has been paid, so a
    record in it cannot be part of "has been the highest-paid ... since".
    """
    out = []
    for field, by_season in held.items():
        seasons = sorted(
            (s for s in by_season if not idx.is_contracted(s)), key=F.season_key)
        for run in _runs(seasons):
            if len(run) < MIN_SPAN:
                continue
            fact = by_season[run[-1]]
            out.append({
                "field": field,
                "seasons": run,
                "open": F.season_key(run[-1]) == idx.current_key,
                "size": fact.get("comparison_size") or 0,
            })
    return out


def _runs(seasons):
    """Consecutive seasons, split where a season is missing."""
    out, run = [], []
    for season in seasons:
        if run and F.season_key(season) != F.season_key(run[-1]) + 1:
            out.append(run)
            run = []
        run.append(season)
    if run:
        out.append(run)
    return out


#: Field sizes a run is sorted into before its length is compared. A ten-season
#: run against 62 player seasons is a smaller fact than a six-season run against
#: 470, and comparing the raw counts would put the narrow one first; comparing
#: the tiers puts the wide fields together and then prefers the longer run.
SIZE_TIERS = (400, 100)


def _tier(size):
    for i, floor in enumerate(SIZE_TIERS):
        if size >= floor:
            return len(SIZE_TIERS) - i
    return 0


def _pick_span(spans, skip=()):
    """The run worth leading on: one still running, against a wide field, held
    for the most seasons."""
    pool = [s for s in spans if s["field"] not in skip]
    if not pool:
        return None
    return max(pool, key=lambda s: (s["open"], _tier(s["size"]),
                                    len(s["seasons"]), s["size"],
                                    str(s["field"])))


# -- the sentences ----------------------------------------------------------

def _span_sentence(idx, subject, span):
    """"... has been the highest-paid No. 15 pick every season since 2021-22." """
    kind, key = span["field"]
    first, last = span["seasons"][0], span["seasons"][-1]
    if kind == "franchise":
        team = (idx.franchises.get(key) or {}).get("name") or key
        # the nickname takes an article: "the Heat's", "the 76ers'"
        who = "the {} highest-paid player".format(_possessive(team))
    else:
        who = "the highest-paid {}".format(_noun(idx, kind, key))
    if span["open"]:
        return "{} has been {} every season since {}.".format(
            _cap(subject), who, first)
    if first == last:
        return "{} was {} in {}.".format(_cap(subject), who, first)
    return "{} was {} in every season from {} to {}.".format(
        _cap(subject), who, first, last)


def _career_sentence(idx, rows, subject, player=None, used=()):
    """(the sentence, the field it used) for what he will have earned."""
    paid, through = _career_total(idx, rows, player)
    if not paid or not through:
        return None, None
    opening = _career_opening(idx, subject, F.fmt_money(paid), through)
    lead = _best_career_claim(rows, used)
    if lead is None:
        return opening + ".", None
    kind, key = _cohort(lead)
    if lead.get("type") in HOLDING:
        # How far clear he is, where the engine found something stronger to say
        # than the name of whoever is second.
        clear = lead.get("lead")
        if clear:
            return "{}, {}.".format(opening, clear), (kind, key)
        return "{}, more than any other {}.".format(
            opening, _noun(idx, kind, key)), (kind, key)
    rank = lead.get("rank")
    if not rank or rank > 5:
        return opening + ".", None
    behind = lead.get("ahead")
    if behind:
        return "{}, the {}-most of any {}, behind {}.".format(
            opening, _word(rank), _noun(idx, kind, key), behind), (kind, key)
    return "{}, the {}-most of any {}.".format(
        opening, _word(rank), _noun(idx, kind, key)), (kind, key)


def _future_sentence(idx, rows, used):
    """(the sentence, minus its subject, the field it used).

    The biggest record still ahead of him, stated conditionally: nobody has been
    paid a contracted salary, so it would be a record rather than is one.
    """
    best = None
    for season, fact in rows:
        if not idx.is_contracted(season):
            continue
        if fact.get("type") not in HOLDING or _measure(fact) != "season":
            continue
        field = _cohort(fact)
        if field[0] is None or field in used:
            continue
        score = (fact.get("comparison_size") or 0, fact.get("value") or 0)
        if best is None or score > best[0]:
            best = (score, season, fact)
    if best is None:
        return None, None
    _score, season, fact = best
    kind, key = _cohort(fact)
    money = F.fmt_money(fact.get("value"))
    if kind == "franchise":
        team = (idx.franchises.get(key) or {}).get("name") or key
        where = "in {} history".format(team)
    else:
        where = "of any {}".format(_noun(idx, kind, key))
    return ("{{}} {} in {} would be the biggest single-season salary {}.".format(
        money, season, where), (kind, key))


def _future_wording(template, subject):
    return template.format(
        "His" if subject == "he" else _possessive(subject))


def _career_opening(idx, subject, money, through):
    """The 2026-27 framing: the season is being played, so it is not earned yet.

    A season under way has months of salary still to be paid, so the total it
    runs through is a figure he will reach rather than one he has.
    """
    tense = F.season_tense(idx, through)
    if tense == F.CURRENT and idx.current_season_in_progress:
        him = "he'll" if subject == "he" else _cap(subject) + " will"
        return "By the end of {} {} have earned {}".format(through, him, money)
    if tense == F.CURRENT:
        return "{} has earned {} through {}".format(_cap(subject), money, through)
    return "{} earned {} through {}".format(_cap(subject), money, through)


def _career_total(idx, rows, player=None):
    """His money already paid, and the season it runs through."""
    if player is None:
        for _season, fact in rows:
            parts = (fact.get("key") or "").split("|")
            if len(parts) > 2:
                player = parts[-2]
                break
    if player is None:
        return None, None
    if not idx.career_rankable(player):
        return None, None
    return idx.paid_through(player)


def _best_career_claim(rows, used=()):
    """The widest field his career total leads, or ranks near the top of."""
    best = None
    for _season, fact in rows:
        if _measure(fact) != "career":
            continue
        if _cohort(fact)[0] is None or _cohort(fact) in used:
            continue
        if fact.get("type") not in HOLDING and (fact.get("rank") or 99) > 5:
            continue
        rank = (fact.get("type") not in HOLDING, fact.get("rank") or 99)
        score = (rank[0], rank[1], -(fact.get("comparison_size") or 0))
        if best is None or score < best[0]:
            best = (score, fact)
    return best[1] if best else None


# -- reading a claim --------------------------------------------------------

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
    if key.startswith("cohort_career") or key.startswith("career_"):
        return "career"
    return "season"


# -- naming a field ---------------------------------------------------------

def _noun(idx, kind, key):
    """The singular a sentence puts after "the highest-paid"."""
    if kind == "college":
        return "{} player".format(idx.college_display(key))
    if kind == "college_position":
        college, position = (key.split("|") + [""])[:2]
        noun = F.position_group(position)[1] or "player"
        return "{} {}".format(idx.college_display(college), noun)
    if kind == "draft_class":
        return "player from the {} draft class".format(key)
    if kind == "draft_slot":
        return "undrafted player" if key == "undrafted" else "No. {} pick".format(key)
    if kind == "position":
        return F.position_group(key)[1] or "player"
    if kind == "nationality":
        article = "the " if key in F.NATIONALITY_TAKES_THE else ""
        return "player from {}{}".format(article, key)
    if kind == "region":
        return (F.REGION_PHRASES.get(key) or {}).get("one") or "player"
    if kind == "pick_range":
        return {"top-10": "top-10 pick", "lottery": "lottery pick",
                "second-round": "second-round pick"}.get(key, "pick")
    if kind == "franchise":
        return (idx.franchises.get(key) or {}).get("name") or key
    return "player"


# -- small helpers ----------------------------------------------------------

WORDS = ("", "", "second", "third", "fourth", "fifth")


def _word(rank):
    return WORDS[rank] if 0 < rank < len(WORDS) else F.ordinal(rank)


def _possessive(name):
    return name + ("'" if name.endswith("s") else "'s")


def _cap(text):
    return text[:1].upper() + text[1:] if text else text
