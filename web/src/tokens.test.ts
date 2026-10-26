import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const CSS = readFileSync(join(dirname(fileURLToPath(import.meta.url)), "index.css"), "utf8");

function token(name: string): string {
  const match = new RegExp(`--color-${name}:\\s*(#[0-9a-fA-F]{6})\\s*;`).exec(CSS);
  if (!match?.[1]) throw new Error(`index.css has no --color-${name} token`);
  return match[1];
}

function luminance(hex: string): number {
  const channel = (i: number) => {
    const c = parseInt(hex.slice(1 + 2 * i, 3 + 2 * i), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(0) + 0.7152 * channel(1) + 0.0722 * channel(2);
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}

// Text color, then the backgrounds it is drawn on. WCAG AA for text is 4.5:1.
const PAIRS: [string, string[]][] = [
  ["ink", ["canvas", "surface", "sunken", "wash", "band", "accent-soft", "chip", "danger-tint", "mark"]],
  ["ink-2", ["canvas", "surface", "sunken", "wash", "band"]],
  ["ink-3", ["canvas", "surface", "sunken", "wash", "band"]],
  ["accent", ["canvas", "surface", "wash", "band", "accent-soft", "chip"]],
  ["accent-strong", ["canvas", "surface", "sunken", "wash", "band", "accent-soft", "chip"]],
  ["on-accent", ["accent", "accent-strong"]],
  ["on-danger", ["danger", "danger-deep"]],
  ["met-fg", ["met-bg", "surface", "canvas", "wash"]],
  ["notmet-fg", ["notmet-bg", "surface", "danger-tint", "canvas", "wash"]],
  ["insufficient-fg", ["insufficient-bg", "surface", "canvas", "wash"]],
  ["invalidated-fg", ["invalidated-bg", "surface", "canvas", "wash"]],
  ["na-fg", ["na-bg", "surface", "canvas", "wash"]],
  ["danger", ["surface", "danger-tint", "canvas"]],
];

describe("design tokens", () => {
  for (const [fg, backgrounds] of PAIRS) {
    for (const bg of backgrounds) {
      it(`${fg} on ${bg} is at least 4.5:1`, () => {
        expect(contrast(token(fg), token(bg))).toBeGreaterThanOrEqual(4.5);
      });
    }
  }

  it("defines one accent and a distinct color set for each verdict state", () => {
    const states = ["met", "notmet", "insufficient", "invalidated", "na"].map((s) => token(`${s}-fg`));
    expect(new Set(states).size).toBe(states.length);
    expect(CSS.match(/--color-accent:/g)).toHaveLength(1);
  });
});

describe("one place for colors", () => {
  const SRC = dirname(fileURLToPath(import.meta.url));
  const sources = (dir: string): string[] =>
    readdirSync(dir).flatMap((name) => {
      const path = join(dir, name);
      if (statSync(path).isDirectory()) return name === "generated" ? [] : sources(path);
      return /\.tsx$/.test(name) && !/\.test\.tsx$/.test(name) ? [path] : [];
    });

  // Tailwind's default palette (bg-slate-100, text-red-900, bg-white ...) is a second source of color.
  const PALETTE = new RegExp(
    "\\b(?:bg|text|border|ring|outline|fill|stroke|divide|from|to|via|shadow)-" +
      "(?:(?:slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-\\d{2,3}|white|black)\\b",
  );

  it("uses theme tokens and never a default palette class in a component", () => {
    const hits = sources(SRC)
      .filter((f) => PALETTE.test(readFileSync(f, "utf8")))
      .map((f) => relative(SRC, f));
    expect(hits).toEqual([]);
  });

  it("writes no hex color in a component", () => {
    const hits = sources(SRC)
      .filter((f) => /#[0-9a-fA-F]{6}\b/.test(readFileSync(f, "utf8")))
      .map((f) => relative(SRC, f));
    expect(hits).toEqual([]);
  });
});
