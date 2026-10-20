import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const SRC = dirname(fileURLToPath(import.meta.url));

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return name === "generated" ? [] : sources(path);
    return /\.(ts|tsx)$/.test(name) ? [path] : [];
  });
}

// Built from parts so this file does not match its own search.
const FORBIDDEN: [string, RegExp][] = [
  ["dangerous HTML injection", new RegExp("dangerously" + "SetInnerHTML")],
  ["eval", new RegExp("\\beval\\s*\\(")],
  ["Function constructor", new RegExp("new\\s+Function\\s*\\(")],
  ["innerHTML assignment", new RegExp("\\.(inner|outer)" + "HTML\\s*=")],
  ["document.write", new RegExp("document\\.write")],
  ["XMLHttpRequest", new RegExp("XMLHttp" + "Request")],
  ["sendBeacon", new RegExp("send" + "Beacon")],
  ["WebSocket", new RegExp("Web" + "Socket")],
];

describe("source rules", () => {
  const files = sources(SRC).filter((f) => !f.endsWith("safety.test.ts"));

  it("scans the source tree", () => {
    expect(files.length).toBeGreaterThan(5);
  });

  for (const [name, pattern] of FORBIDDEN) {
    it(`never uses ${name}`, () => {
      const hits = files.filter((f) => pattern.test(readFileSync(f, "utf8"))).map((f) => relative(SRC, f));
      expect(hits).toEqual([]);
    });
  }

  it("makes no request except same-origin fetches of ./data/ files", () => {
    const fetches = files.flatMap((f) =>
      [...readFileSync(f, "utf8").matchAll(/\bfetch\(/g)].map(() => relative(SRC, f)),
    );
    expect(new Set(fetches)).toEqual(new Set(["lib/data.ts"]));
    expect(readFileSync(join(SRC, "lib/data.ts"), "utf8")).toContain('base = "./data/"');
  });

  // Links to other sites are plain anchors (href on <a>); nothing is loaded from them.
  it("loads no script, image, font or stylesheet from another host", () => {
    const external = files.filter((f) =>
      /(?:\bsrc|@import|url\()\s*=?\s*\(?["']?https?:\/\//.test(readFileSync(f, "utf8")),
    );
    expect(external.map((f) => relative(SRC, f))).toEqual([]);
  });
});
