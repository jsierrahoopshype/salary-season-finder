#!/usr/bin/env python3
"""Fetch the live pages and check what a reader would see.

The pages are built here but served by GitHub Pages behind the hoopsmatic.com
Worker, so the only honest check is a GET of the real URL. Read-only, one
request a second, with a cache-buster so the Worker's edge cache cannot answer
with the version from before the deploy.
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

#: url -> the title it must carry. The root's title is the tool's own.
PAGES = (
    (ROOT, "NBA Salary Season Finder"),
    (ROOT + "/colleges/", "NBA Salaries by College | HoopsMatic"),
    (ROOT + "/college/duke/", "Highest-Paid Duke Players in NBA History | HoopsMatic"),
    (ROOT + "/country/canada/",
     "Highest-Paid NBA Players from Canada of All Time | HoopsMatic"),
    (ROOT + "/player/joel-embiid/", "Joel Embiid: Salary History | HoopsMatic"),
)

SITEMAP = ROOT + "/sitemap.xml"
MIN_SITEMAP_URLS = 200
PAUSE_SECONDS = 1.0
TIMEOUT = 45


def fetch(url, buster):
    """One GET, with the cache-buster appended."""
    joiner = "&" if "?" in url else "?"
    request = urllib.request.Request(
        url + joiner + "cb=" + buster,
        headers={"User-Agent": "hoopsmatic-live-smoke/1 (+github actions)",
                 "Cache-Control": "no-cache"},
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return response.getcode(), response.read().decode("utf-8", "replace")


def check_page(url, expected_title, buster):
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

    bad = [href for href in re.findall(r'href="([^"]+)"', body)
           if "github.io" in href]
    if bad:
        problems.append("{} links to github.io: {}".format(url, ", ".join(bad[:3])))
    return problems


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
    for url, title in PAGES:
        problems.extend(check_page(url, title, args.buster))
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
