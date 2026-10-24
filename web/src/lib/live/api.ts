// The three calls the live pages make, with their outcomes sorted into a few kinds. The UI never
// looks at a status code: it gets one of these kinds, and each kind has a fixed sentence.

import { parseRunState, parseStarted, type RunState, type Started } from "./guards";
import { AbortedError, ExpiredError, type ApiClient } from "./http";

export const PROBE_TIMEOUT_MS = 8_000;

export interface Timers {
  setTimer: (fn: () => void, ms: number) => unknown;
  clearTimer: (id: unknown) => void;
}

export const browserTimers: Timers = {
  setTimer: (fn, ms) => setTimeout(fn, ms),
  clearTimer: (id) => clearTimeout(id as ReturnType<typeof setTimeout>),
};

/**
 * Is the live environment up? One GET /health with no token. It is up only for a 200 whose body
 * says ok; anything else is down: any other status (the gateway answers 500 when the service is
 * at zero tasks), a network or CORS failure, a timeout, a body that is not what the API sends.
 */
export async function probe(
  client: ApiClient,
  options: { signal?: AbortSignal; timers?: Timers; timeoutMs?: number } = {},
): Promise<"up" | "down"> {
  const timers = options.timers ?? browserTimers;
  const controller = new AbortController();
  const onParentAbort = () => controller.abort();
  if (options.signal?.aborted) return "down";
  options.signal?.addEventListener("abort", onParentAbort, { once: true });
  const timer = timers.setTimer(() => controller.abort(), options.timeoutMs ?? PROBE_TIMEOUT_MS);
  try {
    const { status, body } = await client.open("GET", "/health", controller.signal);
    if (status !== 200) return "down";
    const ok = typeof body === "object" && body !== null && (body as Record<string, unknown>).status === "ok";
    return ok ? "up" : "down";
  } catch {
    return "down";
  } finally {
    timers.clearTimer(timer);
    options.signal?.removeEventListener("abort", onParentAbort);
  }
}

export type StartOutcome =
  | { kind: "started"; run: Started }
  | { kind: "conflict" }
  | { kind: "not_found" }
  | { kind: "expired" }
  | { kind: "rate_limited" }
  | { kind: "unexpected" }
  | { kind: "failed" };

export async function startRun(client: ApiClient, caseId: string, signal: AbortSignal): Promise<StartOutcome> {
  try {
    const { status, body } = await client.authed("POST", "/live/runs", signal, { case_id: caseId });
    if (status === 202) {
      const run = parseStarted(body);
      return run === null ? { kind: "unexpected" } : { kind: "started", run };
    }
    if (status === 409) return { kind: "conflict" };
    if (status === 404) return { kind: "not_found" };
    if (status === 401) return { kind: "expired" };
    if (status === 429) return { kind: "rate_limited" };
    return { kind: "failed" };
  } catch (error) {
    if (error instanceof ExpiredError) return { kind: "expired" };
    if (error instanceof AbortedError) throw error;
    return { kind: "failed" };
  }
}

export type PollOutcome =
  | { kind: "state"; state: RunState }
  | { kind: "gone" }
  | { kind: "expired" }
  | { kind: "rate_limited" }
  | { kind: "unexpected" }
  | { kind: "failed" };

export async function getRun(client: ApiClient, runId: string, signal: AbortSignal): Promise<PollOutcome> {
  try {
    const { status, body } = await client.authed("GET", `/live/runs/${encodeURIComponent(runId)}`, signal);
    if (status === 200) {
      const state = parseRunState(body);
      return state === null ? { kind: "unexpected" } : { kind: "state", state };
    }
    if (status === 404) return { kind: "gone" };
    if (status === 401) return { kind: "expired" };
    if (status === 429) return { kind: "rate_limited" };
    return { kind: "failed" };
  } catch (error) {
    if (error instanceof ExpiredError) return { kind: "expired" };
    if (error instanceof AbortedError) throw error;
    return { kind: "failed" };
  }
}
