// Scans the built site for absolute URLs and fails on any outside a short allowlist, so a
// dependency cannot quietly add a request to another host. Run after `npm run build`.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { extname, join, relative } from "node:path";

const dist = new URL("../dist/", import.meta.url).pathname;

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

const TEXT = new Set([".html", ".js", ".css", ".json", ".svg", ".txt", ".map", ".webmanifest"]);
const URL_PATTERN = /\bhttps?:\/\/[^\s"'<>)\\`]+/g;

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
  console.error("dist/ is missing: run `npm run build` first.");
  process.exit(1);
}

const failures = [];
let scanned = 0;
let urls = 0;
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
    if (!ALLOWED.some(([prefix]) => bare.startsWith(prefix))) {
      failures.push(`${relative(dist, file)}: ${match[0]}`);
    }
  }
  if (extname(file) === ".html" && /(?:src|href)\s*=\s*["']\/\//.test(text)) {
    failures.push(`${relative(dist, file)}: protocol-relative URL`);
  }
}

if (failures.length > 0) {
  console.error("Hosts outside the allowlist:");
  for (const f of failures) console.error(`  ${f}`);
  process.exit(1);
}
console.log(`check-dist: ${scanned} files, ${urls} URLs, all within the allowlist:`);
for (const [prefix, why] of ALLOWED) console.log(`  ${prefix}  (${why})`);
