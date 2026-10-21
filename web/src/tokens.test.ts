import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
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
  ["ink", ["canvas", "surface", "sunken", "accent-soft", "danger-tint", "mark"]],
  ["ink-2", ["canvas", "surface", "sunken"]],
  ["ink-3", ["canvas", "surface", "sunken"]],
  ["accent", ["canvas", "surface", "accent-soft"]],
  ["accent-strong", ["canvas", "surface", "accent-soft"]],
  ["on-accent", ["accent", "accent-strong"]],
  ["on-danger", ["danger", "danger-deep"]],
  ["met-fg", ["met-bg", "surface"]],
  ["notmet-fg", ["notmet-bg", "surface", "danger-tint"]],
  ["insufficient-fg", ["insufficient-bg", "surface"]],
  ["invalidated-fg", ["invalidated-bg", "surface"]],
  ["na-fg", ["na-bg", "surface"]],
  ["danger", ["surface", "danger-tint"]],
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
