import { cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import { DataStore } from "./lib/data";
import { normalizeWhitespace } from "./lib/highlight";
import { allCases, diskFetch, readCase, readIndex, readPolicy } from "./test/fixtures";
import { loaded, renderAt } from "./test/render";

const index = readIndex();

describe("every screen", () => {
  it("keeps the short notice in the top bar, with the recorded run behind it", async () => {
    renderAt("#/cases");
    const chip = await loaded();
    expect(chip).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(chip);
    const details = screen.getByTestId("run-details");
    const text = details.textContent ?? "";
    expect(text).toContain(index.run.run_date);
    expect(text).toContain(index.run.prompt_version);
    expect(text).toContain(index.run.system_prompt_sha256.slice(0, 12));
    expect(text).not.toContain(index.run.system_prompt_sha256.slice(0, 13));
    expect(text).toContain(index.run.git_sha);
    expect(text).toContain(index.run.model_id);
    expect(screen.getByRole("link", { name: "Read the notices and the method" })).toHaveAttribute("href", "#/about");
    // Escape closes it and gives the focus back to the chip.
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByTestId("run-details")).toBeNull();
    expect(chip).toHaveFocus();
  });

  it("has a header, a main navigation, a main region and the footer on every page", async () => {
    renderAt("#/cases");
    await loaded();
    expect(screen.getByRole("banner")).toBeInTheDocument();
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(within(nav).getAllByRole("link").map((a) => a.textContent)).toEqual(["Cases", "Policies", "About"]);
    expect(within(nav).getByRole("link", { name: "Cases" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("main")).toBeInTheDocument();
    expect(within(screen.getByRole("banner")).getByRole("link", { name: "Repository" })).toHaveAttribute(
      "href",
      "https://github.com/tejavardhanreddy01/precertly",
    );
    const footer = screen.getByRole("contentinfo");
    expect(footer).toHaveTextContent("Recorded runs only. Nothing runs when you open this site.");
    expect(within(footer).getByRole("link")).toHaveAttribute(
      "href",
      "https://github.com/tejavardhanreddy01/precertly",
    );
  });

  it("shows the full notices on the landing page and About only", async () => {
    for (const route of ["", "#/about"]) {
      renderAt(route);
      const strip = await screen.findByRole("region", { name: "Notices" });
      for (const banner of index.banners) expect(within(strip).getByText(banner)).toBeInTheDocument();
      const label = screen.getByTestId("run-label").textContent ?? "";
      expect(label).toContain(index.run.run_date);
      expect(label).toContain(`prompt version ${index.run.prompt_version}`);
      expect(label).toContain(index.run.system_prompt_sha256.slice(0, 12));
      expect(label).not.toContain(index.run.system_prompt_sha256.slice(0, 13));
      expect(label).toContain(index.run.git_sha);
      expect(label).toContain(index.run.model_id);
      // No collapse control: the notices are always open.
      expect(within(strip).queryByRole("button")).toBeNull();
      expect(strip.querySelector("details")).toBeNull();
      cleanup();
    }
    for (const route of ["#/cases", "#/policies", "#/cases/facet-03"]) {
      renderAt(route);
      await loaded();
      expect(screen.queryByRole("region", { name: "Notices" })).toBeNull();
      cleanup();
    }
  });

  it("never claims to be live or validated", async () => {
    renderAt("");
    await loaded();
    const text = document.body.textContent ?? "";
    expect(text).not.toMatch(/\blive\b/i);
    expect(text).not.toMatch(/clinically validated(?!\.)/i);
    expect(text).toMatch(/not clinically validated/i);
  });
});

describe("landing", () => {
  it("offers the tour and the cases above the four tiles", async () => {
    renderAt("");
    await loaded();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Every verdict, backed by a quote from the chart.");
    expect(screen.getByRole("link", { name: /Start the 4-step tour/ })).toHaveAttribute(
      "href",
      expect.stringMatching(/^#\/cases\/facet-03\?.*tour=1/),
    );
    expect(screen.getByRole("link", { name: "Browse the cases" })).toHaveAttribute("href", "#/cases");
    // The landing page no longer redirects.
    expect(window.location.hash).toBe("");
    expect(screen.getAllByTestId("tile-value")).toHaveLength(4);
  });

  it("starts the tour on the first step", async () => {
    renderAt("");
    await loaded();
    await userEvent.click(screen.getByRole("link", { name: /Start the 4-step tour/ }));
    expect(await screen.findByRole("heading", { level: 1, name: /facet-03/ })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Tour" })).toHaveTextContent("step 1 of 4");
  });
});

describe("cases list", () => {
  const rows = () => [...document.querySelectorAll<HTMLElement>("[data-case-row]")];

  it("lists the 29 cases in a table and marks the 18 unscored", async () => {
    renderAt("#/cases");
    await loaded();
    const table = await screen.findByTestId("cases-table");
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual([
      "Case",
      "Policy",
      "Procedure",
      "Expected",
      "Recorded",
    ]);
    expect(rows()).toHaveLength(29);
    expect(screen.getAllByText("labels only, not yet scored")).toHaveLength(18);
    expect(rows().filter((r) => r.dataset.scored === "false")).toHaveLength(18);
    expect(document.querySelectorAll('[data-correct="true"]')).toHaveLength(index.summary.cases_correct);
    expect(document.querySelectorAll('[data-correct="false"]')).toHaveLength(
      index.summary.cases - index.summary.cases_correct,
    );
  });

  it("makes each row a link to its case that covers the whole row", async () => {
    renderAt("#/cases");
    await loaded();
    await screen.findByTestId("cases-table");
    const link = within(rows()[0] as HTMLElement).getByRole("link");
    expect(link).toHaveAttribute("href", "#/cases/facet-01");
    expect(link.className).toContain("after:absolute");
    expect(link.className).toContain("after:inset-0");
    await userEvent.click(link);
    expect(await screen.findByRole("heading", { level: 1, name: /facet-01/ })).toBeInTheDocument();
  });

  it("filters by policy with chips and says what it shows", async () => {
    renderAt("#/cases");
    await loaded();
    await screen.findByTestId("cases-table");
    const group = screen.getByRole("group", { name: "Filter by policy" });
    const chips = within(group).getAllByRole("button");
    expect(chips.map((c) => (c.textContent ?? "").replace(/\s+\d+$/, ""))).toEqual([
      "All",
      "Facet",
      "PAP",
      "Bariatric",
      "SCS",
    ]);
    expect(chips[0]).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(within(group).getByRole("button", { name: /^Facet/ }));
    expect(rows()).toHaveLength(11);
    expect(rows().every((r) => r.dataset.scored === "true")).toBe(true);
    expect(within(group).getByRole("button", { name: /^Facet/ })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("Showing 11 of 29 cases.")).toBeInTheDocument();
    await userEvent.click(within(group).getByRole("button", { name: /^SCS/ }));
    expect(rows().length).toBe(index.cases.filter((c) => c.set === "spinal-cord-stimulators").length);
    expect(rows().every((r) => r.dataset.scored === "false")).toBe(true);
    // Pressing the chip again clears the filter.
    await userEvent.click(within(group).getByRole("button", { name: /^SCS/ }));
    expect(rows()).toHaveLength(29);
  });
});

describe("case view for every case", () => {
  for (const c of allCases()) {
    it(`${c.id} renders ${c.scored ? "its recorded verdicts" : "labels only"}`, async () => {
      renderAt(`#/cases/${c.id}`);
      await screen.findByRole("heading", { level: 1, name: new RegExp(c.id) });
      const rows = document.querySelectorAll("[data-verdict-row]");
      if (c.scored && c.result) {
        expect(rows).toHaveLength(c.result.verdicts.length);
        expect(screen.queryByText("labels only, not yet scored")).toBeNull();
        expect(screen.getAllByTestId("checklist-item")).toHaveLength(c.criteria.filter((k) => !k.judged).length);
      } else {
        expect(rows).toHaveLength(0);
        expect(screen.getAllByText(/not yet scored/).length).toBeGreaterThan(0);
        // Selecting a criterion shows its label and never a verdict.
        const first = c.criteria.find((k) => k.judged);
        expect(first).toBeDefined();
        await userEvent.click(screen.getByRole("button", { name: new RegExp(first?.criterion ?? "") }));
        expect(await screen.findByRole("heading", { name: "Label (not yet scored by the model)" })).toBeInTheDocument();
        expect(screen.queryByTestId("verdict-panel")).toBeNull();
        expect(screen.queryByText("Recorded verdict")).toBeNull();
      }
    });
  }
});

describe("selecting a criterion", () => {
  it("shows the verdict, highlights the cited quote and scrolls the chart", async () => {
    const scrollTo = vi.fn();
    Element.prototype.scrollTo = scrollTo as unknown as typeof Element.prototype.scrollTo;
    const file = readCase("facet-03");
    const verdict = file.result?.verdicts.find((v) => v.criterion === "chronic-axial-pain-functional-deficit");
    expect(verdict).toBeDefined();
    renderAt("#/cases/facet-03");
    await screen.findByRole("heading", { level: 1, name: /facet-03/ });
    const user = userEvent.setup();
    const row = screen.getByRole("button", { name: /chronic-axial-pain-functional-deficit/ });
    row.focus();
    await user.keyboard("{Enter}");
    expect(row).toHaveAttribute("aria-pressed", "true");
    expect(window.location.hash).toContain("c=chronic-axial-pain-functional-deficit");
    const panel = await screen.findByTestId("verdict-panel");
    expect(within(panel).getByText("Decided by the model")).toBeInTheDocument();
    expect(within(panel).getByText(verdict?.rationale ?? "")).toBeInTheDocument();
    const marks = [...document.querySelectorAll("mark")].map((m) => normalizeWhitespace(m.textContent ?? ""));
    for (const cite of verdict?.citations ?? []) {
      expect(marks.some((m) => m.includes(normalizeWhitespace(cite.quote)))).toBe(true);
    }
    expect(scrollTo).toHaveBeenCalled();
    // The cited chunk is open and the others are closed.
    const open = document.querySelectorAll('[data-testid="chart-viewer"] button[aria-expanded="true"]');
    const cited = new Set((verdict?.citations ?? []).map((c) => c.chunk_id));
    expect(open).toHaveLength(cited.size);
    // Pressing the row again clears the selection.
    await user.click(row);
    expect(window.location.hash).not.toContain("c=");
  });

  it("links a citation to its highlighted spot through the hash", async () => {
    renderAt("#/cases/facet-03?c=chronic-axial-pain-functional-deficit");
    const panel = await screen.findByTestId("verdict-panel");
    const links = within(panel).getAllByRole("link", { name: "Show in the chart" });
    expect(links[1]).toHaveAttribute("href", expect.stringContaining("cite=1"));
    await userEvent.click(links[1] as HTMLElement);
    await waitFor(() => expect(document.querySelector('mark[data-active="true"]')).not.toBeNull());
  });

  it("reopens a cited chunk the reader closed when a citation link points at it", async () => {
    const scrollTo = vi.fn();
    Element.prototype.scrollTo = scrollTo as unknown as typeof Element.prototype.scrollTo;
    renderAt("#/cases/facet-03?c=chronic-axial-pain-functional-deficit");
    const panel = await screen.findByTestId("verdict-panel");
    const viewer = screen.getByTestId("chart-viewer");
    const openButton = () => viewer.querySelector<HTMLElement>('[data-cited="true"] button[aria-expanded]');
    const user = userEvent.setup();
    await user.click(openButton() as HTMLElement);
    expect(viewer.querySelector("mark")).toBeNull();
    scrollTo.mockClear();
    await user.click(within(panel).getAllByRole("link", { name: "Show in the chart" })[0] as HTMLElement);
    await waitFor(() => expect(document.querySelector('mark[data-active="true"]')).not.toBeNull());
    // The chunk was open before the scroll was computed: one call, with a whole-pixel position.
    expect(scrollTo).toHaveBeenCalledTimes(1);
    const arg = scrollTo.mock.calls[0]?.[0] as { top: number };
    expect(Number.isInteger(arg.top)).toBe(true);
  });

  it("explains a verdict a rule decided and shows its computed fact", async () => {
    renderAt("#/cases/facet-02?c=two-mbbs-80pct-sustained-relief");
    const panel = await screen.findByTestId("verdict-panel");
    expect(within(panel).getByText("Decided by a rule")).toBeInTheDocument();
    expect(within(panel).getByText(/deterministic code/)).toBeInTheDocument();
    expect(within(panel).getByTestId("computed-fact")).toHaveTextContent("71%, below 80%");
  });

  it("says when verification changed a verdict to insufficient", async () => {
    const verdict = readCase("facet-01").result?.verdicts.find((v) => v.criterion === "no-non-facet-pathology");
    renderAt("#/cases/facet-01?c=no-non-facet-pathology");
    const box = await screen.findByTestId("invalidated");
    expect(box).toHaveTextContent(
      "The model's citation failed verification, so the verdict became insufficient.",
    );
    expect(box).toHaveTextContent(verdict?.invalidated ?? "missing");
    expect(screen.getByTestId("model-outcome")).toHaveTextContent("Met");
  });

  it("marks the false approval prominently", async () => {
    renderAt("#/cases/facet-11?c=conservative-care-failed");
    await screen.findByTestId("verdict-panel");
    // The summary bar, the criteria row and the verdict pane each say it, as a pill or a block.
    expect(within(screen.getByTestId("summary-bar")).getByText("False approval")).toBeInTheDocument();
    const falseRow = document.querySelector("[data-false-approval-row]");
    expect(falseRow).not.toBeNull();
    expect(falseRow).toHaveAttribute("data-criterion", "conservative-care-failed");
    expect(within(falseRow as HTMLElement).getByText("False approval")).toBeInTheDocument();
    expect(document.querySelectorAll("[data-false-approval-row]")).toHaveLength(1);
    const panel = screen.getByTestId("verdict-panel");
    expect(panel.querySelector("[data-false-approval]")).toHaveTextContent(/False approval/);
  });

  it("lists criteria the copilot does not judge under their stage", async () => {
    renderAt("#/cases/facet-01");
    const section = await screen.findByTestId("not-judged");
    expect(within(section).getByText("Not judged by the copilot")).toBeInTheDocument();
    expect(within(section).getAllByTestId("checklist-item")).toHaveLength(3);
    expect(within(section).getAllByText(/does not judge it/).length).toBeGreaterThan(0);
  });

  it("highlights whole labeled resources on an unscored case", async () => {
    const file = readCase("pap-01");
    const label = file.expected[0];
    renderAt(`#/cases/pap-01?c=${label?.criterion}`);
    const panel = await screen.findByTestId("label-panel");
    expect(panel).toHaveTextContent(label?.fact ?? "missing");
    expect(document.querySelectorAll("mark").length).toBeGreaterThan(0);
  });
});

describe("what the model was shown", () => {
  const topK = index.run.retrieval.top_k;
  const shownBadges = () => [...document.querySelectorAll('[data-testid="chart-viewer"] [data-shown="true"]')];
  const notShownBadges = () => [...document.querySelectorAll('[data-testid="chart-viewer"] [data-shown="false"]')];

  it("badges every chunk for a selected verdict and says what the badges mean", async () => {
    const file = readCase("facet-11");
    const verdict = file.result?.verdicts.find((v) => v.criterion === "conservative-care-failed");
    renderAt("#/cases/facet-11?c=conservative-care-failed");
    await screen.findByTestId("verdict-panel");
    expect(screen.getByTestId("shown-line")).toHaveTextContent(
      `Badges show what the model was given for the selected criterion (the top ${topK} retrieved chunks plus the request fields).`,
    );
    expect(shownBadges()).toHaveLength(verdict?.shown_chunk_ids.length ?? -1);
    expect(notShownBadges()).toHaveLength(file.chart.length - (verdict?.shown_chunk_ids.length ?? 0));
    expect(shownBadges()[0]).toHaveTextContent("shown to the model");
    expect(notShownBadges()[0]).toHaveTextContent("not shown to the model");
    // The therapy records are in the chart but were not given to the model.
    const records = document.querySelector('[data-chunk-id="DocumentReference/note-pt-records#0"]');
    expect(records?.querySelector("[data-shown]")).toHaveTextContent("not shown to the model");
    const cited = document.querySelector('[data-chunk-id="DocumentReference/note-pain-clinic-eval#0"]');
    expect(cited?.querySelector("[data-shown]")?.textContent).toBe("shown to the model");
  });

  it("says a rule decided it, and badges nothing, for a rule-only verdict", async () => {
    renderAt("#/cases/facet-02?c=two-mbbs-80pct-sustained-relief");
    await screen.findByTestId("verdict-panel");
    expect(screen.getByTestId("shown-line")).toHaveTextContent(/model was not called/);
    expect(shownBadges()).toHaveLength(0);
    expect(notShownBadges()).toHaveLength(0);
  });

  it("shows no badge and no line on an unscored case or with nothing selected", async () => {
    const label = readCase("pap-01").expected[0];
    renderAt(`#/cases/pap-01?c=${label?.criterion}`);
    await screen.findByTestId("label-panel");
    expect(screen.queryByTestId("shown-line")).toBeNull();
    expect(document.querySelectorAll("[data-shown]")).toHaveLength(0);
    cleanup();
    renderAt("#/cases/facet-11");
    await screen.findByRole("heading", { level: 1, name: /facet-11/ });
    expect(screen.queryByTestId("shown-line")).toBeNull();
    expect(document.querySelectorAll("[data-shown]")).toHaveLength(0);
  });

  it("gives the tour step 3 a second link to the records the model was not shown", async () => {
    renderAt("#/cases/facet-11?c=conservative-care-failed&tour=3");
    await screen.findByTestId("verdict-panel");
    const bar = screen.getByRole("navigation", { name: "Tour" });
    const link = within(bar).getByRole("link", { name: "See the therapy records the model was not shown" });
    expect(link.getAttribute("href")).toContain("res=DocumentReference%2Fnote-pt-records");
    await userEvent.click(link);
    expect(window.location.hash).toContain("res=DocumentReference%2Fnote-pt-records");
    await waitFor(() => {
      const records = document.querySelector('[data-chunk-id="DocumentReference/note-pt-records#0"]');
      expect(records?.querySelector("mark")).not.toBeNull();
      expect(records?.querySelector("[data-shown]")).toHaveTextContent("not shown to the model");
    });
  });

  it("offers that second link on step 3 only", async () => {
    renderAt("#/cases/facet-03?c=chronic-axial-pain-functional-deficit&tour=1");
    await screen.findByTestId("verdict-panel");
    expect(screen.queryByRole("link", { name: /therapy records/ })).toBeNull();
  });
});

describe("policy view", () => {
  it("shows quotes, sources, stages and nested logic", async () => {
    const policy = readPolicy("facet-joint-interventions");
    renderAt("#/policies/facet-joint-interventions");
    await screen.findByRole("heading", { level: 1 });
    expect(screen.getAllByText(/^Stage:/)).toHaveLength(policy.criteria.length);
    expect(screen.getAllByText("All of:").length).toBeGreaterThan(0);
    expect(document.querySelector("ul ul")).not.toBeNull();
    const link = screen.getAllByRole("link").find((a) => (a.getAttribute("href") ?? "").startsWith("https://www.cms.gov/"));
    expect(link).toBeDefined();
    expect(document.querySelectorAll("blockquote").length).toBeGreaterThanOrEqual(policy.criteria.length);
  });
});

describe("about and method", () => {
  it("shows the recorded numbers with the facet-only caveat and the failing gate", async () => {
    renderAt("#/about");
    await screen.findByRole("heading", { level: 1, name: "About and method" });
    expect(screen.getByText("Facet cases only.")).toBeInTheDocument();
    const figures = screen.getByTestId("run-figures");
    expect(figures).toHaveTextContent(`${index.summary.criteria_correct} of ${index.summary.scored_criteria}`);
    expect(screen.getByRole("heading", { name: /five steps/ })).toBeInTheDocument();
    expect(document.querySelectorAll("#pipe-h ~ ol > li")).toHaveLength(5);
    const gates = screen.getByTestId("gates");
    const row = within(gates).getByRole("row", { name: /Citation precision/ });
    expect(row).toHaveTextContent("74.4% and 74.8%: fails");
    expect(within(row).getByText("Fails")).toBeInTheDocument();
    expect(within(screen.getByRole("article")).getByText(/never denies a request/)).toBeInTheDocument();
  });
});

describe("failure screens", () => {
  it("shows a readable error for a schema version mismatch", async () => {
    const store = new DataStore(async () => new Response(JSON.stringify({ schema_version: 99 })));
    renderAt("#/cases", store);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("version 99");
    expect(screen.getByRole("contentinfo")).toHaveTextContent("Nothing runs when you open this site");
  });

  it("shows a readable error when the fetch fails", async () => {
    const store = new DataStore(async () => {
      throw new TypeError("offline");
    });
    renderAt("#/cases", store);
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not fetch index.json");
  });

  it("shows an error for a case that fails to load, and for unknown routes", async () => {
    const real = diskFetch();
    const store = new DataStore(async (input) =>
      input.includes("cases/facet-05") ? new Response("no", { status: 500 }) : real(input),
    );
    renderAt("#/cases/facet-05", store);
    expect(await screen.findByRole("alert")).toHaveTextContent("HTTP 500");
  });

  it("shows not found for an unknown case and an unknown address", async () => {
    renderAt("#/cases/nope");
    expect(await screen.findByRole("alert")).toHaveTextContent("no case called");
    window.location.hash = "#/whatever";
    expect(await screen.findByText("Page not found")).toBeInTheDocument();
  });
});

describe("accessibility (axe, colour contrast excluded: jsdom cannot compute it)", () => {
  const routes = [
    "#/cases",
    "#/cases/facet-01?c=no-non-facet-pathology",
    "#/cases/facet-03?c=chronic-axial-pain-functional-deficit&tour=1",
    "#/cases/facet-11?c=conservative-care-failed",
    "#/cases/facet-11?c=conservative-care-failed&tour=3&res=DocumentReference%2Fnote-pt-records",
    "#/cases/facet-02?c=two-mbbs-80pct-sustained-relief",
    "#/cases/pap-01",
    "#/policies",
    "#/policies/facet-joint-interventions",
    "#/about",
  ];
  for (const route of routes) {
    it(`${route} has no violations`, async () => {
      renderAt(route);
      await loaded();
      await screen.findByRole("heading", { level: 1 });
      await waitFor(() => expect(screen.queryByText(/^Loading /)).toBeNull());
      const results = await axe.run(document.body, { rules: { "color-contrast": { enabled: false } } });
      expect(results.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
    });
  }
});

describe("policy page: shared criteria and criterion links", () => {
  const POLICY = "facet-joint-interventions";
  const idsIn = (root: Element) =>
    [...root.querySelectorAll("li > span.font-mono")].map((s) => (s.textContent ?? "").replace(/^\(|\)$/g, ""));
  let scrollIntoView: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    scrollIntoView = vi.fn();
    Element.prototype.scrollIntoView = scrollIntoView as unknown as typeof Element.prototype.scrollIntoView;
  });

  afterEach(() => {
    // @ts-expect-error jsdom has no scrollIntoView; remove the stand-in again
    delete Element.prototype.scrollIntoView;
  });

  it("lists the 15 shared criteria once and each procedure's own only", async () => {
    const policy = readPolicy(POLICY);
    renderAt(`#/policies/${POLICY}`);
    const shared = await screen.findByTestId("shared-criteria");
    expect(within(shared).getByRole("heading", { level: 3, name: "Required for every procedure" })).toBeInTheDocument();
    const sharedIds = idsIn(shared);
    expect(sharedIds).toHaveLength(15);
    expect(new Set(sharedIds).size).toBe(15);
    const own: Record<string, string[]> = {};
    for (const v of policy.variants) {
      const card = document.querySelector<HTMLElement>(`[data-variant="${v.id}"]`)!;
      expect(within(card).getByRole("heading", { level: 4, name: "Specific to this procedure" })).toBeInTheDocument();
      own[v.id] = idsIn(card.querySelector("[data-own-criteria]")!);
      for (const id of sharedIds) expect(own[v.id]).not.toContain(id);
    }
    expect(Object.fromEntries(Object.entries(own).map(([id, ids]) => [id, ids.length]))).toEqual({
      "first-diagnostic": 4,
      "second-diagnostic": 8,
      "therapeutic-injection": 6,
      "initial-rfa": 4,
      "repeat-rfa": 4,
    });
    expect(screen.getByText(/apply to each procedure below/)).toBeInTheDocument();
  });

  it("keeps the real nested logic behind a closed 'Show exact logic'", async () => {
    renderAt(`#/policies/${POLICY}`);
    await screen.findByTestId("shared-criteria");
    const summaries = screen.getAllByText("Show exact logic");
    expect(summaries).toHaveLength(5);
    const details = summaries[0]!.closest("details")!;
    expect(details).not.toHaveAttribute("open");
    expect(details.querySelector("ul ul")).not.toBeNull();
    expect(within(details).getAllByText("All of:")).toHaveLength(2); // the exact, nested structure
    // The own lists never print the label twice in a row.
    const own = document.querySelector("[data-own-criteria]")!;
    expect(within(own as HTMLElement).queryAllByText("All of:")).toHaveLength(0);
    await userEvent.click(summaries[0]!);
    expect(details).toHaveAttribute("open");
  });

  it("scrolls to the card on every click of a link, including a second click on the same one", async () => {
    renderAt(`#/policies/${POLICY}`);
    const shared = await screen.findByTestId("shared-criteria");
    const link = within(shared).getAllByRole("link")[0]!;
    const id = idsIn(shared)[0]!;
    scrollIntoView.mockClear();
    await userEvent.click(link);
    await waitFor(() => expect(window.location.hash).toContain(`c=${id}`));
    await waitFor(() => expect(scrollIntoView).toHaveBeenCalledTimes(1));
    expect(scrollIntoView.mock.instances[0]).toHaveAttribute("data-criterion", id);
    // The same link again: the hash is unchanged, so the effect does not run; the click scrolls.
    await userEvent.click(link);
    expect(scrollIntoView).toHaveBeenCalledTimes(2);
    expect(scrollIntoView.mock.instances[1]).toHaveAttribute("data-criterion", id);
    expect(window.location.hash).toContain(`c=${id}`);
    await userEvent.click(link);
    expect(scrollIntoView).toHaveBeenCalledTimes(3);
  });

  it("highlights the selected card and sends 'Back to variants' to the heading without changing the route", async () => {
    renderAt(`#/policies/${POLICY}?c=pain-minimum-3-months`);
    await screen.findByTestId("shared-criteria");
    const card = document.querySelector<HTMLElement>('[data-criterion="pain-minimum-3-months"]')!;
    expect(card).toHaveClass("border-accent");
    const before = window.location.hash;
    scrollIntoView.mockClear();
    await userEvent.click(within(card).getByRole("button", { name: "Back to variants" }));
    expect(scrollIntoView).toHaveBeenCalledTimes(1);
    expect(scrollIntoView.mock.instances[0]).toHaveAttribute("id", "variants-h");
    expect(window.location.hash).toBe(before);
    expect(document.querySelector('a[href="#variants-h"]')).toBeNull();
  });

  it("does not use smooth scrolling when the visitor prefers reduced motion", async () => {
    const original = window.matchMedia;
    window.matchMedia = ((query: string) => ({ matches: query.includes("reduce"), media: query })) as typeof window.matchMedia;
    try {
      renderAt(`#/policies/${POLICY}?c=pain-minimum-3-months`);
      await screen.findByTestId("shared-criteria");
      await waitFor(() => expect(scrollIntoView).toHaveBeenCalled());
      expect(scrollIntoView.mock.calls.at(-1)?.[0]).toMatchObject({ behavior: "auto" });
    } finally {
      window.matchMedia = original;
    }
  });

  it("shows no shared block for the bariatric policy, whose procedures share nothing", async () => {
    renderAt("#/policies/bariatric-surgery");
    await screen.findByRole("heading", { level: 1 });
    expect(screen.queryByTestId("shared-criteria")).toBeNull();
    for (const card of document.querySelectorAll("[data-variant]")) {
      expect(idsIn(card.querySelector("[data-own-criteria]")!)).toHaveLength(4);
    }
  });
});
