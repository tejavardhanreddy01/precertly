import type { ChartChunk, Verdict } from "./types";

/** What the chart viewer says about what the model was given for the selected criterion. */
export type ShownView =
  /** Chunks the model was given; every other chunk is badged "not shown to the model". */
  | { kind: "badges"; shown: ReadonlySet<string>; line: string }
  /** A rule decided the criterion from coded data and no model call was made: nothing was shown. */
  | { kind: "rule_only"; line: string };

export const SHOWN_BADGE = "shown to the model";
export const NOT_SHOWN_BADGE = "not shown to the model";

/**
 * The shown-to-the-model view for a selected verdict, or null when there is nothing to say (no
 * verdict, as for an unscored case or no selection). The sentence names the top-k of the recorded run
 * only when this verdict's recorded chunks match it: top-k retrieved chunks plus every request field.
 */
export function shownView(verdict: Verdict | undefined, chart: readonly ChartChunk[], topK: number): ShownView | null {
  if (!verdict) return null;
  // A rule that decided alone never called the model, whatever retrieval ran beside it.
  if (verdict.decided_by === "rule") {
    return {
      kind: "rule_only",
      line: "A rule decided this criterion from coded chart data, so the model was not called and was shown no chart text. No chunk is badged.",
    };
  }
  const bySource = new Map(chart.map((k) => [k.chunk_id, k.source === "request"]));
  const shownIds = verdict.shown_chunk_ids.filter((id) => bySource.has(id));
  const requestShown = shownIds.filter((id) => bySource.get(id)).length;
  const retrievedShown = shownIds.length - requestShown;
  const requestTotal = chart.filter((k) => k.source === "request").length;
  const matches = retrievedShown === topK && requestShown === requestTotal;
  const line = matches
    ? `Badges show what the model was given for the selected criterion (the top ${topK} retrieved chunks plus the request fields).`
    : `Badges show what the model was given for the selected criterion (${retrievedShown} retrieved chunks and ${requestShown} of ${requestTotal} request fields; the run's retrieval limit was ${topK}).`;
  return { kind: "badges", shown: new Set(shownIds), line };
}
