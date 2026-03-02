/* ============================================
   HoopsMatic Salary Season Finder
   Main Application Logic
   ============================================ */

(function () {
  "use strict";

  // ---- State ----
  let DATA = null;        // raw loaded data
  let filtered = [];      // current filtered results
  let sortCol = "salary";
  let sortDir = "desc";   // "asc" or "desc"
  let activePreset = null;
  let _exactLeagueRank = null;  // set by cell click for exact rank match
  let _exactPos = null;          // set by cell click for exact position match
  let _teammateFilter = null;    // set by teammate preset: Set of "TEAM|SEASON" keys
  let _teammateLabel = null;     // name of the star player for breadcrumb display
  let _teammateExclude = null;   // star player name to exclude from results
  let _undraftedFilter = false;   // filter for undrafted players only
  let _savedRankVis = null;       // saved rank column visibility when entering combined mode

  // Column definitions
  const COLUMNS = [
    { key: "rank",              label: "#",           type: "rank",   default: true,  sortable: false },
    { key: "player",            label: "Player",      type: "text",   default: true,  sortable: true  },
    { key: "season",            label: "Season",      type: "text",   default: true,  sortable: true  },
    { key: "team",              label: "Team",        type: "text",   default: true,  sortable: true  },
    { key: "age",               label: "Age",         type: "num",    default: true,  sortable: true  },
    { key: "salary",            label: "Salary",      type: "salary", default: true,  sortable: true  },
    { key: "salary_cap_pct",    label: "Cap%",        type: "pct",    default: true,  sortable: true  },
    { key: "salary_rank_league",label: "Lg Rank",     type: "num",    default: true,  sortable: true  },
    { key: "years_exp",         label: "Exp",         type: "num",    default: true,  sortable: true  },
    { key: "ppg",               label: "PPG",         type: "stat",   default: true,  sortable: true  },
    { key: "rpg",               label: "RPG",         type: "stat",   default: true,  sortable: true  },
    { key: "apg",               label: "APG",         type: "stat",   default: true,  sortable: true  },
    { key: "spg",               label: "SPG",         type: "stat",   default: false, sortable: true  },
    { key: "bpg",               label: "BPG",         type: "stat",   default: false, sortable: true  },
    { key: "gp",                label: "GP",          type: "num",    default: true,  sortable: true  },
    { key: "fg_pct",            label: "FG%",         type: "pct3",   default: false, sortable: true  },
    { key: "tp_pct",            label: "3P%",         type: "pct3",   default: false, sortable: true  },
    { key: "ft_pct",            label: "FT%",         type: "pct3",   default: false, sortable: true  },
    { key: "cost_per_point",    label: "$/Point",     type: "salary", default: false, sortable: true  },
    { key: "cost_per_game",     label: "$/Game",      type: "salary", default: false, sortable: true  },
    { key: "career_earnings",   label: "Career $",    type: "salary", default: true,  sortable: true  },
    { key: "awards",            label: "Awards",      type: "awards", default: false, sortable: false },
    { key: "pos",               label: "Pos",         type: "text",   default: false, sortable: true  },
    { key: "nationality",       label: "Nat.",        type: "text",   default: false, sortable: true  },
    { key: "college",           label: "College/Club",type: "text",   default: false, sortable: true  },
    { key: "draft_pick",        label: "Pick",        type: "num",    default: false, sortable: true  },
    { key: "draft_year",        label: "Draft Yr",   type: "num",    default: false, sortable: true  },
  ];

  // Track which columns are visible
  let visibleCols = {};

  // ---- Formatting Helpers ----
  function fmtSalary(val) {
    if (val == null) return "-";
    return "$" + Math.round(val).toLocaleString("en-US");
  }

  function fmtPct(val) {
    if (val == null) return "-";
    return val.toFixed(1) + "%";
  }

  function fmtPct3(val) {
    if (val == null) return "-";
    // If value > 1, assume it's already a percentage
    if (val > 1) return val.toFixed(1) + "%";
    return (val * 100).toFixed(1) + "%";
  }

  function fmtStat(val) {
    if (val == null) return "-";
    return val.toFixed(1);
  }

  function fmtNum(val) {
    if (val == null) return "-";
    return String(val);
  }

  function fmtCell(col, val) {
    switch (col.type) {
      case "salary": return fmtSalary(val);
      case "pct":    return fmtPct(val);
      case "pct3":   return fmtPct3(val);
      case "stat":   return fmtStat(val);
      case "num":    return fmtNum(val);
      case "awards":
        if (!val || val.length === 0) return "-";
        return val.map(function(a) {
          var cls = "award-badge clickable";
          if (a.indexOf("All-Star") >= 0) cls += " all-star";
          if (a.indexOf("Most Valuable Player") >= 0) cls += " mvp";
          return '<span class="' + cls + '" data-award="' + escAttr(a) + '">' + escHtml(a) + "</span>";
        }).join(" ");
      default: return val || "-";
    }
  }

  function escHtml(str) {
    if (!str) return "";
    return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function escAttr(str) {
    if (!str) return "";
    return str.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/'/g, "&#39;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function parseMoney(str) {
    if (!str) return null;
    str = str.replace(/[$,\s]/g, "");
    var n = parseFloat(str);
    return isNaN(n) ? null : n;
  }

  function parseNum(str) {
    if (!str && str !== 0) return null;
    var n = parseFloat(str);
    return isNaN(n) ? null : n;
  }

  // Season to sort key (ending year)
  function seasonYear(s) {
    if (!s) return 0;
    var p = s.split("-");
    if (p.length !== 2) return 0;
    var start = parseInt(p[0], 10);
    var endShort = parseInt(p[1], 10);
    if (isNaN(start) || isNaN(endShort)) return 0;
    var century = Math.floor(start / 100) * 100;
    var end = century + endShort;
    if (end <= start) end += 100;
    return end;
  }

  // ---- Data Loading ----
  function loadData() {
    var xhr = new XMLHttpRequest();
    xhr.open("GET", "data/data.json", true);
    xhr.onload = function () {
      if (xhr.status === 200) {
        try {
          DATA = JSON.parse(xhr.responseText);
          init();
        } catch (e) {
          showError("Failed to parse data: " + e.message);
        }
      } else {
        showError("Failed to load data (HTTP " + xhr.status + ")");
      }
    };
    xhr.onerror = function () {
      showError("Network error loading data");
    };
    xhr.send();
  }

  function showError(msg) {
    var el = document.getElementById("loading");
    el.innerHTML = '<div style="color:#d93025;font-size:14px;">' + escHtml(msg) + "</div>";
  }

  // ---- Initialization ----
  function init() {
    document.getElementById("loading").style.display = "none";

    // Detect which fields have data (sample first 200 records)
    var sample = (DATA.seasons || []).slice(0, 200);
    var hasData = {};
    COLUMNS.forEach(function (col) {
      if (col.key === "rank") return;
      hasData[col.key] = sample.some(function (r) {
        var v = r[col.key];
        return v != null && v !== "" && (!Array.isArray(v) || v.length > 0);
      });
    });

    // Init visible columns: use default, but hide columns with no data
    COLUMNS.forEach(function (col) {
      if (col.key === "rank") {
        visibleCols[col.key] = true;
      } else if (!hasData[col.key]) {
        visibleCols[col.key] = false;
      } else {
        visibleCols[col.key] = col.default;
      }
    });

    if (hasData["career_earnings"]) visibleCols["career_earnings"] = true;
    if (hasData["years_exp"]) visibleCols["years_exp"] = true;
    if (!hasData["team"]) visibleCols["team"] = false;
    if (!hasData["age"]) visibleCols["age"] = false;

    // Mobile: fewer default columns so Salary is next to Player
    if (window.innerWidth <= 768) {
      visibleCols["season"] = false;
      visibleCols["team"] = false;
      visibleCols["age"] = false;
      visibleCols["salary_rank_league"] = false;
      visibleCols["years_exp"] = false;
      visibleCols["career_earnings"] = false;
    }

    populateFilters();
    buildColumnToggles();
    bindEvents();
    populatePresets();
    loadStateFromURL();
    applyFilters();

    // Mobile: sidebar starts hidden (drawer is closed by default via CSS transform)
  }

  // ---- Populate filter dropdowns ----
  function populateFilters() {
    // Season dropdowns
    var fromSel = document.getElementById("seasonFrom");
    var toSel = document.getElementById("seasonTo");
    var seasons = DATA.seasons_list || [];

    // From: oldest first
    var seasonsAsc = seasons.slice().reverse();
    seasonsAsc.forEach(function (s) {
      fromSel.appendChild(new Option(s, s));
    });

    // To: newest first
    seasons.forEach(function (s) {
      toSel.appendChild(new Option(s, s));
    });

    // Default: 2025-26 season only
    var defaultSeason = "2025-26";
    fromSel.value = seasons.indexOf(defaultSeason) >= 0 ? defaultSeason : seasonsAsc[0] || "";
    toSel.value = seasons.indexOf(defaultSeason) >= 0 ? defaultSeason : seasons[0] || "";

    // Teams
    var teamSel = document.getElementById("teamFilter");
    (DATA.teams || []).forEach(function (t) {
      teamSel.appendChild(new Option(t, t));
    });

    // Nationality
    var natSel = document.getElementById("nationality");
    var nats = new Set();
    (DATA.seasons || []).forEach(function (r) {
      if (r.nationality) nats.add(r.nationality);
    });
    Array.from(nats).sort().forEach(function (n) {
      natSel.appendChild(new Option(n, n));
    });

    // College/Club
    var collegeSel = document.getElementById("collegeFilter");
    var colleges = new Set();
    (DATA.seasons || []).forEach(function (r) {
      if (r.college) colleges.add(r.college);
    });
    Array.from(colleges).sort().forEach(function (c) {
      collegeSel.appendChild(new Option(c, c));
    });
  }

  // ---- Column toggles ----
  function buildColumnToggles() {
    var container = document.getElementById("colToggles");
    container.innerHTML = "";
    COLUMNS.forEach(function (col) {
      if (col.key === "rank") return; // always shown
      var btn = document.createElement("button");
      btn.className = "col-toggle" + (visibleCols[col.key] ? " active" : "");
      btn.textContent = col.label;
      btn.setAttribute("data-col", col.key);
      btn.addEventListener("click", function () {
        visibleCols[col.key] = !visibleCols[col.key];
        btn.classList.toggle("active", visibleCols[col.key]);
        renderTable();
      });
      container.appendChild(btn);
    });
  }

  // ---- Event Binding ----
  function bindEvents() {
    // Filter section collapse
    document.querySelectorAll(".filter-section-header").forEach(function (hdr) {
      hdr.addEventListener("click", function () {
        hdr.parentElement.classList.toggle("collapsed");
      });
    });

    // Sidebar toggle (mobile slide-out drawer)
    var sidebar = document.getElementById("sidebar");
    var overlay = document.getElementById("sidebarOverlay");
    document.getElementById("sidebarToggle").addEventListener("click", function () {
      sidebar.classList.toggle("drawer-open");
      overlay.classList.toggle("active");
    });
    overlay.addEventListener("click", function () {
      sidebar.classList.remove("drawer-open");
      overlay.classList.remove("active");
    });

    // Clickable table cells (event delegation)
    document.getElementById("tableBody").addEventListener("click", function (e) {
      var badge = e.target.closest(".award-badge[data-award]");
      if (badge) {
        handleCellClick("awards", badge.dataset.award);
        return;
      }
      var cell = e.target.closest("td[data-col]");
      if (cell) {
        handleCellClick(cell.dataset.col, cell.dataset.val);
      }
    });

    // Breadcrumb navigation (Home resets to default)
    document.getElementById("breadcrumbHome").addEventListener("click", breadcrumbHome);

    // Clear filters
    document.getElementById("clearFiltersBtn").addEventListener("click", clearFilters);


    // Share
    document.getElementById("shareBtn").addEventListener("click", shareURL);

    // Shuffle presets button
    document.getElementById("shufflePresetsBtn").addEventListener("click", function () {
      populatePresets();
    });

    // Season presets
    document.querySelectorAll(".season-preset-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var wasActive = btn.classList.contains("active");
        document.querySelectorAll(".season-preset-btn").forEach(function (b) { b.classList.remove("active"); });
        if (wasActive) {
          // Reset to full range
          var seasons = DATA.seasons_list || [];
          var asc = seasons.slice().reverse();
          document.getElementById("seasonFrom").value = asc[0] || "";
          document.getElementById("seasonTo").value = seasons[0] || "";
        } else {
          btn.classList.add("active");
          document.getElementById("seasonFrom").value = btn.dataset.from;
          document.getElementById("seasonTo").value = btn.dataset.to;
        }
        applyFilters();
      });
    });

    // Filter chips (position, awards)
    document.querySelectorAll(".filter-checks .filter-chip").forEach(function (chip) {
      chip.addEventListener("click", function () {
        chip.classList.toggle("active");
        applyFilters();
      });
    });

    // Has any award checkbox
    document.getElementById("hasAnyAward").addEventListener("change", function () {
      applyFilters();
    });

    // Combine seasons toggle
    document.getElementById("combineToggle").addEventListener("change", function () {
      applyFilters();
    });

    // Input-based filters: debounced
    var debounceTimer = null;
    var filterInputIds = [
      "salaryMin", "salaryMax", "capPctMin", "capPctMax",
      "cppMin", "cppMax", "cpgMin", "cpgMax",
      "earningsMin", "earningsMax",
      "ageMin", "ageMax", "expMin", "expMax",
      "draftMin", "draftMax", "draftYearMin", "draftYearMax",
      "ppgMin", "ppgMax", "rpgMin", "rpgMax", "apgMin", "apgMax",
      "fgPctMin", "fgPctMax", "tpPctMin", "tpPctMax", "ftPctMin", "ftPctMax",
      "gpMin", "gpMax",
    ];
    filterInputIds.forEach(function (id) {
      var el = document.getElementById(id);
      if (el) {
        el.addEventListener("input", function () {
          clearTimeout(debounceTimer);
          debounceTimer = setTimeout(function () {
            applyFilters();
          }, 300);
        });
      }
    });

    // Select-based filters
    ["seasonFrom", "seasonTo", "leagueRank", "teamFilter", "nationality", "collegeFilter"].forEach(function (id) {
      document.getElementById(id).addEventListener("change", function () {
        applyFilters();
      });
    });

    // Player search autocomplete
    setupAutocomplete("playerSearch", "playerDropdown", function () {
      return DATA.players || [];
    });
  }

  // ---- Autocomplete ----
  function setupAutocomplete(inputId, dropdownId, getItems) {
    var input = document.getElementById(inputId);
    var dropdown = document.getElementById(dropdownId);
    var highlighted = -1;

    input.addEventListener("input", function () {
      var query = input.value.toLowerCase().trim();
      if (!query || query.length < 2) {
        dropdown.classList.remove("open");
        return;
      }
      var items = getItems();
      var matches = items.filter(function (item) {
        return item.toLowerCase().indexOf(query) >= 0;
      }).slice(0, 20);

      if (matches.length === 0) {
        dropdown.classList.remove("open");
        return;
      }

      dropdown.innerHTML = "";
      highlighted = -1;
      matches.forEach(function (m, i) {
        var div = document.createElement("div");
        div.className = "autocomplete-item";
        div.textContent = m;
        div.addEventListener("mousedown", function (e) {
          e.preventDefault();
          input.value = m;
          dropdown.classList.remove("open");
          applyFilters();
        });
        dropdown.appendChild(div);
      });
      dropdown.classList.add("open");
    });

    input.addEventListener("keydown", function (e) {
      var items = dropdown.querySelectorAll(".autocomplete-item");
      if (!dropdown.classList.contains("open") || items.length === 0) {
        if (e.key === "Enter") {
          applyFilters();
        }
        return;
      }
      if (e.key === "ArrowDown") {
        e.preventDefault();
        highlighted = Math.min(highlighted + 1, items.length - 1);
        updateHighlight(items);
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        highlighted = Math.max(highlighted - 1, 0);
        updateHighlight(items);
      } else if (e.key === "Enter") {
        e.preventDefault();
        if (highlighted >= 0 && highlighted < items.length) {
          input.value = items[highlighted].textContent;
          dropdown.classList.remove("open");
          applyFilters();
        }
      } else if (e.key === "Escape") {
        dropdown.classList.remove("open");
      }
    });

    input.addEventListener("blur", function () {
      setTimeout(function () { dropdown.classList.remove("open"); }, 150);
    });

    function updateHighlight(items) {
      items.forEach(function (item, i) {
        item.classList.toggle("highlighted", i === highlighted);
      });
      if (highlighted >= 0 && items[highlighted]) {
        items[highlighted].scrollIntoView({ block: "nearest" });
      }
    }
  }

  // ---- Presets ----
  // ---- Rotating Presets System ----
  // Team full names for labels
  var TEAM_NAMES = {
    ATL:"Atlanta Hawks",BKN:"Brooklyn Nets",BOS:"Boston Celtics",CHA:"Charlotte Hornets",
    CHI:"Chicago Bulls",CLE:"Cleveland Cavaliers",DAL:"Dallas Mavericks",DEN:"Denver Nuggets",
    DET:"Detroit Pistons",GSW:"Golden State Warriors",HOU:"Houston Rockets",
    IND:"Indiana Pacers",LAC:"Los Angeles Clippers",LAL:"Los Angeles Lakers",
    MEM:"Memphis Grizzlies",MIA:"Miami Heat",MIL:"Milwaukee Bucks",
    MIN:"Minnesota Timberwolves",NOP:"New Orleans Pelicans",NYK:"New York Knicks",
    OKC:"Oklahoma City Thunder",ORL:"Orlando Magic",PHI:"Philadelphia 76ers",
    PHX:"Phoenix Suns",POR:"Portland Trail Blazers",SAC:"Sacramento Kings",
    SAS:"San Antonio Spurs",TOR:"Toronto Raptors",UTA:"Utah Jazz",WAS:"Washington Wizards"
  };

  function buildAllPresets() {
    var P = [];

    // --- By College (top 30) ---
    ["Kentucky","Duke","North Carolina","UCLA","Arizona","Kansas","Michigan",
     "Georgia Tech","Connecticut","Michigan St","Florida","Texas","LSU","Syracuse",
     "Alabama","Arkansas","Georgetown","Villanova","Maryland","USC","Wake Forest",
     "Indiana","Washington","Memphis","Florida St","UNLV","Ohio St","Stanford",
     "Virginia","Gonzaga"].forEach(function(c) {
      P.push({label: c + " Alumni Salaries", f:{college:c}, sort:"salary", dir:"desc", allSeasons:true});
    });

    // --- International Clubs ---
    ["Barcelona","Real Madrid","Partizan","Mega Basket","Baskonia","Anadolu Efes",
     "Olimpia Milano","Fenerbahce","Maccabi Tel Aviv","ASVEL","Cibona","Union Olimpija",
     "Pau Orthez","Split"].forEach(function(c) {
      P.push({label: c + " Alumni Salaries", f:{college:c}, sort:"salary", dir:"desc", allSeasons:true});
    });

    // --- By Nationality (top 20) ---
    [["Canada","Canadian"],["France","French"],["Australia","Australian"],
     ["Serbia","Serbian"],["Nigeria","Nigerian"],["Germany","German"],
     ["Spain","Spanish"],["Croatia","Croatian"],["Turkey","Turkish"],
     ["Brazil","Brazilian"],["Lithuania","Lithuanian"],["Slovenia","Slovenian"],
     ["Great Britain","British"],["Dominican Republic","Dominican"],
     ["Argentina","Argentinian"],["Greece","Greek"],["Cameroon","Cameroonian"],
     ["Japan","Japanese"],["Italy","Italian"],["China","Chinese"]].forEach(function(pair) {
      P.push({label: "Highest-Paid " + pair[1] + " Players", f:{nationality:pair[0]}, sort:"salary", dir:"desc", allSeasons:true});
    });

    // --- By Team (all 30, all-time) ---
    Object.keys(TEAM_NAMES).forEach(function(tm) {
      P.push({label: TEAM_NAMES[tm] + " All-Time Salaries", f:{team:tm}, sort:"salary", dir:"desc", allSeasons:true});
    });

    // --- By Season ---
    ["2025-26","2024-25","2023-24","2022-23","2020-21","2015-16","2010-11",
     "2005-06","2000-01","1995-96","1990-91"].forEach(function(s) {
      P.push({label: "Highest-Paid NBA Players in " + s, f:{seasonFrom:s, seasonTo:s}, sort:"salary", dir:"desc"});
    });

    // --- By Award ---
    P.push({label: "NBA MVP Award Winner Salaries", f:{awards:["Most Valuable Player"]}, sort:"salary", dir:"desc", allSeasons:true});
    P.push({label: "Defensive Player of the Year Salaries", f:{awards:["Defensive Player of the Year"]}, sort:"salary", dir:"desc", allSeasons:true});
    P.push({label: "NBA All-Star Salaries", f:{awards:["All-Star"]}, sort:"salary", dir:"desc", allSeasons:true});
    P.push({label: "NBA Champions' Paychecks", f:{awards:["Champion"]}, sort:"salary", dir:"desc", allSeasons:true});
    P.push({label: "All-NBA Team Salaries", f:{awards:["All-NBA"]}, sort:"salary", dir:"desc", allSeasons:true});
    P.push({label: "Most Improved Player Award Salaries", f:{awards:["Most Improved Player"]}, sort:"salary", dir:"desc", allSeasons:true});
    P.push({label: "Rookie of the Year Award Salaries", f:{awards:["Rookie of the Year"]}, sort:"salary", dir:"desc", allSeasons:true});
    P.push({label: "Sixth Man of the Year Award Salaries", f:{awards:["Sixth Man of the Year"]}, sort:"salary", dir:"desc", allSeasons:true});

    // --- By Draft ---
    P.push({label: "#1 Overall Draft Pick Salaries", f:{draftMin:"1", draftMax:"1"}, sort:"salary", dir:"desc", allSeasons:true});
    P.push({label: "Top 3 Draft Picks' Salaries", f:{draftMin:"1", draftMax:"3"}, sort:"salary", dir:"desc", allSeasons:true});
    P.push({label: "Top 5 Draft Picks' Salaries", f:{draftMin:"1", draftMax:"5"}, sort:"salary", dir:"desc", allSeasons:true});
    P.push({label: "NBA Lottery Pick Salaries", f:{draftMin:"1", draftMax:"14"}, sort:"salary", dir:"desc", allSeasons:true});
    P.push({label: "Highest-Paid Second-Round Picks", f:{draftMin:"31", draftMax:"60", salaryMin:"10000000"}, sort:"salary", dir:"desc", allSeasons:true});
    P.push({label: "Highest-Paid Undrafted Players", f:{undrafted:true}, sort:"salary", dir:"desc", allSeasons:true});
    P.push({label: "Late First-Round Picks Scoring 15+ PPG", f:{draftMin:"20", draftMax:"30", ppgMin:"15"}, sort:"ppg", dir:"desc", allSeasons:true});
    // Famous draft classes
    ["2003","2018","1996","2024","2023","2022","2020","2015","2009","1984","2011"].forEach(function(yr) {
      P.push({label: "NBA " + yr + " Draft Class Salaries", f:{draftYearMin:yr, draftYearMax:yr}, sort:"salary", dir:"desc", allSeasons:true});
    });

    // --- Career Earnings ---
    P.push({label: "$500M Career Earnings Club", f:{earningsMin:"500000000"}, sort:"career_earnings", dir:"desc"});
    P.push({label: "$300M Career Earnings Club", f:{earningsMin:"300000000"}, sort:"career_earnings", dir:"desc"});
    P.push({label: "$200M Career Earnings Club", f:{earningsMin:"200000000"}, sort:"career_earnings", dir:"desc"});
    P.push({label: "$100M Career Earnings Club", f:{earningsMin:"100000000"}, sort:"career_earnings", dir:"desc"});

    // --- Salary Thresholds ---
    P.push({label: "Players Making $50M+ Per Season", f:{salaryMin:"50000000"}, sort:"salary", dir:"desc", allSeasons:true});
    P.push({label: "Players Making $40M+ Per Season", f:{salaryMin:"40000000"}, sort:"salary", dir:"desc", allSeasons:true});
    P.push({label: "Players Making $30M+ Per Season", f:{salaryMin:"30000000"}, sort:"salary", dir:"desc", allSeasons:true});
    P.push({label: "Under-$2M Players Scoring 15+ PPG", f:{salaryMax:"2000000", ppgMin:"15", gpMin:"40"}, sort:"ppg", dir:"desc", allSeasons:true});

    // --- By Age ---
    P.push({label: "Teenage NBA Players' Salaries", f:{ageMin:"18", ageMax:"19"}, sort:"salary", dir:"desc", allSeasons:true});
    P.push({label: "Highest-Paid Under-22 NBA Players", f:{ageMax:"21"}, sort:"salary", dir:"desc", allSeasons:true});
    P.push({label: "Highest-Paid NBA Players 35 and Older", f:{ageMin:"35"}, sort:"salary", dir:"desc", allSeasons:true});
    P.push({label: "Highest-Paid NBA Veterans Age 38+", f:{ageMin:"38"}, sort:"salary", dir:"desc", allSeasons:true});
    P.push({label: "NBA Players Age 40+ Salaries", f:{ageMin:"40"}, sort:"salary", dir:"desc", allSeasons:true});

    // --- By Position ---
    P.push({label: "Highest-Paid NBA Guards", f:{positions:["G"]}, sort:"salary", dir:"desc"});
    P.push({label: "Highest-Paid NBA Forwards", f:{positions:["F"]}, sort:"salary", dir:"desc"});
    P.push({label: "Highest-Paid NBA Centers", f:{positions:["C"]}, sort:"salary", dir:"desc"});

    // --- By Stat ---
    P.push({label: "25+ PPG Scorers' Salaries", f:{ppgMin:"25"}, sort:"salary", dir:"desc", allSeasons:true});
    P.push({label: "20+ PPG Scorers' Salaries", f:{ppgMin:"20"}, sort:"salary", dir:"desc", allSeasons:true});
    P.push({label: "10+ RPG Rebounders' Salaries", f:{rpgMin:"10"}, sort:"salary", dir:"desc", allSeasons:true});
    P.push({label: "10+ APG Playmakers' Salaries", f:{apgMin:"10"}, sort:"salary", dir:"desc", allSeasons:true});
    P.push({label: "50%+ FG Shooters' Salaries", f:{fgPctMin:"50", gpMin:"40"}, sort:"salary", dir:"desc", allSeasons:true});
    P.push({label: "40%+ Three-Point Shooters' Salaries", f:{tpPctMin:"40", gpMin:"40"}, sort:"salary", dir:"desc", allSeasons:true});
    P.push({label: "90%+ Free Throw Shooters' Salaries", f:{ftPctMin:"90", gpMin:"40"}, sort:"salary", dir:"desc", allSeasons:true});

    // --- Value/Contract ---
    P.push({label: "Best Value NBA Contracts (Cost Per Point)", f:{gpMin:"50"}, sort:"cost_per_point", dir:"asc", showCols:["cost_per_point"]});
    P.push({label: "Underpaid Stars: 20+ PPG on Less Than 15% of Cap", f:{ppgMin:"20", capPctMax:"15"}, sort:"salary_cap_pct", dir:"asc", allSeasons:true});
    P.push({label: "Rookie-Scale Contract Salaries (Top 30 Picks)", f:{draftMin:"1", draftMax:"30", expMin:"0", expMax:"3"}, sort:"salary", dir:"desc"});

    // --- Star Teammates ---
    [["Kobe Bryant","Kobe's"],["LeBron James","LeBron's"],["Stephen Curry","Curry's"],
     ["Kevin Durant","Durant's"],["Michael Jordan","Jordan's"],["Tim Duncan","Duncan's"],
     ["Dirk Nowitzki","Dirk's"],["Shaquille O'Neal","Shaq's"],["Dwyane Wade","Wade's"],
     ["Kevin Garnett","Garnett's"],["Giannis Antetokounmpo","Giannis'"],["Nikola Jokic","Jokic's"],
     ["Luka Doncic","Luka's"],["Allen Iverson","Iverson's"],["Vince Carter","Vince Carter's"]].forEach(function(pair) {
      P.push({label: pair[1] + " Teammates' Salaries", teammate: pair[0], sort:"salary", dir:"desc"});
    });

    return P;
  }

  var ALL_PRESETS = null;

  function populatePresets() {
    if (!ALL_PRESETS) ALL_PRESETS = buildAllPresets();
    var bar = document.getElementById("presetsBar");
    // Remove old preset buttons (keep shuffle btn)
    bar.querySelectorAll(".preset-btn").forEach(function(b) { b.remove(); });

    // Pick 5 random presets
    var shuffled = ALL_PRESETS.slice();
    for (var i = shuffled.length - 1; i > 0; i--) {
      var j = Math.floor(Math.random() * (i + 1));
      var temp = shuffled[i]; shuffled[i] = shuffled[j]; shuffled[j] = temp;
    }
    var picks = shuffled.slice(0, 4);

    picks.forEach(function(preset) {
      var btn = document.createElement("button");
      btn.className = "preset-btn";
      btn.textContent = preset.label;
      btn.addEventListener("click", function() {
        var wasActive = btn.classList.contains("active");
        bar.querySelectorAll(".preset-btn").forEach(function(b) { b.classList.remove("active"); });
        if (wasActive) {
          activePreset = null;
          clearFilters();
        } else {
          activePreset = preset;
          btn.classList.add("active");
          applyPreset(preset);
        }
      });
      bar.insertBefore(btn, document.getElementById("shufflePresetsBtn"));
    });
  }

  function applyPreset(preset) {
    clearFiltersQuiet();
    var fl = preset.f || {};

    // Open all seasons if specified
    if (preset.allSeasons) {
      var seasons = DATA.seasons_list || [];
      var asc = seasons.slice().reverse();
      document.getElementById("seasonFrom").value = asc[0] || "";
      document.getElementById("seasonTo").value = "2025-26";
    }

    // Teammate filter
    if (preset.teammate) {
      var starName = preset.teammate;
      _teammateLabel = starName;
      _teammateExclude = starName;
      var starSeasons = [];
      DATA.seasons.forEach(function(r) {
        if (r.player === starName) starSeasons.push(r.team + "|" + r.season);
      });
      _teammateFilter = new Set(starSeasons);
      // Open all seasons for teammate search
      var seasons = DATA.seasons_list || [];
      var asc = seasons.slice().reverse();
      document.getElementById("seasonFrom").value = asc[0] || "";
      document.getElementById("seasonTo").value = "2025-26";
    }

    // Map filter keys to UI elements
    var inputMap = {
      salaryMin:"salaryMin", salaryMax:"salaryMax",
      capPctMin:"capPctMin", capPctMax:"capPctMax",
      cppMin:"cppMin", cppMax:"cppMax",
      cpgMin:"cpgMin", cpgMax:"cpgMax",
      earningsMin:"earningsMin", earningsMax:"earningsMax",
      ppgMin:"ppgMin", ppgMax:"ppgMax",
      rpgMin:"rpgMin", rpgMax:"rpgMax",
      apgMin:"apgMin", apgMax:"apgMax",
      fgPctMin:"fgPctMin", fgPctMax:"fgPctMax",
      tpPctMin:"tpPctMin", tpPctMax:"tpPctMax",
      ftPctMin:"ftPctMin", ftPctMax:"ftPctMax",
      gpMin:"gpMin", gpMax:"gpMax",
      ageMin:"ageMin", ageMax:"ageMax",
      expMin:"expMin", expMax:"expMax",
      draftMin:"draftMin", draftMax:"draftMax",
      draftYearMin:"draftYearMin", draftYearMax:"draftYearMax"
    };
    Object.keys(inputMap).forEach(function(key) {
      if (fl[key] != null) {
        var el = document.getElementById(inputMap[key]);
        if (el) el.value = fl[key];
      }
    });

    // Select elements
    if (fl.college) document.getElementById("collegeFilter").value = fl.college;
    if (fl.nationality) document.getElementById("nationality").value = fl.nationality;
    if (fl.team) document.getElementById("teamFilter").value = fl.team;
    if (fl.leagueRank) document.getElementById("leagueRank").value = fl.leagueRank;
    if (fl.seasonFrom) document.getElementById("seasonFrom").value = fl.seasonFrom;
    if (fl.seasonTo) document.getElementById("seasonTo").value = fl.seasonTo;

    // Position chips
    if (fl.positions) {
      fl.positions.forEach(function(pos) {
        document.querySelectorAll("#positionFilter .filter-chip").forEach(function(c) {
          if (c.dataset.value === pos) c.classList.add("active");
        });
      });
    }

    // Award chips
    if (fl.awards) {
      fl.awards.forEach(function(a) {
        document.querySelectorAll("#awardsFilter .filter-chip").forEach(function(c) {
          if (c.dataset.value === a) c.classList.add("active");
        });
      });
    }

    if (fl.hasAnyAward) document.getElementById("hasAnyAward").checked = true;

    // Undrafted: filter by draft_pick being null (we need a special flag)
    // We'll handle undrafted with a special _undraftedFilter
    if (fl.undrafted) {
      _undraftedFilter = true;
    }

    // Sort and visible columns
    sortCol = preset.sort || "salary";
    sortDir = preset.dir || "desc";
    if (preset.showCols) {
      preset.showCols.forEach(function(col) {
        visibleCols[col] = true;
      });
      buildColumnToggles();
    }

    applyFilters();
  }

  // ---- Filter Logic ----
  function getFilterState() {
    var selectedPositions = [];
    document.querySelectorAll("#positionFilter .filter-chip.active").forEach(function (c) {
      selectedPositions.push(c.dataset.value);
    });

    var selectedAwards = [];
    document.querySelectorAll("#awardsFilter .filter-chip.active").forEach(function (c) {
      selectedAwards.push(c.dataset.value);
    });

    return {
      seasonFrom: document.getElementById("seasonFrom").value,
      seasonTo: document.getElementById("seasonTo").value,
      salaryMin: parseMoney(document.getElementById("salaryMin").value),
      salaryMax: parseMoney(document.getElementById("salaryMax").value),
      capPctMin: parseNum(document.getElementById("capPctMin").value),
      capPctMax: parseNum(document.getElementById("capPctMax").value),
      leagueRank: parseNum(document.getElementById("leagueRank").value),
      cppMin: parseMoney(document.getElementById("cppMin").value),
      cppMax: parseMoney(document.getElementById("cppMax").value),
      cpgMin: parseMoney(document.getElementById("cpgMin").value),
      cpgMax: parseMoney(document.getElementById("cpgMax").value),
      earningsMin: parseMoney(document.getElementById("earningsMin").value),
      earningsMax: parseMoney(document.getElementById("earningsMax").value),
      playerSearch: document.getElementById("playerSearch").value.trim().toLowerCase(),
      positions: selectedPositions,
      ageMin: parseNum(document.getElementById("ageMin").value),
      ageMax: parseNum(document.getElementById("ageMax").value),
      expMin: parseNum(document.getElementById("expMin").value),
      expMax: parseNum(document.getElementById("expMax").value),
      draftMin: parseNum(document.getElementById("draftMin").value),
      draftMax: parseNum(document.getElementById("draftMax").value),
      draftYearMin: parseNum(document.getElementById("draftYearMin").value),
      draftYearMax: parseNum(document.getElementById("draftYearMax").value),
      nationality: document.getElementById("nationality").value,
      college: document.getElementById("collegeFilter").value,
      team: document.getElementById("teamFilter").value,
      ppgMin: parseNum(document.getElementById("ppgMin").value),
      ppgMax: parseNum(document.getElementById("ppgMax").value),
      rpgMin: parseNum(document.getElementById("rpgMin").value),
      rpgMax: parseNum(document.getElementById("rpgMax").value),
      apgMin: parseNum(document.getElementById("apgMin").value),
      apgMax: parseNum(document.getElementById("apgMax").value),
      fgPctMin: parseNum(document.getElementById("fgPctMin").value),
      fgPctMax: parseNum(document.getElementById("fgPctMax").value),
      tpPctMin: parseNum(document.getElementById("tpPctMin").value),
      tpPctMax: parseNum(document.getElementById("tpPctMax").value),
      ftPctMin: parseNum(document.getElementById("ftPctMin").value),
      ftPctMax: parseNum(document.getElementById("ftPctMax").value),
      gpMin: parseNum(document.getElementById("gpMin").value),
      gpMax: parseNum(document.getElementById("gpMax").value),
      awards: selectedAwards,
      hasAnyAward: document.getElementById("hasAnyAward").checked,
    };
  }

  function matchesFilter(record, f) {
    // Season range
    var rYear = seasonYear(record.season);
    var fromYear = seasonYear(f.seasonFrom);
    var toYear = seasonYear(f.seasonTo);
    if (fromYear && rYear < fromYear) return false;
    if (toYear && rYear > toYear) return false;

    // Teammate filter (from preset)
    if (_teammateFilter && !_teammateFilter.has(record.team + "|" + record.season)) return false;
    if (_teammateExclude && record.player === _teammateExclude) return false;

    // Salary
    if (f.salaryMin != null && (record.salary == null || record.salary < f.salaryMin)) return false;
    if (f.salaryMax != null && (record.salary == null || record.salary > f.salaryMax)) return false;

    // Cap %
    if (f.capPctMin != null && (record.salary_cap_pct == null || record.salary_cap_pct < f.capPctMin)) return false;
    if (f.capPctMax != null && (record.salary_cap_pct == null || record.salary_cap_pct > f.capPctMax)) return false;

    // League rank (top N from select, or exact from cell click)
    if (_exactLeagueRank != null && record.salary_rank_league !== _exactLeagueRank) return false;
    if (_exactLeagueRank == null && f.leagueRank != null && (record.salary_rank_league == null || record.salary_rank_league > f.leagueRank)) return false;

    // Cost per point
    if (f.cppMin != null && (record.cost_per_point == null || record.cost_per_point < f.cppMin)) return false;
    if (f.cppMax != null && (record.cost_per_point == null || record.cost_per_point > f.cppMax)) return false;

    // Cost per game
    if (f.cpgMin != null && (record.cost_per_game == null || record.cost_per_game < f.cpgMin)) return false;
    if (f.cpgMax != null && (record.cost_per_game == null || record.cost_per_game > f.cpgMax)) return false;

    // Career earnings
    if (f.earningsMin != null && (record.career_earnings == null || record.career_earnings < f.earningsMin)) return false;
    if (f.earningsMax != null && (record.career_earnings == null || record.career_earnings > f.earningsMax)) return false;

    // Player search
    if (f.playerSearch && record.player.toLowerCase().indexOf(f.playerSearch) < 0) return false;

    // Position (exact match from cell click, or substring from sidebar chips)
    if (_exactPos) {
      if (record.pos !== _exactPos) return false;
    } else if (f.positions.length > 0 && record.pos) {
      var posMatch = false;
      for (var i = 0; i < f.positions.length; i++) {
        if (record.pos.indexOf(f.positions[i]) >= 0) { posMatch = true; break; }
      }
      if (!posMatch) return false;
    } else if (f.positions.length > 0 && !record.pos) {
      return false;
    }

    // Age
    if (f.ageMin != null && (record.age == null || record.age < f.ageMin)) return false;
    if (f.ageMax != null && (record.age == null || record.age > f.ageMax)) return false;

    // Experience
    if (f.expMin != null && (record.years_exp == null || record.years_exp < f.expMin)) return false;
    if (f.expMax != null && (record.years_exp == null || record.years_exp > f.expMax)) return false;

    // Draft pick
    if (f.draftMin != null && (record.draft_pick == null || record.draft_pick < f.draftMin)) return false;
    if (f.draftMax != null && (record.draft_pick == null || record.draft_pick > f.draftMax)) return false;

    // Undrafted filter
    if (_undraftedFilter && record.draft_pick != null) return false;

    // Draft year
    if (f.draftYearMin != null && (record.draft_year == null || record.draft_year < f.draftYearMin)) return false;
    if (f.draftYearMax != null && (record.draft_year == null || record.draft_year > f.draftYearMax)) return false;

    // Nationality
    if (f.nationality && record.nationality !== f.nationality) return false;

    // College/Club
    if (f.college && record.college !== f.college) return false;

    // Team
    if (f.team && record.team !== f.team) return false;

    // Stats
    if (f.ppgMin != null && (record.ppg == null || record.ppg < f.ppgMin)) return false;
    if (f.ppgMax != null && (record.ppg == null || record.ppg > f.ppgMax)) return false;
    if (f.rpgMin != null && (record.rpg == null || record.rpg < f.rpgMin)) return false;
    if (f.rpgMax != null && (record.rpg == null || record.rpg > f.rpgMax)) return false;
    if (f.apgMin != null && (record.apg == null || record.apg < f.apgMin)) return false;
    if (f.apgMax != null && (record.apg == null || record.apg > f.apgMax)) return false;
    if (f.fgPctMin != null && (record.fg_pct == null || record.fg_pct < f.fgPctMin)) return false;
    if (f.fgPctMax != null && (record.fg_pct == null || record.fg_pct > f.fgPctMax)) return false;
    if (f.tpPctMin != null && (record.tp_pct == null || record.tp_pct < f.tpPctMin)) return false;
    if (f.tpPctMax != null && (record.tp_pct == null || record.tp_pct > f.tpPctMax)) return false;
    if (f.ftPctMin != null && (record.ft_pct == null || record.ft_pct < f.ftPctMin)) return false;
    if (f.ftPctMax != null && (record.ft_pct == null || record.ft_pct > f.ftPctMax)) return false;
    if (f.gpMin != null && (record.gp == null || record.gp < f.gpMin)) return false;
    if (f.gpMax != null && (record.gp == null || record.gp > f.gpMax)) return false;

    // Awards (case-insensitive substring match, check each award individually)
    if (f.awards.length > 0) {
      if (!record.awards || record.awards.length === 0) return false;
      var anyMatch = false;
      for (var j = 0; j < f.awards.length; j++) {
        var chipLower = f.awards[j].toLowerCase();
        for (var k = 0; k < record.awards.length; k++) {
          if (record.awards[k].toLowerCase().indexOf(chipLower) >= 0) {
            anyMatch = true; break;
          }
        }
        if (anyMatch) break;
      }
      if (!anyMatch) return false;
    }

    if (f.hasAnyAward && (!record.awards || record.awards.length === 0)) return false;

    return true;
  }

  // ---- Multi-Season Combine ----
  var AWARD_PRIORITY = [
    "Most Valuable Player",
    "Finals Most Valuable Player",
    "All-NBA First Team",
    "All-NBA Second Team",
    "All-NBA Third Team",
    "All-Star",
    "Defensive Player of the Year",
    "Most Improved Player",
    "Rookie of the Year",
    "Sixth Man of the Year",
    "NBA Champion"
  ];

  function highestPriorityAward(awards) {
    if (!awards || awards.length === 0) return [];
    for (var i = 0; i < AWARD_PRIORITY.length; i++) {
      for (var j = 0; j < awards.length; j++) {
        if (awards[j] === AWARD_PRIORITY[i]) return [awards[j]];
      }
    }
    return [awards[0]];
  }

  // Format season list: consecutive runs use "to", gaps use commas
  // e.g. ["2019-20","2020-21","2021-22"] => "2019-20 to 2021-22"
  // e.g. ["2019-20","2024-25","2025-26"] => "2019-20, 2024-25 to 2025-26"
  function formatSeasonRange(seasons) {
    if (seasons.length <= 1) return seasons[0] || "";
    var years = seasons.map(function(s) { return seasonYear(s); });
    var runs = [];
    var runStart = 0;
    for (var i = 1; i <= years.length; i++) {
      if (i < years.length && years[i] === years[i - 1] + 1) continue;
      // End of a run: runStart..i-1
      if (i - 1 === runStart) {
        runs.push(seasons[runStart]);
      } else {
        runs.push(seasons[runStart] + " to " + seasons[i - 1]);
      }
      runStart = i;
    }
    return runs.join(", ");
  }

  function combineByPlayer(records) {
    var groups = {};
    var order = [];
    records.forEach(function (r) {
      var key = r.player.toLowerCase();
      if (!groups[key]) {
        groups[key] = [];
        order.push(key);
      }
      groups[key].push(r);
    });

    var combined = [];
    order.forEach(function (key) {
      var recs = groups[key];
      // Sort by season year (newest first)
      recs.sort(function (a, b) { return seasonYear(b.season) - seasonYear(a.season); });
      var latest = recs[0];
      var earliest = recs[recs.length - 1];

      var totalSalary = 0;
      var totalGP = 0;
      var totalPTS = 0, totalREB = 0, totalAST = 0, totalSTL = 0, totalBLK = 0;
      var wFG = 0, wTP = 0, wFT = 0;
      var capSum = 0, capCount = 0;
      var awardsSet = {};
      var allAwards = [];

      recs.forEach(function (r) {
        if (r.salary) totalSalary += r.salary;
        var gp = r.gp || 0;
        totalGP += gp;
        // Use raw totals for accurate combined averages
        if (r.pts != null) totalPTS += r.pts;
        if (r.reb != null) totalREB += r.reb;
        if (r.ast != null) totalAST += r.ast;
        if (r.stl != null) totalSTL += r.stl;
        if (r.blk != null) totalBLK += r.blk;
        if (gp > 0) {
          if (r.fg_pct != null) wFG += r.fg_pct * gp;
          if (r.tp_pct != null) wTP += r.tp_pct * gp;
          if (r.ft_pct != null) wFT += r.ft_pct * gp;
        }
        if (r.salary_cap_pct != null) { capSum += r.salary_cap_pct; capCount++; }
        if (r.awards) {
          r.awards.forEach(function (a) {
            if (!awardsSet[a]) { awardsSet[a] = true; allAwards.push(a); }
          });
        }
      });

      // Age range: earliest age to latest age
      var ageDisplay = latest.age;
      if (recs.length > 1 && earliest.age != null && latest.age != null && earliest.age !== latest.age) {
        ageDisplay = earliest.age + "-" + latest.age;
      }

      combined.push({
        player: latest.player,
        season: recs.length === 1 ? latest.season : formatSeasonRange(recs.map(function(r){ return r.season; }).reverse()),
        _seasonYear: seasonYear(latest.season),
        _numSeasons: recs.length,
        _combined: true,
        team: latest.team,
        age: ageDisplay,
        salary: totalSalary || null,
        salary_cap_pct: capCount > 0 ? Math.round(capSum / capCount * 10) / 10 : null,
        salary_rank_league: null,
        years_exp: latest.years_exp,
        gp: totalGP || null,
        pts: totalPTS || null,
        reb: totalREB || null,
        ast: totalAST || null,
        stl: totalSTL || null,
        blk: totalBLK || null,
        ppg: totalGP > 0 ? Math.round(totalPTS / totalGP * 10) / 10 : null,
        rpg: totalGP > 0 ? Math.round(totalREB / totalGP * 10) / 10 : null,
        apg: totalGP > 0 ? Math.round(totalAST / totalGP * 10) / 10 : null,
        spg: totalGP > 0 ? Math.round(totalSTL / totalGP * 10) / 10 : null,
        bpg: totalGP > 0 ? Math.round(totalBLK / totalGP * 10) / 10 : null,
        fg_pct: totalGP > 0 ? Math.round(wFG / totalGP * 1000) / 1000 : null,
        tp_pct: totalGP > 0 ? Math.round(wTP / totalGP * 1000) / 1000 : null,
        ft_pct: totalGP > 0 ? Math.round(wFT / totalGP * 1000) / 1000 : null,
        cost_per_point: totalPTS > 0 ? Math.round(totalSalary / totalPTS) : null,
        cost_per_game: totalGP > 0 ? Math.round(totalSalary / totalGP) : null,
        career_earnings: latest.career_earnings,
        awards: highestPriorityAward(allAwards),
        pos: latest.pos,
        nationality: latest.nationality,
        college: latest.college,
        draft_year: latest.draft_year,
        draft_pick: latest.draft_pick,
      });
    });

    return combined;
  }

  function applyFilters() {
    if (!DATA || !DATA.seasons) return;

    var f = getFilterState();
    filtered = DATA.seasons.filter(function (r) {
      return matchesFilter(r, f);
    });

    // If multi-season range and combine is checked, aggregate by player
    var fromYear = seasonYear(f.seasonFrom);
    var toYear = seasonYear(f.seasonTo);
    var isMultiSeason = fromYear !== toYear;
    var combineWrap = document.getElementById("combineToggleWrap");
    var combineEl = document.getElementById("combineToggle");
    if (combineWrap) combineWrap.style.display = isMultiSeason ? "" : "none";
    var isCombineActive = isMultiSeason && combineEl && combineEl.checked;
    if (isCombineActive) {
      filtered = combineByPlayer(filtered);
    }

    // Auto-hide LG RANK in combined mode, restore when not combined
    if (isCombineActive) {
      if (_savedRankVis == null) {
        _savedRankVis = {
          salary_rank_league: visibleCols["salary_rank_league"]
        };
      }
      visibleCols["salary_rank_league"] = false;
    } else if (_savedRankVis != null) {
      visibleCols["salary_rank_league"] = _savedRankVis.salary_rank_league;
      _savedRankVis = null;
    }
    buildColumnToggles();

    // Sort
    sortData();

    // Update summary
    updateSummary();

    // Update URL state
    saveStateToURL();

    // Render
    renderBreadcrumbs();
    updatePageTitle();
    renderTable();
  }

  // ---- Sorting ----
  function sortData() {
    var col = sortCol;
    var dir = sortDir === "asc" ? 1 : -1;
    filtered.sort(function (a, b) {
      var va, vb;
      // For season column, use numeric year for sorting (handles combined "X to Y" strings)
      if (col === "season") {
        va = a._seasonYear || seasonYear(a.season);
        vb = b._seasonYear || seasonYear(b.season);
        if (va === vb) return 0;
        return (va - vb) * dir;
      }
      va = a[col];
      vb = b[col];
      // Handle nulls: push to end
      if (va == null && vb == null) return 0;
      if (va == null) return 1;
      if (vb == null) return -1;
      // Strings
      if (typeof va === "string" && typeof vb === "string") {
        return va.localeCompare(vb) * dir;
      }
      // Arrays (awards)
      if (Array.isArray(va)) {
        return (va.length - vb.length) * dir;
      }
      // Numbers
      return (va - vb) * dir;
    });
  }

  // ---- Summary Bar ----
  function updateSummary() {
    var count = filtered.length;
    var totalSalary = 0;
    var totalCap = 0;
    var capCount = 0;
    var highSalary = 0;
    var highPlayer = "";

    for (var i = 0; i < count; i++) {
      var r = filtered[i];
      if (r.salary) {
        totalSalary += r.salary;
        if (r.salary > highSalary) {
          highSalary = r.salary;
          highPlayer = r.player + " (" + r.season + ")";
        }
      }
      if (r.salary_cap_pct != null) {
        totalCap += r.salary_cap_pct;
        capCount++;
      }
    }

    document.getElementById("summaryCount").textContent = count.toLocaleString();
    document.getElementById("summaryAvgSalary").textContent = count > 0 ? fmtSalary(totalSalary / count) : "$0";
    document.getElementById("summaryAvgCap").textContent = capCount > 0 ? (totalCap / capCount).toFixed(1) + "%" : "0%";
    document.getElementById("summaryHighSalary").textContent = fmtSalary(highSalary);
    document.getElementById("summaryHighPlayer").textContent = highPlayer;
    document.getElementById("summaryTotalSalary").textContent = fmtSalary(totalSalary);
  }

  // ---- Breadcrumb Rendering (shows all active filters) ----
  function renderBreadcrumbs() {
    var bar = document.getElementById("breadcrumbBar");
    var trail = document.getElementById("breadcrumbTrail");
    var f = getFilterState();
    var tags = [];

    // Default season to compare against
    var defaultSeason = "2025-26";
    var isDefaultSeason = (f.seasonFrom === defaultSeason && f.seasonTo === defaultSeason);

    // Season (only show if not default)
    if (!isDefaultSeason && f.seasonFrom && f.seasonTo) {
      var sLabel = f.seasonFrom === f.seasonTo ? f.seasonFrom : f.seasonFrom + " to " + f.seasonTo;
      tags.push({ label: "Season", value: sLabel, clear: function() {
        document.getElementById("seasonFrom").value = defaultSeason;
        document.getElementById("seasonTo").value = defaultSeason;
        document.querySelectorAll(".season-preset-btn.active").forEach(function(b){ b.classList.remove("active"); });
      }});
    }

    // Player
    if (f.playerSearch) tags.push({ label: "Player", value: f.playerSearch, clear: function() {
      document.getElementById("playerSearch").value = "";
    }});

    // Position
    if (f.positions.length > 0) tags.push({ label: "Pos", value: f.positions.join(", "), clear: function() {
      document.querySelectorAll("#positionFilter .filter-chip.active").forEach(function(c){ c.classList.remove("active"); });
    }});

    // College
    if (f.college) tags.push({ label: "College/Club", value: f.college, clear: function() {
      document.getElementById("collegeFilter").value = "";
    }});

    // Age
    if (f.ageMin != null || f.ageMax != null) {
      var v = fmtRange(f.ageMin, f.ageMax);
      tags.push({ label: "Age", value: v, clear: function() {
        document.getElementById("ageMin").value = "";
        document.getElementById("ageMax").value = "";
      }});
    }

    // Experience
    if (f.expMin != null || f.expMax != null) {
      tags.push({ label: "Exp", value: fmtRange(f.expMin, f.expMax), clear: function() {
        document.getElementById("expMin").value = "";
        document.getElementById("expMax").value = "";
      }});
    }

    // Draft Pick
    if (f.draftMin != null || f.draftMax != null) {
      tags.push({ label: "Pick", value: fmtRange(f.draftMin, f.draftMax), clear: function() {
        document.getElementById("draftMin").value = "";
        document.getElementById("draftMax").value = "";
      }});
    }

    // Draft Year
    if (f.draftYearMin != null || f.draftYearMax != null) {
      tags.push({ label: "Draft Class", value: fmtRange(f.draftYearMin, f.draftYearMax), clear: function() {
        document.getElementById("draftYearMin").value = "";
        document.getElementById("draftYearMax").value = "";
      }});
    }

    // Nationality
    if (f.nationality) tags.push({ label: "Nationality", value: f.nationality, clear: function() {
      document.getElementById("nationality").value = "";
    }});

    // Team
    if (f.team) tags.push({ label: "Team", value: f.team, clear: function() {
      document.getElementById("teamFilter").value = "";
    }});

    // Salary
    if (f.salaryMin != null || f.salaryMax != null) {
      tags.push({ label: "Salary", value: fmtRange$(f.salaryMin, f.salaryMax), clear: function() {
        document.getElementById("salaryMin").value = "";
        document.getElementById("salaryMax").value = "";
      }});
    }

    // Cap %
    if (f.capPctMin != null || f.capPctMax != null) {
      tags.push({ label: "Cap%", value: fmtRangePct(f.capPctMin, f.capPctMax), clear: function() {
        document.getElementById("capPctMin").value = "";
        document.getElementById("capPctMax").value = "";
      }});
    }

    // League Rank
    if (_exactLeagueRank != null) {
      tags.push({ label: "Lg Rank", value: "#" + _exactLeagueRank, clear: function() {
        _exactLeagueRank = null;
      }});
    } else if (f.leagueRank != null) {
      tags.push({ label: "Lg Rank", value: "Top " + f.leagueRank, clear: function() {
        document.getElementById("leagueRank").value = "";
      }});
    }

    // Exact position (from cell click)
    if (_exactPos) {
      tags.push({ label: "Pos", value: _exactPos, clear: function() {
        _exactPos = null;
      }});
    }

    // Cost per point
    if (f.cppMin != null || f.cppMax != null) {
      tags.push({ label: "$/Point", value: fmtRange$(f.cppMin, f.cppMax), clear: function() {
        document.getElementById("cppMin").value = "";
        document.getElementById("cppMax").value = "";
      }});
    }

    // Cost per game
    if (f.cpgMin != null || f.cpgMax != null) {
      tags.push({ label: "$/Game", value: fmtRange$(f.cpgMin, f.cpgMax), clear: function() {
        document.getElementById("cpgMin").value = "";
        document.getElementById("cpgMax").value = "";
      }});
    }

    // Career Earnings
    if (f.earningsMin != null || f.earningsMax != null) {
      tags.push({ label: "Career $", value: fmtRange$(f.earningsMin, f.earningsMax), clear: function() {
        document.getElementById("earningsMin").value = "";
        document.getElementById("earningsMax").value = "";
      }});
    }

    // Stats: PPG, RPG, APG, FG%, 3P%, FT%, GP
    var statFilters = [
      { label: "PPG", min: f.ppgMin, max: f.ppgMax, minId: "ppgMin", maxId: "ppgMax" },
      { label: "RPG", min: f.rpgMin, max: f.rpgMax, minId: "rpgMin", maxId: "rpgMax" },
      { label: "APG", min: f.apgMin, max: f.apgMax, minId: "apgMin", maxId: "apgMax" },
      { label: "FG%", min: f.fgPctMin, max: f.fgPctMax, minId: "fgPctMin", maxId: "fgPctMax" },
      { label: "3P%", min: f.tpPctMin, max: f.tpPctMax, minId: "tpPctMin", maxId: "tpPctMax" },
      { label: "FT%", min: f.ftPctMin, max: f.ftPctMax, minId: "ftPctMin", maxId: "ftPctMax" },
      { label: "GP", min: f.gpMin, max: f.gpMax, minId: "gpMin", maxId: "gpMax" },
    ];
    statFilters.forEach(function(sf) {
      if (sf.min != null || sf.max != null) {
        tags.push({ label: sf.label, value: fmtRange(sf.min, sf.max), clear: (function(minId, maxId) {
          return function() {
            document.getElementById(minId).value = "";
            document.getElementById(maxId).value = "";
          };
        })(sf.minId, sf.maxId) });
      }
    });

    // Awards
    if (f.awards.length > 0) {
      tags.push({ label: "Award", value: f.awards.map(function(a) {
        // Shorten for display
        if (a === "Most Valuable Player") return "MVP";
        if (a === "Defensive Player of the Year") return "DPOY";
        if (a === "Most Improved Player") return "MIP";
        if (a === "Rookie of the Year") return "ROY";
        if (a === "Sixth Man of the Year") return "6MOY";
        if (a === "Player of the Week") return "POTW";
        if (a === "Player of the Month") return "POTM";
        if (a === "Defensive Player of the Month") return "DPOTM";
        return a;
      }).join(", "), clear: function() {
        document.querySelectorAll("#awardsFilter .filter-chip.active").forEach(function(c){ c.classList.remove("active"); });
      }});
    }
    if (f.hasAnyAward) tags.push({ label: "Has Award", value: "Yes", clear: function() {
      document.getElementById("hasAnyAward").checked = false;
    }});

    // Teammate filter
    if (_teammateFilter && _teammateLabel) {
      tags.push({ label: "Teammates of", value: _teammateLabel, clear: function() {
        _teammateFilter = null;
        _teammateLabel = null;
        _teammateExclude = null;
      }});
    }

    // Undrafted filter
    if (_undraftedFilter) {
      tags.push({ label: "Draft", value: "Undrafted", clear: function() {
        _undraftedFilter = false;
      }});
    }

    // Render
    if (tags.length === 0) {
      bar.style.display = "none";
      return;
    }
    bar.style.display = "";
    trail.innerHTML = "";
    tags.forEach(function(t) {
      var tag = document.createElement("span");
      tag.className = "breadcrumb-tag";
      tag.innerHTML = '<span class="bc-col">' + escHtml(t.label) + ':</span> ' + escHtml(t.value) + ' <span class="bc-remove" title="Remove filter">\u00D7</span>';
      tag.querySelector(".bc-remove").addEventListener("click", function(e) {
        e.stopPropagation();
        t.clear();
        applyFilters();
      });
      trail.appendChild(tag);
    });
  }

  // Range formatting helpers for breadcrumbs
  function fmtRange(min, max) {
    if (min != null && max != null) {
      return min === max ? String(min) : min + "–" + max;
    }
    if (min != null) return "≥" + min;
    return "≤" + max;
  }

  function fmtRange$(min, max) {
    if (min != null && max != null) return fmtSalary(min) + "–" + fmtSalary(max);
    if (min != null) return "≥" + fmtSalary(min);
    return "≤" + fmtSalary(max);
  }

  function fmtRangePct(min, max) {
    if (min != null && max != null) return min + "%–" + max + "%";
    if (min != null) return "≥" + min + "%";
    return "≤" + max + "%";
  }

  function breadcrumbHome() {
    clearFilters();
  }

  // ---- Dynamic Page Title ----
  function titleCase(str) {
    return str.replace(/\b\w/g, function(c) { return c.toUpperCase(); });
  }

  function updatePageTitle() {
    var base = "HoopsMatic";
    // If a preset is active, use its label
    if (activePreset && activePreset.label) {
      document.title = activePreset.label + " | " + base;
      return;
    }
    // Build title from active filters
    var parts = [];
    var f = getFilterState();

    // Player name (capitalized)
    if (f.playerSearch) parts.push(titleCase(f.playerSearch) + " Salaries");

    // Teammate
    if (_teammateLabel) parts.push(_teammateLabel + " Teammates' Salaries");

    // Team (full name)
    if (f.team) parts.push((TEAM_NAMES[f.team] || f.team) + " Salaries");

    // College
    if (f.college) parts.push(f.college + " Alumni Salaries");

    // Nationality
    if (f.nationality) {
      var natAdj = {
        "United States":"American","Canada":"Canadian","France":"French","Australia":"Australian",
        "Serbia":"Serbian","Nigeria":"Nigerian","Germany":"German","Spain":"Spanish",
        "Croatia":"Croatian","Turkey":"Turkish","Brazil":"Brazilian","Lithuania":"Lithuanian",
        "Slovenia":"Slovenian","Great Britain":"British","Dominican Republic":"Dominican",
        "Argentina":"Argentinian","Greece":"Greek","Cameroon":"Cameroonian","Japan":"Japanese",
        "Italy":"Italian","China":"Chinese","Puerto Rico":"Puerto Rican","Ukraine":"Ukrainian",
        "Mexico":"Mexican","Israel":"Israeli","New Zealand":"New Zealand"
      };
      var adj = natAdj[f.nationality] || f.nationality;
      parts.push("Highest-Paid " + adj + " Players");
    }

    // Position
    if (f.positions.length > 0) {
      var posNames = { G: "Guards", F: "Forwards", C: "Centers" };
      var posLabels = f.positions.map(function(p) { return posNames[p] || p; });
      parts.push("Highest-Paid " + posLabels.join(" & "));
    }

    // Awards
    if (f.awards.length > 0) {
      var awardNames = {
        "Most Valuable Player": "MVP",
        "All-Star": "All-Star",
        "Champion": "NBA Champion",
        "All-NBA": "All-NBA",
        "All-Defensive": "All-Defensive",
        "Defensive Player of the Year": "DPOY",
        "Most Improved Player": "MIP",
        "Rookie of the Year": "Rookie of the Year",
        "Sixth Man of the Year": "Sixth Man",
        "Player of the Week": "Player of the Week",
        "Player of the Month": "Player of the Month",
        "Defensive Player of the Month": "Defensive Player of the Month"
      };
      var aLabels = f.awards.map(function(a) { return awardNames[a] || a; });
      parts.push(aLabels.join(" & ") + " Salaries");
    }

    // Salary threshold
    if (f.salaryMin != null && f.salaryMin >= 30000000) parts.push(fmtSalary(f.salaryMin) + "+ Club");

    // Career earnings
    if (f.earningsMin != null) parts.push(fmtSalary(f.earningsMin) + "+ Career Earnings");

    // PPG threshold
    if (f.ppgMin != null) parts.push(f.ppgMin + "+ PPG Scorers");

    // Age
    if (f.ageMin != null && f.ageMax != null) parts.push("Age " + f.ageMin + "–" + f.ageMax + " Players");
    else if (f.ageMin != null) parts.push("Age " + f.ageMin + "+ Players");
    else if (f.ageMax != null) parts.push("Under " + (f.ageMax + 1) + " Players");

    // Draft
    if (_undraftedFilter) parts.push("Undrafted Players' Salaries");
    if (f.draftMin != null && f.draftMax != null && f.draftMin === 1 && f.draftMax === 1) parts.push("#1 Overall Picks");
    else if (f.draftMin != null && f.draftMax != null) parts.push("Picks " + f.draftMin + "–" + f.draftMax);

    // Experience
    if (f.expMax != null && f.expMax <= 4) parts.push("Rookie-Scale Contracts");

    // Season
    var defaultSeason = "2025-26";
    var seasonLabel = "";
    if (f.seasonFrom === f.seasonTo) {
      seasonLabel = f.seasonFrom;
    } else {
      seasonLabel = f.seasonFrom + " to " + f.seasonTo;
    }

    if (parts.length > 0) {
      // Add season context if not default
      var isDefault = (f.seasonFrom === defaultSeason && f.seasonTo === defaultSeason);
      if (!isDefault && f.seasonFrom === f.seasonTo) {
        document.title = parts.join(" \u2014 ") + " (" + seasonLabel + ") | " + base;
      } else {
        document.title = parts.join(" \u2014 ") + " | " + base;
      }
    } else {
      document.title = "NBA Salary Season Finder \u2014 " + seasonLabel + " | " + base;
    }
  }

  // ---- Clickable Cell Filter ----
  function handleCellClick(colKey, rawValue) {
    if (!rawValue || rawValue === "-") return;

    // Save current season range before clearing filters
    var curFrom = document.getElementById("seasonFrom").value;
    var curTo = document.getElementById("seasonTo").value;

    // Columns that should open all seasons (ignore current range)
    var allSeasonsCols = { player: true, draft_year: true, salary_rank_league: true };

    // Clean slate: clear ALL filters
    clearFiltersQuiet();

    // Restore season range for most columns; open all seasons for exceptions
    var seasons = DATA.seasons_list || [];
    var asc = seasons.slice().reverse();
    if (allSeasonsCols[colKey]) {
      document.getElementById("seasonFrom").value = asc[0] || "";
      document.getElementById("seasonTo").value = seasons[0] || "";
    } else {
      document.getElementById("seasonFrom").value = curFrom;
      document.getElementById("seasonTo").value = curTo;
    }

    // Helper: set ±10% range on two inputs
    function setRange10(minId, maxId, val) {
      var n = parseFloat(val);
      if (isNaN(n) || n === 0) return;
      var lo = Math.round(n * 0.9);
      var hi = Math.round(n * 1.1);
      document.getElementById(minId).value = lo;
      document.getElementById(maxId).value = hi;
    }

    // Helper: set ±10% range for decimal stats
    function setRange10Dec(minId, maxId, val) {
      var n = parseFloat(val);
      if (isNaN(n)) return;
      var lo = (n * 0.9).toFixed(1);
      var hi = (n * 1.1).toFixed(1);
      document.getElementById(minId).value = lo;
      document.getElementById(maxId).value = hi;
    }

    switch (colKey) {
      case "player":
        document.getElementById("playerSearch").value = rawValue;
        // Disable combine so user sees individual seasons
        var combineEl = document.getElementById("combineToggle");
        if (combineEl) combineEl.checked = false;
        sortCol = "season";
        sortDir = "desc";
        break;
      case "season":
        document.getElementById("seasonFrom").value = rawValue;
        document.getElementById("seasonTo").value = rawValue;
        sortCol = "salary";
        sortDir = "desc";
        break;
      case "team":
        document.getElementById("teamFilter").value = rawValue;
        break;
      case "nationality":
        document.getElementById("nationality").value = rawValue;
        break;
      case "college":
        document.getElementById("collegeFilter").value = rawValue;
        break;
      case "pos":
        _exactPos = rawValue;
        break;
      case "awards":
        // Use case-insensitive substring to match award badge click to a chip
        var rawLower = rawValue.toLowerCase();
        document.querySelectorAll("#awardsFilter .filter-chip").forEach(function (c) {
          if (rawLower.indexOf(c.dataset.value.toLowerCase()) >= 0) c.classList.add("active");
        });
        // If the award doesn't match any chip, use hasAnyAward
        var matched = false;
        document.querySelectorAll("#awardsFilter .filter-chip.active").forEach(function () { matched = true; });
        if (!matched) document.getElementById("hasAnyAward").checked = true;
        break;
      // Exact match columns
      case "years_exp":
        document.getElementById("expMin").value = rawValue;
        document.getElementById("expMax").value = rawValue;
        break;
      case "age":
        document.getElementById("ageMin").value = rawValue;
        document.getElementById("ageMax").value = rawValue;
        break;
      case "gp":
        document.getElementById("gpMin").value = rawValue;
        document.getElementById("gpMax").value = rawValue;
        break;
      case "draft_pick":
        document.getElementById("draftMin").value = rawValue;
        document.getElementById("draftMax").value = rawValue;
        break;
      case "draft_year":
        document.getElementById("draftYearMin").value = rawValue;
        document.getElementById("draftYearMax").value = rawValue;
        break;
      // Exact rank match
      case "salary_rank_league":
        _exactLeagueRank = parseInt(rawValue, 10);
        break;
      // ±10% range columns (salary/money)
      case "salary":
        setRange10("salaryMin", "salaryMax", rawValue);
        break;
      case "salary_cap_pct":
        setRange10Dec("capPctMin", "capPctMax", rawValue);
        break;
      case "cost_per_point":
        setRange10("cppMin", "cppMax", rawValue);
        visibleCols["cost_per_point"] = true;
        buildColumnToggles();
        break;
      case "cost_per_game":
        setRange10("cpgMin", "cpgMax", rawValue);
        visibleCols["cost_per_game"] = true;
        buildColumnToggles();
        break;
      case "career_earnings":
        setRange10("earningsMin", "earningsMax", rawValue);
        break;
      // ±10% range columns (stats)
      case "ppg":
        setRange10Dec("ppgMin", "ppgMax", rawValue);
        break;
      case "rpg":
        setRange10Dec("rpgMin", "rpgMax", rawValue);
        break;
      case "apg":
        setRange10Dec("apgMin", "apgMax", rawValue);
        break;
      case "spg":
        // SPG filter inputs don't exist in sidebar, skip range
        break;
      case "bpg":
        // BPG filter inputs don't exist in sidebar, skip range
        break;
      case "fg_pct":
        setRange10Dec("fgPctMin", "fgPctMax", rawValue);
        break;
      case "tp_pct":
        setRange10Dec("tpPctMin", "tpPctMax", rawValue);
        break;
      case "ft_pct":
        setRange10Dec("ftPctMin", "ftPctMax", rawValue);
        break;
      default:
        return; // not a filterable column
    }

    applyFilters();
    scrollToTop();
  }

  // ---- Table Rendering ----
  function renderTable() {
    document.getElementById("tableWrapper").style.display = "";

    var activeCols = COLUMNS.filter(function (c) {
      return c.key === "rank" || visibleCols[c.key];
    });

    // Header
    var thead = document.getElementById("tableHead");
    var headerRow = "<tr>";
    activeCols.forEach(function (col) {
      var isSorted = col.key === sortCol;
      var arrow = col.sortable ? '<span class="sort-arrow">' + (isSorted ? (sortDir === "asc" ? "\u25B2" : "\u25BC") : "\u25BC") + "</span>" : "";
      var cls = isSorted ? ' class="sorted"' : "";
      var clickAttr = col.sortable ? ' data-sort="' + col.key + '"' : "";
      headerRow += "<th" + cls + clickAttr + ">" + col.label + arrow + "</th>";
    });
    headerRow += "</tr>";
    thead.innerHTML = headerRow;

    // Bind sort clicks
    thead.querySelectorAll("th[data-sort]").forEach(function (th) {
      th.addEventListener("click", function () {
        var key = th.dataset.sort;
        if (sortCol === key) {
          sortDir = sortDir === "asc" ? "desc" : "asc";
        } else {
          sortCol = key;
          sortDir = "desc";
        }
        sortData();
        renderTable();
      });
    });

    // Body - render all results (no pagination)
    var total = filtered.length;
    var tbody = document.getElementById("tableBody");
    var html = "";

    if (total === 0) {
      document.getElementById("emptyState").style.display = "";
      tbody.innerHTML = "";
    } else {
      document.getElementById("emptyState").style.display = "none";
      filtered.forEach(function (record, idx) {
        html += "<tr>";
        activeCols.forEach(function (col) {
          if (col.key === "rank") {
            html += '<td class="rank">' + (idx + 1) + "</td>";
          } else {
            var val = record[col.key];
            var tdClass = "";
            if (col.type === "salary") tdClass = "salary";
            else if (col.type === "num" || col.type === "stat" || col.type === "pct" || col.type === "pct3") tdClass = "num";
            else if (col.key === "player") tdClass = "player-name";
            else if (col.key === "awards") tdClass = "awards-cell";
            // Make cells clickable (except awards which use badge-level clicks)
            var hasVal = val != null && val !== "" && (!Array.isArray(val) || val.length > 0);
            if (hasVal && col.key !== "awards") {
              tdClass += " clickable";
              var raw = Array.isArray(val) ? val.join(",") : String(val);
              html += '<td class="' + tdClass + '" data-col="' + col.key + '" data-val="' + escAttr(raw) + '">' + fmtCell(col, val) + "</td>";
            } else {
              html += '<td class="' + tdClass + '">' + fmtCell(col, val) + "</td>";
            }
          }
        });
        html += "</tr>";
      });
      tbody.innerHTML = html;
    }

    // Results count
    var isCombined = total > 0 && filtered[0] && filtered[0]._combined;
    document.getElementById("resultsCount").textContent = isCombined
      ? "Showing " + total.toLocaleString() + " players (combined)"
      : "Showing " + total.toLocaleString() + " results";
  }

  function scrollToTop() {
    document.getElementById("tableWrapper").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // ---- Clear Filters ----
  function clearFiltersQuiet() {
    // Reset all inputs without triggering applyFilters
    var inputs = ["salaryMin", "salaryMax", "capPctMin", "capPctMax",
      "cppMin", "cppMax", "cpgMin", "cpgMax", "earningsMin", "earningsMax",
      "playerSearch", "ageMin", "ageMax", "expMin", "expMax",
      "draftMin", "draftMax", "draftYearMin", "draftYearMax",
      "ppgMin", "ppgMax", "rpgMin", "rpgMax", "apgMin", "apgMax",
      "fgPctMin", "fgPctMax", "tpPctMin", "tpPctMax", "ftPctMin", "ftPctMax",
      "gpMin", "gpMax"];
    inputs.forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.value = "";
    });

    _exactLeagueRank = null;
    _exactPos = null;
    _teammateFilter = null;
    _teammateLabel = null;
    _teammateExclude = null;
    _undraftedFilter = false;
    document.getElementById("leagueRank").value = "";
    document.getElementById("teamFilter").value = "";
    document.getElementById("nationality").value = "";
    document.getElementById("collegeFilter").value = "";
    document.getElementById("hasAnyAward").checked = false;

    // Reset season range to default (2025-26)
    var seasons = DATA.seasons_list || [];
    var defaultSeason = "2025-26";
    var asc = seasons.slice().reverse();
    document.getElementById("seasonFrom").value = seasons.indexOf(defaultSeason) >= 0 ? defaultSeason : asc[0] || "";
    document.getElementById("seasonTo").value = seasons.indexOf(defaultSeason) >= 0 ? defaultSeason : seasons[0] || "";

    // Clear chips
    document.querySelectorAll(".filter-chip.active").forEach(function (c) { c.classList.remove("active"); });
    document.querySelectorAll(".season-preset-btn.active").forEach(function (c) { c.classList.remove("active"); });
    document.querySelectorAll(".preset-btn.active").forEach(function (c) { c.classList.remove("active"); });
    activePreset = null;
  }

  function clearFilters() {
    clearFiltersQuiet();
    sortCol = "salary";
    sortDir = "desc";
    applyFilters();
  }

  // ---- URL State ----
  function saveStateToURL() {
    var f = getFilterState();
    var params = {};

    if (f.seasonFrom) params.from = f.seasonFrom;
    if (f.seasonTo) params.to = f.seasonTo;
    if (f.salaryMin != null) params.salary_min = f.salaryMin;
    if (f.salaryMax != null) params.salary_max = f.salaryMax;
    if (f.capPctMin != null) params.cap_min = f.capPctMin;
    if (f.capPctMax != null) params.cap_max = f.capPctMax;
    if (f.leagueRank != null) params.lg_rank = f.leagueRank;
    if (f.cppMin != null) params.cpp_min = f.cppMin;
    if (f.cppMax != null) params.cpp_max = f.cppMax;
    if (f.cpgMin != null) params.cpg_min = f.cpgMin;
    if (f.cpgMax != null) params.cpg_max = f.cpgMax;
    if (f.earningsMin != null) params.earn_min = f.earningsMin;
    if (f.earningsMax != null) params.earn_max = f.earningsMax;
    if (f.playerSearch) params.player = f.playerSearch;
    if (f.positions.length > 0) params.pos = f.positions.join(",");
    if (f.ageMin != null) params.age_min = f.ageMin;
    if (f.ageMax != null) params.age_max = f.ageMax;
    if (f.expMin != null) params.exp_min = f.expMin;
    if (f.expMax != null) params.exp_max = f.expMax;
    if (f.ppgMin != null) params.ppg_min = f.ppgMin;
    if (f.ppgMax != null) params.ppg_max = f.ppgMax;
    if (f.rpgMin != null) params.rpg_min = f.rpgMin;
    if (f.rpgMax != null) params.rpg_max = f.rpgMax;
    if (f.apgMin != null) params.apg_min = f.apgMin;
    if (f.apgMax != null) params.apg_max = f.apgMax;
    if (f.team) params.team = f.team;
    if (f.college) params.college = f.college;
    if (f.awards.length > 0) params.awards = f.awards.join(",");
    if (f.hasAnyAward) params.has_award = "1";
    if (sortCol !== "salary") params.sort = sortCol;
    if (sortDir !== "desc") params.dir = sortDir;

    var hash = Object.keys(params).map(function (k) {
      return encodeURIComponent(k) + "=" + encodeURIComponent(params[k]);
    }).join("&");

    if (hash) {
      history.replaceState(null, "", "#" + hash);
    } else {
      history.replaceState(null, "", window.location.pathname);
    }
  }

  function loadStateFromURL() {
    var hash = window.location.hash.substring(1);
    if (!hash) return;

    var params = {};
    hash.split("&").forEach(function (pair) {
      var parts = pair.split("=");
      if (parts.length === 2) {
        params[decodeURIComponent(parts[0])] = decodeURIComponent(parts[1]);
      }
    });

    if (params.from) document.getElementById("seasonFrom").value = params.from;
    if (params.to) document.getElementById("seasonTo").value = params.to;
    if (params.salary_min) document.getElementById("salaryMin").value = params.salary_min;
    if (params.salary_max) document.getElementById("salaryMax").value = params.salary_max;
    if (params.cap_min) document.getElementById("capPctMin").value = params.cap_min;
    if (params.cap_max) document.getElementById("capPctMax").value = params.cap_max;
    if (params.lg_rank) document.getElementById("leagueRank").value = params.lg_rank;
    if (params.cpp_min) document.getElementById("cppMin").value = params.cpp_min;
    if (params.cpp_max) document.getElementById("cppMax").value = params.cpp_max;
    if (params.cpg_min) document.getElementById("cpgMin").value = params.cpg_min;
    if (params.cpg_max) document.getElementById("cpgMax").value = params.cpg_max;
    if (params.earn_min) document.getElementById("earningsMin").value = params.earn_min;
    if (params.earn_max) document.getElementById("earningsMax").value = params.earn_max;
    if (params.player) document.getElementById("playerSearch").value = params.player;
    if (params.age_min) document.getElementById("ageMin").value = params.age_min;
    if (params.age_max) document.getElementById("ageMax").value = params.age_max;
    if (params.exp_min) document.getElementById("expMin").value = params.exp_min;
    if (params.exp_max) document.getElementById("expMax").value = params.exp_max;
    if (params.ppg_min) document.getElementById("ppgMin").value = params.ppg_min;
    if (params.ppg_max) document.getElementById("ppgMax").value = params.ppg_max;
    if (params.rpg_min) document.getElementById("rpgMin").value = params.rpg_min;
    if (params.rpg_max) document.getElementById("rpgMax").value = params.rpg_max;
    if (params.apg_min) document.getElementById("apgMin").value = params.apg_min;
    if (params.apg_max) document.getElementById("apgMax").value = params.apg_max;
    if (params.team) document.getElementById("teamFilter").value = params.team;
    if (params.college) document.getElementById("collegeFilter").value = params.college;
    if (params.has_award === "1") document.getElementById("hasAnyAward").checked = true;

    if (params.pos) {
      params.pos.split(",").forEach(function (p) {
        document.querySelectorAll("#positionFilter .filter-chip").forEach(function (c) {
          if (c.dataset.value === p) c.classList.add("active");
        });
      });
    }

    if (params.awards) {
      params.awards.split(",").forEach(function (a) {
        document.querySelectorAll("#awardsFilter .filter-chip").forEach(function (c) {
          if (c.dataset.value === a) c.classList.add("active");
        });
      });
    }

    if (params.sort) sortCol = params.sort;
    if (params.dir) sortDir = params.dir;
  }


  // ---- Share URL ----
  function shareURL() {
    var url = window.location.href;
    if (navigator.clipboard) {
      navigator.clipboard.writeText(url).then(function () {
        var btn = document.getElementById("shareBtn");
        btn.textContent = "Copied!";
        setTimeout(function () { btn.textContent = "Share"; }, 2000);
      });
    }
  }

  // ---- Start ----
  loadData();
})();
