import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import { App } from "../App";
import { DataStore } from "../lib/data";
import fixture from "../lib/live/contract.fixture.json";
import type { ApiClient, ApiResponse } from "../lib/live/http";
import { NetworkError } from "../lib/live/http";
import { MESSAGES } from "../lib/live/messages";
import { LiveSession } from "../lib/live/session";
import { CLIENT_ID, FAKE_TOKEN, HOSTED_UI, SITE, fakeLocation, fakeStorage, makeSession, tokenResponse } from "../lib/live/testing";
import type { LiveFeature } from "../lib/live-feature";
import { diskFetch } from "../test/fixtures";
import { LiveScreen } from "./LiveScreen";

const states = fixture.run_states as Record<string, unknown>;
const ended = fixture.ended_states as { reason: string }[];
const ok = (body: unknown, status = 200): ApiResponse => ({ status, body });

type Responder = (method: string, path: string, signal: AbortSignal) => Promise<ApiResponse> | ApiResponse;

interface Rig {
  client: ApiClient;
  session: LiveSession;
  calls: { method: string; path: string; authed: boolean }[];
  respond: { current: Responder };
}

/** A client whose answers a test sets, and a session that is signed in (or not). */
async function rig(options: { signedIn?: boolean; health?: Responder } = {}): Promise<Rig> {
  const calls: Rig["calls"] = [];
  const respond: { current: Responder } = { current: () => ok({}, 500) };
  const handle = (authed: boolean) => async (method: string, path: string, signal: AbortSignal): Promise<ApiResponse> => {
    calls.push({ method, path, authed });
    if (path === "/health") return (options.health ?? (() => ok({ status: "ok" })))(method, path, signal);
    return respond.current(method, path, signal);
  };
  const open = handle(false);
  const authed = handle(true);
  const client: ApiClient = {
    open: (m, p, s) => open(m, p, s),
    authed: (m, p, s) => authed(m, p, s),
  };
  const h = makeSession({
    search: options.signedIn === false ? "" : "?code=C&state=S",
    stored:
      options.signedIn === false
        ? {}
        : { "precertly.live.signin": JSON.stringify({ state: "S", verifier: "V".repeat(64), returnHash: "#/live", createdAt: 1_000_000 }) },
    fetch: async () => tokenResponse(),
  });
  await h.session.handleCallback();
  return { client, session: h.session, calls, respond };
}

function feature(r: Rig): LiveFeature {
  return { Screen: ({ caseId }) => <LiveScreen caseId={caseId} session={r.session} client={r.client} /> };
}

async function open(r: Rig, hash = "#/live") {
  window.location.hash = hash;
  const view = render(<App store={new DataStore(diskFetch())} live={feature(r)} />);
  await screen.findByRole("heading", { name: "Live run" });
  return view;
}

async function violations(container: Element): Promise<string[]> {
  const result = await axe.run(container as unknown as axe.ElementContext, {
    rules: { "color-contrast": { enabled: false } },
  });
  return result.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.html).join(" | ")}`);
}

const runButton = () => screen.getByRole("button", { name: "Run live" });

describe("the live page: the environment", () => {
  it("asks once when it opens and shows that the environment is running", async () => {
    const r = await rig();
    await open(r);
    await waitFor(() => expect(screen.getByText("The live environment is running.")).toBeInTheDocument());
    expect(r.calls.filter((c) => c.path === "/health")).toEqual([{ method: "GET", path: "/health", authed: false }]);
  });

  it("does not ask again when the visitor moves between #/live and #/live/CASE_ID", async () => {
    const r = await rig();
    await open(r);
    await screen.findByText("The live environment is running.");
    await act(async () => {
      window.location.hash = "#/live/facet-01";
    });
    await act(async () => {
      window.location.hash = "#/live";
    });
    expect(r.calls.filter((c) => c.path === "/health")).toHaveLength(1);
  });

  it("asks again only when 'Check again' is pressed", async () => {
    const r = await rig();
    await open(r);
    await screen.findByText("The live environment is running.");
    await userEvent.click(screen.getByRole("button", { name: "Check again" }));
    await screen.findByText("The live environment is running.");
    expect(r.calls.filter((c) => c.path === "/health")).toHaveLength(2);
  });

  it("asks again after the visitor leaves the live section and comes back", async () => {
    const r = await rig();
    await open(r);
    await screen.findByText("The live environment is running.");
    await act(async () => {
      window.location.hash = "#/cases";
    });
    await screen.findByTestId("cases-table");
    await act(async () => {
      window.location.hash = "#/live";
    });
    await screen.findByRole("heading", { name: "Live run" });
    expect(r.calls.filter((c) => c.path === "/health")).toHaveLength(2);
  });

  it.each([
    ["a 500 from the gateway", () => ok({ message: "Internal Server Error" }, 500)],
    ["a 503", () => ok({}, 503)],
    ["a 404", () => ok({}, 404)],
    ["a network or CORS failure", () => Promise.reject(new NetworkError())],
    ["a body that is not ok", () => ok({ status: "starting" })],
  ])("says the environment is not running after %s, and leaves the recorded site alone", async (_name, health) => {
    const r = await rig({ health });
    await open(r);
    await screen.findByText(MESSAGES.down);
    expect(runButton()).toBeDisabled();
    expect(screen.getByRole("link", { name: "Browse the recorded cases" })).toHaveAttribute("href", "#/cases");
  });

  it("shows a down page that passes axe", async () => {
    const r = await rig({ health: () => ok({}, 500) });
    const { container } = await open(r);
    await screen.findByText(MESSAGES.down);
    expect(await violations(container)).toEqual([]);
  });
});

describe("the live page: signing in", () => {
  it("asks a signed-out visitor to sign in, and keeps the run button off", async () => {
    const r = await rig({ signedIn: false });
    await open(r);
    await screen.findByText("The live environment is running.");
    expect(screen.getByRole("button", { name: "Sign in" })).toBeEnabled();
    expect(runButton()).toBeDisabled();
    expect(runButton()).toHaveAccessibleDescription("Sign in first.");
    expect(r.calls.filter((c) => c.authed)).toEqual([]);
  });

  it("starts sign-in from the current page", async () => {
    const storage = fakeStorage();
    const location = fakeLocation();
    const session = new LiveSession({
      hostedUiOrigin: HOSTED_UI,
      clientId: CLIENT_ID,
      fetch: (async () => tokenResponse()) as unknown as typeof fetch,
      now: () => 1,
      location,
      replaceUrl: () => {},
      storage: () => storage,
    });
    const r = await rig({ signedIn: false });
    await open({ ...r, session }, "#/live/facet-01");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(location.assigned).toHaveLength(1));
    expect(JSON.parse(storage.data.get("precertly.live.signin") ?? "{}").returnHash).toBe("#/live/facet-01");
    expect(location.assigned[0]).toContain(`${HOSTED_UI}/oauth2/authorize`);
    expect(location.assigned[0]).toContain(encodeURIComponent(`${SITE}/`));
  });

  it("shows sign-out when signed in, and signs out", async () => {
    const r = await rig();
    await open(r);
    await userEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(r.session.bearer()).toBeNull();
    await screen.findByRole("button", { name: "Sign in" });
  });

  it("shows a fixed sentence after a failed sign-in", async () => {
    const h = makeSession({ search: "?error=access_denied&error_description=evil%3Cb%3E" });
    await h.session.handleCallback();
    const r = await rig({ signedIn: false });
    await open({ ...r, session: h.session });
    expect(await screen.findByTestId("signin-notice")).toHaveTextContent(MESSAGES.signInCancelled);
    expect(document.body.textContent).not.toContain("evil");
  });

  it("offers 'Sign in again' when the sign-in has expired", async () => {
    const h = makeSession({
      search: "?code=C&state=S",
      stored: { "precertly.live.signin": JSON.stringify({ state: "S", verifier: "V".repeat(64), returnHash: "#/live", createdAt: 1_000_000 }) },
    });
    await h.session.handleCallback();
    h.clock.now += 3_700_000;
    h.session.bearer();
    const r = await rig({ signedIn: false });
    await open({ ...r, session: h.session });
    expect(await screen.findByTestId("signin-notice")).toHaveTextContent(MESSAGES.expired);
    expect(screen.getByRole("button", { name: "Sign in again" })).toBeEnabled();
  });

  it("a signed-in page passes axe", async () => {
    const r = await rig();
    const { container } = await open(r, "#/live/facet-01");
    await screen.findByText("The live environment is running.");
    expect(await violations(container)).toEqual([]);
  });

  it("a signed-out page passes axe", async () => {
    const r = await rig({ signedIn: false });
    const { container } = await open(r);
    await screen.findByText("The live environment is running.");
    expect(await violations(container)).toEqual([]);
  });
});

describe("the live page: the case", () => {
  it("takes the case from the address and lists the recorded cases", async () => {
    const r = await rig();
    await open(r, "#/live/facet-03");
    expect(screen.getByLabelText("Case")).toHaveValue("facet-03");
    expect(within(screen.getByLabelText("Case")).getAllByRole("option").length).toBeGreaterThan(20);
  });

  it("says an unknown case is not available and keeps the run button off", async () => {
    const r = await rig();
    await open(r, "#/live/not-a-case");
    await screen.findByText("The live environment is running.");
    expect(screen.getByText(MESSAGES.notAvailableCase)).toBeInTheDocument();
    expect(runButton()).toBeDisabled();
  });

  it("changing the case changes the address", async () => {
    const r = await rig();
    await open(r, "#/live");
    await userEvent.selectOptions(screen.getByLabelText("Case"), "facet-02");
    expect(window.location.hash).toBe("#/live/facet-02");
  });
});

describe("the live page: a run", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  async function startRun(r: Rig) {
    await open(r, "#/live/facet-01");
    await screen.findByText("The live environment is running.");
    await userEvent.click(runButton());
  }

  it("shows the stages in order with the exact words, announces only the stage, and counts seconds outside it", async () => {
    const r = await rig();
    const queue = [states.checking_limits, states.ingesting, states.judging, states.done];
    r.respond.current = (method) => (method === "POST" ? ok(fixture.started, 202) : ok(queue.shift()));
    await startRun(r);
    const progress = await screen.findByTestId("progress");
    expect(progress).toHaveTextContent("Waking the database (about a minute and a quarter from cold)");
    expect(screen.getByTestId("stage-announcement")).toHaveTextContent(
      "Waking the database (about a minute and a quarter from cold)",
    );
    expect(progress.querySelector('[data-stage="waking_database"]')).toHaveAttribute("aria-current", "step");
    // The counter moves every second and is not in the live region.
    const elapsed = screen.getByTestId("elapsed");
    expect(screen.getByTestId("stage-announcement").contains(elapsed)).toBe(false);
    expect(screen.getByTestId("stage-announcement").closest("[aria-live]")?.contains(elapsed)).toBe(false);
    const before = elapsed.textContent;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1500);
    });
    expect(screen.getByTestId("elapsed").textContent).not.toBe(before);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2600);
    });
    await waitFor(() => expect(screen.getByTestId("stage-announcement")).toHaveTextContent("Checking the daily limits"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4000);
    });
    expect(screen.getByTestId("stage-announcement")).toHaveTextContent("Ingesting the chart");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4000);
    });
    expect(screen.getByTestId("stage-announcement")).toHaveTextContent("Judging the criteria");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4000);
    });
    expect(await screen.findByTestId("live-result")).toBeInTheDocument();
  });

  it("a run in progress passes axe", async () => {
    const r = await rig();
    r.respond.current = (method) => (method === "POST" ? ok(fixture.started, 202) : ok(states.judging));
    await startRun(r);
    const progress = await screen.findByTestId("progress");
    expect(await violations(progress.parentElement as Element)).toEqual([]);
  });

  it("a double click starts one run", async () => {
    const r = await rig();
    r.respond.current = (method) => (method === "POST" ? ok(fixture.started, 202) : ok(states.judging));
    await open(r, "#/live/facet-01");
    await screen.findByText("The live environment is running.");
    await userEvent.dblClick(runButton());
    await screen.findByTestId("progress");
    expect(r.calls.filter((c) => c.method === "POST")).toHaveLength(1);
  });

  it("posts only the case id, with the token", async () => {
    const r = await rig();
    r.respond.current = (method) => (method === "POST" ? ok(fixture.started, 202) : ok(states.judging));
    await startRun(r);
    await screen.findByTestId("progress");
    expect(r.calls.find((c) => c.method === "POST")).toEqual({ method: "POST", path: "/live/runs", authed: true });
  });

  describe("the result", () => {
    async function finished() {
      const r = await rig();
      r.respond.current = (method) => (method === "POST" ? ok(fixture.started, 202) : ok(states.done));
      await startRun(r);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(4100);
      });
      return { r, result: await screen.findByTestId("live-result") };
    }

    it("shows both notices, token counts, latency and the per-stage timings", async () => {
      const { result } = await finished();
      expect(within(result).getByTestId("synthetic-notice")).toHaveTextContent(
        "All data is synthetic; no real patient is involved.",
      );
      expect(within(result).getByTestId("server-notice")).toHaveTextContent(fixture.notice);
      const usage = within(result).getByTestId("live-usage");
      expect(usage).toHaveTextContent("12,000");
      expect(usage).toHaveTextContent("3,400");
      expect(usage).toHaveTextContent("41.3 s");
      expect(within(usage).getAllByTestId("stage-timing")).toHaveLength(4);
      expect(usage).toHaveTextContent("12.3 s");
    });

    it("reads 'Not applicable' for a criterion whose condition does not hold, and shows every verdict as icon and words", async () => {
      const { result } = await finished();
      const b = result.querySelector('[data-criterion="example-criterion-b"]') as HTMLElement;
      expect(within(b).getByText("Not applicable")).toBeInTheDocument();
      expect(b.querySelector('[data-outcome="not_applicable"] svg')).not.toBeNull();
      for (const li of within(result).getAllByTestId("live-verdict")) {
        const pill = li.querySelector("[data-outcome]") as HTMLElement;
        expect(pill.textContent?.trim().length).toBeGreaterThan(2);
        expect(pill.querySelector("svg")).not.toBeNull();
      }
    });

    it("shows quotes and chunk ids as text, and the reviewer checklist", async () => {
      const { result } = await finished();
      expect(within(result).getByText("example quote from a synthetic note")).toBeInTheDocument();
      expect(within(result).getByText("DocumentReference/example-note#0")).toBeInTheDocument();
      expect(within(result).getByTestId("reviewer-checklist")).toHaveTextContent("Confirm the attestation with the provider.");
    });

    it("renders server text as text: markup in a rationale becomes visible characters, not elements", async () => {
      const { result } = await finished();
      expect(result.textContent).toContain("<b>Markup in a rationale is text.</b>");
      expect(result.querySelector("b")).toBeNull();
    });

    it("shows no label, accuracy, correctness or recorded comparison", async () => {
      const { result } = await finished();
      // The server's own notice says "no accuracy claim"; everything else must stay clear of the words.
      const clone = result.cloneNode(true) as HTMLElement;
      clone.querySelector('[data-testid="server-notice"]')?.remove();
      const text = (clone.textContent ?? "").toLowerCase();
      for (const word of ["expected", "incorrect", "correct", "accuracy", "label", "recorded", "false approval", "wrong", "score"]) {
        expect(text).not.toContain(word);
      }
      expect(clone.querySelector("[data-correct], [data-false-approval]")).toBeNull();
    });

    it("never puts the token in the page", async () => {
      const { result } = await finished();
      expect(document.body.innerHTML).not.toContain(FAKE_TOKEN);
      expect(result.innerHTML).not.toContain(FAKE_TOKEN);
    });

    it("a result passes axe", async () => {
      const { result } = await finished();
      expect(await violations(result.parentElement as Element)).toEqual([]);
    });
  });

  describe("failures", () => {
    async function fails(respond: Responder) {
      const r = await rig();
      r.respond.current = respond;
      await startRun(r);
      return r;
    }
    const run = (view: Responder): Responder => (method, path, signal) =>
      method === "POST" ? ok(fixture.started, 202) : view(method, path, signal);

    it.each(ended.map((e) => [e.reason, e] as const))("shows only the server's fixed message for %s", async (reason, state) => {
      await fails(run(() => ok(state)));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(4100);
      });
      const alert = await screen.findByTestId("run-end");
      expect(alert).toHaveTextContent((fixture.messages as Record<string, string>)[reason] ?? "");
    });

    it("never shows text the server sends as an error", async () => {
      const evil = { ...(ended[0] as object), message: "<img src=x onerror=alert(1)> secret-host.example" };
      await fails(run(() => ok({ ...evil, message: "x".repeat(400) })));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(4100);
      });
      const alert = await screen.findByTestId("run-end");
      expect(alert.textContent).not.toContain("xxxx");
      expect(alert).toHaveTextContent("The run failed.");
      expect(alert.querySelector("img")).toBeNull();
    });

    it("says another run is in progress on a 409", async () => {
      await fails((method) => (method === "POST" ? ok({ detail: "a live run is already in progress" }, 409) : ok({})));
      const alert = await screen.findByTestId("run-end");
      expect(alert).toHaveTextContent(MESSAGES.anotherRunInProgress);
      expect(alert.textContent).not.toContain("a live run is already in progress");
    });

    it("says the case is not available on a 404 from the start", async () => {
      await fails((method) => (method === "POST" ? ok({ detail: "not found" }, 404) : ok({})));
      expect(await screen.findByTestId("run-end")).toHaveTextContent(MESSAGES.notAvailableCase);
    });

    it("says the run is no longer available on a 404 while polling", async () => {
      await fails(run(() => ok({ detail: "not found" }, 404)));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(4100);
      });
      expect(await screen.findByTestId("run-end")).toHaveTextContent(
        "This run is no longer available. The live environment may have been stopped.",
      );
    });

    it("ends with 'Your sign-in has expired' and a 'Sign in again' button on a 401", async () => {
      await fails(run(() => ok({ message: "Unauthorized" }, 401)));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(4100);
      });
      expect(await screen.findByTestId("run-end")).toHaveTextContent("Your sign-in has expired");
      expect(screen.getByRole("button", { name: "Sign in again" })).toBeEnabled();
    });

    it("says the connection was lost after three failed polls, with what that means", async () => {
      await fails(run(() => Promise.reject(new NetworkError())));
      for (let i = 0; i < 3; i += 1) {
        await act(async () => {
          await vi.advanceTimersByTimeAsync(4100);
        });
      }
      const alert = await screen.findByTestId("run-end");
      expect(alert).toHaveTextContent(/may still be finishing on the server/);
      expect(alert).toHaveTextContent(/cannot be recovered/);
    });

    it("an unexpected response ends the run with a fixed sentence", async () => {
      await fails(run(() => ok({ run_id: "x", status: "queued" })));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(4100);
      });
      expect(await screen.findByTestId("run-end")).toHaveTextContent(MESSAGES.unexpectedResponse);
    });

    it("a failure state passes axe, and can be dismissed", async () => {
      await fails(run(() => ok({}, 404)));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(4100);
      });
      const alert = await screen.findByTestId("run-end");
      expect(await violations(alert.parentElement as Element)).toEqual([]);
      await userEvent.click(screen.getByRole("button", { name: "Dismiss" }));
      expect(screen.queryByTestId("run-end")).toBeNull();
    });
  });

  it("stops polling when the visitor leaves the page", async () => {
    const r = await rig();
    r.respond.current = (method) => (method === "POST" ? ok(fixture.started, 202) : ok(states.judging));
    const { unmount } = await (async () => {
      const v = await open(r, "#/live/facet-01");
      await screen.findByText("The live environment is running.");
      await userEvent.click(runButton());
      await screen.findByTestId("progress");
      return v;
    })();
    const gets = () => r.calls.filter((c) => c.method === "GET" && c.path !== "/health").length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4100);
    });
    const before = gets();
    unmount();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(gets()).toBe(before);
  });
});

describe("keyboard use", () => {
  it("reaches sign-in, the case list and the run button in a sensible order", async () => {
    const r = await rig({ signedIn: false });
    await open(r, "#/live/facet-01");
    await screen.findByText("The live environment is running.");
    const order: string[] = [];
    const user = userEvent.setup();
    document.body.focus();
    for (let i = 0; i < 40 && !order.includes("Case"); i += 1) {
      await user.tab();
      const el = document.activeElement as HTMLElement;
      order.push(el.getAttribute("aria-label") ?? el.textContent ?? el.id);
    }
    const flat = order.join("|");
    expect(flat.indexOf("Check again")).toBeGreaterThan(-1);
    expect(flat.indexOf("Check again")).toBeLessThan(flat.indexOf("Sign in"));
  });

  it("can start a run with the keyboard alone", async () => {
    const r = await rig();
    r.respond.current = (method) => (method === "POST" ? ok(fixture.started, 202) : ok(states.judging));
    await open(r, "#/live/facet-01");
    await screen.findByText("The live environment is running.");
    runButton().focus();
    await userEvent.keyboard("{Enter}");
    expect(await screen.findByTestId("progress")).toBeInTheDocument();
  });
});
