/** A citation's span inside one chunk's text. Offsets count Unicode code points, as the
 * Python export wrote them, so text outside the Basic Multilingual Plane cannot shift them. */
export interface Span {
  char_start: number;
  char_end: number;
  located: boolean;
  /** Which citation this is (its index in the verdict), carried to the segment it falls in. */
  key?: number;
}

export interface Segment {
  text: string;
  marked: boolean;
  /** The keys of the citations merged into this segment; empty when not marked. */
  keys: number[];
}

/**
 * Splits `text` into marked and unmarked segments for the given citations. Citations that are
 * not located are ignored, spans are clamped to the text, and spans that overlap or touch are
 * merged into one segment. The segments always join back to `text`.
 */
export function highlight(text: string, spans: readonly Span[]): Segment[] {
  if (text === "") return [];
  const chars = Array.from(text);
  const length = chars.length;

  const usable = spans
    .filter((s) => s.located)
    .map((s) => ({
      start: Math.max(0, Math.min(length, s.char_start)),
      end: Math.max(0, Math.min(length, s.char_end)),
      key: s.key,
    }))
    .filter((s) => s.end > s.start)
    .sort((a, b) => a.start - b.start || a.end - b.end);

  const merged: { start: number; end: number; keys: number[] }[] = [];
  for (const s of usable) {
    const last = merged[merged.length - 1];
    const keys = s.key === undefined ? [] : [s.key];
    if (last && s.start <= last.end) {
      last.end = Math.max(last.end, s.end);
      for (const k of keys) if (!last.keys.includes(k)) last.keys.push(k);
    } else {
      merged.push({ start: s.start, end: s.end, keys });
    }
  }

  const segments: Segment[] = [];
  let cursor = 0;
  for (const m of merged) {
    if (m.start > cursor) {
      segments.push({ text: chars.slice(cursor, m.start).join(""), marked: false, keys: [] });
    }
    segments.push({ text: chars.slice(m.start, m.end).join(""), marked: true, keys: m.keys });
    cursor = m.end;
  }
  if (cursor < length) {
    segments.push({ text: chars.slice(cursor).join(""), marked: false, keys: [] });
  }
  return segments;
}

/** Spans covering the whole of each text, for highlighting entire resources. */
export function wholeSpan(text: string): Span[] {
  return [{ char_start: 0, char_end: Array.from(text).length, located: true }];
}

/** Collapses runs of whitespace, the way the verifier compares a quote with its chunk. */
export function normalizeWhitespace(text: string): string {
  return text.replace(/\s+/g, " ").trim();
}
