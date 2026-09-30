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


def repo_path(*parts):
    return os.path.join(REPO, *parts)


# --------------------------------------------------------------------------
# season tables, rendered by the tool's own component
# --------------------------------------------------------------------------

def render_season_tables(idx, identities):
    """Call js/app.js's exported builder so a page's table is the app's table.

    One subprocess for every player, not one each: the harness loads app.js
    once and answers the whole batch.
    """
    payload = {
        "currentSeason": idx.current_season,
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
    """Sentences by player and by cohort.

    A player's sentences keep their season, because a confirmed split gives a
    key two pages and each one may only carry the seasons that are its man's.
    """
    by_player = collections.defaultdict(list)
    by_cohort = collections.defaultdict(list)
    for record_key, facts in sorted(factoids.items()):
        player, season = record_key.split("|", 1)
        for fact in facts:
            by_player[player].append((season, fact["key"], fact["text"]))
            parts = fact["key"].split("|")
            if parts[0] in ("cohort_season", "cohort_career") and len(parts) > 2:
                family = COHORT_KIND_TO_FAMILY.get(parts[1])
                if family:
                    by_cohort[(family, parts[2])].append((season, fact["key"], fact["text"]))
    return (
        {k: sorted(v) for k, v in by_player.items()},
        {k: _ordered_texts(v) for k, v in by_cohort.items()},
    )


def _ordered_texts(rows, limit=C.FACTS_SHOWN):
    """What actually happened first, then what a contract would do.

    A cohort collects a lot of near-identical "would be" sentences about
    seasons nobody has been paid for yet, and they crowd out the ones about
    money that has changed hands. Paid seasons lead, newest first; contracted
    ones follow, nearest first.
    """
    paid, future, seen, out = [], [], set(), []
    for season, key, text in rows:
        if text in seen:
            continue
        seen.add(text)
        (future if " would " in text else paid).append((season, key, text))
    paid.sort(key=lambda r: (-F.season_key(r[0]), r[1]))
    future.sort(key=lambda r: (F.season_key(r[0]), r[1]))
    for _season, _key, text in paid + future:
        out.append(text)
        if len(out) >= limit:
            break
    return out


def player_facts(by_player_facts, ident):
    """The sentences that belong to this man, in season order.

    A page for one segment of a split key carries only that segment's seasons.
    An unconfirmed split carries none: the name on it cannot be trusted.
    """
    if not ident.extra.get("factoids_allowed"):
        return []
    seasons = {r["season"] for r in ident.records}
    rows = by_player_facts.get(ident.data_key) or []
    return [
        text for season, _key, text in rows if season in seasons
    ]


# --------------------------------------------------------------------------
# related links on a player page
# --------------------------------------------------------------------------

def related_links(idx, ident, lookup, depth=2):
    """Teams, cohorts and the sibling tools, where those pages exist."""
    out = []
    seen = set()

    codes = []
    for record in ident.records:
        for code, _amount in F.team_amounts(record):
            if code in idx.franchises and code not in codes:
                codes.append(code)
    for code in codes:
        slug = lookup.get(("team", code))
        if slug:
            out.append((idx.franchises[code]["name"], "{}team/{}/".format(R.up(depth), slug)))

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
            out.append((label, "{}{}/{}/".format(R.up(depth), C.FAMILIES[family]["dir"], slug)))

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
        root="",
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

  window.HoopsMaticPlayerPages = { slugFor: slugFor, slugify: slugify };
})();
"""


def player_pages_js(idx, identities, book):
    """The three maps the tool needs to link a name to its page."""
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
    return PLAYER_PAGES_JS % {
        "aliases": dump(dict(sorted(idx.name_aliases.items()))),
        "splits": dump({k: v for k, v in sorted(splits.items())}),
        "overrides": dump(dict(sorted(overrides.items()))),
    }


BROWSE_START = "<!-- prerender:browse:start -->"
BROWSE_END = "<!-- prerender:browse:end -->"


def browse_block(hub_entries):
    links = "".join(
        '<li><a href="{}/{}/">{}</a></li>'.format(C.TOOL_ROOT, slug, R.esc(label))
        for slug, label in hub_entries
    )
    return (
        "{}\n"
        '      <section class="hm-browse">\n'
        "        <h2>Browse every NBA salary</h2>\n"
        '        <ul class="hm-roll">{}</ul>\n'
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
    tables = render_season_tables(idx, built["players"])
    for ident in built["players"]:
        facts = player_facts(by_player_facts, ident)
        title, description, body = P.player_page(
            idx, ident, tables.get(ident.key, ""), facts,
            related_links(idx, ident, lookup),
        )
        emit(ident, title, description, body, [
            ("Salary Season Finder", C.TOOL_ROOT), (ident.name, None),
        ])

    # ---- cohort pages ----------------------------------------------------
    for entity in built["cohorts"]:
        title, description, body = P.cohort_page(
            idx, entity, built["players"], by_cohort_facts
        )
        hub = C.FAMILIES[entity.family]["hub"]
        emit(entity, title, description, body, [
            ("Salary Season Finder", C.TOOL_ROOT),
            (C.FAMILIES[entity.family]["label"], "../../{}/".format(hub)),
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
    for hub_slug, family in sorted(C.HUBS.items()):
        members = sorted(
            (e for e in built["cohorts"] if e.family == family),
            key=lambda e: _hub_sort(e),
        )
        entries = [(e.name, e.slug, len(e.players)) for e in members]
        title, description, body = P.hub_page(hub_slug, family, entries)
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
        player_pages_js(idx, built["players"], book), today=today,
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


def _hub_sort(entity):
    if entity.family in ("draft",):
        return (-int(entity.key), entity.name)
    if entity.family == "pick":
        return (999 if entity.key == "undrafted" else int(entity.key), entity.name)
    return (0, entity.name)


if __name__ == "__main__":
    sys.exit(main())
