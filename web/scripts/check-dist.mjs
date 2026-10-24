// Scans the built site for absolute URLs and fails on any outside a short allowlist, so a
// dependency cannot quietly add a request to another host. It also fails on anything a strict
// Content Security Policy (default-src 'none'; script-src 'self'; style-src 'self') would block:
// an inline script or style, a style or event-handler attribute in HTML, or a data: URI in CSS.
// Run after `npm run build`.
//
//   node scripts/check-dist.mjs [--dir <path>]
//
// The directory defaults to ../dist/ (the Vite output); --dir or the environment variable
// CHECK_DIST_DIR names another, for a second build with `vite build --outDir`.
//
// Live mode. The three VITE_LIVE_* variables are read from the environment of this script, the
// same ones the build was given (scripts/live-config.mjs validates them for both):
//   - none set: dist must contain neither a live host nor the strings execute-api and
//     amazoncognito, so a recorded-only build can never carry live code;
//   - all set: the gateway origin and the hosted-UI origin are added to the allowlist and must
//     appear in a script of dist, and so must the client id; nothing else is added;
//   - some set, or a value malformed: fail before looking at dist.
// Hosts and ids are never hardcoded here and are never printed.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { extname, join, relative, resolve } from "node:path";
import { readLiveConfig } from "./live-config.mjs";

function distDirectory() {
  const flag = process.argv.indexOf("--dir");
  if (flag !== -1) {
    const value = process.argv[flag + 1];
    if (!value) {
      console.error("--dir needs a directory");
      process.exit(1);
    }
    return resolve(value);
  }
  if (process.env.CHECK_DIST_DIR) return resolve(process.env.CHECK_DIST_DIR);
  return new URL("../dist/", import.meta.url).pathname;
}

const dist = distDirectory();

let live;
try {
  live = readLiveConfig(process.env);
} catch (error) {
  console.error(`check-dist failed: ${error.message}`);
  process.exit(1);
}

// Each entry is a URL prefix (host and path, no scheme) and the reason it is allowed.
const ALLOWED = [
  ["www.w3.org/", "XML, SVG and MathML namespace identifiers inside React and the markup; never fetched"],
  ["react.dev/", "error-decoder links in React's production error messages; text only, never fetched"],
  ["tailwindcss.com", "the license comment at the top of the generated CSS; a comment, never fetched"],
  [
    "github.com/tejavardhanreddy01/precertly",
    "the repository link in the footer and the about page; a plain anchor",
  ],
  [
    "www.cms.gov/medicare-coverage-database/",
    "policy source links inside data/policies/*.json; shown as plain anchors, never fetched",
  ],
];
// The live entries are marked so that nothing printed repeats their values.
const LIVE_ALLOWED = live.enabled
  ? [
      [live.apiOrigin.replace(/^https:\/\//, ""), "the live API gateway, from VITE_LIVE_API_URL"],
      [live.hostedUiOrigin.replace(/^https:\/\//, ""), "the Cognito hosted UI, from VITE_LIVE_HOSTED_UI_URL"],
    ]
  : [];

const TEXT = new Set([".html", ".js", ".css", ".json", ".svg", ".txt", ".map", ".webmanifest"]);
const URL_PATTERN = /\bhttps?:\/\/[^\s"'<>)\\`]+/g;
// What no recorded-only build may contain, whatever its host.
const LIVE_MARKERS = ["execute-api", "amazoncognito"];
// A URL for a failure message; one that looks like a live host is not repeated.
const shown = (url) => (LIVE_MARKERS.some((m) => url.includes(m)) ? "[a live host, value not shown]" : url);

function* walk(dir) {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) yield* walk(path);
    else yield path;
  }
}

let files;
try {
  files = [...walk(dist)];
} catch {
  console.error(`${relative(process.cwd(), dist) || dist}/ is missing: run \`npm run build\` first.`);
  process.exit(1);
}

const failures = [];
let scanned = 0;
let urls = 0;
const seen = { api: false, hostedUi: false, clientId: false };
for (const file of files) {
  if (!TEXT.has(extname(file))) {
    failures.push(`${relative(dist, file)}: unexpected file type (only text files are expected in dist)`);
    continue;
  }
  scanned += 1;
  const text = readFileSync(file, "utf8");
  for (const match of text.matchAll(URL_PATTERN)) {
    urls += 1;
    const bare = match[0].replace(/^https?:\/\//, "");
    if (![...ALLOWED, ...LIVE_ALLOWED].some(([prefix]) => bare.startsWith(prefix))) {
      failures.push(`${relative(dist, file)}: ${shown(match[0])}`);
    }
  }
  if (live.enabled) {
    if (extname(file) === ".js") {
      seen.api ||= text.includes(live.apiOrigin);
      seen.hostedUi ||= text.includes(live.hostedUiOrigin);
      seen.clientId ||= text.includes(live.clientId);
    }
  } else {
    for (const marker of LIVE_MARKERS) {
      if (text.includes(marker)) {
        failures.push(`${relative(dist, file)}: contains "${marker}" but no live configuration is set (a recorded-only build must have no live code)`);
      }
    }
  }
  if (extname(file) === ".html") {
    const rel = relative(dist, file);
    if (/<script\b(?![^>]*\bsrc\s*=)/i.test(text)) failures.push(`${rel}: inline script`);
    if (/<style\b/i.test(text)) failures.push(`${rel}: inline style element`);
    if (/<[a-z][^>]*\sstyle\s*=/i.test(text)) failures.push(`${rel}: style attribute`);
    if (/<[a-z][^>]*\son[a-z]+\s*=/i.test(text)) failures.push(`${rel}: inline event handler`);
  }
  if (extname(file) === ".css" && /url\(\s*["']?data:/i.test(text)) {
    failures.push(`${relative(dist, file)}: data: URI (the CSP img-src and font-src would need data:)`);
  }
  if (extname(file) === ".html" && /(?:src|href)\s*=\s*["']\/\//.test(text)) {
    failures.push(`${relative(dist, file)}: protocol-relative URL`);
  }
}
if (live.enabled) {
  if (!seen.api) failures.push("live mode is configured but the gateway origin is not in any script of dist");
  if (!seen.hostedUi) failures.push("live mode is configured but the hosted-UI origin is not in any script of dist");
  if (!seen.clientId) failures.push("live mode is configured but the app client id is not in any script of dist");
}

if (failures.length > 0) {
  console.error("check-dist failed:");
  for (const f of failures) console.error(`  ${f}`);
  process.exit(1);
}
console.log(
  `check-dist: ${scanned} files, ${urls} URLs, all within the allowlist (${live.enabled ? "live mode: the two live origins and the client id are present, values not shown" : "recorded-only: no live host and no live marker"}):`,
);
for (const [prefix, why] of ALLOWED) console.log(`  ${prefix}  (${why})`);
for (const [, why] of LIVE_ALLOWED) console.log(`  [host not shown]  (${why})`);
