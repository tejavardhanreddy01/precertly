import { useMemo, useState } from "react";
import { TOUR } from "../content/tour";
import { useIndex } from "../lib/index-context";
import { STAGE_TEXT, stageTitle } from "../lib/labels";
import { wholeSpan, type Span } from "../lib/highlight";
import { href, replaceHash, type Route } from "../lib/router";
import { shownView } from "../lib/shown";
import type { CaseCriterion, CaseFile, CaseRow, ExpectedLabel, Verdict } from "../lib/types";
import { useMediaQuery, WIDE_QUERY } from "../lib/useMediaQuery";
import { listedState } from "../lib/verdicts";
import { ChartViewer, type Target } from "./ChartViewer";
import { LabelPanel } from "./LabelPanel";
import { Pill } from "./Pill";
import { RequestPanel } from "./RequestPanel";
import { SegmentedTabs } from "./SegmentedTabs";
import { SummaryBar } from "./SummaryBar";
import { TourBar } from "./TourBar";
import { VerdictPanel } from "./VerdictPanel";

type Tab = "criteria" | "evidence" | "verdict";

const TABS = [
  { id: "criteria", label: "Criteria", controls: "pane-criteria" },
  { id: "evidence", label: "Evidence", controls: "pane-evidence" },
  { id: "verdict", label: "Verdict", controls: "pane-verdict" },
] as const;

function CriterionRow({
  file,
  criterion,
  verdict,
  label,
  selected,
  tour,
  onSelect,
}: {
  file: CaseFile;
  criterion: CaseCriterion;
  verdict: Verdict | undefined;
  label: ExpectedLabel | undefined;
  selected: boolean;
  tour: string | undefined;
  onSelect: () => void;
}) {
  const falseApproval = verdict?.false_approval === true;
  const select = () => {
    replaceHash(href(["cases", file.id], { c: selected ? undefined : criterion.criterion, tour }));
    if (!selected) onSelect();
  };
  return (
    <tr
      data-criterion={criterion.criterion}
      data-verdict-row={verdict ? "" : undefined}
      data-false-approval-row={falseApproval ? "" : undefined}
      className={`relative border-b border-line ${
        falseApproval
          ? "bg-danger-tint shadow-[inset_5px_0_0_var(--color-danger)]"
          : selected
            ? "bg-accent-soft"
            : "bg-surface hover:bg-sunken"
      } ${selected ? "outline-2 -outline-offset-2 outline-accent" : ""}`}
    >
      <td className={`w-px py-2.5 pr-2 align-middle ${falseApproval ? "pl-4" : "pl-3"}`}>
        <span className="flex flex-col items-start gap-1">
          {verdict ? (
            <Pill state={listedState(verdict)} />
          ) : label ? (
            <Pill state={label.outcome} prefix="Label:" />
          ) : null}
          {falseApproval ? <Pill state="false_approval" /> : null}
          {verdict && !verdict.correct && !falseApproval ? (
            <span className="text-xs font-semibold text-notmet-fg">Incorrect</span>
          ) : null}
        </span>
      </td>
      <td className="w-full max-w-0 py-2.5 pr-3 align-middle">
        <button
          type="button"
          aria-pressed={selected}
          onClick={select}
          title={criterion.text}
          className={`block w-full truncate text-left text-sm after:absolute after:inset-0 ${falseApproval ? "font-semibold" : ""}`}
        >
          {criterion.text}
          <span className="sr-only"> ({criterion.criterion})</span>
        </button>
      </td>
    </tr>
  );
}

function NotJudged({ criteria }: { criteria: CaseCriterion[] }) {
  const stages = [...new Set(criteria.map((c) => c.evidence_stage))];
  return (
    <section aria-labelledby="notjudged-h" className="border-t border-line-strong p-3" data-testid="not-judged">
      <h3 id="notjudged-h" className="text-sm font-semibold">
        Not judged by the copilot
      </h3>
      <p className="text-xs text-ink-3">
        The copilot judges only what the chart or the request can show. These criteria go on the reviewer checklist,
        and a person confirms them.
      </p>
      {stages.map((stage) => (
        <div key={stage} className="mt-2 rounded-md border border-line bg-canvas p-2">
          <h4 className="text-xs font-semibold">{stageTitle(stage)} stage</h4>
          <p className="text-xs text-ink-3">
            Each of these {STAGE_TEXT[stage]?.meaning ?? `belongs to the ${stage} stage`}, so the copilot does not
            judge it.
          </p>
          <ul className="mt-1 list-inside list-disc text-xs">
            {criteria
              .filter((c) => c.evidence_stage === stage)
              .map((c) => (
                <li key={c.criterion} data-testid="checklist-item">
                  {c.text} <span className="break-all font-mono text-ink-3">({c.criterion})</span>
                </li>
              ))}
          </ul>
        </div>
      ))}
    </section>
  );
}

/** Chunks to highlight for the selected criterion, keyed by chunk id. */
function buildHighlights(
  file: CaseFile,
  verdict: Verdict | undefined,
  label: ExpectedLabel | undefined,
  resource: string | null,
): Map<string, Span[]> {
  const out = new Map<string, Span[]>();
  const add = (chunkId: string, span: Span) => out.set(chunkId, [...(out.get(chunkId) ?? []), span]);
  if (verdict) {
    verdict.citations.forEach((c, i) => {
      add(c.chunk_id, { char_start: c.char_start, char_end: c.char_end, located: c.located, key: i });
    });
  } else if (label) {
    // An unscored case has labeled resources, not quotes: the whole resource is marked.
    const wanted = new Set(label.evidence);
    for (const chunk of file.chart) {
      if (wanted.has(chunk.chunk_id) || wanted.has(`${chunk.resource_type}/${chunk.resource_id}`)) {
        for (const span of wholeSpan(chunk.text)) add(chunk.chunk_id, span);
      }
    }
  }
  if (resource) {
    for (const chunk of file.chart) {
      const id = `${chunk.resource_type}/${chunk.resource_id}`;
      if ((id === resource || chunk.chunk_id === resource) && !out.has(chunk.chunk_id)) {
        for (const span of wholeSpan(chunk.text)) add(chunk.chunk_id, span);
      }
    }
  }
  return out;
}

function findTarget(
  file: CaseFile,
  verdict: Verdict | undefined,
  highlights: Map<string, Span[]>,
  cite: number | null,
  resource: string | null,
): Target {
  if (verdict && cite !== null) {
    const c = verdict.citations[cite];
    if (c?.located) return { chunkId: c.chunk_id, citeKey: cite };
  }
  if (resource) {
    const chunk = file.chart.find(
      (k) => `${k.resource_type}/${k.resource_id}` === resource || k.chunk_id === resource,
    );
    if (chunk) return { chunkId: chunk.chunk_id };
  }
  if (verdict) {
    const first = verdict.citations.findIndex((c) => c.located);
    const c = verdict.citations[first];
    if (c) return { chunkId: c.chunk_id, citeKey: first };
    return {};
  }
  // Unscored: the first chunk that carries a highlight, in chart order.
  const chunk = file.chart.find((k) => highlights.has(k.chunk_id));
  return chunk ? { chunkId: chunk.chunk_id } : {};
}

function intParam(value: string | null): number | null {
  return value !== null && /^\d+$/.test(value) ? Number(value) : null;
}

const paneHeading = "text-sm font-semibold text-ink max-lg:sr-only";

export function CaseView({ file, row, route }: { file: CaseFile; row: CaseRow; route: Route }) {
  const selectedId = route.query.get("c");
  const cite = intParam(route.query.get("cite"));
  const resource = route.query.get("res");
  const tourParam = route.query.get("tour") ?? undefined;
  const tourStep = intParam(tourParam ?? null);

  const verdicts = useMemo(
    () => new Map((file.result?.verdicts ?? []).map((v) => [v.criterion, v])),
    [file],
  );
  const labels = useMemo(() => new Map(file.expected.map((e) => [e.criterion, e])), [file]);
  const judged = file.criteria.filter((c) => c.judged);
  const notJudged = file.criteria.filter((c) => !c.judged);

  const selected = judged.find((c) => c.criterion === selectedId);
  const verdict = selected ? verdicts.get(selected.criterion) : undefined;
  const label = selected ? labels.get(selected.criterion) : undefined;

  const activeResource = selected ? resource : null;
  const highlights = buildHighlights(file, verdict, label, activeResource);
  const target = findTarget(file, verdict, highlights, cite, activeResource);
  const topK = useIndex().run.retrieval.top_k;
  const shown = shownView(verdict, file.chart, topK);

  // Wide: three panes side by side. Narrow: one pane at a time behind a tab control. A link that
  // opens with a citation or a resource lands on Evidence; one with a criterion on Verdict.
  const wide = useMediaQuery(WIDE_QUERY);
  const [tab, setTab] = useState<Tab>(cite !== null || (resource && selected) ? "evidence" : selected ? "verdict" : "criteria");
  const show = (pane: Tab) => wide || tab === pane;
  const paneClass = (pane: Tab) => (show(pane) ? "" : "hidden");
  const toEvidence = () => setTab("evidence");

  return (
    <article className="flex h-full min-h-0 flex-col">
      <SummaryBar file={file} row={row} />

      {wide ? null : (
        <div className="shrink-0 border-b border-line bg-surface px-3 py-2">
          <SegmentedTabs tabs={TABS} value={tab} onChange={setTab} label="Case sections" />
        </div>
      )}

      <div
        className={
          wide
            ? "grid min-h-0 flex-1 grid-cols-[17.5rem_minmax(0,1fr)_22rem] xl:grid-cols-[22rem_minmax(0,1fr)_27rem]"
            : "flex min-h-0 flex-1 flex-col"
        }
      >
        <section
          id="pane-criteria"
          aria-labelledby="criteria-h"
          tabIndex={0}
          className={`min-h-0 min-w-0 overflow-y-auto border-r border-line bg-surface ${paneClass("criteria")}`}
        >
          <div className="sticky top-0 z-10 border-b border-line bg-surface px-3 py-2">
            <h2 id="criteria-h" className={paneHeading}>
              Criteria <span className="font-normal text-ink-3">({judged.length} judged)</span>
            </h2>
            <p className="text-xs text-ink-3">
              {file.scored
                ? "Select a criterion to see its recorded verdict and the cited text in the chart."
                : "Select a criterion to see its label and the labeled evidence in the chart."}
            </p>
          </div>
          <table className="w-full border-collapse">
            <caption className="sr-only">Judged criteria and their status</caption>
            <thead className="sr-only">
              <tr>
                <th scope="col">Status</th>
                <th scope="col">Criterion</th>
              </tr>
            </thead>
            <tbody>
              {judged.map((c) => (
                <CriterionRow
                  key={c.criterion}
                  file={file}
                  criterion={c}
                  verdict={verdicts.get(c.criterion)}
                  label={labels.get(c.criterion)}
                  selected={c.criterion === selected?.criterion}
                  tour={tourParam}
                  onSelect={() => setTab("verdict")}
                />
              ))}
            </tbody>
          </table>
          {notJudged.length > 0 ? <NotJudged criteria={notJudged} /> : null}
        </section>

        <section
          id="pane-evidence"
          aria-labelledby="chart-h"
          className={`min-h-0 min-w-0 flex-col border-r border-line bg-surface ${show("evidence") ? "flex" : "hidden"} ${wide ? "" : "flex-1"}`}
        >
          <div className="shrink-0 px-3 py-2">
            <h2 id="chart-h" className={paneHeading}>
              Evidence
            </h2>
            <p className="text-xs text-ink-3">
              Synthetic chart text, grouped by resource.{" "}
              {selected ? "Highlighted text is cited or labeled for the selected criterion." : ""}
            </p>
            {shown ? (
              <p className="mt-1 text-xs text-ink-2" data-testid="shown-line">
                {shown.line}
              </p>
            ) : null}
          </div>
          <ChartViewer
            key={`${selected?.criterion ?? ""}|${resource ?? ""}`}
            chart={file.chart}
            highlights={highlights}
            target={target}
            requestKey={cite === null ? "" : String(cite)}
            shown={shown?.kind === "badges" ? shown.shown : null}
            active={show("evidence")}
          />
        </section>

        <section
          id="pane-verdict"
          aria-labelledby="verdict-pane-h"
          tabIndex={0}
          className={`min-h-0 min-w-0 overflow-y-auto bg-canvas p-3 ${paneClass("verdict")}`}
        >
          <h2 id="verdict-pane-h" className={`${paneHeading} mb-3`}>
            Verdict
          </h2>
          {selected && verdict ? (
            <VerdictPanel
              caseId={file.id}
              criterion={selected}
              verdict={verdict}
              label={label}
              tour={tourParam}
              onShowEvidence={toEvidence}
            />
          ) : selected ? (
            <LabelPanel
              caseId={file.id}
              criterion={selected}
              label={label}
              tour={tourParam}
              onShowEvidence={toEvidence}
            />
          ) : (
            <CaseOverview file={file} row={row} />
          )}
        </section>
      </div>

      {tourStep !== null && tourStep >= 1 && tourStep <= TOUR.length ? (
        <TourBar step={tourStep} onShowEvidence={toEvidence} />
      ) : null}
    </article>
  );
}

/** What the verdict pane shows before a criterion is selected. */
function CaseOverview({ file, row }: { file: CaseFile; row: CaseRow }) {
  return (
    <div className="space-y-4 text-sm">
      <section className="rounded-lg border border-line bg-surface p-4">
        <h3 className="text-sm font-semibold">About this case</h3>
        <p className="mt-1 text-ink-2">{file.description}</p>
        <p className="mt-2 text-ink-3">
          {row.procedure}
          {file.tags.length > 0 ? `, tags: ${file.tags.join(", ")}` : ""}
        </p>
      </section>
      <p className="rounded-lg border border-line bg-surface p-4 text-ink-2">
        {file.scored
          ? "Select a criterion to see its recorded verdict. The overall result is computed by code from the criterion verdicts and the policy's variant logic; the model does not decide it."
          : "This case has expected labels only. No model has judged it, so there are no verdicts, rationales or citations to show."}
      </p>
      <RequestPanel request={file.request} />
    </div>
  );
}
