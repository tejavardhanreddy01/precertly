import { tourHref } from "../content/tour";
import {
  DOCS_QUALITY_URL,
  GATES,
  GATES_CAVEAT,
  LIMITATIONS,
  NOTES_URL,
  percent,
  PIPELINE,
  WHAT_IT_IS,
  WHAT_IT_IS_NOT,
  type GateStatus,
} from "../content/about";
import { useIndex } from "../lib/index-context";
import { useTitle } from "../lib/title";

const STATUS: Record<GateStatus, { text: string; style: string; icon: string }> = {
  passes: { text: "Passes", style: "border-green-800 bg-green-50 text-green-900", icon: "M3 8.5l3.2 3.2L13 4.5" },
  fails: { text: "Fails", style: "border-red-800 bg-red-50 text-red-900", icon: "M3.5 3.5l9 9M12.5 3.5l-9 9" },
  reported: { text: "Reported", style: "border-slate-600 bg-slate-100 text-slate-900", icon: "M3 8h10" },
};

function StatusMark({ status }: { status: GateStatus }) {
  const s = STATUS[status];
  return (
    <span className={`inline-flex items-center gap-1 whitespace-nowrap rounded border px-1.5 py-0.5 text-xs font-semibold ${s.style}`}>
      <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
        <path d={s.icon} fill="none" stroke="currentColor" strokeWidth="2" />
      </svg>
      {s.text}
    </span>
  );
}

export function About() {
  const { summary } = useIndex();
  useTitle("About and method");
  const figures: [string, string][] = [
    ["Cases scored", `${summary.cases}, of which ${summary.cases_correct} have the expected overall result`],
    ["Criterion verdicts correct", `${summary.criteria_correct} of ${summary.scored_criteria} (${percent(summary.criterion_accuracy)})`],
    ["False approvals", `${summary.false_approvals} of ${summary.negative_labels} criteria labeled not met or insufficient; ${summary.case_false_approvals} case approved that should not have been`],
    ["Citation precision", `${summary.citations_in_label} of ${summary.citations} citations on a labeled resource (${percent(summary.citation_precision)})`],
  ];
  return (
    <article className="max-w-4xl space-y-8">
      <header>
        <h1 className="text-2xl font-bold">About and method</h1>
        <p className="mt-1">
          <a className="underline" href={tourHref(1)}>
            Take the 4-step tour
          </a>
        </p>
      </header>

      <section aria-labelledby="is-h" className="space-y-2">
        <h2 id="is-h" className="text-lg font-semibold">
          What this is, and what it is not
        </h2>
        {WHAT_IT_IS.map((p) => (
          <p key={p}>{p}</p>
        ))}
        <ul className="list-inside list-disc space-y-1">
          {WHAT_IT_IS_NOT.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      </section>

      <section aria-labelledby="pipe-h">
        <h2 id="pipe-h" className="text-lg font-semibold">
          How a result is made, in five steps
        </h2>
        <ol className="mt-2 list-inside list-decimal space-y-2">
          {PIPELINE.map((step) => (
            <li key={step.title}>
              <span className="font-semibold">{step.title}.</span> {step.text}
            </li>
          ))}
        </ol>
      </section>

      <section aria-labelledby="run-h">
        <h2 id="run-h" className="text-lg font-semibold">
          The recorded run
        </h2>
        <p className="font-semibold">Facet cases only.</p>
        <p className="text-sm text-slate-800">
          These numbers come from the data file this page loaded. The other 18 cases have not been scored.
        </p>
        <dl className="mt-2 grid grid-cols-1 gap-x-4 gap-y-2 sm:grid-cols-[auto_1fr]" data-testid="run-figures">
          {figures.map(([k, v]) => (
            <div key={k} className="contents">
              <dt className="font-medium text-slate-700">{k}</dt>
              <dd>{v}</dd>
            </div>
          ))}
        </dl>
      </section>

      <section aria-labelledby="gates-h">
        <h2 id="gates-h" className="text-lg font-semibold">
          Release gates
        </h2>
        <p className="text-sm text-slate-800">{GATES_CAVEAT}</p>
        <p className="text-sm text-slate-800">
          Thresholds are starting goals, published with the real numbers, including the failures. The table mirrors{" "}
          <a className="underline" href={DOCS_QUALITY_URL}>
            docs/QUALITY.md
          </a>
          .
        </p>
        <div className="mt-2 overflow-x-auto">
          <table className="w-full min-w-[40rem] border-collapse text-left text-sm" data-testid="gates">
            <caption className="sr-only">Release gates, their thresholds and the current results on the facet cases</caption>
            <thead>
              <tr className="border-b-2 border-slate-900">
                <th scope="col" className="p-2">
                  Gate
                </th>
                <th scope="col" className="p-2">
                  Threshold
                </th>
                <th scope="col" className="p-2">
                  Current (facet cases only)
                </th>
                <th scope="col" className="p-2">
                  Status
                </th>
              </tr>
            </thead>
            <tbody>
              {GATES.map((g) => (
                <tr key={g.gate} className="border-b border-slate-300 align-top">
                  <th scope="row" className="p-2 font-semibold">
                    {g.gate}
                  </th>
                  <td className="p-2">{g.threshold}</td>
                  <td className="p-2">{g.current}</td>
                  <td className="p-2">
                    <StatusMark status={g.status} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section aria-labelledby="lim-h">
        <h2 id="lim-h" className="text-lg font-semibold">
          Known limitations
        </h2>
        <ul className="mt-2 space-y-2">
          {LIMITATIONS.map((l) => (
            <li key={l.title} className="rounded border border-slate-300 bg-white p-3">
              <h3 className="font-semibold">{l.title}</h3>
              <p className="text-sm">{l.text}</p>
            </li>
          ))}
        </ul>
        <p className="mt-2 text-sm">
          The full list is in{" "}
          <a className="underline" href={DOCS_QUALITY_URL}>
            docs/QUALITY.md
          </a>{" "}
          and the experiment record in{" "}
          <a className="underline" href={NOTES_URL}>
            evals/results/NOTES.md
          </a>
          .
        </p>
      </section>
    </article>
  );
}
