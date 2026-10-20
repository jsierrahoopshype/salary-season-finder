/**
 * The player season table fits its page: no sideways scroll on the table or
 * the page, at desktop, laptop, tablet and phone widths.
 *
 * Opens the built player pages in Chromium (Playwright) and, at each width,
 * fails when
 *   - the page scrolls sideways (document wider than the viewport),
 *   - the table is wider than its frame, or any cell spills past it,
 *   - anything between the table and the page is a sideways scroller,
 *   - the wrong columns show (12 from 700px up, 6 below),
 *   - award badges show below 768px, or the CONTRACTED tag is back,
 *   - below 700px: Salary and Career $ not in millions ($26.2M), or any
 *     text in the table under 13px; from 700px: not the full figures.
 *
 * Pages: Jaden McDaniels, plus the pages most likely to be widest, picked
 * from every built player page by the longest team cell, the most awards in
 * one season, the longest award label and the longest career figure.
 *
 * Usage: node scripts/tests/season_table_fit.js [slug ...]
 * Needs the playwright package and a Chromium it can launch.
 */
'use strict';
const fs = require('fs');
const path = require('path');
const { pathToFileURL } = require('url');
const { chromium } = require('playwright');

const REPO = path.resolve(__dirname, '..', '..');
const WIDTHS = (process.env.FIT_WIDTHS || "1280,1060,768,390").split(",").map(Number);
const DESKTOP_COLUMNS = ['Season', 'Team', 'Salary', '% of cap', 'Lg rank',
  'Tm rank', 'Career $', 'Age', 'GP', 'PPG', 'RPG', 'APG'];
const PHONE_COLUMNS = ['Season', 'Team', 'Salary', '% of cap', 'Lg rank',
  'Career $'];
const PER_METRIC = 6;
const PHONE_MIN_FONT_PX = 13;

function text(html) {
  return html.replace(/<[^>]+>/g, '').replace(/&[a-z#0-9]+;/g, 'x');
}

// score every built player page on what widens the table, keep the top few
function widestPages() {
  const dir = path.join(REPO, 'player');
  const scores = { team: [], awards: [], label: [], career: [] };
  for (const slug of fs.readdirSync(dir)) {
    const file = path.join(dir, slug, 'index.html');
    if (!fs.existsSync(file)) continue;
    const html = fs.readFileSync(file, 'utf8');
    const start = html.indexOf('<table class="player-season-table">');
    if (start < 0) continue;
    const table = html.slice(start, html.indexOf('</table>', start));
    let team = 0, awards = 0, label = 0, career = 0;
    for (const row of table.split('<tr').slice(2)) {
      const cells = row.match(/<t[dh] class="[^"]*">[\s\S]*?<\/t[dh]>/g) || [];
      const teamCell = cells.find((c) => c.startsWith('<td class="ps-team"'));
      if (teamCell) team = Math.max(team, text(teamCell).length);
      const careerCell = cells.find((c) => c.includes('ps-career'));
      if (careerCell) career = Math.max(career, text(careerCell).length);
      const badges = row.match(/class="award-badge[^"]*"[^>]*>[\s\S]*?<\/(a|span)>/g) || [];
      awards = Math.max(awards, badges.length);
      for (const b of badges) label = Math.max(label, text(b.replace(/^[^>]*>/, '')).length);
    }
    scores.team.push([team, slug]);
    scores.awards.push([awards, slug]);
    scores.label.push([label, slug]);
    scores.career.push([career, slug]);
  }
  const picked = new Set(['jaden-mcdaniels']);
  for (const list of Object.values(scores)) {
    list.sort((a, b) => b[0] - a[0] || (a[1] < b[1] ? -1 : 1));
    list.slice(0, PER_METRIC).forEach(([, slug]) => picked.add(slug));
  }
  return [...picked];
}

// runs in the page
function measure() {
  const out = { problems: [] };
  const doc = document.documentElement;
  out.pageScroll = doc.scrollWidth - doc.clientWidth;
  if (out.pageScroll > 0) out.problems.push('page scrolls sideways by ' + out.pageScroll + 'px');

  const table = document.querySelector('table.player-season-table');
  if (!table) { out.problems.push('no season table'); return out; }
  const frame = table.parentElement;
  const fr = frame.getBoundingClientRect();
  const inner = fr.left + frame.clientLeft + frame.clientWidth;
  const tr = table.getBoundingClientRect();
  out.frameWidth = Math.round(frame.clientWidth);
  out.tableWidth = Math.round(tr.width);
  if (tr.right > inner + 0.5) {
    out.problems.push('table ' + Math.round(tr.width) + 'px is wider than its frame ' + frame.clientWidth + 'px');
  }
  if (frame.scrollWidth > frame.clientWidth) {
    out.problems.push('frame content overflows by ' + (frame.scrollWidth - frame.clientWidth) + 'px');
  }
  table.querySelectorAll('th, td').forEach((cell) => {
    const r = cell.getBoundingClientRect();
    if (r.width && r.right > inner + 0.5) {
      out.problems.push('cell "' + cell.textContent.trim().slice(0, 30) + '" spills past the frame');
    }
  });
  for (let el = table; el && el !== document.body; el = el.parentElement) {
    const ox = getComputedStyle(el).overflowX;
    if (ox === 'auto' || ox === 'scroll') {
      out.problems.push('<' + el.tagName.toLowerCase() + ' class="' + el.className + '"> is a sideways scroller (overflow-x: ' + ox + ')');
    }
  }
  out.columns = [...table.querySelectorAll('thead th')]
    .filter((th) => getComputedStyle(th).display !== 'none')
    .map((th) => th.textContent.trim());
  out.badgesShown = [...table.querySelectorAll('.ps-season-awards')]
    .some((el) => getComputedStyle(el).display !== 'none' && el.getBoundingClientRect().height > 0);
  out.badgeCount = table.querySelectorAll('.ps-season-awards .award-badge').length;
  out.contractedTag = /contracted/i.test(table.innerText);
  // what a money cell shows, and the smallest text anywhere in the table
  const shown = (sel) => [...table.querySelectorAll(sel)]
    .filter((el) => el.getClientRects().length > 0).map((el) => el.textContent);
  out.moneyShort = shown('td.ps-salary .ps-money-short, td.ps-career .ps-money-short');
  out.moneyFull = shown('td.ps-salary .ps-money-full, td.ps-career .ps-money-full');
  out.minFontPx = Infinity;
  const walker = document.createTreeWalker(table, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const el = node.parentElement;
    if (!node.textContent.trim() || el.getClientRects().length === 0) continue;
    out.minFontPx = Math.min(out.minFontPx, parseFloat(getComputedStyle(el).fontSize));
  }
  return out;
}

async function main() {
  const slugs = process.argv.slice(2).length ? process.argv.slice(2) : widestPages();
  const launch = {};
  if (process.env.CHROMIUM_PATH) launch.executablePath = process.env.CHROMIUM_PATH;
  const browser = await chromium.launch(launch);
  const failures = [];
  try {
    for (const width of WIDTHS) {
      const page = await browser.newPage({ viewport: { width, height: 900 } });
      for (const slug of slugs) {
        const url = pathToFileURL(path.join(REPO, 'player', slug, 'index.html')).href;
        await page.goto(url, { waitUntil: 'load' });
        await page.evaluate(() => document.fonts.ready);
        const m = await page.evaluate(measure);
        const want = width < 700 ? PHONE_COLUMNS : DESKTOP_COLUMNS;
        if (JSON.stringify(m.columns) !== JSON.stringify(want)) {
          m.problems.push('columns ' + JSON.stringify(m.columns) + ', want ' + JSON.stringify(want));
        }
        if (width < 768 && m.badgesShown) m.problems.push('award badges show below 768px');
        if (width >= 768 && m.badgeCount && !m.badgesShown) m.problems.push('award badges hidden at ' + width + 'px');
        if (m.contractedTag) m.problems.push('the CONTRACTED tag is still in the table');
        if (width < 700) {
          if (m.moneyFull.length) m.problems.push('full money figures show below 700px: ' + m.moneyFull[0]);
          const bad = m.moneyShort.filter((t) => !/^-?\$\d+\.\d{1,2}M$/.test(t));
          if (!m.moneyShort.length || bad.length) m.problems.push('money not in millions below 700px: ' + (bad[0] || 'none shown'));
          if (m.minFontPx < PHONE_MIN_FONT_PX) m.problems.push('table text at ' + m.minFontPx + 'px, under ' + PHONE_MIN_FONT_PX + 'px');
        } else {
          if (m.moneyShort.length) m.problems.push('short money shows at ' + width + 'px: ' + m.moneyShort[0]);
          if (!m.moneyFull.length) m.problems.push('no full money figures at ' + width + 'px');
        }
        const line = width + 'px ' + slug + ': table ' + m.tableWidth + '/' + m.frameWidth + 'px, text >= ' + m.minFontPx + 'px';
        if (m.problems.length) {
          failures.push(line + '\n    ' + m.problems.join('\n    '));
          console.log('FAIL ' + line);
        } else {
          console.log('ok   ' + line);
        }
      }
      await page.close();
    }
  } finally {
    await browser.close();
  }
  if (failures.length) {
    console.error('\n' + failures.length + ' failure(s):\n' + failures.join('\n'));
    process.exit(1);
  }
  console.log('\n' + slugs.length + ' pages x ' + WIDTHS.length + ' widths: no sideways scroll');
}

main().catch((err) => { console.error(err); process.exit(1); });
