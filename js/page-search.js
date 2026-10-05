/* The compact player search at the top of a pre-rendered page.
 *
 * One input, name suggestions, and a link to the player's own page. The index
 * is data/slugs.json, the same file the pages were built from, so a suggestion
 * and a file on disk can never disagree. It is 130 KB and almost nobody who
 * lands on one of these pages will type in the box, so it is fetched on the
 * first focus rather than on load: until then this costs a listener.
 *
 * Scoring is the tool's own, in the tool's order: an exact name, then a name
 * that starts with what was typed, then a word inside it that does, then
 * anywhere at all. Wired as the ARIA combobox/listbox pair the tool uses.
 */
(function () {
  "use strict";

  var MIN_CHARS = 2;
  var MAX_ROWS = 8;

  var root = document.querySelector(".hm-find");
  if (!root) return;
  var input = root.querySelector("input");
  var list = root.querySelector(".hm-find-list");
  if (!input || !list) return;

  var INDEX = null;        // [{name, lower, slug}], once loaded
  var loading = false;
  var active = -1;
  var shown = [];

  function base() {
    // the page knows how deep it sits; data/ hangs off the tool root
    var up = root.getAttribute("data-root") || "";
    return up + "data/slugs.json";
  }

  function load() {
    if (INDEX || loading) return;
    loading = true;
    fetch(base()).then(function (r) {
      return r.ok ? r.json() : null;
    }).then(function (book) {
      var players = (book && book.slugs && book.slugs.player) || {};
      INDEX = Object.keys(players).map(function (name) {
        return { name: name, lower: name.toLowerCase(), slug: players[name] };
      });
      loading = false;
      if (input.value) render(input.value);
    }).catch(function () {
      loading = false;
      INDEX = [];
    });
  }

  function score(entry, query) {
    var i = entry.lower.indexOf(query);
    if (i < 0) return -1;
    if (entry.lower === query) return 0;
    if (i === 0) return 1;
    var before = entry.lower.charAt(i - 1);
    if (before === " " || before === "-" || before === ".") return 2;
    return 3;
  }

  function matches(query) {
    query = (query || "").toLowerCase().trim();
    if (query.length < MIN_CHARS || !INDEX) return [];
    var hits = [];
    for (var i = 0; i < INDEX.length; i++) {
      var s = score(INDEX[i], query);
      if (s >= 0) hits.push({ entry: INDEX[i], score: s });
    }
    hits.sort(function (a, b) {
      if (a.score !== b.score) return a.score - b.score;
      return a.entry.name.localeCompare(b.entry.name);
    });
    return hits.slice(0, MAX_ROWS).map(function (h) { return h.entry; });
  }

  function close() {
    list.hidden = true;
    list.innerHTML = "";
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
    active = -1;
    shown = [];
  }

  function href(entry) {
    var up = root.getAttribute("data-root") || "";
    return up + "player/" + entry.slug + "/";
  }

  function render(query) {
    shown = matches(query);
    if (!shown.length) {
      close();
      return;
    }
    list.innerHTML = shown.map(function (entry, i) {
      return '<li role="option" id="hm-find-' + i + '" aria-selected="false">' +
        '<a href="' + href(entry) + '">' + escape_(entry.name) + "</a></li>";
    }).join("");
    list.hidden = false;
    input.setAttribute("aria-expanded", "true");
    active = -1;
  }

  function escape_(text) {
    return text.replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function highlight(next) {
    var rows = list.querySelectorAll('[role="option"]');
    if (!rows.length) return;
    if (active >= 0) rows[active].setAttribute("aria-selected", "false");
    active = (next + rows.length) % rows.length;
    rows[active].setAttribute("aria-selected", "true");
    input.setAttribute("aria-activedescendant", rows[active].id);
  }

  input.addEventListener("focus", load);
  input.addEventListener("input", function () { render(input.value); });
  input.addEventListener("keydown", function (event) {
    if (event.key === "ArrowDown") { event.preventDefault(); highlight(active + 1); }
    else if (event.key === "ArrowUp") { event.preventDefault(); highlight(active - 1); }
    else if (event.key === "Enter") {
      // Enter on a highlighted row follows it; Enter with nothing highlighted
      // follows the first, which is what a reader who typed a full name means.
      var rows = list.querySelectorAll('[role="option"] a');
      var pick = rows[active >= 0 ? active : 0];
      if (pick) { event.preventDefault(); window.location.href = pick.href; }
    } else if (event.key === "Escape") { close(); }
  });
  document.addEventListener("click", function (event) {
    if (!root.contains(event.target)) close();
  });
})();
