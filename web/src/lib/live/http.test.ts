import { AbortedError, createApiClient, ExpiredError, NetworkError } from "./http";
import { API, FAKE_TOKEN } from "./testing";

function setup(response: () => Promise<Response> = async () => new Response("{}", { status: 200 }), bearer: string | null = `Bearer ${FAKE_TOKEN}`) {
  const fetchMock = vi.fn(response);
  const client = createApiClient({ apiOrigin: API, session: { bearer: () => bearer }, fetch: fetchMock as unknown as typeof fetch });
  return { fetchMock, client, signal: new AbortController().signal };
}

describe("the origin-guarded client", () => {
  it("sends the token only to the API origin, with credentials omitted", async () => {
    const { client, fetchMock, signal } = setup();
    await client.authed("GET", "/live/runs/abc", signal);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [URL, RequestInit];
    expect(url.origin).toBe(API);
    expect(url.pathname).toBe("/live/runs/abc");
    expect((init.headers as Record<string, string>).authorization).toBe(`Bearer ${FAKE_TOKEN}`);
    expect(init.credentials).toBe("omit");
    expect(init.redirect).toBe("error");
    expect(init.mode).toBe("cors");
  });

  it("sends no Authorization header on an open request", async () => {
    const { client, fetchMock, signal } = setup();
    await client.open("GET", "/health", signal);
    const [, init] = fetchMock.mock.calls[0] as unknown as [URL, RequestInit];
    expect(JSON.stringify(init.headers)).not.toMatch(/authorization/i);
  });

  it.each([
    "https://evil.example/live/runs",
    "//evil.example/live/runs",
    "/\\evil.example",
    "live/runs",
    "",
    "http://abc123def.execute-api.us-east-2.amazonaws.com/health",
    `${API}.evil.example/x`,
    `${API}@evil.example/x`,
  ])("refuses the URL %j before any request is made", async (path) => {
    const { client, fetchMock, signal } = setup();
    await expect(client.authed("GET", path, signal)).rejects.toThrow(/refused/);
    await expect(client.open("GET", path, signal)).rejects.toThrow(/refused/);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("posts JSON with a content type, and only then", async () => {
    const { client, fetchMock, signal } = setup(async () => new Response('{"run_id":"r","status":"waking_database"}', { status: 202 }));
    const res = await client.authed("POST", "/live/runs", signal, { case_id: "facet-01" });
    expect(res.status).toBe(202);
    const [, init] = fetchMock.mock.calls[0] as unknown as [URL, RequestInit];
    expect(init.body).toBe('{"case_id":"facet-01"}');
    expect((init.headers as Record<string, string>)["content-type"]).toBe("application/json");
  });

  it("throws ExpiredError without sending anything when there is no usable token", async () => {
    const { client, fetchMock, signal } = setup(undefined, null);
    await expect(client.authed("GET", "/live/runs/x", signal)).rejects.toBeInstanceOf(ExpiredError);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("turns a network failure into NetworkError, and an abort into AbortedError", async () => {
    const down = setup(async () => {
      throw new TypeError("Failed to fetch https://abc123def.execute-api");
    });
    const error = await down.client.open("GET", "/health", down.signal).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(NetworkError);
    expect(String((error as Error).message)).not.toContain("execute-api");

    const controller = new AbortController();
    const aborted = setup(async () => {
      controller.abort();
      throw new DOMException("aborted", "AbortError");
    });
    await expect(aborted.client.open("GET", "/health", controller.signal)).rejects.toBeInstanceOf(AbortedError);
  });

  it("returns an undefined body when the response is empty or not JSON", async () => {
    for (const text of ["", "<html>500</html>", "{broken"]) {
      const { client, signal } = setup(async () => new Response(text, { status: 500 }));
      expect(await client.open("GET", "/health", signal)).toEqual({ status: 500, body: undefined });
    }
  });
});
