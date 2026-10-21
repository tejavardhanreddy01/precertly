import { useState } from "react";
import { CorrectMark, LabelsOnlyBadge } from "../components/ui";
import { Pill } from "../components/Pill";
import { useIndex } from "../lib/index-context";
import { SET_ORDER, SET_SHORT, SET_TITLE } from "../lib/labels";
import { href } from "../lib/router";
import { useTitle } from "../lib/title";
import type { CaseRow } from "../lib/types";

function Chip({ pressed, onClick, children }: { pressed: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      aria-pressed={pressed}
      onClick={onClick}
      className={`rounded-full border px-3 py-1 text-sm font-medium ${
        pressed
          ? "border-accent bg-accent text-on-accent"
          : "border-line-strong bg-surface text-ink-2 hover:bg-sunken"
      }`}
    >
      {children}
    </button>
  );
}

function CaseTableRow({ row }: { row: CaseRow }) {
  return (
    <tr
      data-case-row={row.id}
      data-scored={row.scored}
      className={`relative border-b border-line last:border-b-0 focus-within:bg-accent-soft ${
        row.scored ? "bg-surface hover:bg-accent-soft" : "bg-sunken text-ink-3 hover:bg-accent-soft"
      }`}
    >
      <td className="px-1.5 py-3 align-top first:pl-3 last:pr-3 sm:p-3">
        <a
          href={href(["cases", row.id])}
          className="font-mono text-sm font-semibold text-inherit after:absolute after:inset-0 hover:underline"
        >
          {row.id}
        </a>
        <span className="mt-0.5 block text-xs text-ink-3 md:hidden">{row.variant}</span>
      </td>
      <td className="hidden p-3 align-top text-sm md:table-cell">
        {SET_SHORT[row.set] ?? row.set}
        <span className="block text-xs text-ink-3">{row.variant}</span>
      </td>
      <td className="hidden p-3 align-top text-sm md:table-cell">{row.procedure}</td>
      <td className="px-1.5 py-3 align-top first:pl-3 last:pr-3 sm:p-3">
        <Pill state={row.expected_overall} />
      </td>
      <td className="px-1.5 py-3 align-top first:pl-3 last:pr-3 sm:p-3">
        {row.scored && row.predicted_overall ? (
          <span className="flex flex-col items-start gap-1 sm:flex-row sm:flex-wrap sm:items-center sm:gap-x-2">
            <Pill state={row.predicted_overall} />
            {row.overall_correct !== null ? <CorrectMark correct={row.overall_correct} /> : null}
          </span>
        ) : (
          <LabelsOnlyBadge />
        )}
      </td>
    </tr>
  );
}

export function CasesList() {
  const { cases, summary } = useIndex();
  const [filter, setFilter] = useState<string | null>(null);
  useTitle("Cases");
  const sets = SET_ORDER.filter((s) => cases.some((c) => c.set === s));
  const ordered = [...cases].sort((a, b) => SET_ORDER.indexOf(a.set) - SET_ORDER.indexOf(b.set));
  const shown = filter ? ordered.filter((c) => c.set === filter) : ordered;
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Cases</h1>
        <p className="mt-1 max-w-text text-ink-2">
          {cases.length} synthetic cases. The recorded run scored the {summary.cases} facet cases; the others show their
          expected labels only.
        </p>
      </div>

      <div role="group" aria-label="Filter by policy" className="flex flex-wrap gap-2">
        <Chip pressed={filter === null} onClick={() => setFilter(null)}>
          All <span className="tabular-nums opacity-90">{cases.length}</span>
        </Chip>
        {sets.map((set) => (
          <Chip key={set} pressed={filter === set} onClick={() => setFilter(filter === set ? null : set)}>
            <span title={SET_TITLE[set]}>{SET_SHORT[set] ?? set}</span>{" "}
            <span className="tabular-nums opacity-90">{cases.filter((c) => c.set === set).length}</span>
          </Chip>
        ))}
      </div>

      <div className="overflow-hidden rounded-lg border border-line">
        <table className="w-full border-collapse text-left" data-testid="cases-table">
          <caption className="sr-only">
            Cases with their expected overall result and the recorded result; select a row to open the case
          </caption>
          <thead className="bg-sunken text-sm text-ink-2">
            <tr className="border-b border-line-strong">
              <th scope="col" className="px-1.5 py-2 font-semibold first:pl-3 last:pr-3 sm:p-3">
                Case
              </th>
              <th scope="col" className="hidden p-3 font-semibold md:table-cell">
                Policy
              </th>
              <th scope="col" className="hidden p-3 font-semibold md:table-cell">
                Procedure
              </th>
              <th scope="col" className="px-1.5 py-2 font-semibold first:pl-3 last:pr-3 sm:p-3">
                Expected
              </th>
              <th scope="col" className="px-1.5 py-2 font-semibold first:pl-3 last:pr-3 sm:p-3">
                Recorded
              </th>
            </tr>
          </thead>
          <tbody>
            {shown.map((row) => (
              <CaseTableRow key={row.id} row={row} />
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-sm text-ink-3" aria-live="polite">
        Showing {shown.length} of {cases.length} cases.
      </p>
    </div>
  );
}
