import type { PillState } from "../components/Pill";
import { shownOutcome } from "./labels";
import type { Verdict } from "./types";

/** Whether verification overruled the model's citation for this verdict. */
export function isInvalidated(v: Pick<Verdict, "invalidated" | "invalidation">): boolean {
  return Boolean(v.invalidated || v.invalidation);
}

/**
 * The state a verdict is listed under. An invalidated verdict is final-outcome insufficient, but the
 * reader should see that verification caused it, so it gets its own state; the detail pane still
 * says insufficient.
 */
export function listedState(
  v: Pick<Verdict, "outcome" | "applicable" | "invalidated" | "invalidation">,
): Exclude<PillState, "false_approval"> {
  return isInvalidated(v) ? "invalidated" : shownOutcome(v);
}

export interface VerdictCounts {
  met: number;
  not_met: number;
  /** Final outcome insufficient and not caused by a failed citation. */
  insufficient: number;
  /** Final outcome insufficient because a citation failed verification. */
  invalidated: number;
  /** The condition for the criterion does not hold, so it counts as satisfied. */
  not_applicable: number;
  /** Verdicts that differ from the expected label. */
  wrong: number;
  /** Wrong verdicts that said met where the label is not met or insufficient. */
  false_approvals: number;
}

/** The counts shown in a case's summary bar. The first five states partition the verdicts. */
export function countVerdicts(verdicts: readonly Verdict[]): VerdictCounts {
  const counts: VerdictCounts = {
    met: 0,
    not_met: 0,
    insufficient: 0,
    invalidated: 0,
    not_applicable: 0,
    wrong: 0,
    false_approvals: 0,
  };
  for (const v of verdicts) {
    counts[listedState(v)]++;
    if (!v.correct) counts.wrong++;
    if (v.false_approval) counts.false_approvals++;
  }
  return counts;
}
