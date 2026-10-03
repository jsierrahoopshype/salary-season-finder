#!/usr/bin/env python3
"""Build the static entity pages, the sitemap and the 404.

Every page carries its own content in the HTML. No page loads data.json, none
reads the query string, and each one embeds only its own slice of the data.

Identity comes from the factoid engine, not from a second reading of data.json:
data/name_aliases.json merges two spellings into one man, data/identity_splits
.json separates two men filed under one name once the split is confirmed, and
data/college_names.json decides how a college is printed. The eleven names
carrying a son's bio metadata and the ten whose career total started under
someone else's name are refused by the same gates the engine uses.

Nothing here can block the daily data update: the workflow runs it after the
data commit is already pushed, and it writes only its own files.
"""

from __future__ import annotations

import argparse
import collections
import datetime
import hashlib
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import factoids as F  # noqa: E402

from prerender import config as C  # noqa: E402
from prerender import entities as E  # noqa: E402
from prerender import pages as P  # noqa: E402
from prerender import render as R  # noqa: E402
from prerender import slugs as S  # noqa: E402
from prerender import linkify  # noqa: E402
from prerender import seasons  # noqa: E402
from prerender import timeline as TL  # noqa: E402
from prerender.media import Media  # noqa: E402
from prerender.phrasing import drop_mirrors, straighten  # noqa: E402


def repo_path(*parts):
    return os.path.join(REPO, *parts)


# --------------------------------------------------------------------------
# season tables, rendered by the tool's own component
# --------------------------------------------------------------------------

def render_season_tables(idx, identities, links=None):
    """Call js/app.js's exported builder so a page's table is the app's table.

    One subprocess for every player, not one each: the harness loads app.js
    once and answers the whole batch.

    ``links`` turns the team, season and award cells into links rather than the
    filter triggers the live tool wires them as: the page is read without the
    app's JavaScript, so a cell that only answers a click answers nothing.
    """
    payload = {
        "currentSeason": idx.current_season,
        "links": links or {},
        "players": [
            {"name": ident.key, "records": ident.records} for ident in identities
        ],
    }
    script = os.path.join(HERE, "prerender", "season_table.js")
    result = subprocess.run(
        ["node", script], input=json.dumps(payload),
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "season table harness failed: {}".format(result.stderr[:2000])
        )
    return json.loads(result.stdout)


# --------------------------------------------------------------------------
# factoids, grouped for the pages that show them
# --------------------------------------------------------------------------

def load_factoids():
    path = repo_path("data", "factoids.json")
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh).get("factoids") or {}


COHORT_KIND_TO_FAMILY = {
    spec["cohort"]: family
    for family, spec in C.FAMILIES.items() if spec["cohort"]
}


def group_factoids(factoids):
    """Claims by player and by cohort, as (season, fact) pairs.

    The fact itself travels, not just its sentence: the page writers need the
    figure, the season it is measured against and whether the money is
    contracted before they can decide what to print.
    """
    by_player = collections.defaultdict(list)
    by_cohort = collections.defaultdict(list)
    for record_key, facts in sorted(factoids.items()):
        player, season = record_key.split("|", 1)
        for fact in facts:
            by_player[player].append((season, fact))
            parts = fact["key"].split("|")
            if parts[0] in ("cohort_season", "cohort_career") and len(parts) > 2:
                family = COHORT_KIND_TO_FAMILY.get(parts[1])
                if family:
                    by_cohort[(family, parts[2])].append((season, fact))
    return by_player, by_cohort


#: What an older season is still worth saying. A claim that he was fourth on a
#: list four years ago is noise once the page groups every season together;
#: what he set, tied, passed or became is not.
PAST_SEASON_TYPES = frozenset({"sets", "ties", "milestone", "rank_shift"})


def player_facts(idx, by_player_facts, ident, current_key):
    """This man's claims, as the two to four sentences his page prints.

    A page for one segment of a split key carries only that segment's seasons.
    An unconfirmed split carries none: the name on it cannot be trusted.
    Mirrored pairs lose a side and forward-pointing comparisons lose their
    verb, both by the rules in prerender.phrasing.
    """
    if not ident.extra.get("factoids_allowed"):
        return []
    his = {r["season"] for r in ident.records}
    rows = [
        (season, fact) for season, fact in (by_player_facts.get(ident.data_key) or [])
        if season in his
    ]
    rows = drop_mirrors(rows)
    rows = [
        (season, fact) for season, fact in rows
        if F.season_key(season) >= current_key
        or fact["type"] in PAST_SEASON_TYPES
    ]
    rows = [(season, dict(fact, text=straighten(fact, season)))
            for season, fact in rows]
    return seasons.summary(idx, ident.name, rows, player=ident.data_key)


# --------------------------------------------------------------------------
# related links on a player page
# --------------------------------------------------------------------------

def related_links(idx, ident, lookup):
    """Teams, cohorts and the sibling tools, where those pages exist."""
    out = []
    seen = set()

    codes = []
    for record in ident.records:
        for code in F.team_codes(record):
            if code in idx.franchises and code not in codes:
                codes.append(code)
    for code in codes:
        slug = lookup.get(("team", code))
        if slug:
            out.append((idx.franchises[code]["name"], R.page_url("team", slug)))

    last = ident.records[-1]
    candidates = [
        ("college", (last.get("college") or "").strip(),
         idx.college_display((last.get("college") or "").strip())),
        ("country", (last.get("nationality") or "").strip(),
         (last.get("nationality") or "").strip()),
        ("draft", str(last.get("draft_year") or ""), "{} draft class".format(last.get("draft_year"))),
        ("position", (F.position_group(last.get("pos"))[0] or ""),
         {"G": "Guards", "F": "Forwards", "C": "Centers"}.get(F.position_group(last.get("pos"))[0] or "", "")),
        ("agent", (last.get("agent") or "").strip(), (last.get("agent") or "").strip()),
    ]
    pick = last.get("draft_pick")
    if pick and 1 <= pick <= F.MAX_DRAFT_SLOT:
        candidates.append(("pick", str(pick), "No. {} picks".format(pick)))
    elif pick is None and last.get("draft_year") is None:
        candidates.append(("pick", "undrafted", "Undrafted players"))

    # the same gate the engine uses: a name carrying a son's bio metadata joins
    # no cohort, so its page must not claim him either
    cohorts_ok = idx.cohorts_allowed(idx.canonical(last["player"]), last["season"])
    for family, key, label in candidates:
        if not key or not label:
            continue
        if family != "agent" and not cohorts_ok:
            continue
        slug = lookup.get((family, key))
        if slug and (family, slug) not in seen:
            seen.add((family, slug))
            out.append((label, R.page_url(family, slug)))

    out.append(("Compare players", C.COMPARE_URL))
    out.append(("Career map", C.CAREER_MAP_URL))
    out.append(("Salary Season Finder", C.TOOL_ROOT))
    return out


# --------------------------------------------------------------------------
# writing
# --------------------------------------------------------------------------

class Writer(object):
    """Writes only what changed, and remembers what has ever been published."""

    def __init__(self, hashes, dry_run=False):
        self.dry_run = dry_run
        self.previous = hashes
        self.current = {}
        self.written = 0
        self.unchanged = 0
        self.total_bytes = 0

    def write(self, path, content, url=None, indexable=False, today=None):
        full = repo_path(path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
        existing = self.previous.get(path) or {}
        if existing.get("hash") == digest and os.path.exists(full):
            lastmod = existing.get("lastmod") or today
            self.unchanged += 1
        else:
            if not self.dry_run:
                with open(full, "w", encoding="utf-8") as fh:
                    fh.write(content)
            lastmod = today
            self.written += 1
        self.total_bytes += len(content.encode("utf-8"))
        self.current[path] = {
            "hash": digest, "lastmod": lastmod,
            "url": url, "indexable": bool(indexable),
        }
        return lastmod

    def retire(self, today):
        """Pages that no longer have an entity behind them.

        Never deleted: the URL has been crawled and linked. The file keeps its
        content and is switched to noindex, and it leaves the sitemap.
        """
        retired = []
        for path, meta in sorted(self.previous.items()):
            if path in self.current:
                continue
            full = repo_path(path)
            if not os.path.exists(full):
                continue
            with open(full, "r", encoding="utf-8") as fh:
                content = fh.read()
            noindexed = content.replace(
                '<meta name="robots" content="index,follow">',
                '<meta name="robots" content="noindex,follow">',
            )
            if noindexed != content:
                if not self.dry_run:
                    with open(full, "w", encoding="utf-8") as fh:
                        fh.write(noindexed)
                self.written += 1
            meta = dict(meta)
            meta["hash"] = hashlib.sha256(noindexed.encode("utf-8")).hexdigest()[:16]
            meta["indexable"] = False
            meta["retired"] = True
            self.current[path] = meta
            retired.append(path)
        return retired


def sitemap_xml(rows):
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemap.org/schemas/sitemap/0.9">'.replace(
                 "sitemap.org/schemas", "sitemaps.org/schemas")]
    for url, lastmod in rows:
        lines.append("  <url><loc>{}</loc><lastmod>{}</lastmod></url>".format(url, lastmod))
    lines.append("</urlset>")
    return "\n".join(lines) + "\n"


def not_found_html(hub_entries):
    body = [
        '<div class="hm-404">',
        "<h1>Page not found</h1>",
        "<p>That page is not here. The NBA Salary Season Finder and every "
        "browse page are below.</p>",
        '<ul class="hm-roll"><li><a href="{}">Salary Season Finder</a></li>{}</ul>'.format(
            C.TOOL_ROOT,
            "".join(
                '<li><a href="{}/{}/">{}</a></li>'.format(C.TOOL_ROOT, slug, R.esc(label))
                for slug, label in hub_entries
            ),
        ),
        "</div>",
    ]
    head = R.HEAD.format(
        title="Page not found | HoopsMatic",
        description="That page is not on HoopsMatic's NBA salary database.",
        robots="noindex,follow",
        url=C.TOOL_ROOT + "/404.html",
        og_title="Page not found | HoopsMatic",
        # 404.html answers for any missing URL at any depth, so its
        # stylesheets cannot be relative to where the reader thought he was
        root=C.TOOL_ROOT + "/",
        breadcrumb_ld=R.breadcrumb_ld([("Salary Season Finder", C.TOOL_ROOT)]),
        crumbs="",
    )
    return head.replace('<main class="hm-page">', "<main>") + "\n".join(body) + R.FOOT


PLAYER_PAGES_JS = """/* Player page slugs for the tool's results table.
 *
 * Written by scripts/prerender_pages.py from the same data/slugs.json the pages
 * are built from, so a link in the tool and a file on disk can never disagree.
 * Three small maps and a mirror of the Python slugify is all the tool needs:
 * the plain case is computed, not looked up, so this file stays tiny.
 */
(function () {
  "use strict";

  // data.json spelling -> the one man he is
  var ALIASES = %(aliases)s;

  // one name covering two men, by season
  var SPLITS = %(splits)s;

  // slugs that are not what slugify would produce, from a collision
  var OVERRIDES = %(overrides)s;

  // the cohort pages that exist, by the value the tool filters on
  var COHORTS = %(cohorts)s;

  var ROOT = "%(root)s";

  function slugify(value) {
    return String(value)
      .normalize("NFKD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "") || "unnamed";
  }

  function seasonKey(season) {
    var year = parseInt(String(season).slice(0, 4), 10);
    return isNaN(year) ? 0 : year;
  }

  function slugFor(name, season) {
    var canonical = ALIASES[name] || name;
    var segments = SPLITS[canonical];
    if (segments) {
      var key = seasonKey(season);
      for (var i = 0; i < segments.length; i++) {
        if (key >= segments[i].from && key <= segments[i].to) return segments[i].slug;
      }
      return null;  // a segment this data cannot name has no page
    }
    return OVERRIDES[canonical] || slugify(canonical);
  }

  function playerUrl(name, season) {
    var slug = slugFor(name, season);
    return slug ? ROOT + "/player/" + slug + "/" : null;
  }

  /** The page for a filter value, or null where that cohort has no page. */
  function cohortUrl(family, value) {
    var group = COHORTS[family];
    if (!group) return null;
    var slug = group[String(value)];
    return slug ? ROOT + "/" + family + "/" + slug + "/" : null;
  }

  window.HoopsMaticPlayerPages = {
    root: ROOT,
    slugFor: slugFor,
    slugify: slugify,
    playerUrl: playerUrl,
    cohortUrl: cohortUrl
  };
})();
"""


def player_pages_js(idx, identities, book, cohorts):
    """The maps the tool needs to link a name, or a filter, to its page."""
    splits = collections.defaultdict(list)
    overrides = {}
    for ident in identities:
        if ident.extra.get("segment") is not None:
            splits[ident.data_key].append({
                "from": F.season_key(ident.records[0]["season"]),
                "to": F.season_key(ident.records[-1]["season"]),
                "slug": ident.slug,
            })
        elif S.slugify(ident.key) != ident.slug:
            overrides[ident.key] = ident.slug
    for segments in splits.values():
        segments.sort(key=lambda seg: seg["from"])
    dump = lambda obj: json.dumps(obj, sort_keys=True, ensure_ascii=False, indent=2)
    # Only the families the tool can filter by: a region or a pick range is not
    # a filter in the tool, so a map from one to its page would never be read.
    by_family = collections.defaultdict(dict)
    for entity in cohorts:
        spec = C.FAMILIES[entity.family]
        if spec["indexable"] and spec.get("filterable"):
            by_family[spec["dir"]][entity.key] = entity.slug
    return PLAYER_PAGES_JS % {
        "aliases": dump(dict(sorted(idx.name_aliases.items()))),
        "splits": dump({k: v for k, v in sorted(splits.items())}),
        "overrides": dump(dict(sorted(overrides.items()))),
        "cohorts": dump({k: dict(sorted(v.items())) for k, v in sorted(by_family.items())}),
        "root": C.TOOL_ROOT,
    }


BROWSE_START = "<!-- prerender:browse:start -->"
BROWSE_END = "<!-- prerender:browse:end -->"


def browse_block(hub_entries):
    """The hub links on the tool root, as chips rather than a bare list.

    Real HTML, always visible: it is how a reader and a crawler reach every
    hub from the tool itself. Absolute URLs, because GitHub Pages serves this
    same file on a host we do not link.
    """
    links = "".join(
        '<li><a href="{}/{}/">{}</a></li>'.format(C.TOOL_ROOT, slug, R.esc(label))
        for slug, label in hub_entries
    )
    return (
        "{}\n"
        '      <section class="hm-browse">\n'
        "        <h2>Browse every NBA salary</h2>\n"
        '        <p>Every college, country, draft class, pick and position has '
        "its own page of salaries and career earnings.</p>\n"
        '        <ul class="hm-chips">{}</ul>\n'
        "      </section>\n"
        "      {}".format(BROWSE_START, links, BROWSE_END)
    )


def update_tool_root(hub_entries):
    """Put the Browse block into index.html, replacing any earlier one.

    Real HTML in the page, never hidden: it is how a reader and a crawler reach
    every hub from the tool itself.
    """
    path = repo_path("index.html")
    with open(path, "r", encoding="utf-8") as fh:
        content = fh.read()
    block = browse_block(hub_entries)
    if BROWSE_START in content and BROWSE_END in content:
        start = content.index(BROWSE_START)
        end = content.index(BROWSE_END) + len(BROWSE_END)
        updated = content[:start] + block + content[end:]
    else:
        anchor = '      <!-- Empty state -->'
        if anchor not in content:
            raise RuntimeError("index.html has no anchor to put the Browse block before")
        updated = content.replace(anchor, block + "\n\n" + anchor, 1)
    if updated == content:
        return False
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(updated)
    return True


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--today", default=None, help="lastmod for pages that changed")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit-players", type=int, default=None,
                        help="build only the first N player pages, for a quick check")
    args = parser.parse_args(argv)

    today = args.today or datetime.date.today().isoformat()

    data = F.load_data()
    idx = F.build_index(data)
    book = S.load(repo_path(C.SLUGS_PATH))
    built = E.build_all(idx, book)
    P.register_team_slugs(built["teams"])

    if args.limit_players is not None:
        built["players"] = built["players"][: args.limit_players]

    lookup = {(e.family, e.key): e.slug for e in built["all"]}
    # every page in each family, for the "More colleges" block at the foot of
    # a cohort page and for the grouped list on its hub
    family_members = collections.defaultdict(list)
    for entity in built["cohorts"]:
        family_members[entity.family].append((entity.name, entity.slug, entity.key))
    media = Media(REPO)
    linker = linkify.build(idx, built["all"], built["cohorts"])
    by_player_facts, by_cohort_facts = group_factoids(load_factoids())

    hashes = {}
    hash_path = repo_path(C.PAGE_HASHES_PATH)
    if os.path.exists(hash_path):
        with open(hash_path, "r", encoding="utf-8") as fh:
            hashes = json.load(fh).get("pages") or {}
    writer = Writer(hashes, dry_run=args.dry_run)
    sitemap_rows = []

    def emit(entity, title, description, body, trail):
        html = R.page(
            title, description, entity.url, 2, trail, body, entity.indexable
        )
        lastmod = writer.write(
            "{}/index.html".format(entity.path), html,
            url=entity.url, indexable=entity.indexable, today=today,
        )
        if entity.indexable:
            sitemap_rows.append((entity.url, lastmod))

    # ---- player pages ----------------------------------------------------
    tables = render_season_tables(idx, built["players"], links={
        "team": {e.extra["code"]: e.url for e in built["teams"] if e.slug},
        "season": {e.key: e.url for e in built["seasons"] if e.slug},
        "awards": C.TOOL_ROOT,
    })
    current_key = F.season_key(idx.current_season)
    # One sweep of the file for every man's timeline, because what a season
    # changed is a question about the seasons before it and there is no point
    # asking it 3,353 times.
    events = TL.build(idx)
    for ident in built["players"]:
        facts = player_facts(idx, by_player_facts, ident, current_key)
        # The same gate the summary is held to: where one data key covers two
        # men and the split is unchecked, nothing is claimed about either.
        story = (TL.lines(idx, ident.records, events)
                 if ident.extra.get("factoids_allowed") else [])
        title, description, body = P.player_page(
            idx, ident, tables.get(ident.key, ""), facts,
            related_links(idx, ident, lookup), linker, story,
        )
        emit(ident, title, description, body, [
            ("Salary Season Finder", C.TOOL_ROOT), (ident.name, None),
        ])

    # ---- cohort pages ----------------------------------------------------
    relatives = cohort_relatives(idx, built["cohorts"])
    for entity in built["cohorts"]:
        title, description, body = P.cohort_page(
            idx, entity, built["players"], by_cohort_facts, media, linker,
            family_members.get(entity.family),
            relatives.get((entity.family, entity.key)),
        )
        hub = C.FAMILIES[entity.family]["hub"]
        emit(entity, title, description, body, [
            ("Salary Season Finder", C.TOOL_ROOT),
            (C.FAMILIES[entity.family]["label"], R.hub_url(hub)),
            (entity.name, None),
        ])

    # ---- team and season pages -------------------------------------------
    for entity in built["teams"]:
        title, description, body = P.team_page(idx, entity, built["players"])
        emit(entity, title, description, body, [
            ("Salary Season Finder", C.TOOL_ROOT), (entity.name, None),
        ])
    for entity in built["seasons"]:
        title, description, body = P.season_page(idx, entity, built["players"])
        emit(entity, title, description, body, [
            ("Salary Season Finder", C.TOOL_ROOT), (entity.name, None),
        ])

    # ---- hubs ------------------------------------------------------------
    hub_entries = []
    for hub_slug, family in sorted(
            C.HUBS.items(), key=lambda kv: C.FAMILIES[kv[1]]["label"]):
        members = sorted(
            (e for e in built["cohorts"] if e.family == family),
            key=lambda e: _hub_sort(e),
        )
        entries = [(e.name, e.slug, len(e.players)) for e in members]
        # the country hub is a list of countries, so it carries their flags
        lead = media.flag if family == "country" else None
        title, description, body = P.hub_page(
            hub_slug, family, entries, lead=lead,
            family_members=family_members.get(family))
        url = "{}/{}/".format(C.TOOL_ROOT, hub_slug)
        html = R.page(title, description, url, 1, [
            ("Salary Season Finder", C.TOOL_ROOT),
            (C.FAMILIES[family]["label"], None),
        ], body, True)
        lastmod = writer.write(
            "{}/index.html".format(hub_slug), html, url=url, indexable=True, today=today
        )
        sitemap_rows.append((url, lastmod))
        hub_entries.append((hub_slug, C.FAMILIES[family]["label"]))

    # ---- root, 404, sitemap ----------------------------------------------
    root_changed = update_tool_root(hub_entries) if not args.dry_run else False
    writer.write(C.NOT_FOUND_PATH, not_found_html(hub_entries), today=today)
    writer.write(
        os.path.join("js", "player-pages.js"),
        player_pages_js(idx, built["players"], book, built["cohorts"]), today=today,
    )

    # The tool root is recorded before anything is retired, so it is never
    # mistaken for a page that lost its entity.
    root_lastmod = (hashes.get("index.html") or {}).get("lastmod") or today
    with open(repo_path("index.html"), "r", encoding="utf-8") as fh:
        root_digest = hashlib.sha256(fh.read().encode("utf-8")).hexdigest()[:16]
    if (hashes.get("index.html") or {}).get("hash") != root_digest:
        root_lastmod = today
    writer.current["index.html"] = {
        "hash": root_digest, "lastmod": root_lastmod,
        "url": C.TOOL_ROOT, "indexable": True,
    }

    retired = writer.retire(today)

    sitemap_rows.insert(0, (C.TOOL_ROOT, root_lastmod))
    sitemap_rows.sort(key=lambda row: row[0])
    sitemap = sitemap_xml(sitemap_rows)
    sitemap_path = repo_path(C.SITEMAP_PATH)
    existing = ""
    if os.path.exists(sitemap_path):
        with open(sitemap_path, "r", encoding="utf-8") as fh:
            existing = fh.read()
    if existing != sitemap:
        if not args.dry_run:
            with open(sitemap_path, "w", encoding="utf-8") as fh:
                fh.write(sitemap)
        writer.written += 1

    if not args.dry_run:
        S.save(book, repo_path(C.SLUGS_PATH))
        with open(hash_path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(
                {
                    "readme": (
                        "One entry per published page: the hash of its content "
                        "and the date that content last changed. The sitemap's "
                        "lastmod comes from here, so it moves only when the "
                        "page moves rather than on every run."
                    ),
                    "pages": dict(sorted(writer.current.items())),
                },
                sort_keys=True, ensure_ascii=False, indent=1,
            ) + "\n")

    counts = collections.Counter(e.family for e in built["all"])
    print("current season {}".format(idx.current_season))
    for family in sorted(counts):
        print("  {:9s} {:>5d} pages ({})".format(
            family, counts[family],
            "indexable" if C.FAMILIES[family]["indexable"] else "noindex"))
    print("  hubs      {:>5d} pages (indexable)".format(len(hub_entries)))
    print("sitemap urls: {}".format(len(sitemap_rows)))
    print("slugs minted this run: {}".format(book.added))
    print("files written: {}, unchanged: {}".format(writer.written, writer.unchanged))
    print("retired pages switched to noindex: {}".format(len(retired)))
    print("generated bytes: {:,} ({:.1f} MiB)".format(
        writer.total_bytes, writer.total_bytes / 1048576.0))
    print("tool root updated: {}".format(root_changed))
    return 0


def cohort_relatives(idx, cohorts):
    """Links from a cohort page to the pages that cut the same players another
    way: a country to its region, a pick to the ranges it falls in, a college to
    its positions and back.

    Only to pages that exist, so a cohort below its family's minimum is never
    linked to.
    """
    have = {(e.family, e.key): e for e in cohorts}

    def url(family, key):
        entity = have.get((family, key))
        return entity.url if entity else None

    out = collections.defaultdict(list)

    for entity in cohorts:
        if entity.family == "country":
            keys = [(idx.continents or {}).get(entity.key)]
            if entity.key != F.DOMESTIC_NATIONALITY:
                keys.append("international")
            for key in keys:
                href = url("region", key) if key else None
                if href:
                    out[("country", entity.key)].append(
                        (have[("region", key)].name, href))

        elif entity.family == "pick" and entity.key != "undrafted":
            pick = int(entity.key)
            for key, first, last, _phrase in F.PICK_RANGES:
                if first <= pick <= last:
                    href = url("pick_range", key)
                    if href:
                        out[("pick", entity.key)].append(
                            (have[("pick_range", key)].name, href))

        elif entity.family == "college_position":
            college = entity.key.split("|", 1)[0]
            href = url("college", college)
            if href:
                out[("college_position", entity.key)].append(
                    ("All {} players".format(have[("college", college)].name), href))
            out[("college", college)].append((entity.name, entity.url))

        elif entity.family == "region" and entity.key != "international":
            href = url("region", "international")
            if href:
                out[("region", entity.key)].append(
                    (have[("region", "international")].name, href))

    for links in out.values():
        links.sort()
    return out


def _hub_sort(entity):
    if entity.family in ("draft",):
        return (-int(entity.key), entity.name)
    if entity.family == "pick":
        return (999 if entity.key == "undrafted" else int(entity.key), entity.name)
    return (0, entity.name)


if __name__ == "__main__":
    sys.exit(main())
