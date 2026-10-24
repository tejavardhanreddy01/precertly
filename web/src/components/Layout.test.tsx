import { screen, within } from "@testing-library/react";
import { loaded, renderAt } from "../test/render";
import { REPO_URL } from "./Layout";

describe("top bar", () => {
  it.each(["#/", "#/cases", "#/cases/facet-03", "#/policies", "#/about"])(
    "keeps the Repository link in the bar on %s, where a case page has no footer",
    async (hash) => {
      renderAt(hash);
      await loaded();
      const bar = screen.getByRole("banner");
      expect(within(bar).getByRole("link", { name: "Repository" })).toHaveAttribute("href", REPO_URL);
    },
  );

  it("names the home link Precertly whether the bar shows the word or the mark", async () => {
    renderAt("#/cases");
    await loaded();
    const home = within(screen.getByRole("banner")).getByRole("link", { name: "Precertly" });
    expect(home).toHaveAttribute("href", "#/");
    // The word is always in the DOM; under 360 px CSS hides it visually (sr-only) behind the mark.
    expect(home.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
  });

  it("has the same navigation items as before", async () => {
    renderAt("#/cases");
    await loaded();
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(within(nav).getAllByRole("link").map((a) => a.textContent)).toEqual(["Cases", "Policies", "About"]);
  });
});
