"""What gets built, what gets indexed, and what each page is called.

One block below decides indexability for every family, and the sitemap is built
from it rather than from a second list that could drift.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from factoids import (  # noqa: E402
    AGENT_FACTOIDS_ENABLED,
    AGENT_MIN_CLIENTS,
    COHORT_MINIMUMS,
    DATA_START_NOTE,
    SCOPE_FIRST_SEASON,
)

#: The tool's public home. No trailing slash: the Worker 301s the slash version
#: to this one, so every link and every canonical uses the bare form.
SITE = "https://hoopsmatic.com"
TOOL_ROOT = SITE + "/salary-season-finder"

#: Sibling tools every player page points at.
COMPARE_URL = SITE + "/compare"
CAREER_MAP_URL = SITE + "/nba-career-map/"

#: Files land at <repo>/<path>/index.html, which GitHub Pages serves at
#: <TOOL_ROOT>/<path>/ once the Worker has mapped the directory.
SITEMAP_PATH = "sitemap.xml"
NOT_FOUND_PATH = "404.html"
SLUGS_PATH = os.path.join("data", "slugs.json")
PAGE_HASHES_PATH = os.path.join("data", "page_hashes.json")

#: The one line about the data window on every page. No claim carries the
#: window any more: saying "since 1990-91" in every sentence read as a hedge on
#: figures that are, for everyone being compared, the whole of what he earned.
SCOPE_NOTE = DATA_START_NOTE

#: Longest title we aim for. Past this the template drops its scope clause.
TITLE_TARGET = 65

#: Ranked tables are capped so a page stays readable and small. Every member is
#: still linked from the roll call below the tables, so nothing is unreachable.
TABLE_ROWS = 25

#: Sentences in a cohort page's written summary. Past this it stops being a
#: summary; the tables below it carry the rest.
SUMMARY_SENTENCES = 4


#: The one block that decides what is indexable.
#:
#: ``indexable``  robots meta and, with it, sitemap membership.
#: ``cohort``     the factoid engine's cohort kind, so the minimum player count
#:                for a page is the same constant the engine ranks by.
#: ``hub``        the family's index page, or None where the family has no hub.
FAMILIES = {
    "player": {
        "dir": "player", "indexable": False, "cohort": None, "hub": None,
        "label": "Players",
        "label_one": "Player",
    },
    "team": {
        "dir": "team", "indexable": False, "cohort": None, "hub": None,
        "label": "Teams",
        "label_one": "Team",
    },
    # Season pages compete with HoopsHype's own season salary pages, so they
    # stay out of the index. Flip this to True to change that; nothing else has
    # to move, because the sitemap reads this block.
    "season": {
        "dir": "season", "indexable": False, "cohort": None, "hub": None,
        "label": "Seasons",
        "label_one": "Season",
    },
    "college": {
        "dir": "college", "indexable": True, "cohort": "college",
        "hub": "colleges", "label": "Colleges",
        "label_one": "College",
    },
    "country": {
        "dir": "country", "indexable": True, "cohort": "nationality",
        "hub": "countries", "label": "Countries",
        "label_one": "Country",
    },
    "draft": {
        "dir": "draft", "indexable": True, "cohort": "draft_class",
        "hub": "draft-classes", "label": "Draft classes",
        "label_one": "Draft class",
    },
    "pick": {
        "dir": "pick", "indexable": True, "cohort": "draft_slot",
        "hub": "picks", "label": "Draft picks",
        "label_one": "Draft pick",
    },
    "position": {
        "dir": "position", "indexable": True, "cohort": "position",
        "hub": "positions", "label": "Positions",
        "label_one": "Position",
    },
    # The agent field is unverified, which is why the factoid family is off. The
    # pages are built so the hub links somewhere real, but they are noindex and
    # out of the sitemap until the field is trusted.
    "agent": {
        "dir": "agent", "indexable": bool(AGENT_FACTOIDS_ENABLED),
        "cohort": None, "hub": "agents", "label": "Agents",
        "label_one": "Agent",
    },
}

#: Hub slug -> the family it indexes. Hubs are always indexable: they are how a
#: reader and a crawler reach every page in a family in one click.
HUBS = {spec["hub"]: family for family, spec in FAMILIES.items() if spec["hub"]}

#: Minimum distinct players before a cohort gets a page, straight from the
#: engine so a page never exists for a cohort the engine will not rank inside.
def cohort_minimum(family):
    kind = FAMILIES[family]["cohort"]
    if kind is None:
        return 0
    return COHORT_MINIMUMS[kind]


AGENT_MINIMUM = AGENT_MIN_CLIENTS

#: Title templates. Each is (full, short); the short one is used when the full
#: one runs past TITLE_TARGET characters, which happens on the longer college
#: and country names.
TITLES = {
    "player": ("{name}: Salary History | HoopsMatic", None),
    "team": ("{name} Payroll and Salary History | HoopsMatic", None),
    "season": ("NBA Salaries {name} | HoopsMatic", None),
    "college": (
        "Highest-Paid {name} Players in NBA History | HoopsMatic",
        "Highest-Paid {name} Players | HoopsMatic",
    ),
    "country": (
        "Highest-Paid NBA Players from {name} of All Time | HoopsMatic",
        "Highest-Paid NBA Players from {name} | HoopsMatic",
    ),
    "draft": (
        "Highest-Paid Players of the {name} NBA Draft | HoopsMatic",
        "Highest-Paid {name} NBA Draft Picks | HoopsMatic",
    ),
    "pick": (
        "Highest-Paid No. {name} Picks in NBA History | HoopsMatic",
        "Highest-Paid No. {name} Picks | HoopsMatic",
    ),
    "pick_undrafted": (
        "Highest-Paid Undrafted Players in the NBA | HoopsMatic", None,
    ),
    "position": (
        "Highest-Paid NBA {name} of All Time | HoopsMatic",
        "Highest-Paid NBA {name} | HoopsMatic",
    ),
    "agent": ("{name}: NBA Clients and Salaries | HoopsMatic", None),
}

HUB_TITLES = {
    "colleges": "NBA Salaries by College | HoopsMatic",
    "countries": "NBA Salaries by Country | HoopsMatic",
    "draft-classes": "NBA Salaries by Draft Class | HoopsMatic",
    "picks": "NBA Salaries by Draft Pick | HoopsMatic",
    "positions": "NBA Salaries by Position | HoopsMatic",
    "agents": "NBA Salaries by Agent | HoopsMatic",
}

HUB_HEADINGS = {
    "colleges": "NBA salaries by college",
    "countries": "NBA salaries by country",
    "draft-classes": "NBA salaries by draft class",
    "picks": "NBA salaries by draft pick",
    "positions": "NBA salaries by position",
    "agents": "NBA salaries by agent",
}


def title_for(family, name):
    """The page title, preferring the full template while it fits."""
    full, short = TITLES[family]
    text = full.format(name=name)
    if short and len(text) > TITLE_TARGET:
        return short.format(name=name)
    return text
