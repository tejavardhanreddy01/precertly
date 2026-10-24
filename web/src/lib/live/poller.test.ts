import fixture from "./contract.fixture.json";
import { browserTimers } from "./api";
import { AbortedError, ExpiredError, NetworkError, type ApiClient, type ApiResponse } from "./http";
import { MAX_BACKOFF_MS, MAX_CONSECUTIVE_FAILURES, POLL_INTERVAL_MS, RunController, type RunView } from "./poller";
import { MESSAGES } from "./messages";

const states = fixture.run_states as Record<string, unknown>;
const ended = fixture.ended_states as { reason: string }[];
const ok = (body: unknown, status = 200): ApiResponse => ({ status, body });

type Step = ApiResponse | Error | "hang";

/** A client whose answers come from two queues: the start (POST) and the polls (GET). */
function scripted(start: Step[], polls: Step[]) {
  const calls = { posts: 0, gets: 0, inFlight: 0, maxInFlight: 0, aborted: 0 };
  const answer = async (queue: Step[], signal: AbortSignal): Promise<ApiResponse> => {
    calls.inFlight += 1;
    calls.maxInFlight = Math.max(calls.maxInFlight, calls.inFlight);
    try {
      const step = queue.shift() ?? ok({}, 500);
      if (step === "hang") {
        await new Promise<void>((_resolve, reject) =>
          signal.addEventListener("abort", () => {
            calls.aborted += 1;
            reject(new AbortedError());
          }),
        );
      }
      if (step instanceof Error) throw step;
      return step as ApiResponse;
    } finally {
      calls.inFlight -= 1;
    }
  };
  const client: ApiClient = {
    open: async () => ok({}),
    authed: (method, _path, signal) => {
      if (method === "POST") {
        calls.posts += 1;
        return answer(start, signal);
      }
      calls.gets += 1;
      return answer(polls, signal);
    },
  };
  return { client, calls };
}

function make(start: Step[], polls: Step[], active: () => boolean = () => true) {
  const s = scripted(start, polls);
  const controller = new RunController({ client: s.client, now: () => Date.now(), sessionActive: active, ...browserTimers });
  const seen: RunView[] = [];
  controller.subscribe(() => seen.push(controller.getSnapshot()));
  return { controller, seen, ...s };
}

const started = ok(fixture.started, 202);
const view = (c: RunController) => c.getSnapshot();
const tick = (ms = POLL_INTERVAL_MS) => vi.advanceTimersByTimeAsync(ms);

describe("a run", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("walks every status in order and stops at done with the result", async () => {
    const { controller, calls } = make([started], [
      ok(states.checking_limits),
      ok(states.ingesting),
      ok(states.judging),
      ok(states.done),
    ]);
    await controller.start("facet-01");
    expect(view(controller)).toMatchObject({ phase: "running", status: "waking_database", caseId: "facet-01" });
    const order: string[] = [];
    for (let i = 0; i < 3; i += 1) {
      await tick();
      const v = view(controller);
      if (v.phase === "running") order.push(v.status);
    }
    await tick();
    expect(order).toEqual(["checking_limits", "ingesting", "judging"]);
    expect(view(controller)).toMatchObject({ phase: "done", state: { status: "done" } });
    expect(calls.gets).toBe(4);
    await tick(60_000);
    expect(calls.gets).toBe(4);
  });

  it("reports the per-stage timings the server sends", async () => {
    const { controller } = make([started], [ok(states.judging)]);
    await controller.start("facet-01");
    await tick();
    const v = view(controller);
    expect(v.phase === "running" && v.timings.ingesting).toBe(12.25);
  });

  it.each(ended.map((e) => [e.reason, e] as const))("stops at a finished run: %s", async (_reason, state) => {
    const { controller, calls } = make([started], [ok(state)]);
    await controller.start("facet-01");
    await tick();
    const v = view(controller);
    expect(v.phase).toBe("ended");
    // The server's fixed message is what is shown.
    expect(v.phase === "ended" && v.message).toBe((fixture.messages as Record<string, string>)[(state as { reason: string }).reason]);
    await tick(60_000);
    expect(calls.gets).toBe(1);
  });

  it("never has two requests in flight, however slow the server is", async () => {
    const slow: Step[] = ["hang"];
    const { controller, calls } = make([started], slow);
    await controller.start("facet-01");
    await tick();
    expect(calls.gets).toBe(1);
    await tick(60_000);
    // The first poll is still waiting; no timer started a second one.
    expect(calls.gets).toBe(1);
    expect(calls.maxInFlight).toBe(1);
    controller.dispose();
  });

  it("a second start while one is active does nothing (a double click starts one run)", async () => {
    const { controller, calls } = make([started, started], [ok(states.judging)]);
    await Promise.all([controller.start("facet-01"), controller.start("facet-01")]);
    await controller.start("facet-02");
    expect(calls.posts).toBe(1);
    controller.dispose();
  });

  it("allows a new run after the last one is over", async () => {
    const { controller, calls } = make([started, started], [ok(states.done)]);
    await controller.start("facet-01");
    await tick();
    expect(view(controller).phase).toBe("done");
    controller.reset();
    expect(view(controller).phase).toBe("idle");
    await controller.start("facet-01");
    expect(calls.posts).toBe(2);
    controller.dispose();
  });

  it("slows down on 429, keeps going, and does not count it as a failure", async () => {
    const { controller, calls } = make([started], [ok({}, 429), ok({}, 429), ok({}, 429), ok({}, 429), ok(states.done)]);
    await controller.start("facet-01");
    await tick();
    expect(calls.gets).toBe(1);
    expect(view(controller)).toMatchObject({ phase: "running", slowed: true });
    // 8 s after the first 429, then 16 s, then capped at 16 s.
    await tick(POLL_INTERVAL_MS * 2 - 1);
    expect(calls.gets).toBe(1);
    await tick(1);
    expect(calls.gets).toBe(2);
    await tick(MAX_BACKOFF_MS);
    expect(calls.gets).toBe(3);
    await tick(MAX_BACKOFF_MS);
    expect(calls.gets).toBe(4);
    await tick(MAX_BACKOFF_MS);
    expect(calls.gets).toBe(5);
    expect(view(controller).phase).toBe("done");
  });

  it("tolerates two failures and declares the connection lost on the third in a row", async () => {
    const { controller, calls } = make([started], [new NetworkError(), new NetworkError(), new NetworkError(), ok(states.done)]);
    await controller.start("facet-01");
    await tick();
    await tick();
    expect(view(controller).phase).toBe("running");
    await tick();
    expect(calls.gets).toBe(MAX_CONSECUTIVE_FAILURES);
    const v = view(controller);
    expect(v).toMatchObject({ phase: "error", kind: "connection_lost", message: MESSAGES.connectionLost });
    expect(MESSAGES.connectionLost).toMatch(/may still be finishing/);
    expect(MESSAGES.connectionLost).toMatch(/cannot be recovered/);
    expect(MESSAGES.connectionLost).toMatch(/memory only/);
    await tick(60_000);
    expect(calls.gets).toBe(3);
  });

  it("resets the failure count after a good answer", async () => {
    const { controller } = make([started], [
      new NetworkError(),
      new NetworkError(),
      ok(states.ingesting),
      new NetworkError(),
      new NetworkError(),
      ok(states.done),
    ]);
    await controller.start("facet-01");
    for (let i = 0; i < 6; i += 1) await tick();
    expect(view(controller).phase).toBe("done");
  });

  it("treats a 5xx like a failure", async () => {
    const { controller } = make([started], [ok({}, 502), ok({}, 503), ok({}, 500)]);
    await controller.start("facet-01");
    for (let i = 0; i < 3; i += 1) await tick();
    expect(view(controller)).toMatchObject({ phase: "error", kind: "connection_lost" });
  });

  it("ends with an unexpected-response failure for an answer the guards refuse", async () => {
    const { controller } = make([started], [ok({ run_id: "x", status: "queued" })]);
    await controller.start("facet-01");
    await tick();
    expect(view(controller)).toMatchObject({ phase: "error", kind: "unexpected", message: MESSAGES.unexpectedResponse });
  });

  it("says the run is gone on a 404 while polling", async () => {
    const { controller } = make([started], [ok({}, 404)]);
    await controller.start("facet-01");
    await tick();
    expect(view(controller)).toMatchObject({ phase: "error", kind: "gone" });
    expect(MESSAGES.runGone).toBe("This run is no longer available. The live environment may have been stopped.");
  });

  it("ends with an expired sign-in on a 401", async () => {
    const { controller } = make([started], [ok({}, 401)]);
    await controller.start("facet-01");
    await tick();
    expect(view(controller)).toMatchObject({ phase: "error", kind: "expired", message: MESSAGES.expired });
  });

  it("ends as expired when the token runs out between polls, without asking the server", async () => {
    let active = true;
    const { controller, calls } = make([started], [ok(states.judging)], () => active);
    await controller.start("facet-01");
    await tick();
    expect(calls.gets).toBe(1);
    active = false;
    await tick();
    expect(calls.gets).toBe(1);
    expect(view(controller)).toMatchObject({ phase: "error", kind: "expired" });
  });

  it("calls a failed request an expiry, not a lost connection, when the token has run out", async () => {
    let active = true;
    const { controller } = make([started], [new NetworkError()], () => active);
    await controller.start("facet-01");
    active = false;
    await tick();
    expect(view(controller)).toMatchObject({ phase: "error", kind: "expired" });
  });

  it("an ExpiredError from the client is an expiry", async () => {
    const { controller } = make([started], [new ExpiredError()]);
    await controller.start("facet-01");
    await tick();
    expect(view(controller)).toMatchObject({ phase: "error", kind: "expired" });
  });

  it("does not start with no usable token", async () => {
    const { controller, calls } = make([started], [], () => false);
    await controller.start("facet-01");
    expect(calls.posts).toBe(0);
    expect(view(controller)).toMatchObject({ phase: "error", kind: "expired" });
  });
});

describe("starting a run", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it.each([
    [ok({}, 409), "conflict", MESSAGES.anotherRunInProgress],
    [ok({}, 404), "case_unavailable", MESSAGES.notAvailableCase],
    [ok({}, 401), "expired", MESSAGES.expired],
    [ok({}, 429), "rate_limited", MESSAGES.rateLimited],
    [ok({}, 500), "start_failed", MESSAGES.couldNotStart],
    [new NetworkError(), "start_failed", MESSAGES.couldNotStart],
    [ok({ nope: 1 }, 202), "unexpected", MESSAGES.unexpectedResponse],
  ])("sorts %j into %s with a fixed sentence", async (response, kind, message) => {
    const { controller, calls } = make([response], []);
    await controller.start("facet-01");
    expect(view(controller)).toMatchObject({ phase: "error", kind, message });
    await tick(60_000);
    expect(calls.gets).toBe(0);
  });

  it("a 409 says another live run is in progress, and a 404 says the case is not available", () => {
    expect(MESSAGES.anotherRunInProgress).toMatch(/another live run is in progress/i);
    expect(MESSAGES.notAvailableCase).toMatch(/not available/i);
  });

  it("does not trust a start response that says the run is already over", async () => {
    const { controller } = make([ok({ run_id: "r", status: "done" }, 202)], []);
    await controller.start("facet-01");
    expect(view(controller)).toMatchObject({ phase: "error", kind: "unexpected" });
  });
});

describe("leaving the page", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("aborts the request in flight and clears the timer, so nothing polls afterwards", async () => {
    const { controller, calls, seen } = make([started], ["hang", ok(states.done)]);
    await controller.start("facet-01");
    await tick();
    expect(calls.gets).toBe(1);
    controller.dispose();
    expect(calls.aborted).toBe(1);
    const before = seen.length;
    await tick(120_000);
    expect(calls.gets).toBe(1);
    expect(seen).toHaveLength(before);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("clears the timer between polls", async () => {
    const { controller, calls } = make([started], [ok(states.judging), ok(states.done)]);
    await controller.start("facet-01");
    expect(vi.getTimerCount()).toBe(1);
    controller.dispose();
    expect(vi.getTimerCount()).toBe(0);
    await tick(60_000);
    expect(calls.gets).toBe(0);
  });

  it("aborts a start that is still waiting", async () => {
    const { controller, calls } = make(["hang"], []);
    const pending = controller.start("facet-01");
    controller.dispose();
    await pending;
    expect(calls.aborted).toBe(1);
    expect(calls.gets).toBe(0);
  });

  it("ignores a start after disposal", async () => {
    const { controller, calls } = make([started], []);
    controller.dispose();
    await controller.start("facet-01");
    expect(calls.posts).toBe(0);
  });
});
