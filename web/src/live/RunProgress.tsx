import { IconCheck, IconInfo } from "../components/icons";
import type { RunView } from "../lib/live/poller";
import { ACTIVE_STATUSES } from "../lib/live/guards";
import { STAGE_LABELS } from "../lib/live/messages";
import { useElapsedSeconds } from "./hooks";

function clock(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

function Elapsed({ startedAtMs }: { startedAtMs: number }) {
  const seconds = useElapsedSeconds(startedAtMs);
  return (
    <p className="text-sm text-ink-2" data-testid="elapsed">
      Elapsed <span className="font-mono tabular-nums">{clock(seconds)}</span>
    </p>
  );
}

/**
 * A run in progress: the four stages, the current one marked, and the seconds so far. Only the
 * stage name is in the live region, so a screen reader hears each change of stage and not every
 * tick of the counter.
 */
export function RunProgress({ view }: { view: Extract<RunView, { phase: "starting" | "running" }> }) {
  if (view.phase === "starting") {
    return (
      <section aria-labelledby="progress-h" className="rounded-lg border border-line bg-surface p-4">
        <h2 id="progress-h" className="text-lg font-semibold">
          Starting the run
        </h2>
        <p role="status" className="mt-1 text-ink-2">
          Sending the request for {view.caseId}…
        </p>
      </section>
    );
  }
  const current = ACTIVE_STATUSES.indexOf(view.status);
  return (
    <section aria-labelledby="progress-h" className="rounded-lg border border-line bg-surface p-4" data-testid="progress">
      <h2 id="progress-h" className="text-lg font-semibold">
        Running {view.caseId}
      </h2>
      <p role="status" aria-live="polite" className="sr-only" data-testid="stage-announcement">
        {STAGE_LABELS[view.status]}
      </p>
      <ol className="mt-3 space-y-2">
        {ACTIVE_STATUSES.map((stage, i) => {
          const state = i < current ? "done" : i === current ? "current" : "waiting";
          const seconds = view.timings[stage];
          return (
            <li
              key={stage}
              aria-current={state === "current" ? "step" : undefined}
              data-stage={stage}
              data-state={state}
              className={`flex items-start gap-2 ${state === "current" ? "font-semibold" : state === "waiting" ? "text-ink-3" : ""}`}
            >
              <span className="mt-0.5 shrink-0" aria-hidden="true">
                {state === "done" ? <IconCheck size={16} /> : <IconInfo size={16} />}
              </span>
              <span className="min-w-0">
                {STAGE_LABELS[stage]}
                <span className="ml-2 text-sm font-normal text-ink-3">
                  {state === "done" ? `done${seconds === undefined ? "" : `, ${seconds.toFixed(1)} s`}` : state === "current" ? "in progress" : "waiting"}
                </span>
              </span>
            </li>
          );
        })}
      </ol>
      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1">
        <Elapsed startedAtMs={view.startedAtMs} />
        {view.slowed ? <p className="text-sm text-ink-2">The live environment is busy; checking less often.</p> : null}
      </div>
      <p className="mt-3 text-sm text-ink-3">
        Stay on the live pages while the run goes: leaving them stops this page from following it, and the result cannot
        be recovered afterwards because results are not stored. Measured once, a run took about 90 seconds when warm and
        about 3.5 minutes from fully cold; that is not a promise.
      </p>
    </section>
  );
}
