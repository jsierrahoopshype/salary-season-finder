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
  let _exactTeamRank = null;    // set by cell click for exact rank match
  let _exactLeagueRank = null;  // set by cell click for exact rank match
  let _exactPos = null;          // set by cell click for exact position match
  let _breadcrumb = null;         // single {col, label, value} for display (clean-slate)

  // Column definitions
  const COLUMNS = [
    { key: "rank",              label: "#",           type: "rank",   default: true,  sortable: false },
    { key: "player",            label: "Player",      type: "text",   default: true,  sortable: true  },
    { key: "season",            label: "Season",      type: "text",   default: true,  sortable: true  },
    { key: "team",              label: "Team",        type: "text",   default: true,  sortable: true  },
    { key: "age",               label: "Age",         type: "num",    default: true,  sortable: true  },
    { key: "salary",            label: "Salary",      type: "salary", default: true,  sortable: true  },
    { key: "salary_cap_pct",    label: "Cap%",        type: "pct",    default: true,  sortable: true  },
    { key: "salary_rank_team",  label: "Tm Rank",     type: "num",    default: true,  sortable: true  },
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

    populateFilters();
    buildColumnToggles();
    bindEvents();
    loadStateFromURL();
    applyFilters();

    // Update subtitle
    if (DATA.meta) {
      document.getElementById("headerSubtitle").textContent =
        DATA.meta.total_records.toLocaleString() + " player-seasons | " + DATA.meta.season_range;
    }

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

    // Export CSV
    document.getElementById("exportCsvBtn").addEventListener("click", exportCSV);

    // Share
    document.getElementById("shareBtn").addEventListener("click", shareURL);

    // Preset buttons
    document.querySelectorAll(".preset-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var wasActive = btn.classList.contains("active");
        document.querySelectorAll(".preset-btn").forEach(function (b) { b.classList.remove("active"); });
        if (wasActive) {
          activePreset = null;
          clearFilters();
        } else {
          activePreset = btn.dataset.preset;
          btn.classList.add("active");
          applyPreset(activePreset);
        }
      });
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
    ["seasonFrom", "seasonTo", "teamRank", "leagueRank", "teamFilter", "nationality"].forEach(function (id) {
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
  function applyPreset(preset) {
    clearFiltersQuiet();
    switch (preset) {
      case "overpaid":
        document.getElementById("salaryMin").value = "30000000";
        document.getElementById("seasonFrom").value = "2015-16";
        sortCol = "salary";
        sortDir = "desc";
        break;
      case "value":
        document.getElementById("gpMin").value = "50";
        sortCol = "cost_per_point";
        sortDir = "asc";
        visibleCols["cost_per_point"] = true;
        buildColumnToggles();
        break;
      case "max-allstar":
        document.getElementById("capPctMin").value = "25";
        document.querySelectorAll("#awardsFilter .filter-chip").forEach(function (c) {
          if (c.dataset.value === "All-Star") c.classList.add("active");
        });
        sortCol = "salary";
        sortDir = "desc";
        break;
      case "underpaid":
        document.getElementById("ppgMin").value = "20";
        document.getElementById("capPctMax").value = "15";
        sortCol = "salary_cap_pct";
        sortDir = "asc";
        break;
      case "40m-club":
        document.getElementById("salaryMin").value = "40000000";
        sortCol = "salary";
        sortDir = "desc";
        break;
      case "rookie-deals":
        document.getElementById("expMax").value = "4";
        document.getElementById("salaryMax").value = "10000000";
        sortCol = "ppg";
        sortDir = "desc";
        break;
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
      teamRank: parseNum(document.getElementById("teamRank").value),
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

    // Salary
    if (f.salaryMin != null && (record.salary == null || record.salary < f.salaryMin)) return false;
    if (f.salaryMax != null && (record.salary == null || record.salary > f.salaryMax)) return false;

    // Cap %
    if (f.capPctMin != null && (record.salary_cap_pct == null || record.salary_cap_pct < f.capPctMin)) return false;
    if (f.capPctMax != null && (record.salary_cap_pct == null || record.salary_cap_pct > f.capPctMax)) return false;

    // Team rank (top N from select, or exact from cell click)
    if (_exactTeamRank != null && record.salary_rank_team !== _exactTeamRank) return false;
    if (_exactTeamRank == null && f.teamRank != null && (record.salary_rank_team == null || record.salary_rank_team > f.teamRank)) return false;

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

    // Draft year
    if (f.draftYearMin != null && (record.draft_year == null || record.draft_year < f.draftYearMin)) return false;
    if (f.draftYearMax != null && (record.draft_year == null || record.draft_year > f.draftYearMax)) return false;

    // Nationality
    if (f.nationality && record.nationality !== f.nationality) return false;

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
      var wPPG = 0, wRPG = 0, wAPG = 0, wSPG = 0, wBPG = 0;
      var wFG = 0, wTP = 0, wFT = 0;
      var capSum = 0, capCount = 0;
      var awardsSet = {};
      var allAwards = [];

      recs.forEach(function (r) {
        if (r.salary) totalSalary += r.salary;
        var gp = r.gp || 0;
        totalGP += gp;
        if (gp > 0) {
          if (r.ppg != null) wPPG += r.ppg * gp;
          if (r.rpg != null) wRPG += r.rpg * gp;
          if (r.apg != null) wAPG += r.apg * gp;
          if (r.spg != null) wSPG += r.spg * gp;
          if (r.bpg != null) wBPG += r.bpg * gp;
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

      var totalPoints = wPPG; // wPPG = sum(ppg * gp) = total points

      combined.push({
        player: latest.player,
        season: recs.length === 1 ? latest.season : earliest.season + " to " + latest.season,
        _seasonYear: seasonYear(latest.season),
        _numSeasons: recs.length,
        _combined: true,
        team: latest.team,
        age: latest.age,
        salary: totalSalary || null,
        salary_cap_pct: capCount > 0 ? Math.round(capSum / capCount * 10) / 10 : null,
        salary_rank_team: null,
        salary_rank_league: null,
        years_exp: latest.years_exp,
        gp: totalGP || null,
        ppg: totalGP > 0 ? Math.round(wPPG / totalGP * 10) / 10 : null,
        rpg: totalGP > 0 ? Math.round(wRPG / totalGP * 10) / 10 : null,
        apg: totalGP > 0 ? Math.round(wAPG / totalGP * 10) / 10 : null,
        spg: totalGP > 0 ? Math.round(wSPG / totalGP * 10) / 10 : null,
        bpg: totalGP > 0 ? Math.round(wBPG / totalGP * 10) / 10 : null,
        fg_pct: totalGP > 0 ? Math.round(wFG / totalGP * 1000) / 1000 : null,
        tp_pct: totalGP > 0 ? Math.round(wTP / totalGP * 1000) / 1000 : null,
        ft_pct: totalGP > 0 ? Math.round(wFT / totalGP * 1000) / 1000 : null,
        cost_per_point: totalPoints > 0 ? Math.round(totalSalary / totalPoints) : null,
        cost_per_game: totalGP > 0 ? Math.round(totalSalary / totalGP) : null,
        career_earnings: latest.career_earnings,
        awards: allAwards,
        pos: latest.pos,
        nationality: latest.nationality,
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
    if (isMultiSeason && combineEl && combineEl.checked) {
      filtered = combineByPlayer(filtered);
    }

    // Sort
    sortData();

    // Update summary
    updateSummary();

    // Update URL state
    saveStateToURL();

    // Render
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

  // ---- Breadcrumb Rendering ----
  function renderBreadcrumb() {
    var bar = document.getElementById("breadcrumbBar");
    var trail = document.getElementById("breadcrumbTrail");
    if (!_breadcrumb) {
      bar.style.display = "none";
      return;
    }
    bar.style.display = "";
    trail.innerHTML = "";
    var tag = document.createElement("span");
    tag.className = "breadcrumb-tag";
    tag.innerHTML = '<span class="bc-col">' + escHtml(_breadcrumb.label) + " =</span> " + escHtml(_breadcrumb.value);
    trail.appendChild(tag);
  }

  function breadcrumbHome() {
    _breadcrumb = null;
    renderBreadcrumb();
    clearFilters();
  }

  // ---- Clickable Cell Filter ----
  function handleCellClick(colKey, rawValue) {
    if (!rawValue || rawValue === "-") return;

    // Build breadcrumb label
    var colDef = COLUMNS.filter(function (c) { return c.key === colKey; })[0];
    var bcLabel = colDef ? colDef.label : colKey;
    var bcValue = rawValue;
    if (colKey === "salary" || colKey === "cost_per_point" || colKey === "cost_per_game" || colKey === "career_earnings") {
      bcValue = fmtSalary(parseFloat(rawValue));
    }
    _breadcrumb = { col: colKey, label: bcLabel, value: bcValue };

    // Clean slate: clear ALL filters, open all seasons
    clearFiltersQuiet();
    var seasons = DATA.seasons_list || [];
    var asc = seasons.slice().reverse();
    document.getElementById("seasonFrom").value = asc[0] || "";
    document.getElementById("seasonTo").value = seasons[0] || "";

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
      case "salary_rank_team":
        _exactTeamRank = parseInt(rawValue, 10);
        break;
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

    renderBreadcrumb();
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

    _exactTeamRank = null;
    _exactLeagueRank = null;
    _exactPos = null;
    document.getElementById("teamRank").value = "";
    document.getElementById("leagueRank").value = "";
    document.getElementById("teamFilter").value = "";
    document.getElementById("nationality").value = "";
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
    _breadcrumb = null;
    renderBreadcrumb();
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
    if (f.teamRank != null) params.tm_rank = f.teamRank;
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
    if (params.tm_rank) document.getElementById("teamRank").value = params.tm_rank;
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

  // ---- Export CSV ----
  function exportCSV() {
    if (filtered.length === 0) return;

    var activeCols = COLUMNS.filter(function (c) {
      return c.key !== "rank" && visibleCols[c.key];
    });

    var headers = activeCols.map(function (c) { return c.label; });
    var rows = [headers.join(",")];

    filtered.forEach(function (r) {
      var row = activeCols.map(function (c) {
        var val = r[c.key];
        if (val == null) return "";
        if (Array.isArray(val)) return '"' + val.join("; ") + '"';
        if (typeof val === "string" && (val.indexOf(",") >= 0 || val.indexOf('"') >= 0)) {
          return '"' + val.replace(/"/g, '""') + '"';
        }
        return val;
      });
      rows.push(row.join(","));
    });

    var csv = rows.join("\n");
    var blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url;
    a.download = "salary_season_finder_" + new Date().toISOString().split("T")[0] + ".csv";
    a.click();
    URL.revokeObjectURL(url);
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
