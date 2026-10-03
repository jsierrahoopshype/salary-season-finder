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
  "Dominck Barlow": "Dominick Barlow",
  "Ricky Council": "Ricky Council IV",
  "Terrence Shannon": "Terrence Shannon Jr",
  "Terrence Shannon Jr.": "Terrence Shannon Jr",
  "Tristan Da Silva": "Tristan da Silva",
  "Wendell Carter": "Wendell Carter Jr"
};

  // one name covering two men, by season
  var SPLITS = {
  "Corey Brewer": [
    {
      "from": 2008,
      "slug": "corey-brewer",
      "to": 2020
    }
  ]
};

  // slugs that are not what slugify would produce, from a collision
  var OVERRIDES = {
  "Bobby Jones (1984)": "bobby-jones",
  "Brandon Williams": "brandon-williams-2021",
  "Brandon Williams (1975)": "brandon-williams",
  "Chris Smith (1970)": "chris-smith",
  "Chris Smith (1987)": "chris-smith-2013-14",
  "Gerald Henderson": "gerald-henderson-jr",
  "Gerald Henderson Sr": "gerald-henderson",
  "John Lucas III": "john-lucas",
  "Ken Johnson (1978)": "ken-johnson",
  "Walter Clayton Jr": "walter-clayton"
};

  // the cohort pages that exist, by the value the tool filters on
  var COHORTS = {
  "college": {
    "Alabama": "alabama",
    "Arizona": "arizona",
    "Arizona St": "arizona-state",
    "Arkansas": "arkansas",
    "Auburn": "auburn",
    "Baylor": "baylor",
    "California": "california",
    "Cincinnati": "cincinnati",
    "Clemson": "clemson",
    "Colorado": "colorado",
    "Connecticut": "connecticut",
    "DePaul": "depaul",
    "Duke": "duke",
    "Florida": "florida",
    "Florida St": "florida-state",
    "Fresno St": "fresno-state",
    "Georgetown": "georgetown",
    "Georgia": "georgia",
    "Georgia Tech": "georgia-tech",
    "Gonzaga": "gonzaga",
    "Houston": "houston",
    "Illinois": "illinois",
    "Indiana": "indiana",
    "Iowa": "iowa",
    "Iowa St": "iowa-state",
    "Kansas": "kansas",
    "Kansas St": "kansas-state",
    "Kentucky": "kentucky",
    "LSU": "lsu",
    "Louisville": "louisville",
    "Marquette": "marquette",
    "Maryland": "maryland",
    "Memphis": "memphis",
    "Miami": "miami",
    "Michigan": "michigan",
    "Michigan St": "michigan-state",
    "Minnesota": "minnesota",
    "Missouri": "missouri",
    "North Carolina": "north-carolina",
    "North Carolina St": "north-carolina-state",
    "Notre Dame": "notre-dame",
    "Ohio St": "ohio-state",
    "Oklahoma": "oklahoma",
    "Oklahoma St": "oklahoma-state",
    "Oregon": "oregon",
    "Pittsburgh": "pittsburgh",
    "Providence": "providence",
    "Purdue": "purdue",
    "Seton Hall": "seton-hall",
    "St. John's": "st-john-s",
    "Stanford": "stanford",
    "Syracuse": "syracuse",
    "Temple": "temple",
    "Tennessee": "tennessee",
    "Texas": "texas",
    "UCLA": "ucla",
    "UNLV": "unlv",
    "USC": "usc",
    "Vanderbilt": "vanderbilt",
    "Villanova": "villanova",
    "Virginia": "virginia",
    "Wake Forest": "wake-forest",
    "Washington": "washington",
    "Wisconsin": "wisconsin",
    "Xavier": "xavier"
  },
  "country": {
    "Argentina": "argentina",
    "Australia": "australia",
    "Bahamas": "bahamas",
    "Bosnia": "bosnia",
    "Brazil": "brazil",
    "Cameroon": "cameroon",
    "Canada": "canada",
    "China": "china",
    "Croatia": "croatia",
    "Czech Republic": "czech-republic",
    "DR Congo": "dr-congo",
    "Dominican Republic": "dominican-republic",
    "France": "france",
    "Germany": "germany",
    "Great Britain": "great-britain",
    "Greece": "greece",
    "Israel": "israel",
    "Italy": "italy",
    "Jamaica": "jamaica",
    "Latvia": "latvia",
    "Lithuania": "lithuania",
    "Mexico": "mexico",
    "Montenegro": "montenegro",
    "Netherlands": "netherlands",
    "Nigeria": "nigeria",
    "Puerto Rico": "puerto-rico",
    "Republic of Georgia": "republic-of-georgia",
    "Russia": "russia",
    "Senegal": "senegal",
    "Serbia": "serbia",
    "Slovenia": "slovenia",
    "South Sudan": "south-sudan",
    "Spain": "spain",
    "Turkey": "turkey",
    "Ukraine": "ukraine",
    "United States": "united-states",
    "Venezuela": "venezuela"
  },
  "draft": {
    "1979": "1979",
    "1980": "1980",
    "1981": "1981",
    "1982": "1982",
    "1983": "1983",
    "1984": "1984",
    "1985": "1985",
    "1986": "1986",
    "1987": "1987",
    "1988": "1988",
    "1989": "1989",
    "1990": "1990",
    "1991": "1991",
    "1992": "1992",
    "1993": "1993",
    "1994": "1994",
    "1995": "1995",
    "1996": "1996",
    "1997": "1997",
    "1998": "1998",
    "1999": "1999",
    "2000": "2000",
    "2001": "2001",
    "2002": "2002",
    "2003": "2003",
    "2004": "2004",
    "2005": "2005",
    "2006": "2006",
    "2007": "2007",
    "2008": "2008",
    "2009": "2009",
    "2010": "2010",
    "2011": "2011",
    "2012": "2012",
    "2013": "2013",
    "2014": "2014",
    "2015": "2015",
    "2016": "2016",
    "2017": "2017",
    "2018": "2018",
    "2019": "2019",
    "2020": "2020",
    "2021": "2021",
    "2022": "2022",
    "2023": "2023",
    "2024": "2024",
    "2025": "2025"
  },
  "pick": {
    "1": "1",
    "10": "10",
    "11": "11",
    "12": "12",
    "13": "13",
    "14": "14",
    "15": "15",
    "16": "16",
    "17": "17",
    "18": "18",
    "19": "19",
    "2": "2",
    "20": "20",
    "21": "21",
    "22": "22",
    "23": "23",
    "24": "24",
    "25": "25",
    "26": "26",
    "27": "27",
    "28": "28",
    "29": "29",
    "3": "3",
    "30": "30",
    "31": "31",
    "32": "32",
    "33": "33",
    "34": "34",
    "35": "35",
    "36": "36",
    "37": "37",
    "38": "38",
    "39": "39",
    "4": "4",
    "40": "40",
    "41": "41",
    "42": "42",
    "43": "43",
    "44": "44",
    "45": "45",
    "46": "46",
    "47": "47",
    "48": "48",
    "49": "49",
    "5": "5",
    "50": "50",
    "51": "51",
    "52": "52",
    "53": "53",
    "54": "54",
    "55": "55",
    "56": "56",
    "57": "57",
    "58": "58",
    "6": "6",
    "60": "60",
    "7": "7",
    "8": "8",
    "9": "9",
    "undrafted": "undrafted"
  },
  "position": {
    "C": "center",
    "F": "forward",
    "G": "guard"
  }
};

  var ROOT = "https://hoopsmatic.com/salary-season-finder";

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
