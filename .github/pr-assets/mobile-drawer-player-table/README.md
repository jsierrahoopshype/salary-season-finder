# Mobile filter drawer + single-table player view

Review assets for the PR that closes the mobile filter rail on load, gives it a
persistent "Filters (n)" button, and replaces the per-season cards in the
single-player view with one table.

Every shot is headless Chromium against the same build and the same
`data/data.json`. Phone shots are 375 CSS px wide, desktop shots 1280.

A note on light / dark: neither `css/polymarket.css` nor `css/styles.css`
contains a single `prefers-color-scheme` rule, so the tool has no dark theme.
Shots captured under `colorScheme: 'dark'` come back byte-identical to their
light twins — `rail-closed-375-dark.png` and `player-sga-contracted-seasons-1280-dark.png`
are here as evidence of that, not as a second palette.

## Mobile filter rail — 375 px

### Closed on load

![Rail closed on load, 375px](rail-closed-375-light.png)

The rail no longer opens over the results on load. The results are the first
thing on screen, with the fixed "Filters" button above them.

### Open

![Drawer open, 375px](rail-open-375-light.png)

Overlay drawer with a backdrop, a close button in the header and "Show Results"
pinned to the bottom. Escape and a backdrop tap close it too, and filter state
survives every close path.

### Active filter count

![Filters (2), 375px](rail-count-375-light.png)

The count is the same tag list the breadcrumb trail renders, so the two cannot
disagree.

### Still there at depth

![Filters button while scrolled, 375px](rail-sticky-scrolled-375-light.png)

Scrolled well down the results; the button is fixed, not sticky-within-a-parent.

### Dark

![Rail closed on load, 375px, dark](rail-closed-375-dark.png)

Identical to the light shot — see the note above.

### Desktop unchanged — 1280 px

![Desktop results, 1280px](rail-closed-1280-light.png)

Element geometry for the sidebar, main content, header, summary strip, table
wrapper, table, first row, column toggles and footer was compared against
`main` at 1280 and 1024 px: no differences.

## Player view

### Mid-season trade — DeMar DeRozan

![DeRozan, 1280px](player-derozan-midseason-trade-1280-light.png)

![DeRozan, 375px](player-derozan-midseason-trade-375-light.png)

Twenty seasons oldest first. 2026-27 has `team_salaries` for two teams, so the
team cell carries the split.

### Three-way split — Kemba Walker

![Kemba Walker, 375px](player-kemba-three-team-split-375-light.png)

![Kemba Walker scrolled right, 375px](player-kemba-three-team-split-375-light-hscroll.png)

2022-23 splits across OKC, DET and DAL. The second shot is the same table
scrolled fully right: the season column stays frozen and the page body has not
moved.

### Contracted future seasons — Shai Gilgeous-Alexander

![Shai Gilgeous-Alexander, 1280px](player-sga-contracted-seasons-1280-light.png)

![Shai Gilgeous-Alexander, 375px](player-sga-contracted-seasons-375-light.png)

2027-28 through 2030-31 sit past the current season, which is derived from
`computeDefaultSeason()` rather than hardcoded. They get the tinted row, the
left accent rule, the "contracted" pill and the footnote.

![Shai Gilgeous-Alexander, 1280px, dark](player-sga-contracted-seasons-1280-dark.png)

### Single season — Patric Young

![Patric Young, 1280px](player-young-single-season-1280-light.png)

![Patric Young, 375px](player-young-single-season-375-light.png)

One row, one season, no contracted footnote, and with no awards to carry the
table fits its container without scrolling.
