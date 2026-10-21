import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { loaded, narrowScreen, renderAt } from "../test/render";

const tab = (name: string) => screen.getByRole("tab", { name });
const pane = (id: string) => document.getElementById(`pane-${id}`) as HTMLElement;
const isShown = (id: string) => !pane(id).classList.contains("hidden");

describe("case workspace on a wide screen", () => {
  it("shows the three panes at once, with no tab control, and each can take the keyboard", async () => {
    renderAt("#/cases/facet-03");
    await loaded();
    await screen.findByTestId("summary-bar");
    expect(screen.queryByRole("tablist")).toBeNull();
    for (const id of ["criteria", "evidence", "verdict"]) expect(isShown(id)).toBe(true);
    expect(pane("criteria")).toHaveAttribute("tabindex", "0");
    expect(pane("verdict")).toHaveAttribute("tabindex", "0");
    // The chart viewer is the evidence pane's own scroll container, and it takes the keyboard too.
    expect(screen.getByTestId("chart-viewer")).toHaveAttribute("tabindex", "0");
    expect(screen.getByTestId("chart-viewer").className).toContain("overflow-y-auto");
  });

  it("pins the tour bar to the bottom of the workspace", async () => {
    renderAt("#/cases/facet-03?c=chronic-axial-pain-functional-deficit&tour=1");
    await screen.findByTestId("verdict-panel");
    const article = screen.getByRole("article");
    expect(article.className).toContain("h-full");
    expect(article.lastElementChild).toBe(screen.getByRole("navigation", { name: "Tour" }));
  });
});

describe("case workspace on a narrow screen", () => {
  beforeEach(narrowScreen);

  it("starts on Criteria and shows one pane at a time", async () => {
    renderAt("#/cases/facet-03");
    await loaded();
    await screen.findByTestId("summary-bar");
    expect(screen.getByRole("tablist", { name: "Case sections" })).toBeInTheDocument();
    expect(tab("Criteria")).toHaveAttribute("aria-selected", "true");
    expect([isShown("criteria"), isShown("evidence"), isShown("verdict")]).toEqual([true, false, false]);
  });

  it("switches to Verdict when a criterion is selected, and keeps the address parameters", async () => {
    renderAt("#/cases/facet-03?tour=1");
    await screen.findByTestId("summary-bar");
    await userEvent.click(screen.getByRole("button", { name: /chronic-axial-pain-functional-deficit/ }));
    expect(tab("Verdict")).toHaveAttribute("aria-selected", "true");
    expect([isShown("criteria"), isShown("evidence"), isShown("verdict")]).toEqual([false, false, true]);
    expect(window.location.hash).toContain("c=chronic-axial-pain-functional-deficit");
    expect(window.location.hash).toContain("tour=1");
    expect(await screen.findByTestId("verdict-panel")).toBeInTheDocument();
  });

  it("switches to Evidence from a citation link and scrolls the chart to the cited text", async () => {
    const scrollTo = vi.fn();
    Element.prototype.scrollTo = scrollTo as unknown as typeof Element.prototype.scrollTo;
    renderAt("#/cases/facet-03?c=chronic-axial-pain-functional-deficit");
    const panel = await screen.findByTestId("verdict-panel");
    expect(tab("Verdict")).toHaveAttribute("aria-selected", "true");
    expect(isShown("evidence")).toBe(false);
    // While Evidence is hidden the chart does not try to scroll (it has no height to scroll in).
    expect(scrollTo).not.toHaveBeenCalled();
    await userEvent.click(within(panel).getAllByRole("link", { name: /Show in the chart/ })[1] as HTMLElement);
    expect(tab("Evidence")).toHaveAttribute("aria-selected", "true");
    expect(isShown("evidence")).toBe(true);
    expect(window.location.hash).toContain("cite=1");
    expect(window.location.hash).toContain("c=chronic-axial-pain-functional-deficit");
    await waitFor(() => expect(document.querySelector('mark[data-active="true"]')).not.toBeNull());
    expect(scrollTo).toHaveBeenCalled();
  });

  it("scrolls the chart when the reader opens Evidence by hand", async () => {
    const scrollTo = vi.fn();
    Element.prototype.scrollTo = scrollTo as unknown as typeof Element.prototype.scrollTo;
    renderAt("#/cases/facet-03?c=chronic-axial-pain-functional-deficit");
    await screen.findByTestId("verdict-panel");
    expect(scrollTo).not.toHaveBeenCalled();
    await userEvent.click(tab("Evidence"));
    expect(scrollTo).toHaveBeenCalledTimes(1);
  });

  it("opens on the tab the address asks for", async () => {
    renderAt("#/cases/facet-03?c=chronic-axial-pain-functional-deficit&cite=0");
    await screen.findByTestId("verdict-panel");
    expect(tab("Evidence")).toHaveAttribute("aria-selected", "true");
  });

  it("switches to Evidence from the tour's link to the records the model was not shown", async () => {
    renderAt("#/cases/facet-11?c=conservative-care-failed&tour=3");
    await screen.findByTestId("verdict-panel");
    expect(tab("Verdict")).toHaveAttribute("aria-selected", "true");
    await userEvent.click(screen.getByRole("link", { name: "See the therapy records the model was not shown" }));
    expect(tab("Evidence")).toHaveAttribute("aria-selected", "true");
    expect(window.location.hash).toContain("res=DocumentReference%2Fnote-pt-records");
  });

  it("moves between the tabs with the arrow keys", async () => {
    renderAt("#/cases/facet-03");
    await screen.findByTestId("summary-bar");
    const user = userEvent.setup();
    tab("Criteria").focus();
    await user.keyboard("{ArrowRight}");
    expect(tab("Evidence")).toHaveFocus();
    expect(isShown("evidence")).toBe(true);
    await user.keyboard("{ArrowRight}{ArrowRight}");
    expect(tab("Criteria")).toHaveFocus();
    expect(isShown("criteria")).toBe(true);
  });
});
