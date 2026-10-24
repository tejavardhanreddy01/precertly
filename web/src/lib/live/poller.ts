// One live run in one tab: start it, then ask for its state every few seconds until it finishes.
// A plain state machine with its clock, timers and requests injected, so tests drive it with
// fake time. Rules: one run at a time (a start while one is active does nothing), at most one
// request in flight, a 429 slows it down, three failures in a row end it as "connection lost",
// a 404, a 401 or a local expiry end it with their own sentence, and dispose() aborts the
// request and clears the timer so nothing polls after the page is gone.

import { type Timers } from "./api";
import { getRun, startRun, type PollOutcome, type StartOutcome } from "./api";
import { isFinished, type ActiveStatus, type RunState } from "./guards";
import { AbortedError, type ApiClient } from "./http";
import { MESSAGES } from "./messages";

export const POLL_INTERVAL_MS = 4_000;
export const MAX_BACKOFF_MS = 16_000;
export const MAX_CONSECUTIVE_FAILURES = 3;

export type RunErrorKind =
  | "expired"
  | "conflict"
  | "case_unavailable"
  | "gone"
  | "connection_lost"
  | "unexpected"
  | "start_failed"
  | "rate_limited";

export type RunView =
  | { phase: "idle" }
  | { phase: "starting"; caseId: string }
  | {
      phase: "running";
      caseId: string;
      runId: string;
      status: ActiveStatus;
      /** The local clock at the moment the start was accepted; the page counts seconds from it. */
      startedAtMs: number;
      timings: Record<string, number>;
      /** True while the poller is waiting longer because the gateway said "too many requests". */
      slowed: boolean;
    }
  | { phase: "done"; state: RunState }
  | { phase: "ended"; state: RunState; message: string }
  | { phase: "error"; kind: RunErrorKind; message: string };

const ERROR_MESSAGES: Record<RunErrorKind, string> = {
  expired: MESSAGES.expired,
  conflict: MESSAGES.anotherRunInProgress,
  case_unavailable: MESSAGES.notAvailableCase,
  gone: MESSAGES.runGone,
  connection_lost: MESSAGES.connectionLost,
  unexpected: MESSAGES.unexpectedResponse,
  start_failed: MESSAGES.couldNotStart,
  rate_limited: MESSAGES.rateLimited,
};

/** The server's fixed message for a failed or rejected run, or a local one if it sent none. */
function endMessage(state: RunState): string {
  const m = state.message;
  return typeof m === "string" && m.length > 0 && m.length <= 300 ? m : "The run failed.";
}

export interface PollerDeps extends Timers {
  client: ApiClient;
  now: () => number;
  /** True while the session holds a token that is not about to expire (the local clock decides). */
  sessionActive: () => boolean;
}

export class RunController {
  readonly #deps: PollerDeps;
  #view: RunView = { phase: "idle" };
  #abort: AbortController | null = null;
  #timer: unknown = null;
  #inFlight = false;
  #failures = 0;
  #backoffs = 0;
  #disposed = false;
  readonly #listeners = new Set<() => void>();

  constructor(deps: PollerDeps) {
    this.#deps = deps;
  }

  subscribe = (listener: () => void): (() => void) => {
    this.#listeners.add(listener);
    return () => {
      this.#listeners.delete(listener);
    };
  };

  getSnapshot = (): RunView => this.#view;

  /** Starts a run unless one is starting or running. Never throws. */
  async start(caseId: string): Promise<void> {
    if (this.#disposed || this.#view.phase === "starting" || this.#view.phase === "running") return;
    if (!this.#deps.sessionActive()) {
      this.#fail("expired");
      return;
    }
    this.#failures = 0;
    this.#backoffs = 0;
    this.#abort = new AbortController();
    const signal = this.#abort.signal;
    this.#set({ phase: "starting", caseId });
    let outcome: StartOutcome;
    try {
      outcome = await startRun(this.#deps.client, caseId, signal);
    } catch (error) {
      if (error instanceof AbortedError || this.#disposed) return;
      throw error;
    }
    if (this.#disposed) return;
    switch (outcome.kind) {
      case "started": {
        // A run cannot be over before its start was accepted; a response saying so is not trusted.
        if (isFinished(outcome.run.status)) return this.#fail("unexpected");
        this.#set({
          phase: "running",
          caseId,
          runId: outcome.run.run_id,
          status: outcome.run.status as ActiveStatus,
          startedAtMs: this.#deps.now(),
          timings: {},
          slowed: false,
        });
        this.#schedule(POLL_INTERVAL_MS);
        return;
      }
      case "conflict":
        return this.#fail("conflict");
      case "not_found":
        return this.#fail("case_unavailable");
      case "expired":
        return this.#fail("expired");
      case "rate_limited":
        return this.#fail("rate_limited");
      case "unexpected":
        return this.#fail("unexpected");
      case "failed":
        // The token may have run out while the request was on its way.
        return this.#fail(this.#deps.sessionActive() ? "start_failed" : "expired");
    }
  }

  /** Back to the start screen after a result or an error. Does nothing while a run is active. */
  reset(): void {
    if (this.#view.phase === "starting" || this.#view.phase === "running") return;
    this.#set({ phase: "idle" });
  }

  /** Makes a disposed controller usable again. React StrictMode runs an effect's cleanup and then its setup again. */
  activate(): void {
    this.#disposed = false;
  }

  dispose(): void {
    this.#disposed = true;
    this.#abort?.abort();
    this.#abort = null;
    this.#clear();
  }

  #schedule(ms: number): void {
    this.#clear();
    if (this.#disposed) return;
    this.#timer = this.#deps.setTimer(() => {
      this.#timer = null;
      void this.#tick();
    }, ms);
  }

  #clear(): void {
    if (this.#timer !== null) {
      this.#deps.clearTimer(this.#timer);
      this.#timer = null;
    }
  }

  async #tick(): Promise<void> {
    const view = this.#view;
    if (this.#disposed || view.phase !== "running" || this.#inFlight) return;
    if (!this.#deps.sessionActive()) {
      this.#fail("expired");
      return;
    }
    this.#inFlight = true;
    this.#abort = new AbortController();
    let outcome: PollOutcome;
    try {
      outcome = await getRun(this.#deps.client, view.runId, this.#abort.signal);
    } catch (error) {
      this.#inFlight = false;
      if (error instanceof AbortedError || this.#disposed) return;
      throw error;
    }
    this.#inFlight = false;
    if (this.#disposed) return;
    switch (outcome.kind) {
      case "state":
        return this.#onState(view, outcome.state);
      case "gone":
        return this.#fail("gone");
      case "expired":
        return this.#fail("expired");
      case "unexpected":
        return this.#fail("unexpected");
      case "rate_limited": {
        this.#backoffs += 1;
        const wait = Math.min(POLL_INTERVAL_MS * 2 ** this.#backoffs, MAX_BACKOFF_MS);
        this.#set({ ...view, slowed: true });
        this.#schedule(wait);
        return;
      }
      case "failed": {
        // A request that failed because the token ran out is an expiry, not a lost connection.
        if (!this.#deps.sessionActive()) return this.#fail("expired");
        this.#failures += 1;
        if (this.#failures >= MAX_CONSECUTIVE_FAILURES) return this.#fail("connection_lost");
        this.#schedule(POLL_INTERVAL_MS);
        return;
      }
    }
  }

  #onState(view: Extract<RunView, { phase: "running" }>, state: RunState): void {
    this.#failures = 0;
    this.#backoffs = 0;
    if (state.status === "done" && state.result !== null) {
      this.#clear();
      this.#set({ phase: "done", state });
      return;
    }
    if (state.status === "failed" || state.status === "rejected") {
      this.#clear();
      this.#set({ phase: "ended", state, message: endMessage(state) });
      return;
    }
    if (state.status === "done") {
      // The guards make this impossible; if it ever happens, say so rather than show nothing.
      this.#fail("unexpected");
      return;
    }
    this.#set({ ...view, status: state.status, timings: state.timings, slowed: false });
    this.#schedule(POLL_INTERVAL_MS);
  }

  #fail(kind: RunErrorKind): void {
    this.#clear();
    this.#abort?.abort();
    this.#abort = null;
    this.#set({ phase: "error", kind, message: ERROR_MESSAGES[kind] });
  }

  #set(view: RunView): void {
    this.#view = view;
    for (const l of [...this.#listeners]) l();
  }
}
