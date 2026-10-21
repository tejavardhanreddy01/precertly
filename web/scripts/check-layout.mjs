// Measures horizontal overflow of the recorded-mode site in a real browser. Run by hand; it is
// not part of CI because it needs a downloaded browser:
//
//   cd web && npm ci && npm run prepare-data && npx playwright install chromium
//   node scripts/check-layout.mjs [--url http://localhost:5173/]
//
// Without --url it starts the Vite dev server on a free local port and stops it afterwards.
// For each width and route it loads the page, waits for the content, and compares
// document.documentElement.scrollWidth with the viewport width, looks for containers that scroll
// sideways on their own, and checks that a false-approval label sits below its row's text on
// narrow widths. Exit code 1 if any check fails.
import { readFileSync } from "node:fs";
import { chromium } from "playwright";
import { createServer } from "vite";

const WIDTHS = [360, 390, 768, 1280];
const HEIGHT = 900;
const dataDir = new URL("../public/data/", import.meta.url);

function readCase(id) {
  return JSON.parse(readFileSync(new URL(`cases/${id}.json`, dataDir), "utf8"));
}

const unscored = readCase("pap-01");
const unscoredCriterion = unscored.criteria.find((c) => c.judged)?.criterion;
if (!unscoredCriterion) throw new Error("pap-01 has no judged criterion in public/data");

// `wait` is a selector that is present once the route has rendered what is being measured.
const ROUTES = [
  { name: "landing (redirect target)", hash: "", wait: '[aria-label="Tour"]' },
  { name: "cases", hash: "#/cases", wait: "h3" },
  {
    name: "scored case, criterion selected",
    hash: "#/cases/facet-03?c=chronic-axial-pain-functional-deficit",
    wait: '[data-testid="verdict-panel"]',
  },
  {
    name: "false approval open (tour step 3)",
    hash: "#/cases/facet-11?c=conservative-care-failed&tour=3",
    wait: '[data-testid="verdict-panel"]',
  },
  {
    name: "rule-decided verdict",
    hash: "#/cases/facet-02?c=two-mbbs-80pct-sustained-relief",
    wait: '[data-testid="computed-fact"]',
  },
  {
    name: "invalidated verdict",
    hash: "#/cases/facet-01?c=no-non-facet-pathology",
    wait: '[data-testid="invalidated"]',
  },
  { name: "unscored case", hash: `#/cases/pap-01?c=${unscoredCriterion}`, wait: '[data-testid="label-panel"]' },
  { name: "policies", hash: "#/policies", wait: "h1" },
  { name: "policy", hash: "#/policies/facet-joint-interventions", wait: "blockquote" },
  { name: "about", hash: "#/about", wait: '[data-testid="gates"]' },
];

// What to run in the page: the widths, and which elements stick out past the viewport edge.
function measure() {
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
  // Below the sm breakpoint a row's false-approval label sits under the criterion text.
  let falseApprovalBeside = false;
  if (viewport < 640) {
    for (const row of document.querySelectorAll("[data-criterion]")) {
      const marker = [...row.querySelectorAll("button > span")].find((s) => /false approval/i.test(s.textContent ?? ""));
      const text = row.querySelector("button > span.flex-1");
      if (marker && text && marker.getBoundingClientRect().top < text.getBoundingClientRect().bottom - 1) {
        falseApprovalBeside = true;
      }
    }
  }
  return { viewport, scrollWidth, wide: wide.slice(0, 6), inner: inner.slice(0, 6), falseApprovalBeside };
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
        const page = await browser.newPage({ viewport: { width, height: HEIGHT } });
        try {
          await page.goto(`${base}${route.hash}`);
          await page.waitForSelector("main h1, main h2, main h3", { timeout: 15000 });
          await page.waitForSelector(route.wait, { timeout: 15000 });
          // Let fonts, the smooth scroll and any layout after the first paint settle.
          await page.evaluate(() => document.fonts?.ready);
          await page.waitForTimeout(400);
          const m = await page.evaluate(measure);
          const over = m.scrollWidth > m.viewport || m.inner.length > 0 || m.falseApprovalBeside;
          const flag = m.scrollWidth > m.viewport ? " OVER" : m.inner.length > 0 ? " CLIP" : m.falseApprovalBeside ? " BESIDE" : "";
          cells.push(`${m.scrollWidth}/${m.viewport}${flag}`);
          if (over) failures.push({ route: route.name, width, ...m });
        } finally {
          await page.close();
        }
      }
      rows.push({ route: route.name, cells });
    }
  } finally {
    await browser.close();
    await server?.close();
  }

  const col = 14;
  console.log(`scrollWidth/viewport at ${WIDTHS.join(", ")} px (OVER = page scrolls sideways, CLIP = a container scrolls sideways, BESIDE = false-approval label beside its text)`);
  console.log("route".padEnd(36) + WIDTHS.map((w) => String(w).padEnd(col)).join(""));
  for (const r of rows) console.log(r.route.padEnd(36) + r.cells.map((c) => c.padEnd(col)).join(""));

  if (failures.length > 0) {
    console.error(`\n${failures.length} measurement(s) wider than the viewport:`);
    for (const f of failures) {
      console.error(`  ${f.route} at ${f.width}px: scrollWidth ${f.scrollWidth}, viewport ${f.viewport}`);
      for (const w of f.wide) console.error(`    right edge ${w.right}: ${w.el} "${w.text}"`);
      for (const w of f.inner) console.error(`    scrolls sideways inside (content ${w.right}): ${w.el} "${w.text}"`);
      if (f.falseApprovalBeside) console.error("    a FALSE APPROVAL label sits beside its criterion text, not below it");
    }
    process.exit(1);
  }
  console.log("\ncheck-layout: no horizontal overflow.");
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
