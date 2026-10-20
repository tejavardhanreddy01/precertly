import { CorrectMark, LabelsOnlyBadge, OutcomeBadge } from "../components/ui";
import { useIndex } from "../lib/index-context";
import { SET_ORDER, SET_TITLE } from "../lib/labels";
import { href } from "../lib/router";
import { useTitle } from "../lib/title";
import type { CaseRow } from "../lib/types";

function Row({ row }: { row: CaseRow }) {
  return (
    <li className="rounded border border-slate-300 bg-white p-3">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h3 className="font-semibold">
          <a className="underline underline-offset-2" href={href(["cases", row.id])}>
            {row.id}
          </a>{" "}
          <span className="font-normal text-slate-700">· {row.variant}</span>
        </h3>
        <p className="text-sm text-slate-700">{row.procedure}</p>
      </div>
      <p className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
        <span className="flex items-center gap-1">
          Expected overall <OutcomeBadge outcome={row.expected_overall} />
        </span>
        {row.scored && row.predicted_overall ? (
          <span className="flex flex-wrap items-center gap-1">
            Recorded <OutcomeBadge outcome={row.predicted_overall} />
            {row.overall_correct !== null ? <CorrectMark correct={row.overall_correct} /> : null}
          </span>
        ) : (
          <LabelsOnlyBadge />
        )}
      </p>
    </li>
  );
}

export function CasesList() {
  const { cases, summary } = useIndex();
  useTitle("Cases");
  const sets = SET_ORDER.filter((s) => cases.some((c) => c.set === s));
  return (
    <div>
      <h1 className="text-2xl font-bold">Cases</h1>
      <p className="mt-1 max-w-3xl text-slate-800">
        {cases.length} synthetic cases. The recorded run scored {summary.cases} of them (the facet set); the others
        show their expected labels only.
      </p>
      {sets.map((set) => {
        const rows = cases.filter((c) => c.set === set);
        const id = `set-${set}`;
        return (
          <section key={set} aria-labelledby={id} className="mt-6">
            <h2 id={id} className="text-lg font-semibold">
              {SET_TITLE[set] ?? set} <span className="text-sm font-normal text-slate-700">({rows.length} cases)</span>
            </h2>
            <ul className="mt-2 grid gap-2 md:grid-cols-2">
              {rows.map((row) => (
                <Row key={row.id} row={row} />
              ))}
            </ul>
          </section>
        );
      })}
    </div>
  );
}
