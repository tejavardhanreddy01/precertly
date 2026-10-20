import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { readIndex } from "../test/fixtures";
import * as about from "./about";

const index = readIndex();
const HERE = dirname(fileURLToPath(import.meta.url));
const QUALITY = readFileSync(join(HERE, "../../../docs/QUALITY.md"), "utf8");

/** Every string in the hand-written content, joined. */
function allText(value: unknown): string[] {
  if (typeof value === "string") return [value];
  if (Array.isArray(value)) return value.flatMap(allText);
  if (value && typeof value === "object") return Object.values(value).flatMap(allText);
  return [];
}
const content = [
  about.WHAT_IT_IS,
  about.WHAT_IT_IS_NOT,
  about.PIPELINE,
  about.GATES,
  about.GATES_CAVEAT,
  about.LIMITATIONS,
]
  .flatMap(allText)
  .join("\n");

describe("about content versus index.json", () => {
  for (const claim of about.SUMMARY_CLAIMS) {
    it(`"${claim.text}" appears and equals the recorded summary`, () => {
      expect(content).toContain(claim.text);
      expect(claim.expected(index)).toBe(claim.text);
    });
  }

  it("has no percentage that is neither checked against the summary nor explained", () => {
    const claimed = about.SUMMARY_CLAIMS.map((c) => c.text).join(" ");
    const tokens = new Set(content.match(/\d+(?:\.\d+)?%/g) ?? []);
    const unexplained = [...tokens].filter((t) => !claimed.includes(t) && !(t in about.OTHER_PERCENTAGES));
    expect(unexplained).toEqual([]);
  });

  it("formats percentages the way the gates table does", () => {
    expect(about.percent(0.962)).toBe("96.2%");
    expect(about.percent(0.7435)).toBe("74.4%");
  });
});

describe("release gates mirror docs/QUALITY.md", () => {
  const plain = (text: string) => text.replace(/`/g, "").replace(/\s+/g, " ").trim();
  const section = QUALITY.slice(QUALITY.indexOf("## Release gates"), QUALITY.indexOf("## Known limitations"));
  const rows = new Map(
    section
      .split("\n")
      .filter((l) => l.startsWith("| ") && !l.startsWith("| Gate") && !l.startsWith("| ---"))
      .map((l) => {
        const cells = l.slice(2, -2).split(" | ").map(plain);
        return [cells[0] ?? "", cells] as const;
      }),
  );

  it("has every row of the file and no other", () => {
    expect([...rows.keys()]).toEqual(about.GATES.map((g) => g.gate));
  });

  for (const gate of about.GATES) {
    it(`${gate.gate}: threshold and current result are the file's`, () => {
      const cells = rows.get(gate.gate);
      expect(cells).toBeDefined();
      expect(plain(gate.threshold)).toBe(cells?.[1]);
      expect(plain(gate.current)).toBe(cells?.[2]);
    });
  }

  it("states the failing gates as failing", () => {
    const status = Object.fromEntries(about.GATES.map((g) => [g.gate, g.status]));
    expect(status["Citation precision"]).toBe("fails");
    expect(status["False-approval rate on the eval set"]).toBe("fails");
    for (const g of about.GATES) {
      if (/: fails/.test(g.current) || /\bfails\b/.test(g.current)) expect(g.status).toBe("fails");
      if (/: passes/.test(g.current)) expect(g.status).toBe("passes");
    }
  });

  it("carries the file's caveat", () => {
    expect(plain(section)).toContain(plain(about.GATES_CAVEAT.split(". ")[0] ?? ""));
  });
});
