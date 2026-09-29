"""HTML for one page.

House style applies to everything generated here: no em dashes, one idea a
sentence, and every all-time figure carries the scope note.
"""

from __future__ import annotations

import html
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import factoids as F  # noqa: E402

from . import config as C  # noqa: E402


def esc(value):
    return html.escape("" if value is None else str(value), quote=True)


def money(value):
    if value is None:
        return "-"
    return "${:,}".format(int(round(value)))


def money_short(value):
    """$62.6 million, the way the factoid engine writes it."""
    return F.fmt_money(value)


def up(depth):
    """Relative prefix back to the tool root from a page ``depth`` deep."""
    return "../" * depth


# --------------------------------------------------------------------------
# shell
# --------------------------------------------------------------------------

HEAD = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<title>{title}</title>
<meta name="description" content="{description}">
<meta name="robots" content="{robots}">
<meta name="hm-prerendered" content="1">
<link rel="canonical" href="{url}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="HoopsMatic">
<meta property="og:title" content="{og_title}">
<meta property="og:description" content="{description}">
<meta property="og:url" content="{url}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<link rel="stylesheet" href="{root}css/polymarket.css">
<link rel="stylesheet" href="{root}css/styles.css">
<link rel="stylesheet" href="{root}css/pages.css">
<script type="application/ld+json">{breadcrumb_ld}</script>
</head>
<body>
<main class="hm-page">
{crumbs}
"""

FOOT = """</main>
<footer class="site-footer">
Anything wrong? Contact us at <a href="mailto:hoopshype@hoopshype.com">hoopshype@hoopshype.com</a>
</footer>
</body>
</html>
"""


def breadcrumbs(trail, depth):
    """Visible crumbs plus the JSON-LD the crumbs describe.

    ``trail`` is [(label, relative href or None)], ending with this page.
    """
    parts = []
    for i, (label, href) in enumerate(trail):
        if i:
            parts.append('<span class="sep">/</span>')
        if href:
            parts.append('<a href="{}">{}</a>'.format(esc(href), esc(label)))
        else:
            parts.append("<span>{}</span>".format(esc(label)))
    visible = '<nav class="hm-crumbs" aria-label="Breadcrumb">{}</nav>'.format(
        "".join(parts)
    )
    return visible


def breadcrumb_ld(trail_absolute):
    """schema.org BreadcrumbList over absolute URLs."""
    items = []
    for i, (label, url) in enumerate(trail_absolute, start=1):
        item = {"@type": "ListItem", "position": i, "name": label}
        if url:
            item["item"] = url
        items.append(item)
    return json.dumps(
        {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": items},
        separators=(",", ":"), sort_keys=False,
    )


def page(title, description, url, depth, trail, body, indexable, og_title=None):
    root = up(depth)
    absolute = []
    for label, href in trail:
        if href is None:
            absolute.append((label, url))
        elif href == C.TOOL_ROOT:
            absolute.append((label, C.TOOL_ROOT))
        else:
            absolute.append((label, C.TOOL_ROOT + "/" + href.replace(root, "", 1)))
    head = HEAD.format(
        title=esc(title),
        description=esc(description),
        robots="index,follow" if indexable else "noindex,follow",
        url=esc(url),
        og_title=esc(og_title or title),
        root=root,
        breadcrumb_ld=breadcrumb_ld(absolute),
        crumbs=breadcrumbs(trail, depth),
    )
    return head + body + FOOT


# --------------------------------------------------------------------------
# blocks
# --------------------------------------------------------------------------

def section(heading, hint, body):
    out = ['<section class="hm-section">', "<h2>{}</h2>".format(esc(heading))]
    if hint:
        out.append('<p class="hm-hint">{}</p>'.format(esc(hint)))
    out.append(body)
    out.append("</section>")
    return "\n".join(out)


def rank_table(columns, rows):
    """A ranked table inside the season table's scroll frame.

    ``columns`` is [(label, css class)], the first of which is the frozen one.
    ``rows`` is a list of lists of ready-made cell HTML.
    """
    if not rows:
        return '<p class="hm-empty">Nothing on file.</p>'
    head = "".join(
        '<th class="{}" scope="col">{}</th>'.format(cls, esc(label))
        for label, cls in columns
    )
    body = []
    for row in rows:
        cells = ['<th class="hm-who" scope="row">{}</th>'.format(row[0])]
        for value, (_label, cls) in zip(row[1:], columns[1:]):
            cells.append('<td class="{}">{}</td>'.format(cls, value))
        body.append("<tr>{}</tr>".format("".join(cells)))
    return (
        '<div class="ps-scroll"><table class="hm-rank-table">'
        "<thead><tr>{}</tr></thead><tbody>{}</tbody></table></div>".format(
            head, "".join(body)
        )
    )


def player_link(ident, depth, rank=None, tag=""):
    href = "{}player/{}/".format(up(depth), ident.slug)
    prefix = '<span class="hm-rank">{}</span>'.format(rank) if rank else ""
    return '{}<a href="{}">{}</a>{}'.format(prefix, esc(href), esc(ident.name), tag)


CONTRACTED_TAG = '<span class="hm-contracted">contracted</span>'


def facts_list(sentences):
    if not sentences:
        return ""
    items = "".join("<li>{}</li>".format(esc(s)) for s in sentences)
    return '<ul class="hm-facts">{}</ul>'.format(items)


def roll_call(entries, depth, family):
    """Every member of a cohort or family, linked."""
    if not entries:
        return '<p class="hm-empty">Nothing on file.</p>'
    items = []
    for name, slug, count in entries:
        href = "{}{}/{}/".format(up(depth), C.FAMILIES[family]["dir"], slug)
        suffix = (
            '<span class="hm-roll-count">{}</span>'.format(count) if count else ""
        )
        items.append('<li><a href="{}">{}{}</a></li>'.format(esc(href), esc(name), suffix))
    return '<ul class="hm-roll">{}</ul>'.format("".join(items))


def links_row(links):
    if not links:
        return ""
    items = "".join(
        '<li><a href="{}">{}</a></li>'.format(esc(href), esc(label))
        for label, href in links
    )
    return '<ul class="hm-links">{}</ul>'.format(items)


def scope_line():
    return '<p class="hm-scope">{}</p>'.format(esc(C.SCOPE_NOTE))
