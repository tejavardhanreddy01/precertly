// Takes the screenshots kept under web/docs/screenshots. Run by hand, like check-layout.mjs:
//
//   cd web && npm ci && npm run prepare-data && npx playwright install chromium
//   node scripts/screenshots.mjs before|after [--url http://localhost:5173/]
//
// The label is the last part of each file name (landing-390-before.png), so a redesign can be
// compared with the screen it replaced. Without --url it starts the Vite dev server on a free local
// port and stops it afterwards. Each shot is one viewport (not the whole page) and must stay
// under 300 KB.
import { mkdirSync, statSync } from "node:fs";
import { chromium } from "playwright";
import { createServer } from "vite";

const LIMIT = 300 * 1024;
const SIZES = [
  { width: 390, height: 844 },
  { width: 1280, height: 800 },
];

const SHOTS = [
  { name: "landing", hash: "", wait: "main h1" },
  {
    name: "case-selected",
    hash: "#/cases/facet-03?c=chronic-axial-pain-functional-deficit",
    wait: '[data-testid="verdict-panel"]',
  },
  {
    name: "tour-step-3",
    hash: "#/cases/facet-11?c=conservative-care-failed&tour=3",
    wait: '[data-testid="verdict-panel"]',
  },
  { name: "cases-list", hash: "#/cases", wait: "main h1" },
];

async function main() {
  const label = process.argv[2];
  if (label !== "before" && label !== "after") throw new Error("usage: screenshots.mjs before|after [--url URL]");
  const out = new URL("../docs/screenshots/", import.meta.url);
  mkdirSync(out, { recursive: true });

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
  const tooBig = [];
  try {
    for (const shot of SHOTS) {
      for (const size of SIZES) {
        const context = await browser.newContext({ viewport: size, reducedMotion: "reduce" });
        const page = await context.newPage();
        try {
          await page.goto(`${base}${shot.hash}`);
          await page.waitForSelector(shot.wait, { timeout: 15000 });
          // The landing page before the redesign redirects to the first tour step; let it settle.
          await page.evaluate(() => document.fonts?.ready);
          await page.waitForTimeout(600);
          const file = new URL(`${shot.name}-${size.width}-${label}.png`, out);
          await page.screenshot({ path: file.pathname });
          const bytes = statSync(file).size;
          console.log(`${file.pathname.split("/docs/screenshots/")[1]}  ${Math.round(bytes / 1024)} KB`);
          if (bytes > LIMIT) tooBig.push(file.pathname);
        } finally {
          await context.close();
        }
      }
    }
  } finally {
    await browser.close();
    await server?.close();
  }
  if (tooBig.length > 0) {
    console.error(`over 300 KB: ${tooBig.join(", ")}`);
    process.exit(1);
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
