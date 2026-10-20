import { STAGE_TEXT } from "../lib/labels";
import { href } from "../lib/router";
import type { CaseCriterion, ExpectedLabel } from "../lib/types";
import { OutcomeBadge } from "./ui";

/** The expected label of one criterion of a case the model has not scored. It never shows a
 * verdict: none exists. */
export function LabelPanel({
  caseId,
  criterion,
  label,
  tour,
}: {
  caseId: string;
  criterion: CaseCriterion;
  label: ExpectedLabel | undefined;
  tour: string | undefined;
}) {
  return (
    <div className="mt-2 space-y-2 rounded border border-slate-300 bg-slate-50 p-3 text-sm" data-testid="label-panel">
      <h3 className="text-base font-semibold">Label (not yet scored by the model)</h3>
      {label ? (
        <>
          <p className="flex flex-wrap items-center gap-2">
            <span>Labeled outcome</span>
            <OutcomeBadge outcome={label.outcome} />
          </p>
          <p>
            <span className="font-semibold">Labeled fact. </span>
            {label.fact}
          </p>
          <div>
            <p className="font-semibold">Labeled evidence resources ({label.evidence.length})</p>
            {label.evidence.length === 0 ? (
              <p className="text-slate-700">None listed.</p>
            ) : (
              <ul className="mt-1 list-inside list-disc">
                {label.evidence.map((e) => (
                  <li key={e} className="break-all">
                    <a
                      className="underline"
                      href={href(["cases", caseId], { c: criterion.criterion, tour, res: e })}
                    >
                      {e}
                    </a>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </>
      ) : (
        <p>
          This criterion has no expected label in the case file
          {criterion.judged ? "." : `, and the copilot does not judge ${STAGE_TEXT[criterion.evidence_stage]?.title.toLowerCase() ?? criterion.evidence_stage} criteria.`}
        </p>
      )}
      <p className="text-xs text-slate-700">
        These are the case author's labels, from the case file. No model has judged this case, so there is no
        verdict, rationale or citation to show.
      </p>
    </div>
  );
}
