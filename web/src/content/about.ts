import type { Index } from "../lib/types";

/**
 * Hand-written content of the About and method screen.
 *
 * Source: the "Release gates" and "Known limitations" sections of docs/QUALITY.md and the
 * "Status" section of CLAUDE.md, as of 2026-10-20 (experiment 1.1, both runs, 11 facet cases).
 * The gate rows copy docs/QUALITY.md word for word apart from Markdown formatting; about.test.ts
 * fails if a row stops matching the file, and fails if a number below that also exists in
 * index.json (the summary) differs from it. Update both when the recorded run or the gates change.
 */

/** The one public description of the hosting. Say "live" only inside it. */
export const PUBLIC_CLAIM = "public read-only site of recorded runs, always available; live environment on request";

export const WHAT_IT_IS: readonly string[] = [
  "Precertly is a prior-authorization copilot. It checks a coverage policy's criteria against a patient's FHIR chart, cites the evidence for every verdict, and drafts a request for a person to review.",
  `This is a ${PUBLIC_CLAIM}.`,
  "It replays results that were recorded earlier. The recorded pages make no model call and need no login, nothing runs when you open them, and every patient in them is synthetic.",
  "A separate, invite-only live environment exists. It is off unless the owner starts it.",
];

export const WHAT_IT_IS_NOT: readonly string[] = [
  "It never denies a request. Its outcomes are met, not met or insufficient, and a human reviewer decides.",
  "It is not clinically validated. The labels were written by a single annotator.",
  "It does not submit anything. A drafted request is never sent and carries no verdict.",
];

export interface PipelineStep {
  title: string;
  text: string;
}

export const PIPELINE: readonly PipelineStep[] = [
  {
    title: "Split the chart into chunks",
    text: "Each FHIR resource becomes text chunks with stable ids. Notes are verbatim slices, so a quote can be checked against exact character positions.",
  },
  {
    title: "Retrieve the relevant chunks",
    text: "For each criterion, a full-text search and a vector search over that case's chart are combined to pick the chunks the model will see.",
  },
  {
    title: "Let deterministic rules go first",
    text: "Where the chart has coded data, such as pain scores, plain code computes the fact or decides the criterion. The model never overrides a rule that decided.",
  },
  {
    title: "Ask the model, then check its quotes",
    text: "For the other criteria the model answers with quotes. A quote that is not found in the chunk it cites, or a met or not met answer with no citation, becomes insufficient.",
  },
  {
    title: "Combine in code and hand over to a person",
    text: "Code combines the criterion verdicts with the policy's logic into an overall result. A person reviews the result, and a draft request is prepared for that review only.",
  },
];

/** How a hand-written number is checked against index.json. */
export interface SummaryClaim {
  /** Text that appears verbatim in this file's content. */
  text: string;
  /** What index.json says it should be. */
  expected: (index: Index) => string;
}

export const percent = (fraction: number): string => `${Math.round(fraction * 1000 + 1e-9) / 10}%`;

export const SUMMARY_CLAIMS: readonly SummaryClaim[] = [
  { text: "96.2%", expected: (i) => percent(i.summary.criterion_accuracy) },
  { text: "74.4%", expected: (i) => percent(i.summary.citation_precision) },
  { text: "1 of 9", expected: (i) => `${i.summary.false_approvals} of ${i.summary.negative_labels}` },
  { text: "184 scored criteria", expected: (i) => `${i.summary.scored_criteria} scored criteria` },
  { text: "the 11 facet cases", expected: (i) => `the ${i.summary.cases} facet cases` },
  { text: "229 of 308", expected: (i) => `${i.summary.citations_in_label} of ${i.summary.citations}` },
  { text: "9 negative criterion labels", expected: (i) => `${i.summary.negative_labels} negative criterion labels` },
  {
    text: "18 of the 29",
    expected: (i) => `${i.cases.filter((c) => !c.scored).length} of the ${i.cases.length}`,
  },
];

/** Percentages in this file that are not in index.json, and why. */
export const OTHER_PERCENTAGES: Readonly<Record<string, string>> = {
  "95.7%": "criterion accuracy of the second run of experiment 1.1; index.json holds the first run",
  "74.8%": "citation precision of the second run of experiment 1.1",
  "2%": "the false-approval threshold",
  "90%": "the accuracy and citation-precision thresholds",
};

export type GateStatus = "passes" | "fails" | "reported";

export interface Gate {
  gate: string;
  threshold: string;
  current: string;
  status: GateStatus;
}

export const GATES_CAVEAT =
  "Current is experiment 1.1, both runs, on the 11 facet cases only (184 scored criteria). The other 18 of the 29 labeled cases have not been scored, so no gate below has a number for them.";

export const GATES: readonly Gate[] = [
  {
    gate: "False-approval rate on the eval set",
    threshold:
      "Under 2%, and zero on adversarial injection cases. The gate is only testable once the set has enough negative labels; until then, results report raw counts (for example 2 of 9), not a rate",
    current:
      "1 of 9 not_met or insufficient labels in each run: fails, though the gate is not meaningful at this sample size. No injection cases yet",
    status: "fails",
  },
  {
    gate: "Criterion accuracy",
    threshold: "Over 90%",
    current: "96.2% and 95.7%: passes",
    status: "passes",
  },
  {
    gate: "Citation precision",
    threshold: "Over 90%",
    current: "74.4% and 74.8%: fails",
    status: "fails",
  },
  {
    gate: "Verdict flips across repeated runs",
    threshold: "Reported; any flip on a not_met or insufficient label is investigated",
    current:
      "3 of 184 verdicts differ between the two recorded runs; the largest difference measured anywhere is 14 of 184",
    status: "reported",
  },
  {
    gate: "PAS conformance",
    threshold: "Zero validator errors on every draft; no warning outside the allowlist",
    current:
      "0 errors on 11 of 11 facet drafts; 383 warnings, all allowlisted (X12 terminology, base R4 identifier-type, narrative best practice)",
    status: "passes",
  },
  {
    gate: "CI",
    threshold:
      "Green, with database tests running, not skipped. Separate jobs: lint (ruff check, ruff format --check), typecheck (pyright, standard mode, on src/), test (alembic upgrade head and alembic check on the service database, evals/build_cases.py --check, pytest) and security (pip-audit)",
    current: "Green; database tests run; pyright reports no errors, with no baseline",
    status: "passes",
  },
];

export interface Limitation {
  title: string;
  text: string;
}

export const LIMITATIONS: readonly Limitation[] = [
  {
    title: "One annotator",
    text: "The expected labels were written by a single annotator and are not clinically validated. A blinded second annotation is planned, not done.",
  },
  {
    title: "Only the facet cases are scored",
    text: "Only the 11 facet cases have a recorded run. 18 of the 29 labeled cases (PAP, bariatric, spinal cord stimulator) show labels only, so every accuracy, citation-precision and false-approval number here is facet-only.",
  },
  {
    title: "One false approval, from a retrieval miss",
    text: "In facet-11 the model approved a criterion because the therapy-records note that contradicts the evaluation note was not among the chunks it was shown. This is the likely mechanism and is a retrieval question, not yet fixed. The facet set has only 9 negative criterion labels, so one false approval moves the rate by 11 points.",
  },
  {
    title: "Citation precision fails its gate",
    text: "Citation precision is the share of the model's citations that land on a resource the label lists as evidence: 229 of 308 in the recorded run. A citation outside the label may still be relevant; the measure does not say. The verifier checks that a quote exists in the chunk it cites, not that it is the best evidence.",
  },
  {
    title: "Runs are not exactly repeatable",
    text: "At temperature 0, two runs on identical evidence are not identical: 3 of 184 verdicts differ between the two recorded runs; the largest difference measured anywhere is 14 of 184. Small differences are noise.",
  },
  {
    title: "Synthetic data",
    text: "The charts are synthetic and cleaner than real ones. Draft requests are not ready to send.",
  },
];

export const DOCS_QUALITY_URL = "https://github.com/tejavardhanreddy01/precertly/blob/main/docs/QUALITY.md";
export const NOTES_URL = "https://github.com/tejavardhanreddy01/precertly/blob/main/evals/results/NOTES.md";
