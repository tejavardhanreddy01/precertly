import { render, screen, within } from "@testing-library/react";
import axe from "axe-core";
import { App } from "../App";
import { DataStore } from "../lib/data";
import type { ApiClient } from "../lib/live/http";
import type { LiveFeature } from "../lib/live-feature";
import { loaded, renderAt } from "../test/render";
import { diskFetch } from "../test/fixtures";

describe("a recorded-only build (no live module given to the app)", () => {
  it("has no Live item in the top bar and no Run live link on a case", async () => {
    renderAt("#/cases/facet-03");
    await loaded();
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(within(nav).queryByRole("link", { name: "Live" })).toBeNull();
    expect(screen.queryByRole("link", { name: /Run live/ })).toBeNull();
  });

  it.each(["#/live", "#/live/facet-01"])("shows the not-found page at %s", async (hash) => {
    renderAt(hash);
    await loaded();
    expect(await screen.findByRole("heading", { name: "Page not found" })).toBeInTheDocument();
    expect(document.title).toContain("Page not found");
    expect(screen.queryByRole("heading", { name: "Live run" })).toBeNull();
  });

  it.each(["#/live", "#/live/facet-01"])("keeps the recorded chip and footer at %s, which is not a page here", async (hash) => {
    renderAt(hash);
    await loaded();
    expect(within(screen.getByRole("banner")).getByRole("button", { name: "Recorded runs on synthetic data" })).toBeInTheDocument();
    expect(screen.getByRole("contentinfo")).toHaveTextContent("Recorded runs only. Nothing runs when you open this site.");
  });

  it("still says nothing runs when the site is open", async () => {
    renderAt("#/");
    await loaded();
    expect(screen.getAllByText(/Nothing runs when you open this site/).length).toBeGreaterThan(0);
  });
});

describe("a build with live mode", () => {
  function setup(hash: string) {
    const calls: string[] = [];
    const client: ApiClient = {
      open: async (_m, path) => {
        calls.push(path);
        return { status: 200, body: { status: "ok" } };
      },
      authed: async (_m, path) => {
        calls.push(path);
        return { status: 200, body: {} };
      },
    };
    const live: LiveFeature = { Screen: ({ caseId }) => <p data-testid="live-screen">{caseId ?? "none"}</p> };
    window.location.hash = hash;
    const view = render(<App store={new DataStore(diskFetch())} live={live} />);
    return { calls, client, view };
  }

  it("adds the Live item after About, and highlights it on the live pages only", async () => {
    setup("#/live/facet-01");
    await loaded();
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(within(nav).getAllByRole("link").map((a) => a.textContent)).toEqual(["Cases", "Policies", "About", "Live"]);
    expect(within(nav).getByRole("link", { name: "Live" })).toHaveAttribute("aria-current", "page");
    expect(within(nav).getByRole("link", { name: "Cases" })).not.toHaveAttribute("aria-current");
    expect(await screen.findByTestId("live-screen")).toHaveTextContent("facet-01");
  });

  it("gives #/live and #/live/CASE_ID the live screen and a deeper address the not-found page", async () => {
    const a = setup("#/live");
    await loaded();
    expect(await screen.findByTestId("live-screen")).toHaveTextContent("none");
    a.view.unmount();
    setup("#/live/a/b");
    await loaded();
    expect(await screen.findByRole("heading", { name: "Page not found" })).toBeInTheDocument();
  });

  it("puts a Run live link in the case summary bar that opens the live page for that case", async () => {
    setup("#/cases/facet-03");
    await loaded();
    const bar = await screen.findByTestId("summary-bar");
    expect(within(bar).getByRole("link", { name: /Run live/ })).toHaveAttribute("href", "#/live/facet-03");
  });

  it("keeps the recorded chip on a case page, which has no footer", async () => {
    setup("#/cases/facet-03");
    await loaded();
    expect(within(screen.getByRole("banner")).getByRole("button", { name: "Recorded runs on synthetic data" })).toBeInTheDocument();
  });

  it("makes no request on any other page (opening the site triggers nothing)", async () => {
    const { calls } = setup("#/");
    await loaded();
    expect(calls).toEqual([]);
  });

  it.each(["#/live", "#/live/facet-01"])("shows the live chip and footer at %s", async (hash) => {
    setup(hash);
    await loaded();
    const chip = within(screen.getByRole("banner")).getByRole("button", { name: "Synthetic data only" });
    expect(chip).toBeInTheDocument();
    expect(within(screen.getByRole("banner")).queryByText("Recorded runs on synthetic data")).toBeNull();
    const footer = screen.getByRole("contentinfo");
    expect(footer).toHaveTextContent(
      "Synthetic data only. A judgment runs only when you press Run live while signed in; the recorded pages run nothing.",
    );
    expect(footer).not.toHaveTextContent("Recorded runs only.");
  });

  it.each(["#/", "#/cases", "#/policies", "#/about", "#/live/a/b"])(
    "keeps the recorded chip and footer at %s, with live mode on",
    async (hash) => {
      setup(hash);
      await loaded();
      expect(within(screen.getByRole("banner")).getByRole("button", { name: "Recorded runs on synthetic data" })).toBeInTheDocument();
      expect(screen.getByRole("contentinfo")).toHaveTextContent("Recorded runs only. Nothing runs when you open this site.");
    },
  );

  it("keeps the top bar accessible with the extra item", async () => {
    const { view } = setup("#/cases");
    await loaded();
    const result = await axe.run(view.container as unknown as axe.ElementContext, { rules: { "color-contrast": { enabled: false } } });
    expect(result.violations.map((v) => v.id)).toEqual([]);
  });
});
