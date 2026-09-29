/**
 * Render player season tables with the tool's own component.
 *
 * js/app.js exposes window.HoopsMaticPlayerSeasonTable for this: the
 * pre-rendered pages have to carry the markup the app produces, not a second
 * copy of it that drifts. app.js is a browser IIFE, so it is evaluated here
 * against a stub window. Nothing in it touches the DOM until loadData's XHR
 * calls back, and the stub XHR never does, so the file loads, defines the
 * component and stops.
 *
 * stdin  {"currentSeason": "2026-27", "players": [{"name": ..., "records": [...]}]}
 * stdout {"<name>": "<html>"}
 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const REPO = path.resolve(__dirname, '..', '..');

function stubWindow() {
  const noop = () => {};
  const element = {
    style: {}, classList: { add: noop, remove: noop, toggle: noop, contains: () => false },
    addEventListener: noop, removeEventListener: noop, appendChild: noop,
    setAttribute: noop, removeAttribute: noop, getAttribute: () => null,
    querySelectorAll: () => [], querySelector: () => null, focus: noop,
    innerHTML: '', textContent: '', value: '', dataset: {}, children: [],
    offsetHeight: 0, offsetParent: null,
  };
  const document = {
    getElementById: () => element,
    querySelector: () => element,
    querySelectorAll: () => [],
    createElement: () => Object.assign({}, element),
    addEventListener: noop,
    documentElement: Object.assign({}, element),
    body: Object.assign({}, element),
  };
  const win = {
    document,
    location: { search: '', href: 'https://hoopsmatic.com/salary-season-finder', hash: '' },
    history: { replaceState: noop },
    navigator: { clipboard: { writeText: () => Promise.resolve() } },
    addEventListener: noop,
    matchMedia: () => ({ matches: false, addEventListener: noop, addListener: noop }),
    setTimeout: noop, clearTimeout: noop, requestAnimationFrame: noop,
    // the component is all this harness wants; the data load must not run
    XMLHttpRequest: function () {
      return { open: noop, send: noop, onload: null, onerror: null };
    },
  };
  win.window = win;
  return win;
}

function main() {
  const input = JSON.parse(fs.readFileSync(0, 'utf8'));
  const sandbox = stubWindow();
  vm.createContext(sandbox);
  const source = fs.readFileSync(path.join(REPO, 'js', 'app.js'), 'utf8');
  vm.runInContext(source, sandbox, { filename: 'js/app.js' });

  const component = sandbox.HoopsMaticPlayerSeasonTable;
  if (!component || typeof component.build !== 'function') {
    throw new Error('js/app.js did not export HoopsMaticPlayerSeasonTable.build');
  }

  const out = {};
  for (const player of input.players) {
    out[player.name] = component.build(player.records, {
      currentSeason: input.currentSeason,
      // the heading and the clicks belong to the page, not the component
      clickable: false,
    });
  }
  process.stdout.write(JSON.stringify(out));
}

main();
