import { screen, within } from "@testing-library/react";
import { readCase } from "../test/fixtures";
import { loaded, renderAt } from "../test/render";
import { countVerdicts } from "../lib/verdicts";

const count = (key: string): number => {
  const el = within(screen.getByTestId("counts")).getByText((_, node) => node?.getAttribute("data-count") === key);
  return Number(el.textContent);
};

/** Counted straight from the recorded verdicts, without the app's own helper. */
function expected(id: string) {
  const verdicts = readCase(id).result?.verdicts ?? [];
  const invalidated = verdicts.filter((v) => v.invalidated || v.invalidation);
  const rest = verdicts.filter((v) => !(v.invalidated || v.invalidation));
  return {
    total: verdicts.length,
    met: rest.filter((v) => v.applicable && v.outcome === "met").length,
    not_met: rest.filter((v) => v.applicable && v.outcome === "not_met").length,
    insufficient: rest.filter((v) => v.applicable && v.outcome === "insufficient").length,
    invalidated: invalidated.length,
    not_applicable: rest.filter((v) => !v.applicable).length,
    wrong: verdicts.filter((v) => !v.correct).length,
  };
}

describe("summary bar counts", () => {
  it("facet-11: 13 met, 3 not applicable, one wrong verdict that is a false approval", async () => {
    renderAt("#/cases/facet-11");
    await loaded();
    await screen.findByTestId("summary-bar");
    const want = expected("facet-11");
    expect(want).toMatchObject({
      total: 16,
      met: 13,
      not_met: 0,
      insufficient: 0,
      invalidated: 0,
      not_applicable: 3,
      wrong: 1,
    });
    expect(count("met")).toBe(want.met);
    expect(count("not_met")).toBe(want.not_met);
    expect(count("insufficient")).toBe(want.insufficient);
    expect(count("invalidated")).toBe(want.invalidated);
    expect(count("not_applicable")).toBe(want.not_applicable);
    expect(count("wrong")).toBe(want.wrong);
    const bar = screen.getByTestId("summary-bar");
    expect(within(bar).getByRole("heading", { level: 1 })).toHaveTextContent("facet-11");
    expect(within(bar).getByTestId("policy-link")).toHaveTextContent("Facet Joint Interventions for Pain Management");
    // The overall result is Met against an expected Insufficient: a false approval, and incorrect.
    expect(within(within(bar).getByTestId("overall")).getByText("Met")).toBeInTheDocument();
    expect(bar.querySelector("[data-false-approval]")).toHaveTextContent("False approval");
    expect(bar.querySelector('[data-outcome="insufficient"]')).not.toBeNull();
    expect(bar.querySelector('[data-correct="false"]')).toHaveTextContent("Incorrect");
    expect(bar).toHaveTextContent("Recorded run");
  });

  it("facet-01: one verdict invalidated by verification, counted apart from insufficient", async () => {
    renderAt("#/cases/facet-01");
    await loaded();
    await screen.findByTestId("summary-bar");
    const want = expected("facet-01");
    expect(want).toMatchObject({ total: 16, met: 14, invalidated: 1, not_applicable: 1, wrong: 1 });
    expect(count("met")).toBe(14);
    expect(count("invalidated")).toBe(1);
    expect(count("insufficient")).toBe(0);
    expect(count("not_met")).toBe(0);
    expect(count("not_applicable")).toBe(1);
    expect(count("wrong")).toBe(1);
    const bar = screen.getByTestId("summary-bar");
    expect(within(within(bar).getByTestId("overall")).getByText("Insufficient")).toBeInTheDocument();
    expect(bar.querySelector("[data-false-approval]")).toBeNull();
  });

  it("partition the verdicts: the five states add up to the criteria judged", () => {
    for (const id of ["facet-01", "facet-02", "facet-11"]) {
      const verdicts = readCase(id).result?.verdicts ?? [];
      const c = countVerdicts(verdicts);
      expect(c.met + c.not_met + c.insufficient + c.invalidated + c.not_applicable).toBe(verdicts.length);
      expect(c).toMatchObject(expected(id) && { wrong: expected(id).wrong });
    }
  });

  it("an unscored case has no counts to show, only its labels", async () => {
    renderAt("#/cases/pap-01");
    await loaded();
    await screen.findByTestId("summary-bar");
    expect(screen.getByTestId("summary-bar")).toHaveTextContent(/Labels only: \d+ criteria labeled/);
    expect(screen.getByTestId("summary-bar")).toHaveTextContent("labels only, not yet scored");
    expect(document.querySelector("[data-count]")).toBeNull();
  });
});
