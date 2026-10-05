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
    """Relative prefix back to the tool root, for the stylesheets only.

    Every link in a page body is absolute instead. The Worker serves these
    pages on hoopsmatic.com while GitHub Pages serves the same files on
    github.io, and a relative href would keep a reader on whichever host he
    landed on. The canonical host is the only one we link.
    """
    return "../" * depth


def page_url(family, slug):
    """The public URL of an entity page."""
    return "{}/{}/{}/".format(C.TOOL_ROOT, C.FAMILIES[family]["dir"], slug)


def hub_url(hub_slug):
    return "{}/{}/".format(C.TOOL_ROOT, hub_slug)


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
<script src="{root}js/page-search.js" defer></script>
<script type="application/ld+json">{breadcrumb_ld}</script>
</head>
<body>
<main class="hm-page">
{crumbs}
{find}
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


def find_bar(depth, tool_link=None):
    """One input, and on a player page a way into the tool beside it.

    The suggestion index is fetched on the first focus, so a reader who came
    for the page pays nothing for a search he never uses. ``data-root`` is how
    the script finds data/slugs.json from whatever depth the page sits at.
    """
    out = ['<div class="hm-find" data-root="{}">'.format(esc(up(depth)))]
    out.append(
        '<label class="hm-find-label" for="hm-find-input">Find a player</label>'
        '<div class="hm-find-row">'
        '<input id="hm-find-input" type="search" autocomplete="off"'
        ' role="combobox" aria-expanded="false" aria-autocomplete="list"'
        ' aria-controls="hm-find-list" placeholder="Search players">'
    )
    if tool_link:
        out.append(
            '<a class="hm-find-tool" href="{}">Open in Salary Finder</a>'.format(
                esc(tool_link))
        )
    out.append(
        '</div><ul class="hm-find-list" id="hm-find-list" role="listbox"'
        ' aria-label="Players" hidden></ul>'
    )
    out.append("</div>")
    return "".join(out)


def page(title, description, url, depth, trail, body, indexable, og_title=None,
         tool_link=None):
    root = up(depth)
    # every crumb href is already absolute, so the JSON-LD is the same list
    absolute = [(label, href if href else url) for label, href in trail]
    head = HEAD.format(
        title=esc(title),
        description=esc(description),
        robots="index,follow" if indexable else "noindex,follow",
        url=esc(url),
        og_title=esc(og_title or title),
        root=root,
        breadcrumb_ld=breadcrumb_ld(absolute),
        crumbs=breadcrumbs(trail, depth),
        find=find_bar(depth, tool_link),
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


def rank_table(columns, rows, table_class=""):
    """A ranked table inside the season table's scroll frame.

    ``columns`` is [(label, css class)], the first of which is the frozen one.
    ``rows`` is a list of lists of ready-made cell HTML. ``table_class`` marks
    a table whose columns are reordered on a phone.
    """
    if not rows:
        return '<p class="hm-empty">Nothing on file.</p>'
    head = "".join(
        '<th class="{}" scope="col">{}</th>'.format(cls, esc(label))
        for label, cls in columns
    )
    # The first column's class comes from the column spec like every other
    # one, so a header can never be aligned one way and its cells another.
    first = columns[0][1] if columns else "hm-who"
    body = []
    for row in rows:
        cells = ['<th class="{}" scope="row">{}</th>'.format(first, row[0])]
        for value, (_label, cls) in zip(row[1:], columns[1:]):
            cells.append('<td class="{}">{}</td>'.format(cls, value))
        body.append("<tr>{}</tr>".format("".join(cells)))
    classes = "hm-rank-table" + (" " + table_class if table_class else "")
    return (
        '<div class="ps-scroll"><table class="{}">'
        "<thead><tr>{}</tr></thead><tbody>{}</tbody></table></div>".format(
            classes, head, "".join(body)
        )
    )


def grouped_rank_table(columns, groups):
    """A ranked table whose rows come in groups, each with its own head row.

    ``groups`` is [(head cells, [row cells])]: a team and the players on it,
    ranked inside it. One tbody a group, so the head row can be styled as one
    and a screen reader reads the group as a unit.
    """
    if not groups:
        return '<p class="hm-empty">Nothing on file.</p>'
    head = "".join(
        '<th class="{}" scope="col">{}</th>'.format(cls, esc(label))
        for label, cls in columns
    )

    def row(cells, kind):
        out = ['<th class="hm-who" scope="row">{}</th>'.format(cells[0])]
        for value, (_label, cls) in zip(cells[1:], columns[1:]):
            out.append('<td class="{}">{}</td>'.format(cls, value))
        return '<tr class="{}">{}</tr>'.format(kind, "".join(out))

    body = []
    for lead, members in groups:
        rows = [row(lead, "hm-group-head")]
        rows += [row(cells, "hm-group-row") for cells in members]
        body.append('<tbody class="hm-group">{}</tbody>'.format("".join(rows)))
    return (
        '<div class="ps-scroll"><table class="hm-rank-table hm-grouped">'
        "<thead><tr>{}</tr></thead>{}</table></div>".format(
            head, "".join(body)
        )
    )


def player_link(ident, rank=None, tag="", face=""):
    href = page_url("player", ident.slug)
    prefix = '<span class="hm-rank">{}</span>'.format(rank) if rank else ""
    return '{}{}<a href="{}">{}</a>{}'.format(
        prefix, face, esc(href), esc(ident.name), tag)


CONTRACTED_TAG = '<span class="hm-contracted">contracted</span>'


def summary_block(sentences, linker=None, url=None):
    """The written summary at the top of a cohort page."""
    if not sentences:
        return ""
    render = (
        (lambda t: linker.sentences_html(t, url)) if linker else esc
    )
    return '<div class="hm-summary">{}</div>'.format(
        "".join("<p>{}</p>".format(render(text)) for text in sentences)
    )


def facts_summary(sentences, linker=None, url=None):
    """One paragraph about a man's whole career, with its names linked.

    This replaced a box per season. Six seasons of the same record made six
    near-identical boxes, and the year-by-year detail is in the table above
    anyway; what a reader wants here is the shape of the career.
    """
    if not sentences:
        return ""
    render = (lambda t: linker.html(t, url)) if linker else esc
    return '<p class="hm-facts">{}</p>'.format(
        " ".join(render(text) for text in sentences))


def timeline_list(entries, linker=None, url=None):
    """The season-by-season timeline, one season a row.

    ``entries`` is [(season, line)], newest first. The season is the term and
    the line is the description, which is what a timeline is: a definition
    list, not a table of numbers.
    """
    if not entries:
        return ""
    render = (lambda t: linker.sentences_html(t, url)) if linker else esc
    rows = []
    for season, text in entries:
        rows.append(
            '<div class="hm-event"><dt>{}</dt><dd>{}</dd></div>'.format(
                esc(season), render(text)))
    return '<dl class="hm-timeline">{}</dl>'.format("".join(rows))


def related_chips(links):
    """Links to pages in other families, as the chips the tool root uses.

    ``links`` is [(label, url)], already absolute, in the order to print.
    """
    if not links:
        return ""
    items = "".join(
        '<li><a href="{}">{}</a></li>'.format(esc(url), esc(label))
        for label, url in links
    )
    return '<ul class="hm-chips">{}</ul>'.format(items)


def roll_call(entries, family, lead=None):
    """Every member of a cohort or family, linked.

    ``lead`` optionally returns a small image (a flag, say) for an entry.
    """
    if not entries:
        return '<p class="hm-empty">Nothing on file.</p>'
    items = []
    for name, slug, count in entries:
        href = page_url(family, slug)
        suffix = (
            '<span class="hm-roll-count">{}</span>'.format(count) if count else ""
        )
        mark = (lead(name) if lead else "") or ""
        items.append('<li><a href="{}">{}{}{}</a></li>'.format(
            esc(href), mark, esc(name), suffix))
    return '<ul class="hm-roll">{}</ul>'.format("".join(items))


# --------------------------------------------------------------------------
# "More colleges": every other page in the family, at the foot of the page
# --------------------------------------------------------------------------

#: Past this many pages a flat row of chips is a wall, so they are grouped.
GROUPED_ABOVE = 40

#: What the block is called, in the words a reader would use.
MORE_LABELS = {
    "college": "colleges",
    "country": "countries",
    "draft": "draft classes",
    "pick": "picks",
    "position": "positions",
    "agent": "agents",
}


def _group_of(family, key, name):
    """The subheading an entry belongs under, or None where none is wanted."""
    if family in ("draft",):
        try:
            year = int(key)
        except ValueError:
            return "Other"
        return "{}s".format(year // 10 * 10)
    if family == "pick":
        if key == "undrafted":
            return "Undrafted"
        try:
            number = int(key)
        except ValueError:
            return "Other"
        low = (number - 1) // 10 * 10 + 1
        return "Picks {} to {}".format(low, low + 9)
    first = (name or "").strip()[:1].upper()
    return first if first.isalpha() else "#"


def _group_sort(family, heading):
    if family == "draft":
        return (0, -int(heading[:-1])) if heading[:-1].isdigit() else (1, 0)
    if family == "pick":
        if heading == "Undrafted":
            return (1, 0)
        parts = heading.split()
        return (0, int(parts[1])) if len(parts) > 1 and parts[1].isdigit() else (2, 0)
    return (0, heading)


def _entry_sort(family, key, name):
    if family == "draft":
        return (0, -int(key)) if key.isdigit() else (1, 0, name)
    if family == "pick":
        if key == "undrafted":
            return (1, 0)
        return (0, int(key)) if key.isdigit() else (2, 0)
    return (0, name.lower())


def more_block(family, entries, current_slug=None, heading=None):
    """Chips to every other page in one family, grouped where there are many.

    A reader who has finished one college page is most likely to want
    another, and until now the only way back was the hub. Everything here is
    a plain link in the flow of the page: no box that scrolls on its own.
    """
    rest = [e for e in entries if e[1] != current_slug]
    if not rest:
        return ""
    rest.sort(key=lambda e: _entry_sort(family, e[2], e[0]))
    label = heading or "More {}".format(MORE_LABELS.get(
        family, C.FAMILIES[family]["label"].lower()))

    body = []
    if len(rest) > GROUPED_ABOVE:
        groups = {}
        for name, slug, key in rest:
            groups.setdefault(_group_of(family, key, name), []).append((name, slug, key))
        for head in sorted(groups, key=lambda h: _group_sort(family, h)):
            body.append('<h3 class="hm-more-head">{}</h3>'.format(esc(head)))
            body.append(roll_call(
                [(name, slug, None) for name, slug, _key in groups[head]], family))
    else:
        body.append(roll_call(
            [(name, slug, None) for name, slug, _key in rest], family))

    return (
        '<section class="hm-section hm-more"><h2>{}</h2>{}</section>'.format(
            esc(label), "".join(body))
    )


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
