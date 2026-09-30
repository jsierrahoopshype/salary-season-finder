/* Player page slugs for the tool's results table.
 *
 * Written by scripts/prerender_pages.py from the same data/slugs.json the pages
 * are built from, so a link in the tool and a file on disk can never disagree.
 * Three small maps and a mirror of the Python slugify is all the tool needs:
 * the plain case is computed, not looked up, so this file stays tiny.
 */
(function () {
  "use strict";

  // data.json spelling -> the one man he is
  var ALIASES = {
  "Andre Jackson": "Andre Jackson Jr",
  "Marcus Thornton II": "Marcus Thornton",
  "Ricky Council": "Ricky Council IV",
  "Terrence Shannon": "Terrence Shannon Jr",
  "Terrence Shannon Jr.": "Terrence Shannon Jr",
  "Tristan Da Silva": "Tristan da Silva",
  "Wendell Carter": "Wendell Carter Jr"
};

  // one name covering two men, by season
  var SPLITS = {
  "Brandon Williams": [
    {
      "from": 1999,
      "slug": "brandon-williams",
      "to": 2000
    },
    {
      "from": 2022,
      "slug": "brandon-williams-2021",
      "to": 2027
    }
  ],
  "Chris Smith": [
    {
      "from": 1993,
      "slug": "chris-smith",
      "to": 1995
    },
    {
      "from": 2014,
      "slug": "chris-smith-2013-14",
      "to": 2014
    }
  ],
  "Corey Brewer": [
    {
      "from": 2008,
      "slug": "corey-brewer",
      "to": 2020
    }
  ],
  "Gerald Henderson": [
    {
      "from": 1991,
      "slug": "gerald-henderson",
      "to": 1991
    },
    {
      "from": 2010,
      "slug": "gerald-henderson-jr",
      "to": 2018
    }
  ],
  "Jaren Jackson Jr": [
    {
      "from": 1993,
      "slug": "jaren-jackson",
      "to": 2002
    },
    {
      "from": 2019,
      "slug": "jaren-jackson-jr",
      "to": 2030
    }
  ]
};

  // slugs that are not what slugify would produce, from a collision
  var OVERRIDES = {};

  function slugify(value) {
    return String(value)
      .normalize("NFKD")
      .replace(/[̀-ͯ]/g, "")
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
