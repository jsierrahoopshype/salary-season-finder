# Mobile: site nav, drawer, suggestions

375x667 (iPhone viewport, DPR 2) unless noted. The HoopsMatic site nav is
injected by the hoopsmatic.com Worker, which this container cannot reach, so
these shots render against a local reconstruction of it: sticky at top:0, links
past the 5th hidden below 480px, burger toggling a dropdown panel. What the
shots are proving is this page's behaviour around whatever is pinned above it,
not the nav's own markup.

| file | what it shows |
| --- | --- |
| `01-375-load-drawer-open.png` | page load: the drawer is open, and it starts below the site nav instead of under it. The × is a 44x44 target. |
| `02-375-closed-scrolled-sticky-nav.png` | drawer closed, scrolled 900px: nav still pinned with its burger, "Filters" pinned directly under it. |
| `03-375-site-burger-open.png` | the site burger menu open. The Filters bar re-measures and drops below the expanded nav. |
| `04-375-chip-panel-full.png` | all 26 column chips, wrapped, no internal scroll and no clipped top row. |
| `05-375-jabari-suggestions.png` | typing "Jabari Smith": both players as tappable rows with season range and last team. |
| `06-375-jabari-smith-season-table.png` | after tapping the first row: drawer closed, the 9-season table for Jabari Smith (not Sr). |
| `07-1280-desktop.png` | 1280px: unchanged except the removed description line and stats card. |
