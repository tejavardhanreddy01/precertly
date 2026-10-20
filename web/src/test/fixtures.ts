import { existsSync, readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import type { CaseFile, Index, PolicyFile } from "../lib/types";

const DATA_DIR = join(dirname(fileURLToPath(import.meta.url)), "../../public/data");

/** Tests read the files written by the Python export; they never skip when those are absent. */
export function dataDir(): string {
  if (!existsSync(join(DATA_DIR, "index.json"))) {
    throw new Error(
      "web/public/data is missing: run `npm run prepare-data` in web/ (it needs `uv sync` first) before the tests.",
    );
  }
  return DATA_DIR;
}

function readJson<T>(relative: string): T {
  return JSON.parse(readFileSync(join(dataDir(), relative), "utf8")) as T;
}

export const readIndex = (): Index => readJson<Index>("index.json");
export const readCase = (id: string): CaseFile => readJson<CaseFile>(`cases/${id}.json`);
export const readPolicy = (id: string): PolicyFile => readJson<PolicyFile>(`policies/${id}.json`);

export function allCases(): CaseFile[] {
  return readdirSync(join(dataDir(), "cases"))
    .filter((f) => f.endsWith(".json"))
    .sort()
    .map((f) => readCase(f.replace(/\.json$/, "")));
}

/** A fetch that serves ./data/* from public/data, for components that load through DataStore. */
export function diskFetch(): (input: string) => Promise<Response> {
  return async (input) => {
    const relative = input.replace(/^\.\/data\//, "");
    try {
      return new Response(readFileSync(join(dataDir(), relative), "utf8"), { status: 200 });
    } catch (error) {
      if (error instanceof Error && error.message.includes("prepare-data")) throw error;
      return new Response("not found", { status: 404 });
    }
  };
}
