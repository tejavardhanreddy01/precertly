import { useLiveEnabled } from "../lib/live-feature";
import { href } from "../lib/router";
import { useIndex } from "../lib/index-context";
import { countVerdicts } from "../lib/verdicts";
import type { CaseFile, CaseRow } from "../lib/types";
import { IconClose } from "./icons";
import { Pill } from "./Pill";
import { RunStamp } from "./RunChip";
import { CorrectMark, LabelsOnlyBadge } from "./ui";

/**
 * The strip across the top of a case: which case, what the recorded run said, whether it was right,
 * and how the criteria came out. On a narrow screen it keeps the id, the overall result and the
 * Correct or Incorrect chip.
 */
export function SummaryBar({ file, row }: { file: CaseFile; row: CaseRow }) {
  const { policies } = useIndex();
  const live = useLiveEnabled();
  const policyTitle = policies.find((p) => p.id === file.policy)?.title ?? file.policy;
  const verdicts = file.result?.verdicts ?? [];
  const counts = countVerdicts(verdicts);
  const falseApproval = row.predicted_overall === "met" && row.expected_overall !== "met";
  const result = file.scored ? file.result : null;
  return (
    <section
      aria-label="Case summary"
      className="shrink-0 border-b border-line bg-surface px-3 py-2 sm:px-5"
      data-testid="summary-bar"
    >
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5">
        <h1 className="font-mono text-lg font-bold">{file.id}</h1>
        <a
          href={href(["policies", file.policy])}
          className="text-sm font-medium text-accent underline underline-offset-2 max-lg:hidden"
          data-testid="policy-link"
        >
          {policyTitle}
        </a>
        <span className="text-sm text-ink-3 max-lg:hidden">
          {file.variant}, {row.procedure}
        </span>
        <span className="flex items-center gap-2" data-testid="overall">
          <span className="text-xs text-ink-3 max-lg:hidden">Result</span>
          {result ? <Pill state={result.predicted_overall} /> : <LabelsOnlyBadge />}
          {falseApproval ? (
            <span data-false-approval>
              <Pill state="false_approval" />
            </span>
          ) : null}
        </span>
        {live ? (
          <a
            href={href(["live", file.id])}
            className="rounded-md border border-line-strong px-2 py-0.5 text-sm font-medium text-accent hover:bg-sunken"
            data-testid="run-live-link"
          >
            Run live
          </a>
        ) : null}
        <span className="flex items-center gap-2 max-lg:contents">
          <span className="flex items-center gap-2 max-lg:hidden">
            <span className="text-xs text-ink-3">Expected</span>
            <Pill state={file.expected_overall} />
          </span>
          {result ? <CorrectMark correct={result.overall_correct} /> : null}
        </span>
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1.5 max-lg:hidden">
        <div className="flex flex-wrap items-center gap-1.5" data-testid="counts">
          {result ? (
            <>
              <span className="mr-1 text-xs text-ink-3">{verdicts.length} criteria</span>
              <Pill state="met" count={counts.met} />
              <Pill state="not_met" count={counts.not_met} />
              <Pill state="insufficient" count={counts.insufficient} />
              <Pill state="invalidated" count={counts.invalidated} />
              {counts.not_applicable > 0 ? <Pill state="not_applicable" count={counts.not_applicable} /> : null}
              <span className="inline-flex items-center gap-1 rounded-full border border-line-strong bg-surface px-2 py-0.5 text-xs font-semibold text-ink">
                <IconClose size={12} />
                Wrong{" "}
                <span data-count="wrong" className="tabular-nums">
                  {counts.wrong}
                </span>
              </span>
            </>
          ) : (
            <span className="text-sm text-ink-3">
              Labels only: {file.expected.length} criteria labeled, none judged by a model.
            </span>
          )}
        </div>
        <div className="ml-auto">
          <RunStamp />
        </div>
      </div>
    </section>
  );
}
