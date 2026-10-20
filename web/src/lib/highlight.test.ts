import { highlight, normalizeWhitespace, wholeSpan, type Span } from "./highlight";
import { allCases, readIndex } from "../test/fixtures";

const span = (char_start: number, char_end: number, key?: number, located = true): Span => ({
  char_start,
  char_end,
  located,
  key,
});

const joined = (segments: ReturnType<typeof highlight>) => segments.map((s) => s.text).join("");
const markedText = (segments: ReturnType<typeof highlight>) =>
  segments.filter((s) => s.marked).map((s) => s.text);

describe("highlight", () => {
  const text = "0123456789";

  it("returns one unmarked segment when there are no citations", () => {
    expect(highlight(text, [])).toEqual([{ text, marked: false, keys: [] }]);
  });

  it("returns no segments for empty text", () => {
    expect(highlight("", [span(0, 3)])).toEqual([]);
  });

  it("marks a span in the middle", () => {
    const segments = highlight(text, [span(2, 5, 0)]);
    expect(segments).toEqual([
      { text: "01", marked: false, keys: [] },
      { text: "234", marked: true, keys: [0] },
      { text: "56789", marked: false, keys: [] },
    ]);
  });

  it("marks a span at the very start and one at the very end", () => {
    expect(highlight(text, [span(0, 2)])).toEqual([
      { text: "01", marked: true, keys: [] },
      { text: "23456789", marked: false, keys: [] },
    ]);
    expect(highlight(text, [span(8, 10)])).toEqual([
      { text: "01234567", marked: false, keys: [] },
      { text: "89", marked: true, keys: [] },
    ]);
  });

  it("marks the whole text when the span covers it", () => {
    expect(highlight(text, [span(0, 10)])).toEqual([{ text, marked: true, keys: [] }]);
  });

  it("merges overlapping spans and keeps both keys", () => {
    const segments = highlight(text, [span(4, 8, 1), span(2, 5, 0)]);
    expect(markedText(segments)).toEqual(["234567"]);
    expect(segments.find((s) => s.marked)?.keys).toEqual([0, 1]);
  });

  it("merges touching spans", () => {
    expect(markedText(highlight(text, [span(2, 4), span(4, 6)]))).toEqual(["2345"]);
  });

  it("keeps spans with a gap of one character apart", () => {
    expect(markedText(highlight(text, [span(2, 4), span(5, 6)]))).toEqual(["23", "5"]);
  });

  it("merges a span contained in another", () => {
    const segments = highlight(text, [span(1, 9, 0), span(3, 5, 1)]);
    expect(markedText(segments)).toEqual(["12345678"]);
  });

  it("ignores citations that are not located", () => {
    expect(highlight(text, [span(2, 5, 0, false)])).toEqual([{ text, marked: false, keys: [] }]);
  });

  it("ignores empty and inverted spans and clamps spans past the end", () => {
    expect(markedText(highlight(text, [span(3, 3), span(6, 4)]))).toEqual([]);
    expect(markedText(highlight(text, [span(8, 99)]))).toEqual(["89"]);
    expect(markedText(highlight(text, [span(-5, 2)]))).toEqual(["01"]);
  });

  it("counts offsets in code points, not UTF-16 units", () => {
    const emoji = "a😀b😀c";
    expect(markedText(highlight(emoji, [span(1, 4)]))).toEqual(["😀b😀"]);
  });

  it("whole-resource spans mark the full text", () => {
    expect(highlight("abc", wholeSpan("abc"))).toEqual([{ text: "abc", marked: true, keys: [] }]);
  });

  it("always joins back to the original text", () => {
    const spans = [span(0, 1), span(3, 6), span(5, 9), span(9, 10)];
    expect(joined(highlight(text, spans))).toBe(text);
  });
});

describe("highlight over every recorded citation", () => {
  const scored = allCases().filter((c) => c.scored);

  it("covers the 11 scored cases", () => {
    expect(scored).toHaveLength(readIndex().summary.cases);
    expect(scored.length).toBeGreaterThan(0);
  });

  it("marks, for each located citation, exactly its quote (up to whitespace)", () => {
    let checked = 0;
    for (const c of scored) {
      const byChunk = new Map(c.chart.map((k) => [k.chunk_id, k]));
      for (const v of c.result?.verdicts ?? []) {
        for (const [i, cite] of v.citations.entries()) {
          if (!cite.located) continue;
          const chunk = byChunk.get(cite.chunk_id);
          expect(chunk, `${c.id}/${v.criterion}: chunk ${cite.chunk_id} is in the chart`).toBeDefined();
          if (!chunk) continue;
          const alone = highlight(chunk.text, [{ ...cite, key: i }]);
          const marked = markedText(alone);
          expect(marked, `${c.id}/${v.criterion} citation ${i}`).toHaveLength(1);
          expect(normalizeWhitespace(marked[0] ?? "")).toBe(normalizeWhitespace(cite.quote));
          expect(joined(alone)).toBe(chunk.text);
          checked += 1;
        }
      }
    }
    expect(checked).toBeGreaterThan(200);
  });

  it("keeps every quote inside the marked text when a verdict's citations on a chunk merge", () => {
    for (const c of scored) {
      const byChunk = new Map(c.chart.map((k) => [k.chunk_id, k]));
      for (const v of c.result?.verdicts ?? []) {
        const spans = v.citations.map((cite, i) => ({ ...cite, key: i }));
        for (const chunkId of new Set(spans.map((s) => s.chunk_id))) {
          const chunk = byChunk.get(chunkId);
          if (!chunk) continue;
          const own = spans.filter((s) => s.chunk_id === chunkId);
          const segments = highlight(chunk.text, own);
          for (const s of own) {
            const segment = segments.find((g) => g.marked && g.keys.includes(s.key));
            expect(normalizeWhitespace(segment?.text ?? "")).toContain(normalizeWhitespace(s.quote));
          }
        }
      }
    }
  });
});
