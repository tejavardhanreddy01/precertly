import { DECIDED_BY_TEXT, humanize, outcomeText, shownOutcome } from "../lib/labels";
import { href } from "../lib/router";
import type { ExpectedLabel, Verdict } from "../lib/types";
import { CorrectMark, FalseApprovalMarker, OutcomeBadge } from "./ui";

export const INVALIDATED_TEXT = "The model's citation failed verification, so the verdict became insufficient.";

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

/** The recorded verdict for one criterion of a scored case, shown as recorded. */
export function VerdictPanel({
  caseId,
  verdict,
  label,
  tour,
}: {
  caseId: string;
  verdict: Verdict;
  label: ExpectedLabel | undefined;
  tour: string | undefined;
}) {
  const decided = DECIDED_BY_TEXT[verdict.decided_by];
  const final = shownOutcome(verdict);
  const link = (extra: Record<string, string>) =>
    href(["cases", caseId], { c: verdict.criterion, tour, ...extra });
  return (
    <div className="mt-2 space-y-3 rounded border border-slate-300 bg-slate-50 p-3 text-sm" data-testid="verdict-panel">
      <h3 className="text-base font-semibold">Recorded verdict</h3>

      {verdict.false_approval ? (
        <FalseApprovalMarker>
          The recorded run said met, but the label for this criterion is {outcomeText(verdict.expected)}.
        </FalseApprovalMarker>
      ) : null}

      <p className="flex flex-wrap items-center gap-2">
        <span>Final outcome</span>
        <OutcomeBadge outcome={final} />
        {final === "not_applicable" ? (
          <span className="text-slate-700">The condition for this criterion does not hold; it counts as satisfied.</span>
        ) : null}
      </p>
      <ModelDifference verdict={verdict} />

      <div>
        <p className="font-semibold">{decided.title}</p>
        <p className="text-slate-800">{decided.explanation}</p>
      </div>

      {verdict.invalidated || verdict.invalidation ? (
        <div className="rounded border border-amber-800 bg-amber-50 p-2 text-amber-950" data-testid="invalidated">
          <p className="font-semibold">{INVALIDATED_TEXT}</p>
          {verdict.invalidated ? <p className="mt-1 break-words font-mono text-xs">{verdict.invalidated}</p> : null}
          {verdict.invalidation ? <p className="mt-1 text-xs">Kind: {humanize(verdict.invalidation)}</p> : null}
        </div>
      ) : null}

      <div>
        <p className="font-semibold">Recorded rationale</p>
        <p className="whitespace-pre-wrap text-slate-900">{verdict.rationale}</p>
        {verdict.invalidated ? (
          <p className="mt-1 text-xs text-slate-700">
            This is the model's rationale as recorded. Its citation did not verify, so it is not evidence.
          </p>
        ) : null}
      </div>

      {verdict.computed.length > 0 ? (
        <div>
          <p className="font-semibold">Computed facts (as recorded)</p>
          <ul className="mt-1 space-y-2">
            {verdict.computed.map((fact, i) => (
              <li key={i} className="rounded border border-slate-300 bg-white p-2" data-testid="computed-fact">
                <p>{fact.statement}</p>
                <p className="mt-1 text-xs text-slate-700">
                  {fact.rule ? <>Rule {fact.rule}</> : null}
                  {fact.rules_version ? <> (rules version {fact.rules_version})</> : null}
                  {fact.branch ? <> · branch {fact.branch}</> : null} ·{" "}
                  {fact.cited_by_model ? "cited by the model" : "not cited by the model"}
                </p>
                {fact.inputs.length > 0 ? (
                  <p className="mt-1 text-xs text-slate-700">
                    Inputs:{" "}
                    {fact.inputs.map((input, j) => (
                      <span key={input}>
                        {j > 0 ? ", " : ""}
                        <a className="break-all underline" href={link({ res: input })}>
                          {input}
                        </a>
                      </span>
                    ))}
                  </p>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <div>
        <p className="font-semibold">Citations ({verdict.citations.length})</p>
        {verdict.citations.length === 0 ? (
          <p className="text-slate-700">
            {verdict.invalidated
              ? "None are kept: the model's citation failed verification."
              : verdict.computed.length > 0
                ? "None from the chart: the computed facts above are the evidence."
                : "None."}
          </p>
        ) : (
          <ol className="mt-1 space-y-2">
            {verdict.citations.map((c, i) => (
              <li key={i} className="rounded border border-slate-300 bg-white p-2" data-testid="citation">
                <blockquote className="whitespace-pre-wrap border-l-4 border-slate-400 pl-2">{c.quote}</blockquote>
                <p className="mt-1 text-xs text-slate-700">
                  <span className="break-all">{c.chunk_id}</span>, characters {c.char_start} to {c.char_end}.{" "}
                  {c.located ? (
                    <a className="underline" href={link({ cite: String(i) })}>
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
      </div>

      <div className="rounded border border-slate-300 bg-white p-2">
        <p className="flex flex-wrap items-center gap-2 font-semibold">
          Expected label <OutcomeBadge outcome={verdict.expected} />
          <CorrectMark correct={verdict.correct} what={verdict.correct ? "(matches the label)" : "(differs from the label)"} />
        </p>
        {label ? <p className="mt-1 text-slate-900">{label.fact}</p> : null}
      </div>
    </div>
  );
}
