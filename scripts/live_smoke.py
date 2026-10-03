#!/usr/bin/env python3
"""Fetch the live pages and check what a reader would see.

The pages are built here but served by GitHub Pages behind the hoopsmatic.com
Worker, so the only honest check is a GET of the real URL. Read-only, one
request a second.

Each request carries ?nocache=<buster>. The name is what matters: the Worker
holds one cached entry per path and skips it only when the request carries a
`nocache` parameter, and its cache key drops the query string, so any other
buster asks the same stored entry and is answered from before the deploy. The
value is the buster, which keeps each URL unique for the file paths (the
sitemap) that the Worker hands to Cloudflare's own fetch cache instead.

A title alone would pass on a page that had quietly lost a section, so every
page family also names a string its body must contain.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

ROOT = "https://hoopsmatic.com/salary-season-finder"

#: url -> the title it must carry, and the strings its body must contain.
#:
#: The root's title is not the one in index.html: the hoopsmatic.com Worker
#: rewrites it on the way through, on purpose. What this file checks is what a
#: reader is served, so the expectation is the Worker's title. Every subpage
#: passes through untouched and carries the title this repository built.
#:
#: One entry per page family, and the strings are the headings the pages
#: render, so a section that stops being built fails the check instead of
#: reaching a reader. A test asserts every one of them against the built page,
#: which is what stops a heading being typed here from memory. The root and the
#: hubs carry no content expectation: the root is the tool itself, and a hub is
#: a list of links with no section to lose. The season entry names one fixed
#: season rather than the current one: a past season page renders the same
#: sections, and the test below would fail the day the slug stopped existing.
COHORT_EARNINGS = "Highest career earnings"

PAGES = (
    (ROOT, "NBA Player Salaries by season and position | HoopsMatic", ()),
    (ROOT + "/colleges/", "NBA Salaries by College | HoopsMatic", ()),
    (ROOT + "/team/76ers/", "76ers Payroll and Salary History | HoopsMatic",
     ("Most career earnings with the",)),
    (ROOT + "/player/joel-embiid/", "Joel Embiid: Salary History | HoopsMatic",
     ("What the numbers say", "Season by season, what changed")),
    (ROOT + "/college/duke/", "Highest-Paid Duke Players in NBA History | HoopsMatic",
     (COHORT_EARNINGS,)),
    (ROOT + "/country/canada/",
     "Highest-Paid NBA Players from Canada of All Time | HoopsMatic",
     (COHORT_EARNINGS,)),
    (ROOT + "/draft/2013/", "Highest-Paid Players of the 2013 NBA Draft | HoopsMatic",
     (COHORT_EARNINGS,)),
    (ROOT + "/pick/8/", "Highest-Paid No. 8 Picks in NBA History | HoopsMatic",
     (COHORT_EARNINGS,)),
    (ROOT + "/season/2026-27/", "NBA Salaries 2026-27 | HoopsMatic",
     ("Team payrolls",)),
    (ROOT + "/position/guard/", "Highest-Paid NBA Guards of All Time | HoopsMatic",
     (COHORT_EARNINGS,)),
    (ROOT + "/region/europe/", "Highest-Paid European Players in NBA History | HoopsMatic",
     (COHORT_EARNINGS,)),
)

SITEMAP = ROOT + "/sitemap.xml"
MIN_SITEMAP_URLS = 200
PAUSE_SECONDS = 1.0
TIMEOUT = 45


def fetch(url, buster):
    """One GET, past the Worker's per-path cache.

    `nocache` is the parameter the Worker reads; the buster is its value so the
    URL is still unique for whatever caches on the full URL.
    """
    joiner = "&" if "?" in url else "?"
    request = urllib.request.Request(
        url + joiner + "nocache=" + buster,
        headers={"User-Agent": "hoopsmatic-live-smoke/1 (+github actions)",
                 "Cache-Control": "no-cache"},
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return response.getcode(), response.read().decode("utf-8", "replace")


def check_page(url, expected_title, must_contain, buster):
    problems = []
    try:
        status, body = fetch(url, buster)
    except urllib.error.HTTPError as err:
        return ["{} returned HTTP {}".format(url, err.code)]
    except Exception as err:                                  # noqa: BLE001
        return ["{} could not be fetched: {}".format(url, err)]

    if status != 200:
        problems.append("{} returned HTTP {}".format(url, status))

    title = re.search(r"<title>(.*?)</title>", body, re.S)
    if not title:
        problems.append("{} has no <title>".format(url))
    elif title.group(1).strip() != expected_title:
        problems.append("{} title is {!r}, expected {!r}".format(
            url, title.group(1).strip(), expected_title))

    canonical = re.search(r'<link rel="canonical" href="([^"]+)"', body)
    if not canonical:
        problems.append("{} has no canonical".format(url))
    elif canonical.group(1) != url:
        problems.append("{} canonical is {}, expected {}".format(
            url, canonical.group(1), url))

    for wanted in must_contain:
        if wanted not in body:
            problems.append("{} does not contain {!r}".format(url, wanted))

    for href, tag, snippet in github_io_links(body):
        problems.append(
            "{} links to github.io: {}\n    tag: {}\n    around: {}".format(
                url, href, tag, snippet)
        )
    return problems


#: How much of the page around a bad link to quote, either side.
CONTEXT_CHARS = 140


def github_io_links(body):
    """Every href pointing at github.io, with the tag it sits in.

    A bare URL in a failure report says nothing about where it came from. The
    tag and the few lines around it say whether it is a canonical, a nav link
    or something injected downstream, which is the difference between a fix
    here and a fix in the Worker.
    """
    out = []
    for match in re.finditer(r'href="([^"]*github\.io[^"]*)"', body):
        start = body.rfind("<", 0, match.start())
        end = body.find(">", match.end())
        tag = body[start:end + 1] if start != -1 and end != -1 else match.group(0)
        left = max(0, (start if start != -1 else match.start()) - CONTEXT_CHARS)
        right = min(len(body), (end if end != -1 else match.end()) + CONTEXT_CHARS)
        snippet = " ".join(body[left:right].split())
        out.append((match.group(1), " ".join(tag.split()), snippet))
    return out


def check_sitemap(buster):
    try:
        status, body = fetch(SITEMAP, buster)
    except Exception as err:                                  # noqa: BLE001
        return ["{} could not be fetched: {}".format(SITEMAP, err)]
    if status != 200:
        return ["{} returned HTTP {}".format(SITEMAP, status)]
    try:
        tree = ET.fromstring(body)
    except ET.ParseError as err:
        return ["{} does not parse: {}".format(SITEMAP, err)]
    namespace = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    locs = tree.findall("s:url/s:loc", namespace)
    if len(locs) < MIN_SITEMAP_URLS:
        return ["{} lists {} URLs, expected at least {}".format(
            SITEMAP, len(locs), MIN_SITEMAP_URLS)]
    bad = [loc.text for loc in locs if "github.io" in (loc.text or "")]
    if bad:
        return ["{} lists github.io URLs: {}".format(SITEMAP, ", ".join(bad[:3]))]
    return []


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--buster", default=str(int(time.time())))
    parser.add_argument("--out", default=None, help="write the report here")
    args = parser.parse_args(argv)

    problems = []
    for url, title, must_contain in PAGES:
        problems.extend(check_page(url, title, must_contain, args.buster))
        time.sleep(PAUSE_SECONDS)
    problems.extend(check_sitemap(args.buster))

    report = "\n".join("- " + line for line in problems)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(report + ("\n" if report else ""))
    if problems:
        print("live check failed:")
        print(report)
        return 1
    print("live check passed: {} pages and the sitemap".format(len(PAGES)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
