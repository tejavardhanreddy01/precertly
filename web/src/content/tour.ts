import { href } from "../lib/router";

/**
 * The four-step tour. Each step points at one criterion of one recorded case; the test in
 * tour.test.ts asserts every claim a step makes against public/data, so a re-export that changes
 * the data cannot leave the tour saying something false.
 *
 * Choice rules (by case id, then by the order of verdicts in the case):
 *  1. "verified-citation": the first case whose overall result is correct and that has no
 *     invalidated verdict; its first verdict decided by the model that is met, applicable and
 *     has a located citation.
 *  2. "rule-computed": the first case with a verdict decided by a rule alone, with a computed
 *     fact, whose outcome is not met (a rule stating why a threshold was missed teaches more
 *     than one that passes).
 *  3. "false-approval": the verdict flagged false_approval (the run summary says there is one).
 *  4. "invalidated": the first verdict whose citation failed verification.
 */
export type TourKind = "verified-citation" | "rule-computed" | "false-approval" | "invalidated";

export interface TourStep {
  kind: TourKind;
  caseId: string;
  criterion: string;
  title: string;
  caption: string;
}

export const TOUR: readonly TourStep[] = [
  {
    kind: "verified-citation",
    caseId: "facet-03",
    criterion: "chronic-axial-pain-functional-deficit",
    title: "A verdict with checked evidence",
    caption:
      "The model said met and quoted two passages from the chart; both were found where it said they were, and they are highlighted on the right.",
  },
  {
    kind: "rule-computed",
    caseId: "facet-02",
    criterion: "two-mbbs-80pct-sustained-relief",
    title: "A rule, not the model",
    caption:
      "A deterministic rule computed the relief from coded pain scores: 86% after the first block but 71% after the second, below the 80% the policy states.",
  },
  {
    kind: "false-approval",
    caseId: "facet-11",
    criterion: "conservative-care-failed",
    title: "The mistake in the run",
    caption:
      "The one false approval: the model marked this met from a note claiming 12 weeks of physical therapy, while the therapy records, which it was not shown, say 2 of 12 sessions.",
  },
  {
    kind: "invalidated",
    caseId: "facet-01",
    criterion: "no-non-facet-pathology",
    title: "When a citation fails",
    caption:
      "The model said met, but its quote was not found in the text it cited, so the verdict became insufficient instead of an approval.",
  },
];

export function tourHref(step: number): string {
  const s = TOUR[step - 1];
  if (!s) throw new Error(`No tour step ${step}`);
  return href(["cases", s.caseId], { c: s.criterion, tour: String(step) });
}
