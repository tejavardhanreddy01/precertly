import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import { DataStore } from "./lib/data";
import { normalizeWhitespace } from "./lib/highlight";
import { allCases, diskFetch, readCase, readIndex, readPolicy } from "./test/fixtures";
import { loaded, renderAt } from "./test/render";

const index = readIndex();

describe("every screen", () => {
  it("shows all banners, the run label and the footer", async () => {
    renderAt("#/cases");
    const strip = await loaded();
    for (const banner of index.banners) expect(within(strip).getByText(banner)).toBeInTheDocument();
    const label = screen.getByTestId("run-label").textContent ?? "";
    expect(label).toContain(index.run.run_date);
    expect(label).toContain(`prompt version ${index.run.prompt_version}`);
    expect(label).toContain(index.run.system_prompt_sha256.slice(0, 12));
    expect(label).not.toContain(index.run.system_prompt_sha256.slice(0, 13));
    expect(label).toContain(index.run.git_sha);
    expect(label).toContain(index.run.model_id);
    const footer = screen.getByRole("contentinfo");
    expect(footer).toHaveTextContent("Recorded runs only. Nothing runs when you open this site.");
    expect(within(footer).getByRole("link")).toHaveAttribute(
      "href",
      "https://github.com/tejavardhanreddy01/precertly",
    );
  });

  it("never claims to be live or validated", async () => {
    renderAt("#/cases");
    await loaded();
    const text = document.body.textContent ?? "";
    expect(text).not.toMatch(/\blive\b/i);
    expect(text).not.toMatch(/clinically validated(?!\.)/i);
    expect(text).toMatch(/not clinically validated/i);
  });

  it("keeps the notices strip free of any collapse control", async () => {
    renderAt("#/cases");
    const strip = await loaded();
    expect(within(strip).queryByRole("button")).toBeNull();
    expect(strip.querySelector("details")).toBeNull();
  });
});

describe("landing", () => {
  it("redirects to the first tour case and offers the tour", async () => {
    renderAt("");
    await loaded();
    await waitFor(() => expect(window.location.hash).toMatch(/^#\/cases\/facet-03\?.*tour=1/));
    expect(screen.getAllByRole("link", { name: "Take the 4-step tour" }).length).toBeGreaterThan(0);
    expect(await screen.findByRole("heading", { level: 1, name: /facet-03/ })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Tour" })).toHaveTextContent("step 1 of 4");
  });
});

describe("cases list", () => {
  it("groups the 29 cases by set and marks the 18 unscored", async () => {
    renderAt("#/cases");
    await loaded();
    const headings = (await screen.findAllByRole("heading", { level: 2 })).map((h) => h.textContent ?? "");
    expect(headings.map((h) => h.replace(/\s*\(\d+ cases\)$/, ""))).toEqual([
      "Facet joint interventions",
      "Positive airway pressure (PAP)",
      "Bariatric surgery",
      "Spinal cord stimulators",
    ]);
    expect(screen.getAllByRole("heading", { level: 3 })).toHaveLength(29);
    expect(screen.getAllByText("labels only, not yet scored")).toHaveLength(18);
    expect(document.querySelectorAll('[data-correct="true"]')).toHaveLength(index.summary.cases_correct);
    expect(document.querySelectorAll('[data-correct="false"]')).toHaveLength(
      index.summary.cases - index.summary.cases_correct,
    );
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
    expect(row).toHaveAttribute("aria-expanded", "true");
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
    // One in the overall panel, one in the verdict panel.
    expect(document.querySelectorAll("[data-false-approval]")).toHaveLength(2);
    expect(screen.getAllByText(/False approval/).length).toBeGreaterThanOrEqual(2);
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
    "#/cases/pap-01",
    "#/policies",
    "#/policies/facet-joint-interventions",
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
