"""What a page is allowed to say, on top of what the engine wrote.

scripts/factoids.py writes one sentence per claim and feeds the Slack digest,
so its text stays as it is. A page is different: it shows many claims at once,
where two problems appear that a single sentence never has.

Mirrors. The same pair of salaries gets a claim from each side, "A is second
behind B" and "B is first ahead of A". Read together they stutter, so one goes.

Backwards comparisons. A claim about 2023-24 can name a holder whose figure is
from 2024-25, and "passing" a number nobody has reached yet is nonsense. The
verb is swapped for a plain comparison rather than the claim being dropped.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import factoids as F  # noqa: E402


def universe_of(key):
    """The comparison a claim was made inside, without its subject.

    ``cohort_season|college|Kansas|Joel Embiid|2026-27`` is the Kansas
    single-season list; ``franchise|PHI|...`` is the 76ers list.
    """
    parts = key.split("|")
    return "|".join(parts[:-2]) if len(parts) > 2 else key


def _holder(fact):
    holder = fact.get("previous_holder") or {}
    if holder.get("season") is None or holder.get("value") is None:
        return None
    return (holder["season"], holder["value"])


def points_forward(fact, season):
    """True when the figure being compared against is from a later season."""
    holder = _holder(fact)
    if holder is None:
        return False
    return F.season_key(holder[0]) > F.season_key(season)


#: Verbs that assert the subject went past the other figure. None of them can
#: be true of a season that has not happened yet.
_FORWARD_SWAPS = (
    ("breaking his own mark of", "ahead of his own"),
    ("passing his own mark of", "ahead of his own"),
    ("passing", "ahead of"),
    ("breaking", "ahead of"),
)


def straighten(fact, season):
    """The fact's text, with a forward-pointing comparison made plain."""
    text = fact["text"]
    if not points_forward(fact, season):
        return text
    for verb, plain in _FORWARD_SWAPS:
        if verb in text:
            text = text.replace(verb, plain, 1)
            # "ahead of his own $23 million in 2024-25" reads with no "mark of"
            break
    return text


def drop_mirrors(entries):
    """One of each mirrored pair, keeping the bigger figure.

    ``entries`` is [(season, fact)]. Two facts mirror when each names the
    other's season and figure as the one it is measured against, inside the
    same comparison.
    """
    by_universe = {}
    for i, (season, fact) in enumerate(entries):
        by_universe.setdefault(universe_of(fact["key"]), []).append((i, season, fact))

    drop = set()
    for rows in by_universe.values():
        for a in range(len(rows)):
            ia, season_a, fact_a = rows[a]
            if ia in drop:
                continue
            for b in range(a + 1, len(rows)):
                ib, season_b, fact_b = rows[b]
                if ib in drop:
                    continue
                if _holder(fact_a) != (season_b, fact_b.get("value")):
                    continue
                if _holder(fact_b) != (season_a, fact_a.get("value")):
                    continue
                # the record side survives, the runner-up side goes
                loser = ib if (fact_a.get("value") or 0) >= (fact_b.get("value") or 0) else ia
                drop.add(loser)
    return [row for i, row in enumerate(entries) if i not in drop]
