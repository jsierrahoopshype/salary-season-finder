"""Tests for the pre-rendered entity pages.

Most of these read the built output in the repository, which is what actually
ships. A few build small fixtures to pin a rule down on its own.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

import factoids as F
from prerender import config as C
from prerender import entities as E
from prerender import slugs as S

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
        href = '../{}/{}/'.format(C.FAMILIES[family]["dir"], parts[1])
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


@built
def test_no_github_io_anywhere_in_the_output():
    hits = []
    for path in list(all_pages()) + [C.SITEMAP_PATH]:
        full = repo(path)
        if not os.path.exists(full):
            continue
        with open(full, "r", encoding="utf-8") as fh:
            if "github.io" in fh.read():
                hits.append(path)
    assert hits == []


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
