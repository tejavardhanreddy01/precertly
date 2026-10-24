import { getRun, probe, startRun } from "./api";
import fixture from "./contract.fixture.json";
import { AbortedError, ExpiredError, NetworkError, type ApiClient, type ApiResponse } from "./http";

const ok = (body: unknown, status = 200): ApiResponse => ({ status, body });

function client(open: ApiClient["open"], authed: ApiClient["authed"] = async () => ok({})): ApiClient {
  return { open, authed };
}

describe("probe", () => {
  afterEach(() => vi.useRealTimers());

  it("is up for a 200 whose body says ok", async () => {
    expect(await probe(client(async () => ok({ status: "ok", version: "1" })))).toBe("up");
  });

  it.each([
    ["a 500 (what the gateway sends with no task running)", ok({ message: "Internal Server Error" }, 500)],
    ["a 503", ok({}, 503)],
    ["a 404", ok({}, 404)],
    ["a 200 with no body", ok(undefined)],
    ["a 200 with the wrong body", ok({ status: "starting" })],
    ["a 200 with an array", ok([])],
    ["a 301", ok({}, 301)],
  ])("is down for %s", async (_name, response) => {
    expect(await probe(client(async () => response))).toBe("down");
  });

  it("is down for a network or CORS failure", async () => {
    expect(
      await probe(
        client(async () => {
          throw new NetworkError();
        }),
      ),
    ).toBe("down");
  });

  it("is down, and aborts the request, after the timeout", async () => {
    vi.useFakeTimers();
    let sawAbort = false;
    const hung = client(
      (_m, _p, signal) =>
        new Promise<ApiResponse>((_resolve, reject) => {
          signal.addEventListener("abort", () => {
            sawAbort = true;
            reject(new AbortedError());
          });
        }),
    );
    const result = probe(hung, { timeoutMs: 8000 });
    await vi.advanceTimersByTimeAsync(7999);
    expect(sawAbort).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    expect(await result).toBe("down");
    expect(sawAbort).toBe(true);
  });

  it("is down at once when the caller has already given up", async () => {
    const c = new AbortController();
    c.abort();
    const open = vi.fn(async () => ok({ status: "ok" }));
    expect(await probe(client(open), { signal: c.signal })).toBe("down");
    expect(open).not.toHaveBeenCalled();
  });

  it("asks for /health once, with GET", async () => {
    const open = vi.fn(async () => ok({ status: "ok" }));
    await probe(client(open));
    expect(open).toHaveBeenCalledTimes(1);
    expect(open.mock.calls[0]?.slice(0, 2)).toEqual(["GET", "/health"]);
  });
});

describe("startRun", () => {
  const signal = new AbortController().signal;
  const run = (response: ApiResponse | Error) =>
    startRun(
      client(async () => ok({}), async () => {
        if (response instanceof Error) throw response;
        return response;
      }),
      "facet-01",
      signal,
    );

  it("posts the case id and reads the 202", async () => {
    const authed = vi.fn(async () => ok(fixture.started, 202));
    const outcome = await startRun(client(async () => ok({}), authed), "facet-01", signal);
    expect(outcome).toMatchObject({ kind: "started", run: { status: "waking_database" } });
    expect(authed).toHaveBeenCalledWith("POST", "/live/runs", signal, { case_id: "facet-01" });
  });

  it.each([
    [ok({}, 409), "conflict"],
    [ok({}, 404), "not_found"],
    [ok({}, 401), "expired"],
    [ok({}, 429), "rate_limited"],
    [ok({}, 500), "failed"],
    [ok({}, 502), "failed"],
    [ok({ run_id: "x", status: "queued" }, 202), "unexpected"],
    [ok(undefined, 202), "unexpected"],
    [new NetworkError(), "failed"],
    [new ExpiredError(), "expired"],
  ])("sorts %j into %s", async (response, kind) => {
    expect((await run(response)).kind).toBe(kind);
  });

  it("lets an abort through to the caller", async () => {
    await expect(run(new AbortedError())).rejects.toBeInstanceOf(AbortedError);
  });
});

describe("getRun", () => {
  const signal = new AbortController().signal;
  const poll = (response: ApiResponse | Error) =>
    getRun(
      client(async () => ok({}), async () => {
        if (response instanceof Error) throw response;
        return response;
      }),
      "run-1",
      signal,
    );

  it("asks for the run by id, escaped", async () => {
    const authed = vi.fn(async () => ok(fixture.run_states.judging));
    await getRun(client(async () => ok({}), authed), "a/b?c", signal);
    expect(authed).toHaveBeenCalledWith("GET", "/live/runs/a%2Fb%3Fc", signal);
  });

  it("reads a valid state", async () => {
    const outcome = await poll(ok(fixture.run_states.ingesting));
    expect(outcome).toMatchObject({ kind: "state", state: { status: "ingesting" } });
  });

  it.each([
    [ok({}, 404), "gone"],
    [ok({}, 401), "expired"],
    [ok({}, 429), "rate_limited"],
    [ok({}, 500), "failed"],
    [ok({ status: "ingesting" }, 200), "unexpected"],
    [ok(undefined, 200), "unexpected"],
    [new NetworkError(), "failed"],
    [new ExpiredError(), "expired"],
  ])("sorts %j into %s", async (response, kind) => {
    expect((await poll(response)).kind).toBe(kind);
  });
});
