// Measures horizontal overflow of the recorded-mode site in a real browser. Run by hand; it is
// not part of CI because it needs a downloaded browser:
//
//   cd web && npm ci && npm run prepare-data && npx playwright install chromium
//   node scripts/check-layout.mjs [--url http://localhost:5173/]
//
// Without --url it starts the Vite dev server on a free local port and stops it afterwards.
// For each width and route it loads the page, waits for the content, and compares
// document.documentElement.scrollWidth with the viewport width, looks for containers that scroll
// sideways on their own, and checks that a case page does not scroll itself (its panes do), that
// the tour bar is pinned inside the viewport, that a false-approval pill sits whole inside the
// criteria pane, and that the first highlight is centered in the chart panel (when the chart is
// on screen: under 1024 px it is behind the Evidence tab). It also checks the top bar itself:
// its content must not be wider than the bar, it must keep one line, and the Repository link must be
// in view on every route (a case page has no footer). A fourth nav item, "Live", is added to the
// bar before measuring when the page has none, to prove the room for it. Exit code 1 if any check fails.
import { readFileSync } from "node:fs";
import { chromium } from "playwright";
import { createServer } from "vite";

const WIDTHS = [320, 360, 390, 768, 1280];
const HEIGHT = 900;
const dataDir = new URL("../public/data/", import.meta.url);

function readCase(id) {
  return JSON.parse(readFileSync(new URL(`cases/${id}.json`, dataDir), "utf8"));
}

const unscored = readCase("pap-01");
const unscoredCriterion = unscored.criteria.find((c) => c.judged)?.criterion;
if (!unscoredCriterion) throw new Error("pap-01 has no judged criterion in public/data");

// `wait` is a selector that is present once the route has rendered what is being measured;
// `scrollChunk` names the chunk whose first mark the page should have scrolled to.
// `workspace` marks a case page: the page itself must not scroll and the tour bar, when there is
// one, must sit inside the viewport.
const ROUTES = [
  { name: "landing", hash: "", wait: '[data-testid="tile-value"]' },
  { name: "cases", hash: "#/cases", wait: '[data-testid="cases-table"]' },
  {
    name: "scored case, criterion selected",
    hash: "#/cases/facet-03?c=chronic-axial-pain-functional-deficit",
    wait: '[data-testid="verdict-panel"]',
    workspace: true,
  },
  {
    name: "false approval open (tour step 3)",
    hash: "#/cases/facet-11?c=conservative-care-failed&tour=3",
    wait: '[data-testid="verdict-panel"]',
    workspace: true,
  },
  {
    name: "unseen resource (tour link)",
    hash: "#/cases/facet-11?c=conservative-care-failed&tour=3&res=DocumentReference%2Fnote-pt-records",
    wait: '[data-chunk-id="DocumentReference/note-pt-records#0"] mark',
    scrollChunk: "DocumentReference/note-pt-records#0",
    workspace: true,
  },
  {
    name: "rule-decided verdict",
    hash: "#/cases/facet-02?c=two-mbbs-80pct-sustained-relief",
    wait: '[data-testid="computed-fact"]',
    workspace: true,
  },
  {
    name: "invalidated verdict",
    hash: "#/cases/facet-01?c=no-non-facet-pathology",
    wait: '[data-testid="invalidated"]',
    workspace: true,
  },
  {
    name: "case, nothing selected",
    hash: "#/cases/facet-11",
    wait: '[data-testid="summary-bar"]',
    workspace: true,
  },
  {
    name: "unscored case",
    hash: `#/cases/pap-01?c=${unscoredCriterion}`,
    wait: '[data-testid="label-panel"]',
    workspace: true,
  },
  { name: "policies", hash: "#/policies", wait: "h1" },
  { name: "policy", hash: "#/policies/facet-joint-interventions", wait: "blockquote" },
  { name: "about", hash: "#/about", wait: '[data-testid="gates"]' },
];

// What to run in the page: the widths, and which elements stick out past the viewport edge.
function measure({ scrollChunk, workspace }) {
  // The bar must have room for one more short nav item than the recorded site has.
  const nav = document.querySelector('header nav[aria-label="Main"]');
  if (nav && !nav.querySelector('a[href="#/live"]')) {
    const extra = document.createElement("a");
    extra.href = "#/live";
    extra.textContent = "Live";
    extra.className = nav.firstElementChild?.className ?? "";
    nav.appendChild(extra);
  }
  const bar = document.querySelector("header > div");
  let barProblem = null;
  if (!bar) barProblem = "the top bar is missing";
  else {
    if (bar.scrollWidth > bar.clientWidth) barProblem = `the top bar content is ${bar.scrollWidth} px in a bar ${bar.clientWidth} px wide`;
    else if (bar.getBoundingClientRect().height > document.querySelector("header").getBoundingClientRect().height + 1) barProblem = "the top bar wrapped";
    const repo = [...bar.querySelectorAll("a")].find((a) => (a.textContent ?? "").includes("Repository"));
    const r = repo?.getBoundingClientRect();
    if (!barProblem && (!r || r.width < 24 || r.right > document.documentElement.clientWidth + 0.5 || r.left < 0)) {
      barProblem = "the Repository link is missing or outside the viewport";
    }
  }
  const viewport = document.documentElement.clientWidth;
  const scrollWidth = document.documentElement.scrollWidth;
  const label = (el) => {
    const id = el.id ? `#${el.id}` : "";
    const cls = typeof el.className === "string" && el.className ? `.${el.className.trim().split(/\s+/).slice(0, 3).join(".")}` : "";
    return `${el.tagName.toLowerCase()}${id}${cls}`;
  };
  const wide = [];
  for (const el of document.body.querySelectorAll("*")) {
    const r = el.getBoundingClientRect();
    if (r.width > 0 && r.right > viewport + 0.5) {
      wide.push({ el: label(el), right: Math.round(r.right), text: (el.textContent ?? "").trim().slice(0, 40) });
    }
  }
  // A container that scrolls sideways on its own hides text without widening the page.
  const inner = [];
  for (const el of document.body.querySelectorAll("*")) {
    const style = getComputedStyle(el);
    // Ellipsis truncation (the closed-chunk preview) and the visually hidden skip link clip on purpose.
    // A wide table in a focusable scroll region (tabindex) is deliberate: the page itself stays put.
    if (style.textOverflow === "ellipsis" || el.clientWidth <= 1 || el.hasAttribute("tabindex")) continue;
    if (el.scrollWidth > el.clientWidth + 1 && style.overflowX !== "visible") {
      inner.push({ el: label(el), right: el.scrollWidth, text: (el.textContent ?? "").trim().slice(0, 40) });
    }
  }
  // A false-approval pill must sit whole inside the criteria pane, beside some visible criterion text.
  // The row is in the page at every width but only on screen when the criteria pane is.
  let falseApprovalClipped = false;
  for (const row of document.querySelectorAll("[data-false-approval-row]")) {
    const pill = row.querySelector('[data-outcome="false_approval"]');
    const pane = row.closest("section");
    const text = row.querySelector("button");
    if (!pill || !pane || pane.getBoundingClientRect().width === 0) continue;
    const p = pill.getBoundingClientRect();
    const box = pane.getBoundingClientRect();
    if (p.right > box.right + 0.5 || p.left < box.left - 0.5 || (text?.getBoundingClientRect().width ?? 0) < 40) {
      falseApprovalClipped = true;
    }
  }
  // A case page fills the viewport and its panes scroll; the page itself does not. The tour bar is
  // pinned inside the viewport.
  let workspaceProblem = null;
  if (workspace) {
    const doc = document.documentElement;
    if (doc.scrollHeight > doc.clientHeight + 1) workspaceProblem = `the page scrolls (${doc.scrollHeight} px tall in ${doc.clientHeight})`;
    const tour = document.querySelector('nav[aria-label="Tour"]');
    if (!workspaceProblem && tour && tour.getBoundingClientRect().bottom > doc.clientHeight + 1) {
      workspaceProblem = "the tour bar is below the viewport";
    }
    if (!workspaceProblem && viewport >= 1024) {
      const panes = ["criteria", "evidence", "verdict"].map((id) => document.getElementById(`pane-${id}`)?.getBoundingClientRect());
      if (panes.some((r) => !r || r.width < 200 || r.height < 100)) workspaceProblem = "a pane is missing or too small";
    }
  }
  // The first highlighted mark should sit in the middle of the chart panel, clear of its sticky
  // heading, unless the content is too short to scroll that far.
  let scroll = null;
  const viewer = document.querySelector('[data-testid="chart-viewer"]');
  // The mark the page scrolled to: the active citation, or the first mark of the named chunk.
  const scope = scrollChunk
    ? [...(viewer?.querySelectorAll("[data-chunk-id]") ?? [])].find((el) => el.dataset.chunkId === scrollChunk)
    : viewer;
  const mark = scope?.querySelector('mark[data-active="true"]') ?? scope?.querySelector("mark");
  // Under 1024 px the chart is behind the Evidence tab and has no size to measure.
  if (viewer && viewer.clientHeight > 0 && mark) {
    const box = viewer.getBoundingClientRect();
    const rect = mark.getBoundingClientRect();
    const inset = viewer.querySelector("h3")?.getBoundingClientRect().height ?? 0;
    const visibleTop = box.top + viewer.clientTop + inset;
    const visibleBottom = box.top + viewer.clientTop + viewer.clientHeight;
    const clamped = viewer.scrollTop <= 1 || viewer.scrollTop >= viewer.scrollHeight - viewer.clientHeight - 1;
    scroll = {
      offCenter: Math.round(rect.top + rect.height / 2 - (visibleTop + visibleBottom) / 2),
      clamped,
      under: rect.top < visibleTop - 1 || (rect.height <= visibleBottom - visibleTop && rect.bottom > visibleBottom + 1),
      tall: rect.height > visibleBottom - visibleTop,
      top: Math.round(rect.top - visibleTop),
    };
  }
  return { viewport, scrollWidth, wide: wide.slice(0, 6), inner: inner.slice(0, 6), falseApprovalClipped, workspaceProblem, barProblem, scroll };
}

// A mark that fits the visible area is centered within 12 px (or the content ends first); one taller
// than the visible area starts at least 24 px down. This is the same threshold as targetScrollTop.
function scrollProblem(sc) {
  if (sc.under) return "the first mark is cut off by the panel edge or its sticky heading";
  if (sc.tall) return sc.top >= 24 || sc.clamped ? null : `a tall mark starts ${sc.top} px below the heading, not 24`;
  if (sc.clamped || Math.abs(sc.offCenter) <= 12) return null;
  return `the first mark is ${sc.offCenter} px from the center of the panel`;
}

async function main() {
  const urlArg = process.argv.indexOf("--url");
  let server = null;
  let base;
  if (urlArg !== -1) {
    base = process.argv[urlArg + 1];
    if (!base) throw new Error("--url needs a value");
  } else {
    server = await createServer({
      root: new URL("../", import.meta.url).pathname,
      logLevel: "error",
      server: { host: "127.0.0.1", port: 0, strictPort: false },
    });
    await server.listen();
    base = server.resolvedUrls?.local[0];
    if (!base) throw new Error("the dev server did not report an address");
  }

  const browser = await chromium.launch();
  const failures = [];
  const rows = [];
  try {
    for (const route of ROUTES) {
      const cells = [];
      for (const width of WIDTHS) {
        // Reduced motion makes the chart jump, so the position is final once the page has settled.
        const context = await browser.newContext({ viewport: { width, height: HEIGHT }, reducedMotion: "reduce" });
        const page = await context.newPage();
        try {
          await page.goto(`${base}${route.hash}`);
          await page.waitForSelector("main h1, main h2, main h3", { timeout: 15000 });
          await page.waitForSelector(route.wait, { timeout: 15000 });
          // Let fonts, the smooth scroll and any layout after the first paint settle.
          await page.evaluate(() => document.fonts?.ready);
          await page.waitForTimeout(400);
          const m = await page.evaluate(measure, { scrollChunk: route.scrollChunk ?? null, workspace: route.workspace === true });
          const badScroll = m.scroll !== null && scrollProblem(m.scroll) !== null;
          const over = m.scrollWidth > m.viewport || m.inner.length > 0 || m.falseApprovalClipped || m.workspaceProblem !== null || m.barProblem !== null || badScroll;
          const flag = m.scrollWidth > m.viewport ? " OVER" : m.inner.length > 0 ? " CLIP" : m.falseApprovalClipped ? " PILL" : m.workspaceProblem !== null ? " PAGE" : m.barProblem !== null ? " BAR" : badScroll ? " SCROLL" : "";
          cells.push(`${m.scrollWidth}/${m.viewport}${flag}`);
          if (over) failures.push({ route: route.name, width, ...m });
        } finally {
          await context.close();
        }
      }
      rows.push({ route: route.name, cells });
    }
  } finally {
    await browser.close();
    await server?.close();
  }

  const col = 18;
  console.log(`scrollWidth/viewport at ${WIDTHS.join(", ")} px (OVER = page scrolls sideways, CLIP = a container scrolls sideways, PILL = false-approval pill clipped, PAGE = a case page scrolls itself or its tour bar or panes are off, BAR = the top bar overflows, wraps or lost the Repository link, SCROLL = first highlight not centered in the chart panel)`);
  console.log("route".padEnd(36) + WIDTHS.map((w) => String(w).padEnd(col)).join(""));
  for (const r of rows) console.log(r.route.padEnd(36) + r.cells.map((c) => c.padEnd(col)).join(""));

  if (failures.length > 0) {
    console.error(`\n${failures.length} measurement(s) failed:`);
    for (const f of failures) {
      console.error(`  ${f.route} at ${f.width}px: scrollWidth ${f.scrollWidth}, viewport ${f.viewport}`);
      for (const w of f.wide) console.error(`    right edge ${w.right}: ${w.el} "${w.text}"`);
      for (const w of f.inner) console.error(`    scrolls sideways inside (content ${w.right}): ${w.el} "${w.text}"`);
      if (f.scroll && scrollProblem(f.scroll)) console.error(`    chart scroll: ${scrollProblem(f.scroll)}`);
      if (f.falseApprovalClipped) console.error("    the FALSE APPROVAL pill is clipped by the criteria pane or leaves no room for the text");
      if (f.workspaceProblem) console.error(`    case page: ${f.workspaceProblem}`);
      if (f.barProblem) console.error(`    top bar: ${f.barProblem}`);
    }
    process.exit(1);
  }
  console.log("\ncheck-layout: no horizontal overflow, case pages do not scroll themselves, and the first highlight is centered in the chart panel.");
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
