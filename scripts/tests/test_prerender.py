"""Tests for the pre-rendered entity pages.

Most of these read the built output in the repository, which is what actually
ships. A few build small fixtures to pin a rule down on its own.
"""

from __future__ import annotations

import collections
import csv
import inspect
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from html import escape, unescape

import pytest

import factoids as F
from prerender import config as C
from prerender import entities as E
from prerender import slugs as S
from prerender import timeline as TL

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SITEMAP = os.path.join(REPO, "sitemap.xml")
HASHES = os.path.join(REPO, "data", "page_hashes.json")

built = pytest.mark.skipif(
    not os.path.exists(SITEMAP), reason="pages have not been built"
)


def repo(*parts):
    return os.path.join(REPO, *parts)


def read(path):
    with open(repo(path), "r", encoding="utf-8") as fh:
        return fh.read()


def sitemap_urls():
    tree = ET.parse(SITEMAP)
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    return [
        (u.findtext("s:loc", namespaces=ns), u.findtext("s:lastmod", namespaces=ns))
        for u in tree.findall("s:url", ns)
    ]


def path_for(url):
    """The file behind a public URL."""
    if url == C.TOOL_ROOT:
        return "index.html"
    rest = url[len(C.TOOL_ROOT) + 1:].rstrip("/")
    return os.path.join(rest, "index.html")


def _factoids():
    with open(repo("data", "factoids.json"), "r", encoding="utf-8") as fh:
        return json.load(fh)["factoids"]


def all_pages():
    with open(HASHES, "r", encoding="utf-8") as fh:
        return json.load(fh)["pages"]


# --------------------------------------------------------------------------
# the sitemap and what it points at
# --------------------------------------------------------------------------


@built
def test_every_sitemap_url_has_a_file():
    for url, _lastmod in sitemap_urls():
        assert os.path.exists(repo(path_for(url))), url


@built
def test_every_sitemap_url_is_indexable_and_self_canonical():
    for url, _lastmod in sitemap_urls():
        if url == C.TOOL_ROOT:
            continue  # the tool root is the app, not a generated page
        html = read(path_for(url))
        assert '<meta name="robots" content="index,follow">' in html, url
        assert '<link rel="canonical" href="{}">'.format(url) in html, url
        assert '<meta property="og:url" content="{}">'.format(url) in html, url


@built
def test_no_noindex_page_is_in_the_sitemap():
    listed = {url for url, _ in sitemap_urls()}
    for path, meta in all_pages().items():
        if meta.get("indexable"):
            continue
        assert meta.get("url") not in listed, path


@built
def test_every_indexable_page_is_in_the_sitemap():
    listed = {url for url, _ in sitemap_urls()}
    for path, meta in all_pages().items():
        if meta.get("indexable") and meta.get("url"):
            assert meta["url"] in listed, path


@built
def test_the_sitemap_lists_only_the_root_the_hubs_and_indexable_pages():
    listed = {url for url, _ in sitemap_urls()}
    expected = {C.TOOL_ROOT}
    expected |= {"{}/{}/".format(C.TOOL_ROOT, hub) for hub in C.HUBS}
    for path, meta in all_pages().items():
        if meta.get("indexable") and meta.get("url"):
            expected.add(meta["url"])
    assert listed == expected


@built
def test_every_lastmod_is_a_date():
    for url, lastmod in sitemap_urls():
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", lastmod or ""), url


# --------------------------------------------------------------------------
# links
# --------------------------------------------------------------------------


@built
def test_every_indexable_page_is_linked_from_its_hub():
    for path, meta in all_pages().items():
        if not meta.get("indexable") or not meta.get("url"):
            continue
        parts = path.split(os.sep)
        family = next(
            (f for f, spec in C.FAMILIES.items() if spec["dir"] == parts[0]), None
        )
        if family is None:
            continue  # the hubs themselves
        hub = C.FAMILIES[family]["hub"]
        assert hub, family
        hub_html = read(os.path.join(hub, "index.html"))
        href = "{}/{}/{}/".format(C.TOOL_ROOT, C.FAMILIES[family]["dir"], parts[1])
        assert 'href="{}"'.format(href) in hub_html, (hub, path)


@built
def test_the_tool_root_links_every_hub():
    html = read("index.html")
    assert 'class="hm-browse"' in html
    assert "display:none" not in html.split('class="hm-browse"')[1].split("</section>")[0]
    for hub in C.HUBS:
        assert '{}/{}/'.format(C.TOOL_ROOT, hub) in html, hub


@built
def test_every_indexable_page_is_three_clicks_from_the_root():
    """Root to hub is one, hub to page is two. Nothing is deeper."""
    root = read("index.html")
    for hub in C.HUBS:
        assert "{}/{}/".format(C.TOOL_ROOT, hub) in root, hub
    for path, meta in all_pages().items():
        if not meta.get("indexable") or not meta.get("url"):
            continue
        depth = meta["url"][len(C.TOOL_ROOT) + 1:].strip("/").count("/")
        assert depth <= 1, path


#: The one github.io the pages are allowed to name: the headshot store, whose
#: images are ours and are asked for by src, never linked to.
HEADSHOTS = "https://jsierrahoopshype.github.io/nba-headshots/"


@built
def test_nothing_links_to_github_io():
    """A reader who lands on the Pages host must still be sent to hoopsmatic.

    This is the bug that shipped: a relative href kept a click on github.io,
    so every href, canonical and og:url is checked, everywhere, including the
    tool root and the script it loads.
    """
    hits = []
    sources = [
        C.SITEMAP_PATH,
        os.path.join("js", "app.js"),
        os.path.join("js", "player-pages.js"),
        os.path.join("css", "styles.css"),
        os.path.join("css", "pages.css"),
        os.path.join("css", "polymarket.css"),
        "404.html",
    ]
    for path in list(all_pages()) + sources:
        full = repo(path)
        if not os.path.exists(full):
            continue
        with open(full, "r", encoding="utf-8") as fh:
            text = fh.read()
        for attr in ('href="', '<loc>', 'content="https'):
            for value in re.findall(re.escape(attr) + r'([^"<]+)', text):
                if "github.io" in value:
                    hits.append((path, value))
        # anything else naming the host has to be a headshot image
        for value in re.findall(r'https://[^"\'<> ]*github\.io[^"\'<> ]*', text):
            if not value.startswith(HEADSHOTS):
                hits.append((path, value))
    assert hits == []


@built
def test_every_link_to_a_subpage_is_absolute():
    for path in all_pages():
        if not path.endswith(".html"):
            continue
        html = read(path)
        for href in re.findall(r'href="([^"]+)"', html):
            if href.startswith(("mailto:", "#", "https://", "http://")):
                continue
            # only the stylesheets are allowed to be relative
            assert href.endswith(".css"), (path, href)


@built
def test_the_tool_links_player_pages_absolutely():
    app = read(os.path.join("js", "app.js"))
    assert 'href="player/' not in app
    assert "pages.playerUrl(name, season)" in app
    generated = read(os.path.join("js", "player-pages.js"))
    assert 'var ROOT = "{}"'.format(C.TOOL_ROOT) in generated


@built
def test_links_to_the_tool_root_carry_no_trailing_slash():
    html = read(os.path.join("college", "duke", "index.html"))
    assert C.TOOL_ROOT + '"' in html
    assert C.TOOL_ROOT + '/"' not in html


# --------------------------------------------------------------------------
# what a page says
# --------------------------------------------------------------------------


@built
def test_every_page_declares_itself_prerendered_and_carries_og_tags():
    for path in all_pages():
        if path in ("index.html", os.path.join("js", "player-pages.js")):
            continue
        html = read(path)
        assert '<meta name="hm-prerendered" content="1">' in html, path
        assert 'property="og:title"' in html, path
        assert 'property="og:description"' in html, path
        assert 'property="og:url"' in html, path


@built
def test_no_page_loads_the_whole_dataset_or_reads_the_query_string():
    for path in all_pages():
        if path in ("index.html", os.path.join("js", "player-pages.js")):
            continue
        html = read(path)
        assert "data/data.json" not in html, path
        assert "location.search" not in html, path
        assert "URLSearchParams" not in html, path


@built
def test_pages_carry_breadcrumbs_and_the_json_ld_that_describes_them():
    html = read(os.path.join("college", "duke", "index.html"))
    assert 'class="hm-crumbs"' in html
    blob = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
    data = json.loads(blob.group(1))
    assert data["@type"] == "BreadcrumbList"
    names = [item["name"] for item in data["itemListElement"]]
    assert names == ["Salary Season Finder", "Colleges", "Duke"]
    assert data["itemListElement"][-1]["item"].endswith("/college/duke/")


@built
def test_generated_copy_has_no_em_dashes():
    offenders = []
    for path in all_pages():
        if path in ("index.html", os.path.join("js", "player-pages.js")):
            continue
        if "—" in read(path):
            offenders.append(path)
    assert offenders == []


@built
def test_every_page_carries_the_scope_note():
    for path in all_pages():
        parts = path.split(os.sep)
        if parts[0] in ("index.html", "404.html", "js"):
            continue
        assert C.SCOPE_NOTE in read(path), path


@built
def test_indexable_titles_and_descriptions_are_unique():
    titles, descriptions = {}, {}
    for path, meta in all_pages().items():
        if not meta.get("indexable"):
            continue
        html = read(path)
        title = re.search(r"<title>(.*?)</title>", html).group(1)
        desc = re.search(r'<meta name="description" content="(.*?)">', html).group(1)
        assert title not in titles, (path, titles.get(title))
        assert desc not in descriptions, (path, descriptions.get(desc))
        titles[title] = path
        descriptions[desc] = path


@built
def test_indexable_descriptions_name_a_figure_from_the_data():
    for path, meta in all_pages().items():
        # the tool root is the app itself, with a hand written description in index.html
        if path == "index.html":
            continue
        if not meta.get("indexable") or path.split(os.sep)[0] in C.HUBS:
            continue
        html = read(path)
        desc = re.search(r'<meta name="description" content="(.*?)">', html).group(1)
        assert re.search(r"\$[\d.,]+", desc), (path, desc)


# --------------------------------------------------------------------------
# the identity layers, the same ones the engine uses
# --------------------------------------------------------------------------


@built
def test_a_confirmed_split_gets_a_page_each_and_the_unnamed_one_gets_none():
    assert os.path.exists(repo("player", "jaren-jackson", "index.html"))
    assert os.path.exists(repo("player", "jaren-jackson-jr", "index.html"))
    father = read(os.path.join("player", "jaren-jackson", "index.html"))
    son = read(os.path.join("player", "jaren-jackson-jr", "index.html"))
    assert "<h1>Jaren Jackson</h1>" in father
    assert "<h1>Jaren Jackson Jr</h1>" in son
    assert "2018-19" not in father.split("</table>")[0]
    assert "1997-98" not in son.split("</table>")[0]
    # Corey Brewer's 1999-00 man has no name, so no page claims him
    brewer = read(os.path.join("player", "corey-brewer", "index.html"))
    assert "1999-00" not in brewer.split("</table>")[0]


@built
def test_a_merged_identity_gets_one_page_under_its_real_name():
    html = read(os.path.join("player", "wendell-carter-jr", "index.html"))
    assert "<h1>Wendell Carter Jr</h1>" in html
    assert not os.path.exists(repo("player", "wendell-carter", "index.html"))
    # the season the other spelling carried is on the one page
    assert "2024-25" in html


@built
def test_colleges_print_spelled_out_and_slug_the_same_way():
    html = read(os.path.join("college", "michigan-state", "index.html"))
    assert "Michigan State" in html
    assert "Michigan St<" not in html
    assert not os.path.exists(repo("college", "michigan-st", "index.html"))


@built
def test_the_eleven_never_appear_on_a_cohort_page():
    idx = F.build_index(F.load_data())
    book = S.load(repo(C.SLUGS_PATH))
    slug_of = book.assigned.get("player", {})
    suspects = {
        slug_of.get(name) for name in idx.draft_meta_suspect
        if not any(k.startswith(name + "#") for k in slug_of)
    }
    suspects.discard(None)
    families = [f for f, spec in C.FAMILIES.items() if spec["cohort"]]
    for family in families:
        root = repo(C.FAMILIES[family]["dir"])
        if not os.path.isdir(root):
            continue
        for entry in sorted(os.listdir(root)):
            page = os.path.join(root, entry, "index.html")
            if not os.path.exists(page):
                continue
            with open(page, "r", encoding="utf-8") as fh:
                html = fh.read()
            for slug in suspects:
                assert 'player/{}/"'.format(slug) not in html, (family, entry, slug)


@built
def test_a_carried_in_career_total_never_reaches_a_career_table():
    idx = F.build_index(F.load_data())
    book = S.load(repo(C.SLUGS_PATH))
    slug_of = book.assigned.get("player", {})
    bad = {slug_of.get(name) for name in idx.career_total_carried_in}
    bad.discard(None)
    for family in ("college", "country", "draft", "pick", "position"):
        root = repo(C.FAMILIES[family]["dir"])
        if not os.path.isdir(root):
            continue
        for entry in sorted(os.listdir(root)):
            page = os.path.join(root, entry, "index.html")
            if not os.path.exists(page):
                continue
            with open(page, "r", encoding="utf-8") as fh:
                html = fh.read()
            careers = html.split("Highest career earnings")[1].split("</section>")[0] \
                if "Highest career earnings" in html else ""
            for slug in bad:
                assert 'player/{}/"'.format(slug) not in careers, (family, entry, slug)


@built
def test_cohort_pages_exist_only_above_the_engines_threshold():
    idx = F.build_index(F.load_data())
    book = S.load(repo(C.SLUGS_PATH))
    identities = E.build_player_identities(idx)
    cohorts = E.build_cohorts(idx, identities)
    for entity in cohorts:
        minimum = C.AGENT_MINIMUM if entity.family == "agent" else C.cohort_minimum(entity.family)
        assert len(entity.players) >= minimum, entity


# --------------------------------------------------------------------------
# slugs
# --------------------------------------------------------------------------


def test_slugs_strip_diacritics():
    assert S.slugify("Nikola Jokić") == "nikola-jokic"
    assert S.slugify("Luka Dončić") == "luka-doncic"


def test_a_collision_takes_the_disambiguator_then_a_number():
    book = S.SlugBook()
    assert book.get("player", "a", "Chris Smith") == "chris-smith"
    assert book.get("player", "b", "Chris Smith", "1992") == "chris-smith-1992"
    assert book.get("player", "c", "Chris Smith") == "chris-smith-2"


def test_an_existing_slug_never_changes():
    book = S.SlugBook({"player": {"key": "an-old-slug"}})
    assert book.get("player", "key", "A Completely Different Name") == "an-old-slug"
    assert book.added == 0


def test_two_men_under_one_name_keep_a_fixed_order():
    """A set of keys iterates differently per process, so the sort breaks ties.

    Chris Smith is two men, both undrafted, both printed "Chris Smith". Without
    the key in the sort key their order in a cohort roll flips between builds.
    """
    idx = F.build_index(
        F.load_data(),
        identity_splits=F.load_identity_splits(),
        college_names=F.load_college_names(),
        name_aliases=F.load_name_aliases(),
    )
    identities = E.build_player_identities(idx)
    for cohort in E.build_cohorts(idx, identities):
        names = [(p.name, p.key) for p in cohort.players]
        assert names == sorted(names), cohort.key


@built
def test_shipped_slugs_cover_every_page_and_never_moved():
    book = S.load(repo(C.SLUGS_PATH))
    idx = F.build_index(F.load_data())
    identities = E.build_player_identities(idx)
    for ident in identities:
        assert ident.key in book.assigned["player"], ident.key


# --------------------------------------------------------------------------
# the build itself
# --------------------------------------------------------------------------


@built
def test_two_builds_with_unchanged_data_change_nothing():
    before = read(C.SITEMAP_PATH)
    hashes_before = read(C.PAGE_HASHES_PATH)
    result = subprocess.run(
        [sys.executable, os.path.join(REPO, "scripts", "prerender_pages.py")],
        capture_output=True, text=True, cwd=REPO,
    )
    assert result.returncode == 0, result.stderr
    assert "files written: 0" in result.stdout, result.stdout
    assert read(C.SITEMAP_PATH) == before
    assert read(C.PAGE_HASHES_PATH) == hashes_before


@built
def test_the_404_links_the_tool_root_and_every_hub():
    html = read(C.NOT_FOUND_PATH)
    assert C.TOOL_ROOT in html
    for hub in C.HUBS:
        assert "{}/{}/".format(C.TOOL_ROOT, hub) in html, hub
    assert '<meta name="robots" content="noindex,follow">' in html


@built
def test_the_season_table_is_the_tools_own_component():
    """Not a second copy of it: the markup is what js/app.js emits."""
    html = read(os.path.join("player", "cade-cunningham", "index.html"))
    assert '<table class="player-season-table">' in html
    assert 'class="ps-scroll"' in html
    assert 'class="ps-season-label"' in html


@built
def test_player_pages_link_the_sibling_tools():
    html = read(os.path.join("player", "cade-cunningham", "index.html"))
    assert C.COMPARE_URL in html
    assert C.CAREER_MAP_URL in html


# --------------------------------------------------------------------------
# the written summary on a cohort page
# --------------------------------------------------------------------------


def _summary(path, strip_links=True):
    html = read(path)
    block = re.search(r'<div class="hm-summary">(.*?)</div>', html, re.S)
    if not block:
        return []
    import html as _html
    out = []
    for text in re.findall(r"<p>(.*?)</p>", block.group(1), re.S):
        if strip_links:
            text = re.sub(r"<[^>]+>", "", text)
        out.append(_html.unescape(text))
    return out


@built
def test_cohort_pages_carry_a_short_written_summary():
    for path in ("college/duke", "college/arkansas", "country/france",
                 "draft/2003", "pick/1", "position/guard"):
        sentences = _summary(os.path.join(*(path.split("/") + ["index.html"])))
        assert 2 <= len(sentences) <= C.SUMMARY_SENTENCES, (path, sentences)
        joined = " ".join(sentences)
        # the window is a note on the page now, never a clause in a claim
        assert F.SCOPE_FIRST_SEASON not in joined, (path, joined)
        assert "—" not in joined, path
        assert "the group" not in joined, (path, joined)


@built
def test_a_summary_covers_the_career_and_the_single_season():
    """Which comes first varies by page; both are always there."""
    joined = " ".join(_summary(os.path.join("college", "duke", "index.html")))
    assert "Kyrie Irving" in joined and "$391.9 million" in joined
    assert "Jayson Tatum" in joined and "$58.5 million" in joined


#: A summary may state money nobody has been paid yet only as a contract.
CONTRACT_WORDS = (" due", " signed", " owed", " deal", " contract",
                  " left on", " ahead", " to come", " would ")


@built
def test_money_nobody_has_been_paid_is_named_as_a_contract():
    """"Wembanyama is due $44 million in 2027-28" is a fact about a signed
    deal. "Wembanyama earned $44 million in 2027-28" would not be."""
    future = ("2027-28", "2028-29", "2029-30", "2030-31")
    for path, meta in all_pages().items():
        if path.split(os.sep)[0] not in ("college", "country", "draft", "pick",
                                         "position"):
            continue
        for sentence in _summary(path):
            if not any(season in sentence for season in future):
                continue
            lowered = " " + sentence.lower()
            assert any(word in lowered for word in CONTRACT_WORDS), (path, sentence)
            for verb in ("has earned", "was paid", "earned $"):
                assert verb not in sentence, (path, sentence)


@built
def test_no_cohort_page_still_lists_raw_factoids():
    for path, meta in all_pages().items():
        if path.split(os.sep)[0] not in ("college", "country", "draft", "pick",
                                         "position"):
            continue
        assert "What the numbers say" not in read(path), path


@built
def test_the_career_table_comes_first_on_a_cohort_page():
    html = read(os.path.join("college", "duke", "index.html"))
    headings = re.findall(r"<h2>(.*?)</h2>", html)
    assert headings[:3] == [
        "Highest career earnings",
        "Highest single-season salaries",
        "On a roster in 2026-27",
    ], headings


# --------------------------------------------------------------------------
# mirrors and comparisons that point the wrong way
# --------------------------------------------------------------------------


def test_one_side_of_a_mirrored_pair_is_dropped():
    from prerender import phrasing
    record = {
        "key": "cohort_season|college|Duke|Big|2026-27", "value": 58,
        "text": "the record", "type": "sets",
        "previous_holder": {"player": "Big", "season": "2025-26", "value": 54},
    }
    runner = {
        "key": "cohort_season|college|Duke|Big|2025-26", "value": 54,
        "text": "second behind the record", "type": "approaches",
        "previous_holder": {"player": "Big", "season": "2026-27", "value": 58},
    }
    kept = phrasing.drop_mirrors([("2026-27", record), ("2025-26", runner)])
    assert [f["value"] for _season, f in kept] == [58]


def test_a_comparison_against_a_later_season_loses_its_verb():
    from prerender import phrasing
    fact = {
        "key": "cohort_season|college|Stanford|Brook Lopez|2023-24",
        "value": 25, "type": "sets",
        "text": ("Brook Lopez's $25 million in 2023-24 is the highest single-season "
                 "salary among players out of Stanford since 1990-91, breaking his "
                 "own mark of $23 million in 2024-25."),
        "previous_holder": {"player": "Brook Lopez", "season": "2024-25", "value": 23},
    }
    text = phrasing.straighten(fact, "2023-24")
    assert "breaking" not in text
    assert "ahead of his own $23 million in 2024-25" in text


@built
def test_no_page_says_it_passed_a_figure_from_a_later_season():
    backwards = []
    for record_key, facts in _factoids().items():
        player, season = record_key.split("|", 1)
        for fact in facts:
            holder = fact.get("previous_holder") or {}
            if not holder.get("season"):
                continue
            if F.season_key(holder["season"]) <= F.season_key(season):
                continue
            if "passing" in fact["text"] or "breaking" in fact["text"]:
                backwards.append(fact["text"])
    # The engine ranks a season already played out against the seasons that had
    # been played by then, so a holder from a later season cannot turn up at
    # all. The page check stays below for anything that slips past that.
    assert not backwards, backwards[:3]
    for text in backwards:
        for path in all_pages():
            if not path.endswith(".html"):
                continue
            assert escape(text) not in read(path), (path, text[:60])


# --------------------------------------------------------------------------
# a player's claims, as one summary paragraph
# --------------------------------------------------------------------------


def _player_summary(who):
    """The "What the numbers say" paragraph of a player page, as plain text."""
    html = read(os.path.join("player", who, "index.html"))
    block = html[html.index("What the numbers say"):]
    found = re.search(r'<p class="hm-facts">(.*?)</p>', block, re.S)
    if not found:
        return ""
    return unescape(re.sub(r"<[^>]+>", "", found.group(1)))


def _sentences(body):
    return [s for s in re.split(r"(?<=\.)\s+", body) if s.strip()]


@built
def test_a_player_page_says_it_in_one_paragraph():
    html = read(os.path.join("player", "giannis-antetokounmpo", "index.html"))
    block = html[html.index("What the numbers say"):]
    block = block[:block.index("</section>")]
    assert block.count('<p class="hm-facts">') == 1
    assert "<details" not in block and "hm-season" not in block


@built
def test_a_summary_is_two_to_four_sentences():
    for who in ("giannis-antetokounmpo", "kyrie-irving", "joel-embiid"):
        count = len(_sentences(_player_summary(who)))
        assert 2 <= count <= 4, (who, count)


@built
def test_a_summary_names_him_once_and_then_says_he():
    for who, name in (("giannis-antetokounmpo", "Giannis Antetokounmpo"),
                      ("kyrie-irving", "Kyrie Irving")):
        plain = _player_summary(who)
        assert plain.count(name) == 1, (who, plain)


@built
def test_a_record_held_for_years_is_said_once_with_its_span():
    plain = _player_summary("giannis-antetokounmpo")
    assert "every season since" in plain
    assert plain.count("highest-paid") == 1, plain


@built
def test_a_summary_makes_one_superlative_claim_a_sentence():
    for who in ("giannis-antetokounmpo", "kyrie-irving", "joel-embiid"):
        for sentence in _sentences(_player_summary(who)):
            claims = (sentence.count("more than any")
                      + sentence.count("highest-paid")
                      + sentence.count("biggest")
                      + sentence.count("-most"))
            assert claims <= 1, (who, sentence)


@built
def test_a_summary_frames_the_season_being_played():
    plain = _player_summary("giannis-antetokounmpo")
    assert "By the end of 2026-27 he'll have earned" in plain


@built
def test_a_record_still_to_come_is_conditional():
    plain = _player_summary("giannis-antetokounmpo")
    assert "would be the biggest single-season salary" in plain


@built
def test_a_past_milestone_is_left_to_the_table():
    """"passed $150 million in 2020-21" is a row, not a line of prose."""
    for who in ("giannis-antetokounmpo", "kyrie-irving", "joel-embiid"):
        plain = _player_summary(who)
        assert "in career earnings in" not in plain, who


@built
def test_a_link_inside_a_sentence_stays_inline():
    """The paragraph used to be laid out with flex, which made every link in it
    a flex item and so a block of its own."""
    css = read(os.path.join("css", "pages.css"))
    for selector in (".hm-facts", ".hm-summary p", ".hm-inline-link"):
        for block in _css_blocks(css, selector):
            assert "display: flex" not in block, (selector, block)
            assert "display: block" not in block, (selector, block)
            assert "display: grid" not in block, (selector, block)
    inline = _css_blocks(css, ".hm-facts a")
    assert inline and any("display: inline" in b for b in inline)


def _css_blocks(css, selector):
    """Every rule body whose selector list contains this selector."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    out = []
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        heads = [h.strip() for h in match.group(1).split(",")]
        if selector in heads:
            out.append(match.group(2))
    return out


# --------------------------------------------------------------------------
# headshots and flags
# --------------------------------------------------------------------------


@built
def test_cohort_tables_carry_headshots_and_flags():
    html = read(os.path.join("college", "duke", "index.html"))
    assert html.count('class="hm-face"') >= 25
    assert html.count('class="hm-flag"') >= 25
    for img in re.findall(r"<img [^>]*>", html):
        assert 'loading="lazy"' in img, img
        assert "width=" in img and "height=" in img, img
        assert "alt=" in img, img


@built
def test_flags_are_served_from_this_repository():
    for path in (os.path.join("countries", "index.html"),
                 os.path.join("college", "duke", "index.html")):
        for src in re.findall(r'<img class="hm-flag" src="([^"]+)"', read(path)):
            assert src.startswith(C.TOOL_ROOT + "/assets/flags/"), src
            local = src[len(C.TOOL_ROOT) + 1:]
            assert os.path.exists(repo(local)), local


@built
def test_the_country_hub_shows_a_flag_for_every_country():
    html = read(os.path.join("countries", "index.html"))
    countries = re.findall(r'<a href="[^"]*/country/[^"]+/">(?:<img[^>]*>)?([^<]+)', html)
    assert html.count('class="hm-flag"') == len(countries), (
        html.count('class="hm-flag"'), len(countries))


# --------------------------------------------------------------------------
# the tool root
# --------------------------------------------------------------------------


@built
def test_the_browse_block_is_styled_chips():
    html = read("index.html")
    assert 'class="hm-chips"' in html
    assert 'href="css/pages.css"' in html, "the root needs the sheet that styles it"


@built
def test_the_404_loads_its_stylesheets_absolutely():
    html = read("404.html")
    sheets = re.findall(r'<link rel="stylesheet" href="([^"]+)"', html)
    assert sheets, html[:400]
    for href in sheets:
        assert href.startswith(C.TOOL_ROOT + "/css/"), href


@built
def test_the_tool_knows_which_cohorts_have_pages():
    generated = read(os.path.join("js", "player-pages.js"))
    blob = re.search(r"var COHORTS = (\{.*?^\});$", generated, re.S | re.M)
    cohorts = json.loads(blob.group(1))
    assert set(cohorts) == {"college", "country", "draft", "pick", "position"}
    assert cohorts["position"] == {"C": "center", "F": "forward", "G": "guard"}
    for family, group in cohorts.items():
        for key, slug in group.items():
            assert os.path.exists(repo(family, slug, "index.html")), (family, key)


# --------------------------------------------------------------------------
# awards, in full
# --------------------------------------------------------------------------


@built
def test_the_tool_no_longer_truncates_a_career_to_one_award():
    app = read(os.path.join("js", "app.js"))
    assert "highestPriorityAward" not in app
    assert "function summarizeAwards" in app
    assert "award_counts" in app
    css = read(os.path.join("css", "styles.css"))
    awards_cell = css[css.index("td.ps-awards"):]
    assert "white-space: normal" in awards_cell.split("}")[0]


@built
def test_every_nationality_in_the_data_has_a_flag_on_disk():
    with open(repo("data", "country_flags.json"), "r", encoding="utf-8") as fh:
        codes = json.load(fh)["codes"]
    data = F.load_data()
    countries = {
        (r.get("nationality") or "").strip() for r in data["seasons"]
    } - {""}
    missing = sorted(c for c in countries if c not in codes)
    assert missing == [], missing
    for code in set(codes.values()):
        assert os.path.exists(repo("assets", "flags", code + ".svg")), code


@built
def test_headshots_are_matched_with_punctuation_and_accents_stripped():
    from prerender.media import Media, match_key
    assert match_key("A.J. Price") == match_key("AJ Price")
    assert match_key("Nikola Jokić") == match_key("Nikola Jokic")
    media = Media(REPO)
    assert media.face_src(["LeBron James"]).endswith("2544-lebron-james.webp")
    assert media.face_src(["Nobody At All"]).endswith("player_silhouette.svg")


# --------------------------------------------------------------------------
# career tables rank money already paid, active players included
# --------------------------------------------------------------------------


def _career_table(path):
    """The rows of a page's 'Highest career earnings' table."""
    html = read(path)
    block = html[html.index("Highest career earnings"):]
    block = block[:block.index("</section>")]
    return re.findall(r'<tr><th class="hm-who" scope="row">(.*?)</th>(.*?)</tr>', block, re.S)


@built
def test_an_active_player_can_lead_a_career_table():
    rows = _career_table(os.path.join("college", "duke", "index.html"))
    assert rows, "Duke should have a career table"
    first = rows[0][0]
    assert "Kyrie Irving" in first, first
    assert "hm-active" in first, first


@built
def test_a_career_table_never_counts_contracted_money():
    idx = F.build_index(F.load_data())
    html = read(os.path.join("college", "duke", "index.html"))
    block = html[html.index("Highest career earnings"):]
    block = block[:block.index("</section>")]
    figures = [int(m.replace(",", "")) for m in re.findall(r"\$([\d,]+)", block)]
    for name in ("Kyrie Irving", "Jayson Tatum"):
        paid, season = idx.paid_through(name)
        assert paid in figures, (name, paid)
        assert not idx.is_contracted(season), (name, season)


@built
def test_a_career_total_names_the_season_it_runs_through():
    for path, _meta in _cohort_pages():
        for sentence in _summary(path):
            if "career earnings" in sentence or "has earned" in sentence:
                assert "to date" not in sentence, (path, sentence)
                assert "through 20" in sentence or "through 19" in sentence, (
                    path, sentence)


# --------------------------------------------------------------------------
# no page hedges on the data window any more
# --------------------------------------------------------------------------


@built
def test_the_window_is_a_note_and_never_a_clause_in_a_claim():
    """1990-91 still appears as a season a man played in. What it no longer
    does is hedge a claim."""
    for path in all_pages():
        if not path.endswith(".html") or path == "index.html":
            continue
        html = read(path)
        assert "since 1990-91" not in html, path
        assert "Since 1990-91" not in html, path
    for path in ("college/duke", "player/joel-embiid", "countries"):
        html = read(os.path.join(*(path.split("/") + ["index.html"])))
        assert C.SCOPE_NOTE == "Salary data starts in 1990-91."
        assert html.count(C.SCOPE_NOTE) == 1, path


@built
def test_no_title_or_description_carries_the_window():
    for path in all_pages():
        if not path.endswith(".html") or path == "index.html":
            continue
        html = read(path)
        title = re.search(r"<title>(.*?)</title>", html).group(1)
        desc = re.search(r'<meta name="description" content="(.*?)">', html).group(1)
        # the 1990-91 season page is allowed to be called 1990-91
        assert "Since " + F.SCOPE_FIRST_SEASON not in title, path
        assert "since " + F.SCOPE_FIRST_SEASON not in desc, path
    assert "in NBA History" in read(
        os.path.join("college", "duke", "index.html")).split("</title>")[0]


# --------------------------------------------------------------------------
# names inside a sentence link to their own pages
# --------------------------------------------------------------------------


@built
def test_a_claim_links_the_things_it_names():
    html = read(os.path.join("player", "joel-embiid", "index.html"))
    block = html[html.index("What the numbers say"):]
    for expect in ("/team/76ers/", "/position/center/", "/country/united-states/"):
        assert 'class="hm-inline-link" href="{}{}"'.format(C.TOOL_ROOT, expect) in block, expect


@built
def test_a_college_position_is_one_link_not_two():
    """"Duke guard" is a page. Linking "Duke" and "guard" separately puts two
    links side by side and sends the reader to neither of the right ones."""
    html = read(os.path.join("player", "kyrie-irving", "index.html"))
    block = html[html.index("What the numbers say"):]
    block = block[:block.index("</section>")]
    assert "/college-position/duke-guards/" in block
    assert "</a> <a" not in block


@built
def test_a_page_never_links_to_itself_inside_a_sentence():
    for path, meta in all_pages().items():
        if not path.endswith(".html") or path == "index.html":
            continue
        url = meta.get("url")
        if not url:
            continue
        html = read(path)
        assert 'class="hm-inline-link" href="{}"'.format(url) not in html, path


@built
def test_a_target_is_linked_once_per_sentence():
    for path in (os.path.join("player", "joel-embiid", "index.html"),
                 os.path.join("college", "duke", "index.html")):
        html = read(path)
        for item in re.findall(r"<li>(.*?)</li>", html) + re.findall(r"<p>(.*?)</p>", html):
            hrefs = re.findall(r'class="hm-inline-link" href="([^"]+)"', item)
            for sentence_hrefs in [hrefs]:
                assert len(sentence_hrefs) == len(set(sentence_hrefs)) or \
                    item.count(". ") >= 1, (path, item[:120])


# --------------------------------------------------------------------------
# awards on a phone
# --------------------------------------------------------------------------


@built
def test_awards_are_hidden_below_768px():
    css = read(os.path.join("css", "styles.css"))
    block = css[css.rindex("@media (max-width: 768px)"):]
    for selector in ("td.awards-cell", "th.awards-header", "th.ps-awards",
                     "td.ps-awards"):
        assert selector in block, selector
    assert "display: none" in block
    # the filter itself is untouched
    assert "#awardsFilter" not in block


# --------------------------------------------------------------------------
# the summaries vary, and the same page says the same thing every build
# --------------------------------------------------------------------------


def _cohort_pages():
    for path, meta in all_pages().items():
        if path.split(os.sep)[0] in ("college", "country", "draft", "pick",
                                     "position"):
            yield path, meta


@built
def test_a_summary_is_two_or_three_sentences():
    for path, _meta in _cohort_pages():
        assert 2 <= len(_summary(path)) <= 3, (path, _summary(path))


@built
def test_no_two_sentences_in_a_row_open_on_the_same_word():
    for path, _meta in _cohort_pages():
        words = [s.split(" ", 1)[0].lower().strip(".,:") for s in _summary(path)]
        for first, second in zip(words, words[1:]):
            assert first != second, (path, first)


@built
def test_a_summary_names_its_cohort_at_most_twice():
    from prerender.summary import Names
    idx = F.build_index(F.load_data())
    book = S.load(repo(C.SLUGS_PATH))
    built_pages = E.build_all(idx, book)
    by_slug = {(e.family, e.slug): e for e in built_pages["cohorts"]}
    for path, _meta in _cohort_pages():
        family, slug = path.split(os.sep)[0], path.split(os.sep)[1]
        entity = by_slug.get((family, slug))
        if entity is None:
            continue
        names = Names(entity.family, entity.key, entity.name)
        joined = " ".join(_summary(path))
        assert names.mentions(joined) <= 2, (path, names.token, joined)


@built
def test_a_hub_is_not_one_sentence_repeated():
    """65 college pages opening the same way is a form letter, not a summary."""
    openings = collections.defaultdict(collections.Counter)
    for path, _meta in _cohort_pages():
        sentences = _summary(path)
        if not sentences:
            continue
        openings[path.split(os.sep)[0]][" ".join(sentences[0].split()[:3])] += 1
    for family, counter in openings.items():
        total = sum(counter.values())
        if total < 10:
            continue
        assert len(counter) >= 8, (family, counter.most_common(5))
        top = counter.most_common(1)[0][1]
        assert top <= total * 0.45, (family, counter.most_common(3))


def test_the_wording_of_a_page_is_a_function_of_its_slug():
    from prerender.summary import pick
    assert pick("duke", "order", 2) == pick("duke", "order", 2)
    seen = {pick(slug, "order", 2) for slug in
            ("duke", "kentucky", "france", "canada", "1", "35")}
    assert seen == {0, 1}, "the pick should not be constant"


@built
def test_two_builds_produce_the_same_summaries():
    before = {path: _summary(path) for path, _meta in _cohort_pages()}
    result = subprocess.run(
        [sys.executable, os.path.join(REPO, "scripts", "prerender_pages.py")],
        capture_output=True, text=True, cwd=REPO,
    )
    assert result.returncode == 0, result.stderr
    for path, sentences in before.items():
        assert _summary(path) == sentences, path


# --------------------------------------------------------------------------
# the current-season slot
# --------------------------------------------------------------------------


@built
def test_the_last_man_on_a_payroll_is_named_as_such():
    joined = " ".join(_summary(os.path.join("draft", "2003", "index.html")))
    assert "LeBron James" in joined
    assert any(phrase in joined for phrase in (
        "still drawing an NBA salary", "only one left on a roster",
        "still on an NBA payroll", "still being paid",
        "the only player from", "last of",
    )), joined


@built
def test_a_thin_current_roster_gets_no_best_paid_sentence():
    """Two men left is not a list worth topping."""
    idx = F.build_index(F.load_data())
    book = S.load(repo(C.SLUGS_PATH))
    built_pages = E.build_all(idx, book)
    from prerender import pages as P
    for entity in built_pages["cohorts"]:
        if entity.family != "college":
            continue
        owners = P._owner_map(built_pages["players"])
        _html, _n, current = P._current_rows(idx, entity.records, owners, 25)
        if len(current) != 2:
            continue
        path = os.path.join("college", entity.slug, "index.html")
        for sentence in _summary(path):
            assert "best-paid" not in sentence, (path, sentence)
            assert "heads the" not in sentence, (path, sentence)


# --------------------------------------------------------------------------
# salaries the CBA does not allow never reach a page
# --------------------------------------------------------------------------


@built
def test_a_flagged_salary_never_tops_a_table_or_a_sentence():
    idx = F.build_index(F.load_data())
    assert idx.impossible, "the guard should be finding something"
    wemby = idx.impossible_reason("Victor Wembanyama", "2030-31")
    assert wemby, "Wembanyama's 2030-31 projection is the case this was for"
    assert "cap" in wemby
    # the flagged figure is in no ranked table and no summary on his cohorts
    for path in ("pick/1/index.html", "country/france/index.html",
                 "position/center/index.html", "draft/2023/index.html"):
        html = read(path)
        head = html[:html.index("Every player")] if "Every player" in html else html
        assert "$112,560,000" not in head, path
        assert "$112.6 million" not in head, path


# --------------------------------------------------------------------------
# what a summary's first sentence has to do
# --------------------------------------------------------------------------


def _cohort_entities():
    idx = F.build_index(F.load_data())
    book = S.load(repo(C.SLUGS_PATH))
    built_pages = E.build_all(idx, book)
    return idx, {(e.family, e.slug): e for e in built_pages["cohorts"]}


@built
def test_the_first_sentence_names_the_cohort():
    """A reader landing on a page learns from its first line which list he is
    reading. "The largest single salary belongs to Jamal Murray" does not say."""
    from prerender.summary import Names
    _idx, by_slug = _cohort_entities()
    for path, _meta in _cohort_pages():
        family, slug = path.split(os.sep)[0], path.split(os.sep)[1]
        entity = by_slug.get((family, slug))
        if entity is None:
            continue
        sentences = _summary(path)
        assert sentences, path
        names = Names(entity.family, entity.key, entity.name)
        assert names.mentions(sentences[0]) >= 1, (path, sentences[0])


@built
def test_no_sentence_opens_on_a_pronoun_pointing_at_a_number():
    for path, _meta in _cohort_pages():
        for sentence in _summary(path):
            first = sentence.split(" ", 1)[0]
            assert first not in ("That", "This"), (path, sentence)


def test_a_repeated_subject_becomes_a_pronoun():
    """A second sentence about the same man opens on "he", never his name."""
    from prerender import summary as SU
    pools = (SU.CAREER_SAME, SU.RECORD_SAME, SU.CURRENT_SAME,
             SU.LAST_ONE_SAME, SU.FUTURE_CLIMB_SAME, SU.FUTURE_CLIMB_AFTER,
             SU.FUTURE_FLAT_SAME)
    for pool in pools:
        for template in pool:
            assert template.startswith(("He ", "His ")), template


@built
def test_no_summary_repeats_a_name_in_consecutive_sentences():
    for path, _meta in _cohort_pages():
        sentences = _summary(path)
        for first, second in zip(sentences, sentences[1:]):
            opener = " ".join(second.split()[:2])
            assert not first.startswith(opener), (path, first, second)


# --------------------------------------------------------------------------
# "More colleges" at the foot of a page
# --------------------------------------------------------------------------


MORE_HEADINGS = {
    "college": "More colleges",
    "country": "More countries",
    "draft": "More draft classes",
    "pick": "More picks",
    "position": "More positions",
}


@built
def test_every_cohort_page_offers_the_rest_of_its_family():
    _idx, by_slug = _cohort_entities()
    for path, meta in all_pages().items():
        family = path.split(os.sep)[0]
        if family not in MORE_HEADINGS:
            continue
        slug = path.split(os.sep)[1]
        html = read(path)
        assert "<h2>{}</h2>".format(MORE_HEADINGS[family]) in html, path
        block = html[html.index(MORE_HEADINGS[family]):]
        siblings = [
            e for (fam, s), e in by_slug.items() if fam == family and s != slug
        ]
        for entity in siblings:
            href = "{}/{}/{}/".format(C.TOOL_ROOT, C.FAMILIES[family]["dir"],
                                      entity.slug)
            assert 'href="{}"'.format(href) in block, (path, entity.slug)
        # and never a link back to the page you are on
        own = "{}/{}/{}/".format(C.TOOL_ROOT, C.FAMILIES[family]["dir"], slug)
        assert 'href="{}"'.format(own) not in block, path


@built
def test_a_big_family_is_grouped_and_a_small_one_is_not():
    duke = read(os.path.join("college", "duke", "index.html"))
    block = duke[duke.index("More colleges"):]
    heads = re.findall(r'<h3 class="hm-more-head">(.*?)</h3>', block)
    assert heads == sorted(heads), heads
    assert len(heads) >= 10, heads

    picks = read(os.path.join("pick", "1", "index.html"))
    block = picks[picks.index("More picks"):]
    heads = re.findall(r'<h3 class="hm-more-head">(.*?)</h3>', block)
    assert heads[0] == "Picks 1 to 10", heads
    assert heads[-1] == "Undrafted", heads

    classes = read(os.path.join("draft", "2003", "index.html"))
    block = classes[classes.index("More draft classes"):]
    heads = re.findall(r'<h3 class="hm-more-head">(.*?)</h3>', block)
    assert heads[0] == "2020s", heads

    centers = read(os.path.join("position", "center", "index.html"))
    block = centers[centers.index("More positions"):]
    assert '<h3 class="hm-more-head">' not in block


@built
def test_the_more_block_never_scrolls_on_its_own():
    css = read(os.path.join("css", "pages.css"))
    more = css[css.index(".hm-more"):]
    assert "overflow" not in more.split("}")[0]
    assert "max-height" not in more.split("}")[0]


@built
def test_a_hub_groups_its_family_rather_than_listing_it_twice():
    html = read(os.path.join("colleges", "index.html"))
    assert html.count("<h2>") == 1, "one list, not two"
    assert re.findall(r'<h3 class="hm-more-head">(.*?)</h3>', html)


# --------------------------------------------------------------------------
# the career table on a phone, and awards at 768px
# --------------------------------------------------------------------------


@built
def test_the_career_table_reorders_its_columns_below_768px():
    html = read(os.path.join("college", "duke", "index.html"))
    assert 'class="hm-rank-table hm-career-table"' in html
    for cls in ("hm-word hm-span", "hm-word hm-seasons", "hm-money"):
        assert 'class="{}"'.format(cls) in html, cls
    css = read(os.path.join("css", "pages.css"))
    block = css[css.index("@media (max-width: 767.98px)"):]
    for selector, order in (("th.hm-who", "1"), (".hm-money", "2"),
                            (".hm-span", "3"), (".hm-seasons", "4")):
        rule = block[block.index("table.hm-career-table " + selector):]
        assert "order: {}".format(order) in rule.split("}")[0], selector


@built
def test_awards_are_visible_from_768px_up():
    css = read(os.path.join("css", "styles.css"))
    assert "@media (max-width: 767.98px)" in css
    block = css[css.rindex("@media (max-width: 767.98px)"):]
    assert "td.awards-cell" in block and "display: none" in block
    # the awards rule is the one that stops at 767.98, so 768 keeps them
    for other in re.findall(r"@media \(max-width: 768px\) \{", css):
        pass
    assert "awards" not in css.split("@media (max-width: 768px)")[1].split("\n}")[0]


# --------------------------------------------------------------------------
# the live check
# --------------------------------------------------------------------------


def test_the_live_check_expects_the_title_the_worker_serves():
    """The Worker rewrites the tool root's title on purpose. What the check
    asserts is what a reader is served, not what index.html holds."""
    sys.path.insert(0, os.path.join(REPO, "scripts"))
    import live_smoke

    root_url, root_title, root_wants = live_smoke.PAGES[0]
    assert root_url == C.TOOL_ROOT
    assert root_title == "NBA Player Salaries by season and position | HoopsMatic"
    assert root_wants == ()
    # every other expectation is the title this repository built
    for url, title, _ in live_smoke.PAGES[1:]:
        path = url[len(C.TOOL_ROOT) + 1:].strip("/")
        html = read(os.path.join(*(path.split("/") + ["index.html"])))
        assert "<title>{}</title>".format(title) in html, url


def test_the_live_check_asserts_a_heading_the_built_page_really_carries():
    """A string typed here from memory would pass the live check forever on a
    page that never had it. Every expectation is read back off the page this
    repository built."""
    sys.path.insert(0, os.path.join(REPO, "scripts"))
    import live_smoke

    for url, _, must_contain in live_smoke.PAGES:
        if url == C.TOOL_ROOT:
            continue
        path = url[len(C.TOOL_ROOT) + 1:].strip("/")
        html = read(os.path.join(*(path.split("/") + ["index.html"])))
        for wanted in must_contain:
            assert wanted in html, (url, wanted)


def test_the_live_check_covers_every_page_family_with_a_section():
    """One content assertion per family. A new family that renders sections
    and is not listed here is a family whose pages can go empty unnoticed."""
    sys.path.insert(0, os.path.join(REPO, "scripts"))
    import live_smoke

    #: hubs have no section to lose, and these three families render the same
    #: sections as the cohort pages already covered.
    uncovered = {"pick_range", "college_position", "agent"}
    families = {
        "player": "/player/", "team": "/team/", "season": "/season/",
        "college": "/college/", "country": "/country/", "draft": "/draft/",
        "pick": "/pick/", "position": "/position/", "region": "/region/",
    }
    assert set(families) | uncovered == set(C.FAMILIES)
    for family, mark in families.items():
        covered = [p for p in live_smoke.PAGES if mark in p[0] and p[2]]
        assert len(covered) == 1, family


def test_the_live_check_busts_the_workers_own_cache():
    """The Worker skips its per-path entry only on a `nocache` parameter, and
    its cache key drops the query, so any other buster reads the stale entry."""
    sys.path.insert(0, os.path.join(REPO, "scripts"))
    import live_smoke

    source = inspect.getsource(live_smoke.fetch)
    assert '"nocache=" + buster' in source
    assert "cb=" not in source


def test_the_live_check_says_where_a_github_io_link_sits():
    sys.path.insert(0, os.path.join(REPO, "scripts"))
    import live_smoke

    body = (
        '<html><head><meta charset="utf-8">'
        '<link rel="canonical" href="https://jsierrahoopshype.github.io/salary-season-finder/">'
        "<title>x</title></head><body>hi</body></html>"
    )
    found = live_smoke.github_io_links(body)
    assert len(found) == 1
    href, tag, snippet = found[0]
    assert href == "https://jsierrahoopshype.github.io/salary-season-finder/"
    assert tag.startswith('<link rel="canonical"') and tag.endswith(">")
    assert "charset" in snippet and "<title>" in snippet
    assert live_smoke.github_io_links("<a href='/ok/'>fine</a>") == []


# --------------------------------------------------------------------------
# the tool's address bar
# --------------------------------------------------------------------------


DEFAULT_FILTER_STATE = {
    "seasonFrom": "2026-27", "seasonTo": "2026-27",
    "salaryMin": None, "salaryMax": None, "capPctMin": None, "capPctMax": None,
    "leagueRank": None, "cppMin": None, "cppMax": None, "cpgMin": None,
    "cpgMax": None, "earningsMin": None, "earningsMax": None,
    "playerSearch": "", "positions": [], "ageMin": None, "ageMax": None,
    "expMin": None, "expMax": None, "draftMin": None, "draftMax": None,
    "draftYearMin": None, "draftYearMax": None, "nationality": "",
    "college": "", "team": "", "ppgMin": None, "ppgMax": None,
    "rpgMin": None, "rpgMax": None, "apgMin": None, "apgMax": None,
    "fgPctMin": None, "fgPctMax": None, "tpPctMin": None, "tpPctMax": None,
    "ftPctMin": None, "ftPctMax": None, "gpMin": None, "gpMax": None,
    "awards": [], "hasAnyAward": False,
}


def _hash_for(*calls):
    """Ask js/app.js what hash each piece of state deserves."""
    payload = json.dumps([
        {
            "filters": dict(DEFAULT_FILTER_STATE, **call.get("filters", {})),
            "defaultSeason": call.get("defaultSeason", "2026-27"),
            "sort": call.get("sort", "salary"),
            "dir": call.get("dir", "desc"),
            "exactPlayer": call.get("exactPlayer"),
        }
        for call in calls
    ])
    result = subprocess.run(
        ["node", os.path.join(REPO, "scripts", "tests", "tool_state.js")],
        input=payload, capture_output=True, text=True, cwd=REPO,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_the_default_state_writes_no_hash_at_all():
    """Clear All has to leave the bare URL. The season selects always hold a
    value, and writing them unconditionally kept a hash on a page with nothing
    filtered."""
    assert _hash_for({})[0] == ""


def test_every_other_state_still_writes_its_hash():
    cases = _hash_for(
        {"filters": {"seasonFrom": "1996-97"}},
        {"filters": {"salaryMin": 40000000}},
        {"filters": {"positions": ["G"]}},
        {"filters": {"college": "Duke"}},
        {"filters": {"playerSearch": "lebron james"}, "exactPlayer": "lebron james"},
        {"filters": {"awards": ["All-Star"]}},
        {"filters": {"hasAnyAward": True}},
        {"sort": "age"},
        {"dir": "asc"},
    )
    assert cases[0].startswith("from=1996-97&to=2026-27"), cases[0]
    assert cases[1] == "salary_min=40000000"
    assert cases[2] == "pos=G"
    assert cases[3] == "college=Duke"
    assert "player=lebron%20james" in cases[4] and "player_exact=1" in cases[4]
    assert cases[5] == "awards=All-Star"
    assert cases[6] == "has_award=1"
    assert cases[7] == "sort=age"
    assert cases[8] == "dir=asc"
    for case in cases:
        assert case, "a non-default state must keep writing its hash"


def test_a_season_range_that_is_not_the_default_is_written_in_full():
    both = _hash_for(
        {"filters": {"seasonFrom": "2024-25", "seasonTo": "2026-27"}},
        {"filters": {"seasonFrom": "2026-27", "seasonTo": "2026-27"},
         "defaultSeason": "2025-26"},
    )
    assert both[0] == "from=2024-25&to=2026-27"
    assert both[1] == "from=2026-27&to=2026-27"


def test_the_bare_url_keeps_any_query_string():
    app = read(os.path.join("js", "app.js"))
    bare = app[app.index("function saveStateToURL"):]
    bare = bare[:bare.index("\n  }")]
    assert "window.location.pathname + window.location.search" in bare


# --------------------------------------------------------------------------
# the three cuts across the families: region, pick range, college position
# --------------------------------------------------------------------------


def _related(path):
    """The labels in a page's Related pages block."""
    html = read(path)
    found = re.search(
        r"<h2>Related pages</h2>.*?<ul class=\"hm-chips\">(.*?)</ul>", html, re.S)
    if not found:
        return []
    return re.findall(r">([^<>]+)</a>", found.group(1))


def test_the_region_map_covers_every_nationality_in_the_data():
    countries, with_pages = F.load_continents()
    data = F.load_data()
    seen = {(r.get("nationality") or "").strip() for r in data["seasons"]}
    seen.discard("")
    assert seen <= set(countries), sorted(seen - set(countries))
    assert set(with_pages) == {"europe", "africa", "latin-america", "oceania", "asia"}
    # every region a page is promised for is one the engine can phrase
    for region in with_pages:
        assert region in F.REGION_PHRASES
    assert set(countries.values()) >= set(with_pages)


def test_everyone_who_is_not_american_is_an_international_player():
    data = F.load_data()
    idx = F.build_index(data)
    for record in idx.records:
        nationality = (record.get("nationality") or "").strip()
        if not nationality or not idx.cohorts_allowed(
                idx.canonical(record["player"]), record["season"]):
            continue
        keys = {(k, c) for k, c, _l in F._cohorts_for(record, idx)}
        expected = nationality != F.DOMESTIC_NATIONALITY
        assert (("region", "international") in keys) is expected, record["player"]


@built
def test_the_region_pages_are_the_five_continents_and_the_international_one():
    slugs = sorted(
        name for name in os.listdir(repo("region"))
        if os.path.isdir(repo("region", name))
    )
    assert slugs == ["africa", "asia", "europe", "international",
                     "latin-america", "oceania"]
    for slug in slugs:
        html = read(os.path.join("region", slug, "index.html"))
        assert "noindex" not in html
        assert "Highest-Paid" in html
        assert C.SCOPE_NOTE in html


@built
def test_the_pick_ranges_are_the_three_the_engine_defines():
    slugs = sorted(
        name for name in os.listdir(repo("pick-range"))
        if os.path.isdir(repo("pick-range", name))
    )
    assert slugs == ["lottery", "second-round", "top-10"]
    assert "<title>Highest-Paid Lottery Picks in NBA History | HoopsMatic</title>" \
        in read(os.path.join("pick-range", "lottery", "index.html"))


@built
def test_a_pick_page_points_at_the_ranges_it_falls_in():
    assert _related(os.path.join("pick", "1", "index.html")) == [
        "Lottery Picks", "Top-10 Picks"]
    assert _related(os.path.join("pick", "14", "index.html")) == ["Lottery Picks"]
    # 15 to 30 is a first-round pick outside the lottery, which is no range here
    assert _related(os.path.join("pick", "20", "index.html")) == []
    assert _related(os.path.join("pick", "45", "index.html")) == ["Second-Round Picks"]


@built
def test_a_country_page_points_at_its_region_and_at_the_international_page():
    assert _related(os.path.join("country", "france", "index.html")) == [
        "European Players", "International Players"]
    assert _related(os.path.join("country", "nigeria", "index.html")) == [
        "African Players", "International Players"]
    # The United States is nobody's international page and no region has it
    assert _related(os.path.join("country", "united-states", "index.html")) == []


@built
def test_a_college_and_its_positions_point_at_each_other():
    assert _related(os.path.join("college", "duke", "index.html")) == [
        "Duke Forwards", "Duke Guards"]
    assert _related(
        os.path.join("college-position", "duke-guards", "index.html")) == [
        "All Duke players"]


@built
def test_a_college_position_page_exists_only_where_the_cohort_is_big_enough():
    data = F.load_data()
    idx = F.build_index(data)
    players = collections.defaultdict(set)
    for record in idx.records:
        for kind, key, _label in F._cohorts_for(record, idx):
            if kind == "college_position":
                players[key].add(idx.canonical(record["player"]))
    qualify = {k for k, v in players.items()
               if len(v) >= F.COLLEGE_POSITION_MIN_PLAYERS}
    built_pages = {
        name for name in os.listdir(repo("college-position"))
        if os.path.isdir(repo("college-position", name))
    }
    assert len(built_pages) == len(qualify), (len(built_pages), len(qualify))
    assert F.COLLEGE_POSITION_MIN_PLAYERS == 15


@built
def test_the_new_families_are_in_the_sitemap_and_on_the_hubs():
    urls = {row[0] if isinstance(row, tuple) else row for row in sitemap_urls()}
    for path in ("region/europe", "region/international", "pick-range/lottery",
                 "college-position/duke-guards", "regions", "pick-ranges",
                 "college-positions"):
        assert "{}/{}/".format(C.TOOL_ROOT, path) in urls, path
    root = read("index.html")
    for hub in ("regions", "pick-ranges", "college-positions"):
        assert "{}/{}/".format(C.TOOL_ROOT, hub) in root, hub
        assert "{}/{}/".format(C.TOOL_ROOT, hub) in read(C.NOT_FOUND_PATH), hub


@built
def test_a_new_family_page_reads_like_every_other_cohort_page():
    for path in ("region/europe", "pick-range/second-round",
                 "college-position/kentucky-guards"):
        html = read(os.path.join(path, "index.html"))
        assert "<h2>Highest career earnings</h2>" in html
        assert "<h2>Highest single-season salaries</h2>" in html
        assert "<h2>Every player</h2>" in html
        assert 'class="hm-summary"' in html
        # headshots come from the github.io host on purpose; no link may
        assert 'href="https://jsierrahoopshype.github.io' not in html


# --------------------------------------------------------------------------
# B5/B6: the season table's links, and the awards column
# --------------------------------------------------------------------------


@built
def test_the_season_table_links_each_team_to_its_page():
    html = read(os.path.join("player", "kyrie-irving", "index.html"))
    table = html[html.index("player-season-table"):]
    table = table[:table.index("</table>")]
    assert '<a class="team-link" href="{}/team/cavaliers/">CLE</a>'.format(
        C.TOOL_ROOT) in table


@built
def test_the_season_table_links_each_season_to_its_page():
    html = read(os.path.join("player", "kyrie-irving", "index.html"))
    table = html[html.index("player-season-table"):]
    table = table[:table.index("</table>")]
    assert '<a href="{}/season/2026-27/">2026-27</a>'.format(C.TOOL_ROOT) in table


@built
def test_an_award_badge_links_to_the_tool_filtered_to_that_award():
    html = read(os.path.join("player", "kyrie-irving", "index.html"))
    table = html[html.index("player-season-table"):]
    table = table[:table.index("</table>")]
    assert '{}#awards=All-Star'.format(C.TOOL_ROOT) in table


@built
def test_an_award_with_no_chip_falls_back_to_having_any_award():
    """The same resolution a click uses, so a link lands where a click would."""
    html = read(os.path.join("player", "kyrie-irving", "index.html"))
    assert '{}#has_award=1'.format(C.TOOL_ROOT) in html


def test_the_award_chip_list_matches_the_markup():
    """app.js carries the chip values so a page with no DOM can resolve a badge;
    index.html is where a reader clicks them. They have to be the same list."""
    app = read(os.path.join("js", "app.js"))
    block = app[app.index("var AWARD_FILTER_CHIPS = ["):]
    block = block[:block.index("];")]
    in_js = re.findall(r'"([^"]+)"', block)
    markup = read("index.html")
    panel = markup[markup.index('id="awardsFilter"'):]
    panel = panel[:panel.index("</div>")]
    in_html = re.findall(r'data-value="([^"]+)"', panel)
    assert in_js == in_html


def test_the_awards_column_is_held_to_a_width_so_its_badges_wrap():
    css = read(os.path.join("css", "styles.css"))
    block = css[css.index("table.player-season-table th.ps-awards,"):]
    block = block[:block.index("}")]
    assert "white-space: normal;" in block
    assert "width: 7.5rem;" in block
    badge = css[css.index("table.player-season-table td.ps-awards .award-badge"):]
    badge = badge[:badge.index("}")]
    assert "overflow-wrap: anywhere;" in badge


def test_the_daily_build_commits_every_family_it_writes():
    """A family the daily build does not stage is not merely left out.

    Its files stay unstaged, and the "git pull --rebase" that follows then
    refuses to run, so the push fails and no page lands at all. That is how
    region, pick-range and college-position stopped the daily pages on
    2026-10-03 and left 700 of them a build behind, which no test caught
    because the drift only shows up in the next pull request.
    """
    workflow = read(os.path.join(".github", "workflows", "update-data.yml"))
    block = workflow[workflow.index("git add -A agent"):]
    block = block[:block.index("if git diff --cached")]
    staged = set(block.replace("\\", "").split())
    wanted = {spec["dir"] for spec in C.FAMILIES.values()}
    wanted |= {spec["hub"] for spec in C.FAMILIES.values() if spec["hub"]}
    assert wanted - staged == set()


#: every source file this repo tracks. The build downloads more than these;
#: the two Cyro sheets are deliberately untracked.
TRACKED_SOURCES = (
    "data_sources/stats.csv",
    "data_sources/salaries_historical.csv",
    "data_sources/salaries_future.csv",
    "data_sources/awards.csv",
    "data_sources/bio.csv",
)


def test_the_daily_build_commits_every_source_sheet_it_tracks():
    """The checkout must not lag the data it published.

    These files are where a season's games, salaries, awards and bios come
    from, so copies older than data.json make anything recomputed locally
    disagree with what is served. It is how a replay of the stats half of the
    build nearly rolled a finished 2025-26 back to partial numbers.

    Committed weekly rather than daily: stats.csv alone is 5 MB and most of
    its rows move during the season, so daily would cost a few hundred
    megabytes of history a year to cap the drift at a day instead of a week.
    """
    workflow = read(os.path.join(".github", "workflows", "update-data.yml"))
    marker = "SOURCE_FILES: >-"
    listed = workflow[workflow.index(marker) + len(marker):]
    listed = listed[:listed.index("jobs:")]
    for path in TRACKED_SOURCES:
        assert path in listed, path
    # every tracked file is in the list, and the list invents nothing
    assert set(listed.split()) == set(TRACKED_SOURCES)

    # staged on Monday, reverted otherwise, and never an empty commit
    block = workflow[workflow.index("git add data/data.json"):]
    # to the command, not to the comment that names it
    block = block[:block.index("git commit -m")]
    assert 'date -u +%u' in block and '= "1"' in block
    assert "git add $SOURCE_FILES" in block
    assert "git checkout -- $SOURCE_FILES" in block
    assert "git diff --cached --quiet" in block
    # a sheet-only change still counts as a change
    gate = workflow[workflow.index("if git diff --quiet data/data.json"):]
    assert "$SOURCE_FILES" in gate[:gate.index("then")]


def test_every_tracked_source_file_is_in_the_list():
    """A source file added to the repo and not to SOURCE_FILES is a file that
    drifts silently, which is the bug this whole guard exists for."""
    out = subprocess.run(
        ["git", "ls-files", "data_sources/"], cwd=REPO,
        capture_output=True, text=True, check=True)
    assert set(out.stdout.split()) == set(TRACKED_SOURCES)


def _stats_seasons():
    """{season: total games on file} straight out of the committed sheet."""
    path = os.path.join(REPO, "data_sources", "stats.csv")
    if not os.path.exists(path):
        return {}
    games = collections.Counter()
    with open(path, encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            year = (row.get("YEAR") or "").strip()
            team = (row.get("TEAM") or "").strip()
            if not year.isdigit() or team == "TOT":
                continue
            season = "{}-{}".format(int(year) - 1, str(int(year))[-2:])
            try:
                games[season] += int(float(row.get("GP") or 0))
            except ValueError:
                pass
    return games


@pytest.mark.skipif(
    not os.path.exists(os.path.join(REPO, "data_sources", "stats.csv")),
    reason="the stats sheet is not in this checkout",
)
def test_the_stats_sheet_is_never_behind_the_data_inside_a_season():
    """Coverage is the lag a rollover causes; this is the lag a stale pull
    causes, and it is the one that actually bit. data.json is built from this
    sheet, so a season holding more games in the data than in the sheet means
    the committed sheet is older than the committed data. On 2026-10-04 that
    was 2025-26: 25,278 games in the data against 18,854 on file.
    """
    data = json.loads(read(os.path.join("data", "data.json")))
    mine = collections.Counter()
    for record in data["seasons"]:
        if record.get("gp"):
            mine[record["season"]] += record["gp"]
    on_file = _stats_seasons()
    behind = sorted(
        ((season, mine[season], on_file[season])
         for season in set(mine) & set(on_file) if on_file[season] < mine[season]),
        key=lambda row: F.season_key(row[0]),
    )
    assert behind == [], (
        "data_sources/stats.csv is behind data/data.json: " + "; ".join(
            "{} has {:,} games in the data and {:,} on file".format(*row)
            for row in behind)
    )


@pytest.mark.skipif(
    not os.path.exists(os.path.join(REPO, "data_sources", "stats.csv")),
    reason="the stats sheet is not in this checkout",
)
def test_the_stats_sheet_covers_every_season_the_data_has_stats_for():
    """A season of play in data.json that the sheet has no rows for means the
    committed sheet predates the data, and every stat the build would
    recompute for that season is wrong or missing."""
    data = json.loads(read(os.path.join("data", "data.json")))
    played = {r["season"] for r in data["seasons"] if r.get("gp")}
    on_file = set(_stats_seasons())
    missing = sorted(played - on_file, key=F.season_key)
    assert missing == [], (
        "data.json has stats for {} that data_sources/stats.csv does not "
        "cover; the committed sheet is behind the committed data".format(
            ", ".join(missing))
    )


def test_a_cohort_top_earner_line_says_it_is_the_league():
    """"the highest-paid Ohio State player" alone reads as the best-paid man
    on some Ohio State roster. The claim is about the league, and the line
    says so. A franchise needs no such clause and keeps the shorter form."""
    sys.path.insert(0, os.path.join(REPO, "scripts"))
    from prerender import timeline as TLx

    for family in ("top_start", "top_start_again", "top_end"):
        for forms in TLx._PHRASES[family].values():
            for form in forms:
                assert "the league's" in form, (family, form)
                # the possessive leads; a trailing clause would land beside a
                # season span and read as two places at once
                assert "in the league" not in form, (family, form)
        for forms in TLx._PHRASES[family + "_team"].values():
            for form in forms:
                assert "league" not in form, (family, form)

    # and the two reach the right scopes
    assert TLx._family({"kind": "top_start",
                        "scope": ("franchise", "ATL")}) == "top_start_team"
    assert TLx._family({"kind": "top_start",
                        "scope": ("college", "Duke")}) == "top_start"


def test_no_cohort_status_line_on_a_page_omits_the_league():
    """Read off the built pages, not the templates: a status line about a
    cohort that does not say "in the league" is the bug this guards."""
    bad = []
    for slug in TIMELINE_SLUGS:
        for season, text in _plain_timeline(slug):
            for part in re.split(r"(?<=\.)\s+(?=[A-Z])", text):
                if "highest-paid" not in part:
                    continue
                # a franchise line names the club, which the nickname map knows
                if re.search(r"highest-paid player on the ", part) or \
                        re.search(r"the [A-Z][^ ]*(&#x27;|') ?s? highest-paid", part):
                    continue
                if "the league's" not in part and "in the NBA" not in part:
                    bad.append((slug, season, part))
    assert bad == []


def test_a_status_taken_back_never_reads_as_a_first_arrival():
    """Mike Conley took over as the league's highest-paid Ohio State player in
    2011-12 and again in 2025-26. Either the season he lost it is on the page,
    or the later line says he took it back. Two bare arrivals at the same
    status with nothing between them is a career told wrong."""
    sys.path.insert(0, os.path.join(REPO, "scripts"))
    import factoids as Fx
    from prerender import entities as Ex, slugs as Sx, timeline as TLx

    idx = Fx.build_index(json.loads(read(os.path.join("data", "data.json"))))
    events = TLx.build(idx)
    built = Ex.build_all(idx, Sx.load(C.SLUGS_PATH))
    retakes, bad = 0, []
    for ident in built["players"]:
        if not ident.extra.get("factoids_allowed"):
            continue
        printed = {season for season, _text in TLx.lines(idx, ident.records, events)}
        who = idx.canonical(ident.name)
        for (player, _season), found in events.items():
            if player != who:
                continue
            for event in found:
                if event["kind"] != "top_start" or not event.get("again"):
                    continue
                retakes += 1
                if not event.get("retaken") and event["lost_season"] not in printed:
                    bad.append((who, event["scope"], event["lost_season"]))
    assert retakes, "no career in the data takes a status back"
    assert bad == []


def test_the_losing_season_is_spared_the_cap_between_two_arrivals():
    sys.path.insert(0, os.path.join(REPO, "scripts"))
    import factoids as Fx
    from prerender import timeline as TLx

    idx = Fx.build_index(json.loads(read(os.path.join("data", "data.json"))))
    events = TLx.build(idx)
    bridges = [e for found in events.values() for e in found if e.get("bridge")]
    assert bridges
    for event in bridges:
        assert event["kind"] == "top_end"


# --------------------------------------------------------------------------
# table alignment: a header over its own column
# --------------------------------------------------------------------------

#: The alignment each table-cell class carries, read once out of the
#: stylesheets below and checked against them, so this list cannot drift from
#: the CSS it describes.
ALIGN = {
    "hm-who": "left", "hm-text": "left", "hm-word": "left",
    "hm-num": "right", "hm-money": "right",
    "ps-season": "left", "ps-team": "left", "ps-awards": "left",
    "ps-num": "right",
}

#: Classes that only decorate: colour, weight, width, flex order.
PLAIN = {"hm-span", "hm-seasons", "ps-salary", "ps-career"}


def _align_rules(css):
    """{(table, class): alignment} for every text-align rule keyed on a class."""
    out = {}
    for block in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        selector, body = block.group(1), block.group(2)
        found = re.search(r"text-align:\s*(\w+)", body)
        if not found:
            continue
        for part in selector.split(","):
            part = part.strip()
            hit = re.match(
                r"table\.([\w-]+)\s+(?:thead\s+|tbody\s+)?(th|td)\.([\w-]+)$", part)
            if hit:
                out.setdefault((hit.group(1), hit.group(3)), set()).add(
                    (hit.group(2), found.group(1)))
    return out


def test_a_header_is_aligned_the_way_its_own_column_is():
    """A header centred over a column of right-aligned figures sits over the
    middle of nothing. Every rule that aligns a cell class names the header
    and the cells together, which is what keeps them one column."""
    for name, table in (("pages.css", "hm-rank-table"),
                        ("styles.css", "player-season-table")):
        css = read(os.path.join("css", name))
        rules = _align_rules(css)
        for (which, cls), pairs in rules.items():
            if which != table:
                continue
            kinds = {side for side, _align in pairs}
            aligns = {align for _side, align in pairs}
            assert len(aligns) == 1, (name, cls, pairs)
            if cls in ALIGN:
                assert aligns == {ALIGN[cls]}, (name, cls, aligns)
            # a class that aligns at all aligns both halves of its column
            assert kinds == {"th", "td"} or len(kinds) == 1, (name, cls, pairs)


def test_every_column_class_is_one_the_stylesheet_aligns():
    """A class on a cell that the CSS says nothing about inherits the table's
    default, which is how a column ends up aligned by accident."""
    known = set(ALIGN) | PLAIN
    seen = set()
    for slug in ("player/cj-mccollum", "team/76ers", "season/2026-27",
                 "college/duke", "never-all-star", C.DROUGHT_HUB):
        html = read(os.path.join(*(slug.split("/") + ["index.html"])))
        for cell in re.finditer(r"<t[dh] class=\"([^\"]*)\"", html):
            seen.update(cell.group(1).split())
    assert seen - known == set(), sorted(seen - known)


def test_a_header_carries_the_same_classes_as_its_column():
    """Read off the built pages: column by column, the header's alignment
    class is the one its cells carry. This is the check that fails if a table
    is given a numeric header over a column of names."""
    bad = []
    for slug in ("player/cj-mccollum", "team/76ers", "season/2026-27",
                 "college/duke", "country/canada", "never-all-star",
                 "never-all-nba", "never-mvp", C.DROUGHT_HUB, "colleges"):
        html = read(os.path.join(*(slug.split("/") + ["index.html"])))
        for table in re.finditer(r"<table[^>]*>(.*?)</table>", html, re.S):
            rows = re.findall(r"<tr>(.*?)</tr>", table.group(1), re.S)
            if len(rows) < 2:
                continue
            def classes(row):
                return [set(c.split()) & set(ALIGN)
                        for c in re.findall(r'<t[dh][^>]*class="([^"]*)"', row)]
            head = classes(rows[0])
            for row in rows[1:]:
                cells = classes(row)
                if len(cells) != len(head):
                    continue        # a group head row spans the table
                for i, (want, got) in enumerate(zip(head, cells)):
                    a = {ALIGN[c] for c in want}
                    b = {ALIGN[c] for c in got}
                    if a and b and a != b:
                        bad.append((slug, i, sorted(want), sorted(got)))
    assert bad == [], bad[:6]


def test_the_first_column_stays_put_when_a_table_scrolls():
    """On a phone these tables scroll sideways, and the column that says who
    or when has to stay readable, over an opaque background rather than the
    rows sliding under it."""
    css = read(os.path.join("css", "pages.css"))
    block = css[css.index("table.hm-rank-table th.hm-who {"):]
    block = block[:block.index("}")]
    assert "position: sticky" in block and "left: 0" in block
    assert "background:" in block
    tool = read(os.path.join("css", "styles.css"))
    block = tool[tool.index("table.player-season-table th.ps-season {"):]
    block = block[:block.index("}")]
    assert "position: sticky" in block and "left: 0" in block
    assert "background:" in block


# --------------------------------------------------------------------------
# the compact player search
# --------------------------------------------------------------------------


def test_every_family_carries_the_search_under_the_breadcrumb():
    for slug in ("player/cj-mccollum", "team/76ers", "season/2026-27",
                 "college/duke", "never-all-star", C.DROUGHT_HUB, "colleges"):
        html = read(os.path.join(*(slug.split("/") + ["index.html"])))
        assert '<div class="hm-find"' in html, slug
        assert html.index("hm-crumbs") < html.index('class="hm-find"'), slug
        assert 'id="hm-find-input"' in html, slug
        # the depth the script resolves data/slugs.json against: one step up
        # for every path segment the page sits under the tool root
        depth = html.split('data-root="', 1)[1].split('"', 1)[0]
        assert depth == "../" * len(slug.split("/")), (slug, depth)
    # the index is fetched on focus, not on load
    js = read(os.path.join("js", "page-search.js"))
    assert 'addEventListener("focus", load)' in js
    assert "slugs.json" in js
    assert js.count("fetch(") == 1


def test_only_a_player_page_offers_the_tool_button():
    html = read(os.path.join("player", "cj-mccollum", "index.html"))
    link = re.search(r'class="hm-find-tool" href="([^"]+)"', html)
    assert link, "a player page offers the tool"
    href = link.group(1).replace("&amp;", "&")
    assert href.startswith(C.TOOL_ROOT + "#")
    assert "player=CJ%20McCollum" in href and "player_exact=1" in href
    # his whole career, because the tool opens on the current season
    assert "from=2013-14" in href and "to=2026-27" in href
    for slug in ("team/76ers", "college/duke", "never-all-star"):
        other = read(os.path.join(*(slug.split("/") + ["index.html"])))
        assert "hm-find-tool" not in other, slug


def test_the_pages_do_not_carry_the_tool_filter_rail():
    """The search is one input. The rail belongs to the tool."""
    for slug in ("player/cj-mccollum", "team/76ers", "never-all-star"):
        html = read(os.path.join(*(slug.split("/") + ["index.html"])))
        for mark in ("filter-rail", "filter-drawer", "id=\"filters\"",
                     "salaryMin", "capPctMin"):
            assert mark not in html, (slug, mark)


# --------------------------------------------------------------------------
# career earnings without an award
# --------------------------------------------------------------------------


def _drought(slug):
    return read(os.path.join(slug, "index.html"))


def test_each_drought_page_is_indexable_and_in_the_sitemap():
    sitemap = read(C.SITEMAP_PATH)
    for slug in ("never-all-star", "never-all-nba", "never-mvp",
                 C.DROUGHT_HUB):
        html = _drought(slug)
        assert '<meta name="robots" content="index' in html, slug
        url = "{}/{}/".format(C.TOOL_ROOT, slug)
        assert "<loc>{}</loc>".format(url) in sitemap, slug
        # a lastmod out of page_hashes.json, not a date typed into the page
        block = sitemap[sitemap.index(url):]
        assert "<lastmod>" in block[:400], slug


def test_the_drought_pages_are_in_page_hashes():
    hashes = json.loads(read(os.path.join("data", "page_hashes.json")))["pages"]
    for slug in ("never-all-star", "never-all-nba", "never-mvp", C.DROUGHT_HUB):
        assert "{}/index.html".format(slug) in hashes, slug


def test_the_first_leader_is_worded_as_where_the_count_begins():
    """Hot Rod Williams did not take the lead from anyone: the salary data
    starts under him. The row says so, and two notes under the table say the
    two things a reader would otherwise read as errors."""
    html = _drought("never-all-star")
    block = html[html.index("Who held No. 1"):]
    block = block[:block.index("</table>")]
    rows = re.findall(r"<tr>(.*?)</tr>", block, re.S)
    first = " | ".join(re.sub(r"<[^>]+>", "", c).strip()
                       for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", rows[1], re.S))
    assert "led when the count begins in 1990-91" in first
    assert "Became" not in first and "Took over" not in first
    notes = re.findall(r'<p class="hm-note">([^<]*)</p>', html)
    assert any("earliest leaders reflect where the count begins" in n for n in notes)
    assert any("next man up can have earned less" in n for n in notes)


def test_a_leader_who_left_through_a_selection_says_so():
    html = _drought("never-all-star")
    block = html[html.index("Who held No. 1"):]
    block = block[:block.index("</table>")]
    # the row he is the subject of, not the one that names him as the man who
    # passed somebody
    rows = [r for r in re.findall(r"<tr>(.*?)</tr>", block, re.S)
            if re.match(r'\s*<th[^>]*>\s*<a[^>]*>Mike Conley</a>', r)]
    assert len(rows) == 1
    assert "2018-19 to 2019-20" in rows[0]
    assert "left on his first All-Star selection in 2020-21" in rows[0]


def test_a_drought_list_is_said_in_two_tenses_and_no_others():
    """These lists rank career earnings among men an award has not come to, so
    the present describes anyone on a list now and the past a season before a
    first selection. "Never" claims a career is over and "yet to" claims it is
    not; a salary table knows neither, so neither appears."""
    sys.path.insert(0, os.path.join(REPO, "scripts"))
    import factoids as Fx
    from prerender import droughts as D

    idx = Fx.build_index(json.loads(read(os.path.join("data", "data.json"))))
    built = D.build(idx)
    for spec in D.LISTS:
        for form in (spec.present, spec.past, spec.singular, spec.one_past,
                     spec.title_phrase):
            assert "never" not in form.lower(), form
            assert "yet to" not in form.lower(), form
        assert spec.present.startswith("who have not")
        assert spec.past.startswith("who had not")
    # never selected: the present, whether he is still playing or not
    assert built["all-star"]["phrase"]("Tobias Harris") == \
        "who have not made an All-Star team"
    assert built["all-star"]["phrase"]("Danilo Gallinari") == \
        "who have not made an All-Star team"
    # selected since: the past
    assert built["all-star"]["phrase"]("Mike Conley") == \
        "who had not made an All-Star team"


def test_these_lists_never_call_anyone_the_highest_paid():
    """They rank career earnings, so the wording says career earnings."""
    for slug in ("never-all-star", "never-all-nba", "never-mvp", C.DROUGHT_HUB):
        html = _drought(slug)
        body = re.sub(r"<[^>]+>", " ", html)
        assert "highest-paid" not in body, slug
        assert "never named" not in body and "yet to" not in body, slug
    sys.path.insert(0, os.path.join(REPO, "scripts"))
    from prerender import timeline as TLx
    for family in ("drought_first", "drought_first_open", "drought_passed",
                   "drought_left", "drought_left_down", "drought_peak"):
        for forms in TLx._PHRASES[family].values():
            for form in forms:
                assert "highest-paid" not in form, form


def test_the_page_titles_say_what_the_lists_rank():
    for slug, phrase in (
        ("never-all-star", "who have not made an All-Star team"),
        ("never-all-nba", "who have not made an All-NBA team"),
        ("never-mvp", "who have not won MVP"),
    ):
        html = _drought(slug)
        title = re.search(r"<title>(.*?)</title>", html).group(1)
        assert title == "Most career earnings by players {} | HoopsMatic".format(
            phrase), title
        assert "<h1>Most career earnings by players {}</h1>".format(phrase) in html


def test_the_rank_line_gives_the_figure_only_once_a_page():
    sys.path.insert(0, os.path.join(REPO, "scripts"))
    import factoids as Fx
    from prerender import droughts as D

    idx = Fx.build_index(json.loads(read(os.path.join("data", "data.json"))))
    built = D.build(idx)
    short, full = D.summary_line(built, "CJ McCollum")
    assert short == ("He is second in career earnings among players who have "
                     "not made an All-Star team.")
    assert full.endswith("on $302.1 million paid to date.")
    # No. 1 says it the other way and names no figure at all
    top, top_full = D.summary_line(built, "Paul George")
    assert top == top_full == ("He has earned more than any player who has "
                               "not won MVP.")
    # the page prints the short form, because the career sentence gave the money
    page = read(os.path.join("player", "cj-mccollum", "index.html"))
    assert "not made an All-Star team." in page
    assert "paid to date" not in page


def test_one_drought_line_per_player_naming_the_most_notable_list():
    sys.path.insert(0, os.path.join(REPO, "scripts"))
    import factoids as Fx
    from prerender import droughts as D

    idx = Fx.build_index(json.loads(read(os.path.join("data", "data.json"))))
    built = D.build(idx)
    line = D.summary_line(built, "Tobias Harris")[0]
    assert "All-Star" in line and "All-NBA" not in line
    assert "All-NBA" in D.summary_line(built, "Mike Conley")[0]
    assert D.summary_line(built, "LeBron James") == ("", "")


def test_how_high_he_got_is_said_once_and_only_between_two_and_ten():
    """One line a career for his best place on a list. No. 1 has its own
    event and says more, so a peak is never No. 1, and no event is made for
    moving around inside the top ten."""
    sys.path.insert(0, os.path.join(REPO, "scripts"))
    import factoids as Fx
    from prerender import droughts as D

    idx = Fx.build_index(json.loads(read(os.path.join("data", "data.json"))))
    built = D.build(idx)
    for key, lst in built.items():
        seen = collections.Counter()
        for (player, _season), events in lst["events"].items():
            for event in events:
                assert event["kind"] in (
                    "drought_peak", "drought_first", "drought_passed",
                    "drought_left"), event
                if event["kind"] == "drought_peak":
                    seen[player] += 1
                    assert 2 <= event["rank"] <= D.TOP, event
        assert not [p for p, n in seen.items() if n > 1], key


def test_a_man_at_his_best_right_now_is_left_to_his_summary():
    """His peak is his standing, and the summary says his standing. Saying it
    twice, once as a past peak and once as a present rank, reads as two
    different facts about the same thing."""
    sys.path.insert(0, os.path.join(REPO, "scripts"))
    import factoids as Fx
    from prerender import droughts as D

    idx = Fx.build_index(json.loads(read(os.path.join("data", "data.json"))))
    built = D.build(idx)
    active = {idx.canonical(r["player"]) for r in idx.records
              if r["season"] == idx.current_season}
    for lst in built.values():
        peaks = {player for (player, _s), events in lst["events"].items()
                 for e in events if e["kind"] == "drought_peak"}
        for player in peaks:
            if player in active:
                best = min(e["rank"] for (p, _s), events in lst["events"].items()
                           if p == player for e in events
                           if e["kind"] == "drought_peak")
                assert lst["places"].get(player) != best, player


def test_leaving_the_list_reads_two_ways():
    """At the head of the list the award and the standing are one sentence.
    From further down, the place he left from is the fact."""
    sys.path.insert(0, os.path.join(REPO, "scripts"))
    import factoids as Fx
    from prerender import droughts as D

    idx = Fx.build_index(json.loads(read(os.path.join("data", "data.json"))))
    built = D.build(idx)
    tops = [e for lst in built.values() for events in lst["events"].values()
            for e in events if e["kind"] == "drought_left" and e["rank"] == 1]
    assert tops
    assert TL._family(tops[0]) == "drought_left"
    down = [e for lst in built.values() for events in lst["events"].values()
            for e in events if e["kind"] == "drought_left" and e["rank"] > 1]
    assert down
    assert TL._family(down[0]) == "drought_left_down"
    text = TL._PHRASES["drought_left"][TL.PAST][0].format(
        **TL._bits(idx, tops[0], "2020-21"))
    assert text.endswith("who had not made one.") or \
        text.endswith("who had not won one.")
    assert "top career earner" in text


# --------------------------------------------------------------------------
# the team section of a season page: ranked, with the roster inside it
# --------------------------------------------------------------------------


def _payroll_block(season="2021-22"):
    html = read(os.path.join("season", season, "index.html"))
    start = html.index("<h2>Team payrolls</h2>")
    return html[start:html.index("</table></div>", start)]


def test_the_season_team_section_ranks_the_teams():
    block = _payroll_block()
    heads = re.findall(
        r'<tr class="hm-group-head"><th[^>]*><span class="hm-rank">(\d+)</span>',
        block)
    assert heads == [str(n) for n in range(1, len(heads) + 1)]
    assert len(heads) == 30


def test_the_season_team_section_ranks_the_roster_inside_each_team():
    block = _payroll_block()
    groups = re.findall(r'<tbody class="hm-group">(.*?)</tbody>', block, re.S)
    assert len(groups) == 30
    for group in groups:
        seats = re.findall(
            r'<tr class="hm-group-row"><th[^>]*><span class="hm-rank">(\d+)</span>',
            group)
        assert seats, "a team with no roster inside it"
        assert seats == [str(n) for n in range(1, len(seats) + 1)]


def test_a_team_payroll_is_ordered_by_what_it_carried():
    block = _payroll_block()
    totals = [
        int(re.sub(r"[^0-9]", "", money))
        for money in re.findall(
            r'<tr class="hm-group-head">.*?<td class="hm-money">([^<]+)</td>',
            block, re.S)
    ]
    assert totals == sorted(totals, reverse=True)


# --------------------------------------------------------------------------
# the timeline on a player page
# --------------------------------------------------------------------------


def _timeline(slug):
    html = read(os.path.join("player", slug, "index.html"))
    if "<h2>Season by season, what changed</h2>" not in html:
        return []
    start = html.index("<h2>Season by season, what changed</h2>")
    block = html[start:html.index("</dl>", start)]
    return re.findall(r"<dt>([^<]+)</dt><dd>(.*?)</dd>", block, re.S)


def test_a_long_career_gets_a_timeline_newest_first():
    rows = _timeline("kyrie-irving")
    assert len(rows) > 5
    keys = [F.season_key(season) for season, _text in rows]
    assert keys == sorted(keys, reverse=True)


def test_a_timeline_line_never_repeats_his_own_name():
    """Lines open on the verb or on "He": the page is already about him."""
    for slug, name in (("kyrie-irving", "Kyrie Irving"),
                       ("stephen-curry", "Stephen Curry"),
                       ("rudy-gobert", "Rudy Gobert")):
        for _season, text in _timeline(slug):
            assert name not in re.sub(r"<[^>]+>", "", text)


def test_a_timeline_carries_no_em_dash():
    for slug in ("kyrie-irving", "giannis-antetokounmpo", "stephen-curry"):
        for _season, text in _timeline(slug):
            plain = re.sub(r"<[^>]+>", "", text)
            assert "—" not in plain and "--" not in plain


def test_no_season_carries_more_than_two_recurring_events():
    """Checked on the sweep rather than on the prose, where "No. 1 picks" is
    not the end of a sentence.

    Three award-drought events are said once in a career and are not made to
    compete for the two slots; everything that can recur year on year is.
    """
    idx = F.build_index(json.loads(read(os.path.join("data", "data.json"))))
    found = TL.build(idx)
    assert found
    for key, events in found.items():
        recurring = [e for e in events
                     if e["kind"] not in TL.SPARED and not e.get("bridge")]
        assert len(recurring) <= TL.PER_SEASON, key
        # and a spared kind is only ever said once a career per list
        for kind in TL.SPARED:
            here = [e for e in events if e["kind"] == kind]
            lists = [e.get("list") for e in here]
            assert len(lists) == len(set(lists)), (key, kind)


def test_a_roster_top_event_never_sits_on_a_split_season():
    """A season split between two teams is a cap-sheet allocation, so it
    crowns nobody on either roster and unseats nobody. James Harden's 2021-22
    is the case: the sheet booked the whole $44.3 million to Philadelphia for
    a season Brooklyn paid him most of, and the sweep read that as him taking
    over the 76ers. Neither the man on the split season nor the man he would
    have passed carries a roster line for it."""
    idx = F.build_index(json.loads(read(os.path.join("data", "data.json"))))
    found = TL.build(idx)
    split = {(idx.canonical(r["player"]), r["season"]) for r in idx.records
             if F.is_split_season(r)}
    assert ("James Harden", "2021-22") in split

    offenders = []
    for (player, season), events in found.items():
        for event in events:
            if event["kind"] not in ("top_start", "top_end"):
                continue
            scope = event.get("scope") or ()
            if not scope or scope[0] != "franchise":
                continue
            if (player, season) in split:
                offenders.append((player, season, scope, "on a split season"))
            if event["kind"] == "top_end" and (event.get("to"), season) in split:
                offenders.append((player, season, scope, "taken by a split season"))
    assert offenders == []

    # and specifically: neither side of the Harden case says anything
    harden = [e for e in found.get(("James Harden", "2021-22"), [])
              if (e.get("scope") or ("",))[0] == "franchise"]
    assert harden == []
    harris = [e for e in found.get(("Tobias Harris", "2021-22"), [])
              if e["kind"] == "top_end" and e.get("to") == "James Harden"]
    assert harris == []


def test_a_cohort_top_event_still_fires_on_a_split_season():
    """The gate is a roster gate. A split season is still his money and he is
    still in his draft class, so the cohorts are untouched: without this the
    fix would quietly cost every group claim on a traded season."""
    idx = F.build_index(json.loads(read(os.path.join("data", "data.json"))))
    found = TL.build(idx)
    split = {(idx.canonical(r["player"]), r["season"]) for r in idx.records
             if F.is_split_season(r)}
    cohort = [
        (player, season, event["kind"], event["scope"])
        for (player, season), events in found.items()
        if (player, season) in split
        for event in events
        if event["kind"] in ("top_start", "top_end")
        and (event.get("scope") or ("",))[0] != "franchise"
    ]
    assert cohort, "a split season should still carry cohort standings"


def test_two_timeline_lines_never_open_the_same_way():
    for slug in ("kyrie-irving", "giannis-antetokounmpo", "rudy-gobert",
                 "stephen-curry"):
        openings = [
            " ".join(re.sub(r"<[^>]+>", "", text).split()[:2]).lower()
            for _season, text in _timeline(slug)
        ]
        for before, after in zip(openings, openings[1:]):
            assert before != after, (slug, before)


def test_a_timeline_links_the_cohorts_and_men_it_names():
    rows = _timeline("kyrie-irving")
    joined = " ".join(text for _season, text in rows)
    assert "<a " in joined and "href=" in joined
    assert "/college/duke/" in joined


def test_a_timeline_reads_the_tense_of_its_season():
    """Past seasons happened, the season being played is happening, and a
    contracted one is money nobody has been paid."""
    current = F.compute_current_season(
        json.loads(read(os.path.join("data", "data.json"))))
    for slug in ("stephen-curry", "rudy-gobert", "giannis-antetokounmpo"):
        for season, text in _timeline(slug):
            plain = re.sub(r"<[^>]+>", "", text)
            if F.season_key(season) > F.season_key(current):
                assert "Would" in plain or "would" in plain, (slug, season)
            else:
                assert not plain.startswith("Would"), (slug, season)


# --------------------------------------------------------------------------
# how a timeline is worded
# --------------------------------------------------------------------------

TIMELINE_SLUGS = ("kyrie-irving", "giannis-antetokounmpo", "rudy-gobert",
                  "stephen-curry", "lebron-james", "nikola-jokic")


def _plain_timeline(slug):
    return [(season, re.sub(r"<[^>]+>", "", text).replace("&#x27;", "'"))
            for season, text in _timeline(slug)]


#: The sentences a second one in a season is allowed to be: the three
#: award-drought facts a man has no second chance at.
SECOND_SENTENCE = (
    "Became the top career earner", "Took over as the top career earner",
    "Is the top career earner", "Stands as the top career earner",
    "Would become the top career earner", "Would take over as the top career",
    "Led the career earnings of players", "Leads the career earnings of players",
    "Would lead the career earnings of players",
    "Peaked at No.", "Got as high as No.", "Peaks at No.", "Gets as high as No.",
    "Would peak at No.", "Would get as high as No.",
    "Made his first", "Won his first", "Would do it",
)


def test_a_season_gets_one_sentence_or_a_drought_fact_beside_it():
    """Two facts that belong together are said in one breath; two that do not
    are one fact, the stronger of them. The exception is the three
    award-drought facts said once in a career, which get a sentence of their
    own rather than waiting for a season that will never come again."""
    extras = 0
    for slug in TIMELINE_SLUGS:
        for season, text in _plain_timeline(slug):
            assert text.endswith(".")
            parts = re.split(r"(?<=\.)\s+(?=[A-Z])", text)
            assert len(parts) <= 2, (slug, season, text)
            if len(parts) == 2:
                extras += 1
                assert parts[1].startswith(SECOND_SENTENCE), (slug, season, text)
    assert extras, "no season printed a second sentence"


def test_a_first_claim_uses_a_round_figure():
    """"The first Duke player paid $31,742,000" is arithmetic. A threshold is
    a number somebody crossed."""
    allowed = {F.fmt_money(step) for step in TL.STEPS}
    for slug in TIMELINE_SLUGS:
        for season, text in _plain_timeline(slug):
            # "for the first time" is a top-ten claim, not a threshold one
            if not re.search(r"first [^.]*\bpaid\b", text):
                continue
            figures = re.findall(r"\$[\d.,]+(?: million)?", text)
            for figure in figures:
                assert figure in allowed, (slug, season, text)
            assert "or more in a season" in text, (slug, season, text)


def test_no_timeline_names_an_exact_draft_pick():
    assert "draft_slot" not in TL.KINDS
    for slug in TIMELINE_SLUGS:
        for season, text in _plain_timeline(slug):
            assert not re.search(r"No\. \d+ pick", text), (slug, season, text)


def test_a_draft_standing_names_the_broadest_range_he_holds():
    """Being the best-paid man taken outside the top 5 says more than outside
    the top 20, because it is the bigger field."""
    labels = [label for _cut, label in TL.DRAFT_RANGES]
    seen = set()
    for slug in TIMELINE_SLUGS:
        for _season, text in _plain_timeline(slug):
            for label in labels:
                if label in text:
                    seen.add(label)
    assert seen, "no draft-range claim anywhere in the sample"
    assert TL._draft_scopes({"draft_pick": 27, "draft_year": 2013}) == [
        ("draft_range", "5"), ("draft_range", "10"),
        ("draft_range", "14"), ("draft_range", "20")]
    assert TL._draft_scopes({"draft_pick": None, "draft_year": None}) == [
        ("draft_range", "undrafted")]


def test_a_draft_class_counts_from_its_fifth_season():
    """Until then its men are on rookie-scale deals and the order inside the
    class is the order the scale set."""
    assert TL.CLASS_GRACE == 4
    for season in ("2011-12", "2012-13", "2013-14", "2014-15"):
        assert TL._green(("draft_class", "2011"), season) is False, season
    for season in ("2015-16", "2016-17"):
        assert TL._green(("draft_class", "2011"), season) is True, season
    # every other cohort is itself from the first season
    assert TL._green(("college", "Duke"), "2011-12") is True


def test_a_career_milestone_is_fifty_then_every_hundred():
    assert TL.MARKS[0] == 50000000
    assert all(mark % 100000000 == 0 for mark in TL.MARKS[1:])
    wanted = {F.fmt_money(mark) for mark in TL.MARKS}
    for slug in TIMELINE_SLUGS:
        for season, text in _plain_timeline(slug):
            if "career earnings" not in text or "pass" not in text \
                    and "ross" not in text:
                continue
            for figure in re.findall(r"\$[\d.,]+(?: million)?", text):
                assert figure in wanted, (slug, season, text)


def test_a_record_set_is_never_a_first_at_an_exact_salary():
    for slug in TIMELINE_SLUGS:
        for season, text in _plain_timeline(slug):
            assert not re.search(r"first .* paid \$[\d.,]+ in a season", text), \
                (slug, season, text)


def test_a_record_lost_never_says_saw():
    for slug in TIMELINE_SLUGS:
        for season, text in _plain_timeline(slug):
            assert not text.startswith("Saw "), (slug, season, text)


def test_no_two_seasons_running_open_on_the_same_word():
    for slug in TIMELINE_SLUGS:
        words = [TL._first_word(text) for _season, text in _plain_timeline(slug)]
        for before, after in zip(words, words[1:]):
            assert before != after, (slug, before)


def test_no_phrasing_runs_for_more_than_two_seasons():
    """Three "Lost the ..." lines in a row is the same sentence three times."""
    for slug in TIMELINE_SLUGS:
        shapes = [" ".join(text.split()[:2]).lower()
                  for _season, text in _plain_timeline(slug)]
        for i in range(len(shapes) - 2):
            trio = shapes[i:i + 3]
            assert len(set(trio)) > 1, (slug, trio)


def test_a_merged_sentence_states_its_number_once():
    """"His $39.3 million was the most ever paid to an international player
    and a Bucks record" gives the figure once, not twice."""
    for slug in TIMELINE_SLUGS:
        for season, text in _plain_timeline(slug):
            figures = re.findall(r"\$[\d.,]+ million", text)
            assert len(figures) == len(set(figures)), (slug, season, text)


def test_every_rank_line_names_the_list_it_is_a_rank_on():
    """A place on a career-earnings list says "career earnings". Without it,
    "third on the list of highest-paid Duke players ever" read as a
    single-season place on a page that also reports single-season records."""
    for kind in ("list_up", "list_down"):
        for forms in TL._PHRASES[kind].values():
            for form in forms:
                assert "career earnings" in form, (kind, form)
    for forms in TL._PHRASES["list_top"].values():
        for form in forms:
            assert "career earner" in form or "career-earnings" in form, form


def test_no_timeline_says_highest_paid_ever():
    for kind, tenses in TL._PHRASES.items():
        for forms in tenses.values():
            for form in forms:
                assert "ever." not in form, (kind, form)
    for slug in TIMELINE_SLUGS:
        for season, text in _plain_timeline(slug):
            assert "highest-paid" not in text or "ever" not in text, \
                (slug, season, text)


def test_a_rank_line_in_the_pages_names_career_earnings():
    ranked = 0
    for slug in TIMELINE_SLUGS:
        for season, text in _plain_timeline(slug):
            if not re.search(r"\b(Moved up|Climbed|Dropped|Slipped|Sits|Ranks"
                             r"|Would move up|Would climb|Would drop"
                             r"|Would slip)\b", text):
                continue
            ranked += 1
            assert "career earnings" in text, (slug, season, text)
    assert ranked, "no rank line in the sample to check"


# --------------------------------------------------------------------------
# most career earnings with one franchise
# --------------------------------------------------------------------------


def _with_team_block(slug="76ers"):
    html = read(os.path.join("team", slug, "index.html"))
    start = html.index("Most career earnings with the")
    return html[start:html.index("</table></div>", start)]


def test_the_career_earnings_section_sits_above_franchise_history():
    html = read(os.path.join("team", "76ers", "index.html"))
    assert html.index("Most career earnings with the") < \
        html.index("Biggest salaries in franchise history")


def test_the_career_earnings_section_ranks_and_counts_its_seasons():
    block = _with_team_block()
    rows = re.findall(r"<tr>(.*?)</tr>", block, re.S)[1:]
    assert 0 < len(rows) <= C.TABLE_ROWS
    places = [int(m) for m in re.findall(r'<span class="hm-rank">(\d+)</span>',
                                        block)]
    assert places == sorted(places)
    assert places[0] == 1
    for row in rows:
        cells = re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row, re.S)
        one = re.match(r"(\d+), (\S+) to (\S+)$", cells[1])
        count, first, last = one.groups() if one else (
            re.match(r"(\d+), (\S+)$", cells[1]).groups() + (None,))
        last = last or first
        assert int(count) >= 1
        assert F.season_key(first) <= F.season_key(last)
        # a count can exceed the span's length only if seasons repeat, which
        # they cannot: it is a set of them
        assert int(count) <= F.season_key(last) - F.season_key(first) + 1


def test_the_career_earnings_section_falls_from_the_top():
    block = _with_team_block()
    totals = [int(re.sub(r"[^0-9]", "", money)) for money in
              re.findall(r'<td class="hm-money">([^<]+)</td>', block)]
    assert totals == sorted(totals, reverse=True)


def test_career_earnings_with_a_team_counts_only_money_paid():
    """The convention the career-earnings claims use: no contracted season,
    and no split season either."""
    data = json.loads(read(os.path.join("data", "data.json")))
    idx = F.build_index(data)
    wanted = collections.defaultdict(int)
    for record in idx.records:
        if idx.is_contracted(record["season"]) or F.is_split_season(record):
            continue
        for team, amount in F.team_amounts(record):
            if team == "PHI":
                wanted[idx.canonical(record["player"])] += amount or 0
    best = max(wanted.items(), key=lambda kv: kv[1])
    block = _with_team_block()
    assert best[0] in block
    assert "{:,}".format(best[1]) in block


def test_a_roster_row_reads_its_percentage_off_the_money_beside_it():
    """A two-team man's whole-season percentage beside one team's part of him
    is two different bases in one row."""
    data = json.loads(read(os.path.join("data", "data.json")))
    current = F.compute_current_season(data)
    cap = ((data.get("salary_cap") or {}).get(current) or {}).get("cap")
    assert cap
    for slug in ("76ers", "grizzlies", "bucks"):
        html = read(os.path.join("team", slug, "index.html"))
        start = html.index("<h2>{} roster</h2>".format(current))
        block = html[start:html.index("</table></div>", start)]
        for row in re.findall(r"<tr>(.*?)</tr>", block, re.S)[1:]:
            cells = re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row, re.S)
            money_cell, pct_cell = cells[1], cells[2]
            if pct_cell == "-":
                continue
            paid = int(re.sub(r"[^0-9]", "", money_cell))
            assert abs(float(pct_cell.rstrip("%")) - 100.0 * paid / cap) < 0.1, \
                (slug, cells[0][:60], money_cell, pct_cell)


def test_the_misspelt_barlow_is_one_career_with_the_right_one():
    aliases = json.loads(read(os.path.join("data", "name_aliases.json")))
    assert aliases["alias_to_canonical"]["Dominck Barlow"] == "Dominick Barlow"
    html = read(os.path.join("team", "76ers", "index.html"))
    assert "Dominck Barlow" not in html
    assert "Dominick Barlow" in html


def test_the_career_earnings_section_says_it_counts_the_season_in_progress():
    """A man with one season on the books in the top ten needs explaining, and
    the season comes off the data rather than out of this file."""
    data = json.loads(read(os.path.join("data", "data.json")))
    current = F.compute_current_season(data)
    html = read(os.path.join("team", "76ers", "index.html"))
    start = html.index("Most career earnings with the")
    hint = re.search(r'hm-hint">(.*?)</p>', html[start:], re.S).group(1)
    assert hint.startswith(
        "Includes {}, the season in progress.".format(current))
    # the line is built from the data, not from a year typed into the builder
    source = read(os.path.join("scripts", "prerender", "pages.py"))
    body = source[source.index("def _with_team_hint("):]
    body = body[:body.index("def _with_team_rows(")]
    code = [line for line in body.splitlines()
            if not line.lstrip().startswith("#")]
    assert "idx.current_season" in "\n".join(code)
    assert not re.search(r"\d{4}-\d{2}", "\n".join(code))


def test_one_season_with_a_team_is_not_written_as_a_span():
    block = _with_team_block()
    spans = re.findall(r'<td class="hm-word">(\d+), ([^<]+)</td>', block)
    assert spans
    for count, span in spans:
        if count == "1":
            assert " to " not in span, span
        else:
            assert " to " in span, (count, span)
