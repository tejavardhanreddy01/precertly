import type { Verdict } from "./types";

/** What is shown for an outcome. "not_applicable" is a label value and a displayed state; the
 * recorded verdict of a criterion whose condition does not hold carries outcome "met" with
 * applicable false, because not applicable counts as satisfied. */
export type Shown = "met" | "not_met" | "insufficient" | "not_applicable";

export const OUTCOME_TEXT: Record<Shown, string> = {
  met: "Met",
  not_met: "Not met",
  insufficient: "Insufficient",
  not_applicable: "Not applicable",
};

export function outcomeText(outcome: string): string {
  return OUTCOME_TEXT[outcome as Shown] ?? outcome;
}

/** The outcome as displayed for a recorded verdict. */
export function shownOutcome(v: Pick<Verdict, "outcome" | "applicable">): Shown {
  return v.applicable ? v.outcome : "not_applicable";
}

export const DECIDED_BY_TEXT: Record<Verdict["decided_by"], { title: string; explanation: string }> = {
  model: {
    title: "Decided by the model",
    explanation:
      "The model read the retrieved chart text and gave this answer. Its quotes were then checked against the chart.",
  },
  rule: {
    title: "Decided by a rule",
    explanation:
      "A rule is deterministic code that reads coded chart data, such as pain scores, and computes the answer. The model did not decide this verdict.",
  },
  "rule+model": {
    title: "Decided by a rule and the model together",
    explanation:
      "A rule is deterministic code. It computed a fact from coded chart data, and the model's answer rests on that fact plus any text it cited.",
  },
};

export const STAGE_TEXT: Record<string, { title: string; meaning: string }> = {
  chart: { title: "Chart", meaning: "can be found in the patient's chart" },
  request: { title: "Request", meaning: "is stated in the request itself" },
  provider_attestation: {
    title: "Provider attestation",
    meaning: "is attested by the ordering provider",
  },
  post_service: {
    title: "After the service",
    meaning: "can only be confirmed once the service has been done",
  },
  payer: { title: "Payer", meaning: "is decided by the payer" },
};

export function stageTitle(stage: string): string {
  return STAGE_TEXT[stage]?.title ?? stage;
}

export const SET_TITLE: Record<string, string> = {
  facet: "Facet joint interventions",
  pap: "Positive airway pressure (PAP)",
  bariatric: "Bariatric surgery",
  "spinal-cord-stimulators": "Spinal cord stimulators",
};

/** The policy filter chips on the cases list. */
export const SET_SHORT: Record<string, string> = {
  facet: "Facet",
  pap: "PAP",
  bariatric: "Bariatric",
  "spinal-cord-stimulators": "SCS",
};

export const SET_ORDER = ["facet", "pap", "bariatric", "spinal-cord-stimulators"];

/** "pain_scale" → "pain scale". */
export function humanize(key: string): string {
  return key.replace(/_/g, " ");
}

export function shortHash(hash: string): string {
  return hash.slice(0, 12);
}
