import { screen, within } from "@testing-library/react";
import { GATES, percent } from "../content/about";
import { DataStore } from "../lib/data";
import { tiles } from "../lib/tiles";
import type { Index } from "../lib/types";
import { diskFetch, readIndex } from "../test/fixtures";
import { loaded, renderAt } from "../test/render";

const real = readIndex();

/** A store that serves index.json with the summary replaced and everything else from disk. */
function storeWith(summary: Partial<Index["summary"]>): DataStore {
  const disk = diskFetch();
  return new DataStore(async (input) => {
    if (!input.endsWith("index.json")) return disk(input);
    return new Response(JSON.stringify({ ...real, summary: { ...real.summary, ...summary } }));
  });
}

const tile = (key: string) => within(screen.getByTestId(`tile-${key}`));

describe("landing tiles", () => {
  it("show the recorded summary from index.json", async () => {
    renderAt("");
    await loaded();
    const s = real.summary;
    expect(tile("accuracy").getByTestId("tile-value")).toHaveTextContent(percent(s.criterion_accuracy));
    expect(
      tile("accuracy").getByText(`${s.criteria_correct} of ${s.scored_criteria} verdicts match the label`),
    ).toBeInTheDocument();
    expect(tile("citations").getByTestId("tile-value")).toHaveTextContent(percent(s.citation_precision));
    expect(tile("cases").getByTestId("tile-value")).toHaveTextContent(`${s.cases_correct} of ${s.cases}`);
    expect(tile("false-approvals").getByTestId("tile-value")).toHaveTextContent(String(s.false_approvals));
    expect(
      tile("false-approvals").getByText(`of ${s.negative_labels} criteria labeled not met or insufficient`),
    ).toBeInTheDocument();
  });

  it("show the failing citation-precision gate as words and an icon", async () => {
    renderAt("");
    await loaded();
    const citations = tile("citations");
    expect(citations.getByText("Fails")).toBeInTheDocument();
    expect(citations.getByText("Gate: over 90%")).toBeInTheDocument();
    expect(tile("accuracy").getByText("Passes")).toBeInTheDocument();
    expect(tile("false-approvals").getByText("Fails")).toBeInTheDocument();
  });

  it("change when index.json changes, so nothing is hard-coded", async () => {
    renderAt(
      "",
      storeWith({
        criterion_accuracy: 0.5,
        criteria_correct: 92,
        citation_precision: 0.95,
        citations: 100,
        citations_in_label: 95,
        cases_correct: 3,
        false_approvals: 0,
        false_approval_rate: 0,
        negative_labels: 20,
      }),
    );
    await loaded();
    expect(tile("accuracy").getByTestId("tile-value")).toHaveTextContent("50%");
    expect(tile("accuracy").getByText("Fails")).toBeInTheDocument();
    expect(tile("citations").getByTestId("tile-value")).toHaveTextContent("95%");
    expect(tile("citations").getByText("Passes")).toBeInTheDocument();
    expect(tile("cases").getByTestId("tile-value")).toHaveTextContent("3 of 11");
    expect(tile("false-approvals").getByTestId("tile-value")).toHaveTextContent("0");
    expect(tile("false-approvals").getByText("Passes")).toBeInTheDocument();
  });

  it("agree with the gate table on About", () => {
    const status = (gate: string) => GATES.find((g) => g.gate === gate)?.status;
    const byKey = new Map(tiles(real.summary).map((t) => [t.key, t.gate?.status]));
    expect(byKey.get("accuracy")).toBe(status("Criterion accuracy"));
    expect(byKey.get("citations")).toBe(status("Citation precision"));
    expect(byKey.get("false-approvals")).toBe(status("False-approval rate on the eval set"));
  });
});
