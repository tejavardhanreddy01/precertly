/** Every state a pill can show. Each has its own words, its own icon silhouette and its own colors. */
export type PillState =
  | "met"
  | "not_met"
  | "insufficient"
  | "invalidated"
  | "false_approval"
  | "not_applicable";

export const PILL_TEXT: Record<PillState, string> = {
  met: "Met",
  not_met: "Not met",
  insufficient: "Insufficient",
  invalidated: "Invalidated",
  false_approval: "False approval",
  not_applicable: "Not applicable",
};
