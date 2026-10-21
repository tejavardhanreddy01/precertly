import { DECIDED_BY_TEXT, humanize, outcomeText, shownOutcome, stageTitle } from "../lib/labels";
import { href } from "../lib/router";
import { isInvalidated } from "../lib/verdicts";
import type { CaseCriterion, ExpectedLabel, Verdict } from "../lib/types";
import { IconEye } from "./icons";
import { Pill } from "./Pill";
import { CorrectMark, FalseApprovalMarker } from "./ui";

export const INVALIDATED_TEXT = "The model's citation failed verification, so the verdict became insufficient.";

const evidenceLink = "font-medium text-accent underline underline-offset-2";

function ModelDifference({ verdict }: { verdict: Verdict }) {
  const final = shownOutcome(verdict);
  if (verdict.model_outcome === final) return null;
  const model = outcomeText(verdict.model_outcome);
  let why: string;
  if (verdict.invalidated) {
    why = "Verification changed it (see below).";
  } else if (verdict.decided_by !== "model") {
    why = "A deterministic rule changed it. Rules are code, not model output.";
  } else {
    why = "The recorded outcome differs from the model's raw answer.";
  }
  return (
    <p data-testid="model-outcome" className="text-sm">
      The model's own answer was <strong>{model}</strong>. {why}
    </p>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h4 className="text-sm font-semibold text-ink">{title}</h4>
      <div className="mt-1">{children}</div>
    </section>
  );
}

/** The recorded verdict for one criterion of a scored case, shown as recorded. */
export function VerdictPanel({
  caseId,
  criterion,
  verdict,
  label,
  tour,
  onShowEvidence,
}: {
  caseId: string;
  criterion: CaseCriterion;
  verdict: Verdict;
  label: ExpectedLabel | undefined;
  tour: string | undefined;
  /** Called when a link sends the reader to the chart, so a narrow screen can switch tabs. */
  onShowEvidence: () => void;
}) {
  const decided = DECIDED_BY_TEXT[verdict.decided_by];
  const final = shownOutcome(verdict);
  const link = (extra: Record<string, string>) => href(["cases", caseId], { c: verdict.criterion, tour, ...extra });
  return (
    <div className="space-y-4 text-sm wrap-anywhere" data-testid="verdict-panel">
      <div>
        <p className="text-base font-semibold leading-snug">{criterion.text}</p>
        <p className="mt-1 text-xs text-ink-3">
          <span className="font-mono">{criterion.criterion}</span>{" "}
          <span className="rounded-full border border-line-strong px-2 py-0.5">
            {stageTitle(criterion.evidence_stage)} stage
          </span>
        </p>
      </div>

      {verdict.false_approval ? (
        <FalseApprovalMarker>
          The recorded run said met, but the label for this criterion is {outcomeText(verdict.expected)}.
        </FalseApprovalMarker>
      ) : null}

      <section
        aria-labelledby="verdict-h"
        className={`rounded-lg border bg-surface p-4 ${verdict.false_approval ? "border-2 border-danger" : "border-line-strong"}`}
      >
        <h3 id="verdict-h" className="text-xs font-semibold text-ink-3">
          Recorded verdict
        </h3>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <span className="text-sm text-ink-2">Final outcome</span>
          <Pill state={final} large />
          {isInvalidated(verdict) ? <Pill state="invalidated" /> : null}
        </div>
        {final === "not_applicable" ? (
          <p className="mt-2 text-ink-3">The condition for this criterion does not hold; it counts as satisfied.</p>
        ) : null}
        <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-line pt-3">
          <span className="inline-flex items-center gap-1.5">
            Expected label <Pill state={verdict.expected} />
          </span>
          <CorrectMark
            correct={verdict.correct}
            what={verdict.correct ? "(matches the label)" : "(differs from the label)"}
          />
        </div>
        {label ? <p className="mt-2 text-ink-2">{label.fact}</p> : null}
      </section>

      <ModelDifference verdict={verdict} />

      <div>
        <p className="font-semibold">{decided.title}</p>
        <p className="text-ink-2">{decided.explanation}</p>
      </div>

      {isInvalidated(verdict) ? (
        <div
          className="rounded-md border border-invalidated-line bg-invalidated-bg p-3 text-invalidated-fg"
          data-testid="invalidated"
        >
          <p className="font-semibold">{INVALIDATED_TEXT}</p>
          {verdict.invalidated ? <p className="mt-1 break-words font-mono text-xs">{verdict.invalidated}</p> : null}
          {verdict.invalidation ? <p className="mt-1 text-xs">Kind: {humanize(verdict.invalidation)}</p> : null}
        </div>
      ) : null}

      <Section title="Recorded rationale">
        <p className="whitespace-pre-wrap">{verdict.rationale}</p>
        {verdict.invalidated ? (
          <p className="mt-1 text-xs text-ink-3">
            This is the model's rationale as recorded. Its citation did not verify, so it is not evidence.
          </p>
        ) : null}
      </Section>

      {verdict.computed.length > 0 ? (
        <Section title="Computed facts (as recorded)">
          <ul className="space-y-2">
            {verdict.computed.map((fact, i) => (
              <li key={i} className="rounded-md border border-line bg-surface p-3" data-testid="computed-fact">
                <p>{fact.statement}</p>
                <p className="mt-1 text-xs text-ink-3">
                  {fact.rule ? <>Rule {fact.rule}</> : null}
                  {fact.rules_version ? <> (rules version {fact.rules_version})</> : null}
                  {fact.branch ? <> · branch {fact.branch}</> : null} ·{" "}
                  {fact.cited_by_model ? "cited by the model" : "not cited by the model"}
                </p>
                {fact.inputs.length > 0 ? (
                  <p className="mt-1 text-xs text-ink-3">
                    Inputs:{" "}
                    {fact.inputs.map((input, j) => (
                      <span key={input}>
                        {j > 0 ? ", " : ""}
                        <a
                          className={`break-all font-mono ${evidenceLink}`}
                          href={link({ res: input })}
                          onClick={onShowEvidence}
                        >
                          {input}
                        </a>
                      </span>
                    ))}
                  </p>
                ) : null}
              </li>
            ))}
          </ul>
        </Section>
      ) : null}

      <Section title={`Citations (${verdict.citations.length})`}>
        {verdict.citations.length === 0 ? (
          <p className="text-ink-3">
            {verdict.invalidated
              ? "None are kept: the model's citation failed verification."
              : verdict.computed.length > 0
                ? "None from the chart: the computed facts above are the evidence."
                : "None."}
          </p>
        ) : (
          <ol className="space-y-2">
            {verdict.citations.map((c, i) => (
              <li key={i} className="rounded-md border border-line bg-surface p-3" data-testid="citation">
                <blockquote className="whitespace-pre-wrap border-l-4 border-mark pl-3 font-mono text-[0.8125rem] leading-relaxed">
                  {c.quote}
                </blockquote>
                <p className="mt-2 text-xs text-ink-3">
                  <span className="break-all font-mono">{c.chunk_id}</span>, characters {c.char_start} to {c.char_end}.{" "}
                  {c.located ? (
                    <a
                      className={`inline-flex items-center gap-1 ${evidenceLink}`}
                      href={link({ cite: String(i) })}
                      onClick={onShowEvidence}
                    >
                      <IconEye size={12} />
                      Show in the chart
                    </a>
                  ) : (
                    "The quote could not be located in the chart."
                  )}
                </p>
              </li>
            ))}
          </ol>
        )}
      </Section>
    </div>
  );
}
