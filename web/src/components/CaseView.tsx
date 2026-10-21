import { useMemo } from "react";
import { TOUR } from "../content/tour";
import { shownOutcome, STAGE_TEXT, stageTitle } from "../lib/labels";
import { wholeSpan, type Span } from "../lib/highlight";
import { href, replaceHash, type Route } from "../lib/router";
import type { CaseCriterion, CaseFile, CaseRow, ExpectedLabel, Verdict } from "../lib/types";
import { ChartViewer, type Target } from "./ChartViewer";
import { LabelPanel } from "./LabelPanel";
import { RequestPanel } from "./RequestPanel";
import { TourBar } from "./TourBar";
import { CorrectMark, FalseApprovalMarker, LabelsOnlyBadge, OutcomeBadge } from "./ui";
import { VerdictPanel } from "./VerdictPanel";

function OverallPanel({ file, row }: { file: CaseFile; row: CaseRow }) {
  const falseApproval = row.predicted_overall === "met" && row.expected_overall !== "met";
  return (
    <section aria-labelledby="overall-h" className="rounded border border-slate-300 bg-white p-3">
      <h2 id="overall-h" className="text-base font-semibold">
        Overall
      </h2>
      <p className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
        <span className="flex items-center gap-1">
          Expected <OutcomeBadge outcome={file.expected_overall} />
        </span>
        {file.scored && file.result ? (
          <span className="flex flex-wrap items-center gap-1">
            Recorded <OutcomeBadge outcome={file.result.predicted_overall} />
            <CorrectMark correct={file.result.overall_correct} />
          </span>
        ) : (
          <LabelsOnlyBadge />
        )}
      </p>
      {falseApproval ? (
        <div className="mt-2">
          <FalseApprovalMarker>
            The recorded run said met for a request labeled {file.expected_overall.replace("_", " ")}.
          </FalseApprovalMarker>
        </div>
      ) : null}
      {!file.scored ? (
        <p className="mt-2 text-sm text-slate-800">
          This case has expected labels only. No model has judged it, so there are no verdicts, rationales or
          citations to show.
        </p>
      ) : (
        <p className="mt-2 text-sm text-slate-800">
          The overall result is computed by code from the criterion verdicts and the policy's variant logic; the
          model does not decide it.
        </p>
      )}
    </section>
  );
}

function CriterionRow({
  file,
  criterion,
  verdict,
  label,
  selected,
  tour,
}: {
  file: CaseFile;
  criterion: CaseCriterion;
  verdict: Verdict | undefined;
  label: ExpectedLabel | undefined;
  selected: boolean;
  tour: string | undefined;
}) {
  const panelId = `panel-${criterion.criterion}`;
  const select = () =>
    replaceHash(href(["cases", file.id], { c: selected ? undefined : criterion.criterion, tour }));
  return (
    <li
      data-criterion={criterion.criterion}
      data-verdict-row={verdict ? "" : undefined}
      className={`rounded border bg-white ${selected ? "border-slate-900 ring-1 ring-slate-900" : "border-slate-300"}`}
    >
      <button
        type="button"
        aria-expanded={selected}
        aria-controls={selected ? panelId : undefined}
        onClick={select}
        className="flex w-full flex-wrap items-start gap-x-2 gap-y-1 p-2 text-left hover:bg-slate-50"
      >
        {verdict ? (
          <OutcomeBadge outcome={shownOutcome(verdict)} />
        ) : label ? (
          <OutcomeBadge outcome={label.outcome} prefix="Label:" />
        ) : null}
        <span className="min-w-0 flex-1 basis-48">
          <span className="block text-sm">{criterion.text}</span>
          <span className="block break-all text-xs text-slate-700">{criterion.criterion}</span>
        </span>
        {verdict?.false_approval ? (
          <span className="w-full text-xs font-bold uppercase text-red-950 sm:w-auto">False approval</span>
        ) : verdict && !verdict.correct ? (
          <span className="w-full sm:w-auto">
            <CorrectMark correct={false} />
          </span>
        ) : null}
      </button>
      {selected ? (
        <div id={panelId} className="px-2 pb-2">
          {verdict ? (
            <VerdictPanel caseId={file.id} verdict={verdict} label={label} tour={tour} />
          ) : (
            <LabelPanel caseId={file.id} criterion={criterion} label={label} tour={tour} />
          )}
        </div>
      ) : null}
    </li>
  );
}

function NotJudged({ criteria }: { criteria: CaseCriterion[] }) {
  const stages = [...new Set(criteria.map((c) => c.evidence_stage))];
  return (
    <section aria-labelledby="notjudged-h" className="mt-4" data-testid="not-judged">
      <h3 id="notjudged-h" className="font-semibold">
        Not judged by the copilot
      </h3>
      <p className="text-sm text-slate-800">
        The copilot judges only what the chart or the request can show. These criteria go on the reviewer checklist,
        and a person confirms them.
      </p>
      {stages.map((stage) => (
        <div key={stage} className="mt-2 rounded border border-slate-300 bg-white p-2">
          <h4 className="text-sm font-semibold">{stageTitle(stage)} stage</h4>
          <p className="text-xs text-slate-700">
            Each of these {STAGE_TEXT[stage]?.meaning ?? `belongs to the ${stage} stage`}, so the copilot does not
            judge it.
          </p>
          <ul className="mt-1 list-inside list-disc text-sm">
            {criteria
              .filter((c) => c.evidence_stage === stage)
              .map((c) => (
                <li key={c.criterion} data-testid="checklist-item">
                  {c.text} <span className="break-all text-xs text-slate-700">({c.criterion})</span>
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

  return (
    <article className="space-y-4">
      <header>
        <p className="text-sm">
          <a className="underline" href="#/cases">
            Cases
          </a>{" "}
          / {file.set}
        </p>
        <h1 className="text-2xl font-bold">
          {file.id} <span className="text-lg font-normal text-slate-700">· {file.variant}</span>
        </h1>
        <p className="text-slate-800">{row.procedure}</p>
        <p className="mt-1 max-w-4xl text-sm text-slate-800">{file.description}</p>
        <p className="mt-1 text-sm">
          Policy:{" "}
          <a className="underline" href={href(["policies", file.policy])}>
            {file.policy}
          </a>
          {file.tags.length > 0 ? <span className="text-slate-700"> · tags: {file.tags.join(", ")}</span> : null}
        </p>
      </header>

      {tourStep !== null && tourStep >= 1 && tourStep <= TOUR.length ? <TourBar step={tourStep} /> : null}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <OverallPanel file={file} row={row} />
        <RequestPanel request={file.request} />
      </div>

      <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-2">
        <section aria-labelledby="criteria-h" className="min-w-0">
          <h2 id="criteria-h" className="text-lg font-semibold">
            Criteria <span className="text-sm font-normal text-slate-700">({judged.length} judged)</span>
          </h2>
          <p className="mb-2 text-sm text-slate-800">
            {file.scored
              ? "Select a criterion to see its recorded verdict and the cited text in the chart."
              : "Select a criterion to see its label and the labeled evidence in the chart."}
          </p>
          <ul className="space-y-2">
            {judged.map((c) => (
              <CriterionRow
                key={c.criterion}
                file={file}
                criterion={c}
                verdict={verdicts.get(c.criterion)}
                label={labels.get(c.criterion)}
                selected={c.criterion === selected?.criterion}
                tour={tourParam}
              />
            ))}
          </ul>
          {notJudged.length > 0 ? <NotJudged criteria={notJudged} /> : null}
        </section>

        <section aria-labelledby="chart-h" className="min-w-0 lg:sticky lg:top-2">
          <h2 id="chart-h" className="text-lg font-semibold">
            Chart
          </h2>
          <p className="mb-2 text-sm text-slate-800">
            Synthetic chart text, grouped by resource. {selected ? "Highlighted text is cited or labeled for the selected criterion." : ""}
          </p>
          <ChartViewer
            key={`${selected?.criterion ?? ""}|${resource ?? ""}`}
            chart={file.chart}
            highlights={highlights}
            target={target}
            requestKey={cite === null ? "" : String(cite)}
          />
        </section>
      </div>
    </article>
  );
}
