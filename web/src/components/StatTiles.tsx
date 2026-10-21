import { tiles } from "../lib/tiles";
import type { RunSummary } from "../lib/types";
import { GateMark } from "./GateMark";

/** The four headline numbers of the recorded run, every figure read from index.json's summary. */
export function StatTiles({ summary, wide = false, id }: { summary: RunSummary; wide?: boolean; id?: string }) {
  return (
    <ul className={`grid grid-cols-1 gap-3 min-[540px]:grid-cols-2 ${wide ? "lg:grid-cols-4" : ""}`} data-testid={id}>
      {tiles(summary).map((t) => (
        <li
          key={t.key}
          data-testid={`tile-${t.key}`}
          className={`flex flex-col rounded-lg border border-t-4 bg-surface p-4 ${t.alarm ? "border-danger" : "border-line"}`}
        >
          <p className="text-sm font-medium text-ink-2">{t.label}</p>
          <p className="mt-1 text-3xl font-bold tabular-nums" data-testid="tile-value">
            {t.value}
          </p>
          <p className="mt-1 flex-1 text-sm text-ink-3">{t.detail}</p>
          {t.gate ? (
            <p className="mt-3 flex flex-wrap items-center gap-2 text-xs text-ink-2">
              {t.gate.text}
              <GateMark status={t.gate.status} />
            </p>
          ) : null}
        </li>
      ))}
    </ul>
  );
}
