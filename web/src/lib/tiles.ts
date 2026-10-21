import { percent, type GateStatus } from "../content/about";
import type { RunSummary } from "./types";

// The gate each tile reports on, from docs/QUALITY.md (mirrored in content/about.ts).
const ACCURACY_GATE = 0.9;
const FALSE_APPROVAL_GATE = 0.02;

export interface Tile {
  key: string;
  label: string;
  value: string;
  detail: string;
  gate: { text: string; status: GateStatus } | null;
  alarm?: boolean;
}

/** The four headline numbers, every figure read from index.json's summary. */
export function tiles(s: RunSummary): Tile[] {
  return [
    {
      key: "accuracy",
      label: "Criterion accuracy",
      value: percent(s.criterion_accuracy),
      detail: `${s.criteria_correct} of ${s.scored_criteria} verdicts match the label`,
      gate: { text: "Gate: over 90%", status: s.criterion_accuracy > ACCURACY_GATE ? "passes" : "fails" },
    },
    {
      key: "citations",
      label: "Citation precision",
      value: percent(s.citation_precision),
      detail: `${s.citations_in_label} of ${s.citations} citations land on a labeled resource`,
      gate: { text: "Gate: over 90%", status: s.citation_precision > ACCURACY_GATE ? "passes" : "fails" },
    },
    {
      key: "cases",
      label: "Cases correct overall",
      value: `${s.cases_correct} of ${s.cases}`,
      detail: "facet cases whose overall result matches the label",
      gate: null,
    },
    {
      key: "false-approvals",
      label: "False approvals",
      value: String(s.false_approvals),
      detail: `of ${s.negative_labels} criteria labeled not met or insufficient`,
      gate: {
        text: "Gate: under 2%",
        status: s.false_approval_rate < FALSE_APPROVAL_GATE ? "passes" : "fails",
      },
      alarm: s.false_approvals > 0,
    },
  ];
}
