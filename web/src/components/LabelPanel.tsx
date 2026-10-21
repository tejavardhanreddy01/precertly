import { STAGE_TEXT, stageTitle } from "../lib/labels";
import { href } from "../lib/router";
import type { CaseCriterion, ExpectedLabel } from "../lib/types";
import { Pill } from "./Pill";

/** The expected label of one criterion of a case the model has not scored. It never shows a
 * verdict: none exists. */
export function LabelPanel({
  caseId,
  criterion,
  label,
  tour,
  onShowEvidence,
}: {
  caseId: string;
  criterion: CaseCriterion;
  label: ExpectedLabel | undefined;
  tour: string | undefined;
  onShowEvidence: () => void;
}) {
  return (
    <div className="space-y-4 text-sm wrap-anywhere" data-testid="label-panel">
      <div>
        <p className="text-base font-semibold leading-snug">{criterion.text}</p>
        <p className="mt-1 text-xs text-ink-3">
          <span className="font-mono">{criterion.criterion}</span>{" "}
          <span className="rounded-full border border-line-strong px-2 py-0.5">
            {stageTitle(criterion.evidence_stage)} stage
          </span>
        </p>
      </div>
      <section className="rounded-lg border border-line-strong bg-surface p-4">
        <h3 className="text-base font-semibold">Label (not yet scored by the model)</h3>
        {label ? (
          <div className="mt-2 space-y-3">
            <p className="flex flex-wrap items-center gap-2">
              <span>Labeled outcome</span>
              <Pill state={label.outcome} large />
            </p>
            <p>
              <span className="font-semibold">Labeled fact. </span>
              {label.fact}
            </p>
            <div>
              <p className="font-semibold">Labeled evidence resources ({label.evidence.length})</p>
              {label.evidence.length === 0 ? (
                <p className="text-ink-3">None listed.</p>
              ) : (
                <ul className="mt-1 list-inside list-disc">
                  {label.evidence.map((e) => (
                    <li key={e} className="break-all">
                      <a
                        className="font-mono text-xs font-medium text-accent underline underline-offset-2"
                        href={href(["cases", caseId], { c: criterion.criterion, tour, res: e })}
                        onClick={onShowEvidence}
                      >
                        {e}
                      </a>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        ) : (
          <p className="mt-2">
            This criterion has no expected label in the case file
            {criterion.judged
              ? "."
              : `, and the copilot does not judge ${STAGE_TEXT[criterion.evidence_stage]?.title.toLowerCase() ?? criterion.evidence_stage} criteria.`}
          </p>
        )}
      </section>
      <p className="text-xs text-ink-3">
        These are the case author's labels, from the case file. No model has judged this case, so there is no verdict,
        rationale or citation to show.
      </p>
    </div>
  );
}
