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
// on screen: under 1024 px it is behind the Evidence tab).
//
//   node scripts/check-layout.mjs --live
//
// builds the site with clearly fake live placeholders, serves it from a local folder, answers every
// request itself with Playwright's page.route (nothing leaves the machine; a request to anywhere
// else fails the check) and measures the live page at 320, 390 and 1280 px in four states: the
// environment down, up and signed out, a run in progress, and a result shown.
//
// It also checks the top bar itself:
// its content must not be wider than the bar, it must keep one line, and the Repository link must be
// in view on every route (a case page has no footer). A fourth nav item, "Live", is added to the
// bar before measuring when the page has none, to prove the room for it. Exit code 1 if any check fails.
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { createServer as createHttpServer } from "node:http";
import { tmpdir } from "node:os";
import { extname, join, normalize } from "node:path";
import { chromium } from "playwright";
import { build as viteBuild, createServer } from "vite";

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
  const hadLiveNav = Boolean(nav?.querySelector('a[href="#/live"]'));
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
  return { viewport, scrollWidth, wide: wide.slice(0, 6), inner: inner.slice(0, 6), falseApprovalClipped, workspaceProblem, barProblem, hadLiveNav, scroll };
}

// A mark that fits the visible area is centered within 12 px (or the content ends first); one taller
// than the visible area starts at least 24 px down. This is the same threshold as targetScrollTop.
function scrollProblem(sc) {
  if (sc.under) return "the first mark is cut off by the panel edge or its sticky heading";
  if (sc.tall) return sc.top >= 24 || sc.clamped ? null : `a tall mark starts ${sc.top} px below the heading, not 24`;
  if (sc.clamped || Math.abs(sc.offCenter) <= 12) return null;
  return `the first mark is ${sc.offCenter} px from the center of the panel`;
}

// ---------------------------------------------------------------------------------------------
// --live: the live page, with every request answered by this script
// ---------------------------------------------------------------------------------------------

const LIVE_WIDTHS = [320, 390, 1280];
// Clearly fake, of the right shape. They are built into a folder in the temp directory and never
// leave this machine: every request to them is answered by page.route below.
const FAKE = {
  VITE_LIVE_API_URL: "https://abcdefghij.execute-api.us-east-2.amazonaws.com",
  VITE_LIVE_HOSTED_UI_URL: "https://placeholder-prefix.auth.us-east-2.amazoncognito.com",
  VITE_LIVE_CLIENT_ID: "PlaceholderClientId0123456789",
};
const contract = JSON.parse(readFileSync(new URL("../src/lib/live/contract.fixture.json", import.meta.url), "utf8"));
// What the page keeps in sessionStorage between leaving for the sign-in page and coming back.
const SIGN_IN_KEY = "precertly.live.signin";

const LIVE_STATES = [
  {
    // A case page in a live-enabled build has the Run live link in its summary bar.
    name: "case page, Run live link",
    hash: "#/cases/facet-03",
    workspace: true,
    scenario: { health: 200 },
    wait: '[data-testid="run-live-link"]',
  },
  { name: "live: environment down", scenario: { health: 500 }, wait: '[data-testid="environment"] [data-state="down"]' },
  { name: "live: up, signed out", scenario: { health: 200 }, wait: '[data-testid="signin"] button' },
  {
    name: "live: run in progress",
    scenario: { health: 200, signedIn: true, poll: "judging" },
    click: "Run live",
    wait: '[data-testid="progress"] [data-stage="judging"][data-state="current"]',
  },
  {
    name: "live: result shown",
    scenario: { health: 200, signedIn: true, poll: "done" },
    click: "Run live",
    wait: '[data-testid="live-result"]',
    timeout: 20000,
  },
];

const CONTENT_TYPES = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".json": "application/json", ".svg": "image/svg+xml" };

function serve(dir) {
  const server = createHttpServer((req, res) => {
    const path = new URL(req.url ?? "/", "http://localhost").pathname;
    const file = normalize(join(dir, path === "/" ? "index.html" : path));
    if (!file.startsWith(dir)) {
      res.writeHead(403).end();
      return;
    }
    try {
      const body = readFileSync(file);
      res.writeHead(200, { "content-type": CONTENT_TYPES[extname(file)] ?? "application/octet-stream" });
      res.end(body);
    } catch {
      res.writeHead(404).end();
    }
  });
  return new Promise((resolve) => server.listen(0, "127.0.0.1", () => resolve(server)));
}

/** Answers every request: the site from the local server, the fake API and the fake sign-in page from here. */
function interceptor(origin, scenario, stray) {
  const apiHost = new URL(FAKE.VITE_LIVE_API_URL).host;
  const authHost = new URL(FAKE.VITE_LIVE_HOSTED_UI_URL).host;
  const cors = {
    "access-control-allow-origin": origin,
    "access-control-allow-headers": "authorization,content-type",
    "access-control-allow-methods": "GET,POST,OPTIONS",
  };
  const json = (route, status, body) =>
    route.fulfill({ status, headers: { ...cors, "content-type": "application/json" }, body: JSON.stringify(body) });
  return async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin === origin) return route.continue();
    if (url.host === apiHost || url.host === authHost) {
      if (request.method() === "OPTIONS") return route.fulfill({ status: 204, headers: cors });
      if (url.host === authHost && url.pathname === "/oauth2/token") {
        return json(route, 200, { access_token: "FAKE.LAYOUT.TOKEN", token_type: "Bearer", expires_in: 3600 });
      }
      if (url.host === apiHost && url.pathname === "/health") {
        return scenario.health === 200 ? json(route, 200, { status: "ok" }) : json(route, 500, { message: "Internal Server Error" });
      }
      if (url.host === apiHost && url.pathname === "/live/runs" && request.method() === "POST") {
        return json(route, 202, contract.started);
      }
      if (url.host === apiHost && url.pathname.startsWith("/live/runs/")) {
        return json(route, 200, scenario.poll === "done" ? contract.run_states.done : contract.run_states.judging);
      }
    }
    // Anything else is not answered and is a failure of this check: no real host is contacted.
    stray.push(`${url.protocol}//${url.host.replace(/^[^.]+/, "<id>")}${url.pathname}`);
    return route.abort();
  };
}

async function runLive() {
  const root = new URL("../", import.meta.url).pathname;
  const out = mkdtempSync(join(tmpdir(), "check-layout-live-"));
  const saved = Object.fromEntries(Object.keys(FAKE).map((k) => [k, process.env[k]]));
  Object.assign(process.env, FAKE);
  try {
    await viteBuild({ root, logLevel: "error", build: { outDir: out, emptyOutDir: true } });
  } finally {
    for (const [k, v] of Object.entries(saved)) {
      if (v === undefined) delete process.env[k];
      else process.env[k] = v;
    }
  }
  const server = await serve(out);
  const origin = `http://127.0.0.1:${server.address().port}`;
  const browser = await chromium.launch();
  const failures = [];
  const rows = [];
  try {
    for (const state of LIVE_STATES) {
      const cells = [];
      for (const width of LIVE_WIDTHS) {
        const context = await browser.newContext({ viewport: { width, height: HEIGHT }, reducedMotion: "reduce" });
        const page = await context.newPage();
        const stray = [];
        await page.route("**/*", interceptor(origin, state.scenario, stray));
        try {
          let target = `${origin}/${state.hash ?? "#/live/facet-03"}`;
          if (state.scenario.signedIn) {
            // A return from the sign-in page: the attempt it stored, and the code and state in the address.
            await context.addInitScript(
              ([key, attempt]) => {
                if (!window.sessionStorage.getItem("__layout_seeded")) {
                  window.sessionStorage.setItem(key, JSON.stringify({ ...attempt, createdAt: Date.now() }));
                  window.sessionStorage.setItem("__layout_seeded", "1");
                }
              },
              [SIGN_IN_KEY, { state: "STATE", verifier: "V".repeat(64), returnHash: "#/live/facet-03" }],
            );
            target = `${origin}/?code=FAKECODE&state=STATE`;
          }
          await page.goto(target);
          await page.waitForSelector("main h1", { timeout: 15000 });
          if (state.click) {
            await page.getByRole("button", { name: state.click }).click({ timeout: 15000 });
          }
          await page.waitForSelector(state.wait, { timeout: state.timeout ?? 15000 });
          await page.evaluate(() => document.fonts?.ready);
          await page.waitForTimeout(400);
          const m = await page.evaluate(measure, { scrollChunk: null, workspace: state.workspace === true });
          const over = m.scrollWidth > m.viewport || m.inner.length > 0 || m.barProblem !== null || m.workspaceProblem !== null || !m.hadLiveNav || stray.length > 0;
          const flag = m.scrollWidth > m.viewport ? " OVER" : m.inner.length > 0 ? " CLIP" : m.barProblem !== null ? " BAR" : m.workspaceProblem !== null ? " PAGE" : !m.hadLiveNav ? " NAV" : stray.length > 0 ? " NET" : "";
          cells.push(`${m.scrollWidth}/${m.viewport}${flag}`);
          if (over) failures.push({ route: state.name, width, stray, ...m });
        } finally {
          await context.close();
        }
      }
      rows.push({ route: state.name, cells });
    }
  } finally {
    await browser.close();
    server.close();
    rmSync(out, { recursive: true, force: true });
  }
  const col = 18;
  console.log(`live page, scrollWidth/viewport at ${LIVE_WIDTHS.join(", ")} px (OVER = page scrolls sideways, CLIP = a container scrolls sideways, BAR = the top bar overflows, PAGE = a case page scrolls itself, NAV = no Live item in the bar, NET = a request left the page's stand-in server and fake API)`);
  console.log("state".padEnd(30) + LIVE_WIDTHS.map((w) => String(w).padEnd(col)).join(""));
  for (const r of rows) console.log(r.route.padEnd(30) + r.cells.map((c) => c.padEnd(col)).join(""));
  if (failures.length > 0) {
    console.error(`\n${failures.length} measurement(s) failed:`);
    for (const f of failures) {
      console.error(`  ${f.route} at ${f.width}px: scrollWidth ${f.scrollWidth}, viewport ${f.viewport}`);
      for (const w of f.wide) console.error(`    right edge ${w.right}: ${w.el} "${w.text}"`);
      for (const w of f.inner) console.error(`    scrolls sideways inside (content ${w.right}): ${w.el} "${w.text}"`);
      if (f.barProblem) console.error(`    top bar: ${f.barProblem}`);
      if (f.workspaceProblem) console.error(`    case page: ${f.workspaceProblem}`);
      if (!f.hadLiveNav) console.error("    the top bar has no Live item");
      for (const u of f.stray) console.error(`    request that was not answered: ${u}`);
    }
    process.exit(1);
  }
  console.log("\ncheck-layout --live: no horizontal overflow in any live state, the top bar fits with the Live item, and no request left the machine.");
}

async function main() {
  if (process.argv.includes("--live")) return runLive();
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
