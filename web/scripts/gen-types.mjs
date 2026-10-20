// Generates TypeScript types from public/data/schema.json (written by the Python export from its
// Pydantic models) into src/generated, which is git-ignored. Run through `npm run types`.
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { compile } from "json-schema-to-typescript";

const schemaPath = new URL("../public/data/schema.json", import.meta.url);
const outDir = new URL("../src/generated/", import.meta.url);

let schema;
try {
  schema = JSON.parse(readFileSync(schemaPath, "utf8"));
} catch {
  console.error("public/data/schema.json is missing: run `npm run data` (or `npm run prepare-data`).");
  process.exit(1);
}

mkdirSync(outDir, { recursive: true });
const files = { Index: "index.ts", PolicyFile: "policy.ts", CaseFile: "case.ts" };
for (const [name, file] of Object.entries(files)) {
  const root = schema[name];
  if (!root) {
    console.error(`schema.json has no ${name}`);
    process.exit(1);
  }
  const ts = await compile(root, name, {
    bannerComment: "/* Generated from public/data/schema.json by scripts/gen-types.mjs. Do not edit. */",
    additionalProperties: false,
    format: false,
  });
  writeFileSync(new URL(file, outDir), ts);
}
console.log(`wrote ${Object.values(files).join(", ")} to src/generated`);
