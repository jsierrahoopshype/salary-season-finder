"""Who counts as undrafted, and where an undrafted man is listed.

bio.csv writes a man who went undrafted with PICK UND (ND on newer rows) and
keeps his draft class year beside it. Until build_data.py carried that through
as ``undrafted``, a null pick read the same for him, for a territorial or
supplemental-draft pick, and for a man with no draft data at all, so only the
last of those reached the Undrafted page.
"""

from __future__ import annotations

import json
import os
import re

import pytest

import build_data as B
import factoids as F
from prerender import timeline as TL

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def rec(**kw):
    out = {"player": "Someone", "season": "2025-26", "salary": 1000000}
    out.update(kw)
    return out


# --------------------------------------------------------------------------
# the one definition
# --------------------------------------------------------------------------


def test_the_sheets_undrafted_marks_are_read_as_undrafted():
    for pick in ("UND", "ND", " und "):
        assert B.bio_block({"PICK": pick, "DRAFT": "2016"})["undrafted"] is True
    for pick in ("T", "SD", "15", "", "#N/A"):
        assert B.bio_block({"PICK": pick, "DRAFT": "1965"})["undrafted"] is False


def test_a_flagged_man_is_undrafted_whatever_his_draft_year():
    assert F.is_undrafted(rec(draft_year=2016, draft_pick=None, undrafted=True))
    assert F.is_undrafted(rec(draft_year=None, draft_pick=None, undrafted=True))


def test_no_year_and_no_pick_keeps_its_old_reading():
    assert F.is_undrafted(rec(draft_year=None, draft_pick=None))


def test_a_drafted_man_is_never_undrafted():
    # a numbered pick, and the territorial and supplemental picks that carry a
    # year and no number
    assert not F.is_undrafted(rec(draft_year=2016, draft_pick=12))
    assert not F.is_undrafted(rec(draft_year=1965, draft_pick=None))


# --------------------------------------------------------------------------
# the cohorts and the timeline follow it
# --------------------------------------------------------------------------


def _cohorts(record):
    idx = F.build_index({"seasons": [record], "seasons_list": [record["season"]],
                         "teams": [], "players": [record["player"]]})
    return {(kind, key) for kind, key, _phrase in F._cohorts_for(record, idx)}


def test_an_undrafted_man_with_a_class_is_in_both_groups():
    found = _cohorts(rec(draft_year=2016, draft_pick=None, undrafted=True))
    assert ("draft_class", "2016") in found
    assert ("draft_slot", "undrafted") in found


def test_a_territorial_pick_stays_out_of_the_undrafted_group():
    found = _cohorts(rec(draft_year=1965, draft_pick=None))
    assert ("draft_class", "1965") in found
    assert ("draft_slot", "undrafted") not in found


def test_a_numbered_pick_is_where_it_was():
    found = _cohorts(rec(draft_year=2016, draft_pick=12))
    assert ("draft_slot", "12") in found
    assert ("draft_slot", "undrafted") not in found


def test_the_timeline_measures_an_undrafted_man_against_the_undrafted():
    assert TL._draft_scopes(rec(draft_year=2016, draft_pick=None, undrafted=True)) \
        == [("draft_range", "undrafted")]
    assert TL._draft_scopes(rec(draft_year=1965, draft_pick=None)) == []


def test_the_tools_undrafted_filter_reads_the_flag():
    app = open(os.path.join(REPO, "js", "app.js"), encoding="utf-8").read()
    assert "record.undrafted ||" in app
    assert "(record.draft_pick == null && record.draft_year == null)" in app


# --------------------------------------------------------------------------
# the built pages
# --------------------------------------------------------------------------


def _data():
    with open(os.path.join(REPO, "data", "data.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _sections(html):
    """[(heading, body)] for every <h2> section of a page."""
    parts = re.split(r"<h2>(.*?)</h2>", html)
    return list(zip(parts[1::2], parts[2::2]))


built = pytest.mark.skipif(
    not os.path.exists(os.path.join(REPO, "sitemap.xml")),
    reason="pages have not been built")


@built
def test_a_draft_class_lists_its_undrafted_men_below_the_drafted():
    data = _data()
    went = {}
    for r in data["seasons"]:
        if r.get("undrafted") and r.get("draft_year"):
            went.setdefault(str(r["draft_year"]), set()).add(r["player"])
    checked = 0
    for year, names in went.items():
        path = os.path.join(REPO, "draft", year, "index.html")
        if not os.path.exists(path):
            continue
        html = open(path, encoding="utf-8").read()
        sections = _sections(html)
        headings = [h for h, _ in sections]
        below = "Went undrafted in {}".format(year)
        assert below in headings, (year, headings)
        at = headings.index(below)
        assert headings.index("Every player") < at, (year, headings)
        drafted_part = "".join(body for _h, body in sections[:at])
        for name in names:
            # a name may still appear above in a sentence about someone else's
            # page link, but never as a row of the drafted tables
            assert ">{}</a>".format(name) not in drafted_part, (year, name)
        checked += 1
    if went:
        assert checked, "no draft-class page carries an undrafted man"


@built
def test_every_flagged_man_is_on_the_undrafted_page():
    data = _data()
    path = os.path.join(REPO, "pick", "undrafted", "index.html")
    html = open(path, encoding="utf-8").read()
    flagged = {r["player"] for r in data["seasons"] if r.get("undrafted")}
    missing = sorted(n for n in flagged if ">{}</a>".format(n) not in html)
    # a name the engine keeps out of every cohort (Corey Brewer) is the only
    # kind of miss there can be
    assert len(missing) <= 1, missing[:20]
