/* Ask js/app.js what hash a piece of tool state deserves.
 *
 * The file is a browser IIFE, so it gets the smallest window and document it
 * will accept, then the question goes to the one function it exposes for the
 * purpose. Reads a JSON array of calls on stdin, writes the answers on stdout.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const APP = path.join(__dirname, "..", "..", "js", "app.js");

function element() {
  return new Proxy({}, {
    get(target, prop) {
      if (prop === "value") return "";
      if (prop === "checked") return false;
      if (prop === "dataset" || prop === "style") return {};
      if (prop === "classList") {
        return { add() {}, remove() {}, toggle() {}, contains() { return false; } };
      }
      if (prop === "children" || prop === "childNodes") return [];
      if (typeof prop === "string") return () => element();
      return undefined;
    },
  });
}

const sandbox = {
  window: {},
  console,
  setTimeout,
  clearTimeout,
  navigator: {},
  location: { search: "", hash: "", pathname: "/salary-season-finder" },
  history: { replaceState() {} },
  XMLHttpRequest: function () { this.open = () => {}; this.send = () => {}; },
  document: {
    addEventListener() {},
    getElementById() { return element(); },
    querySelector() { return element(); },
    querySelectorAll() { return []; },
    createElement() { return element(); },
    documentElement: { style: { setProperty() {} } },
    body: element(),
  },
};
sandbox.window.location = sandbox.location;
sandbox.window.history = sandbox.history;
sandbox.window.addEventListener = () => {};
sandbox.globalThis = sandbox;

vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(APP, "utf8"), sandbox, { filename: "app.js" });

const api = sandbox.window.HoopsMaticToolState;
if (!api || typeof api.hashFor !== "function") {
  console.error("js/app.js does not expose HoopsMaticToolState.hashFor");
  process.exit(2);
}

let input = "";
process.stdin.on("data", chunk => { input += chunk; });
process.stdin.on("end", () => {
  const calls = JSON.parse(input);
  const out = calls.map(c =>
    api.hashFor(c.filters, c.defaultSeason, c.sort, c.dir, c.exactPlayer || null)
  );
  process.stdout.write(JSON.stringify(out));
});
