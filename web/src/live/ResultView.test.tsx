import { render, screen, within } from "@testing-library/react";
import { ResultView } from "./ResultView";
import type { LiveResult, LiveVerdict } from "../lib/live/guards";

function verdict(over: Partial<LiveVerdict>): LiveVerdict {
  return {
    criterion: "c",
    outcome: "insufficient",
    applicable: true,
    decided_by: "model",
    confidence: 0,
    rationale: "r",
    citations: [],
    computed: [],
    ...over,
  };
}

function show(verdicts: LiveVerdict[]) {
  const result: LiveResult = {
    case_id: "facet-01",
    policy: "p",
    variant: "v",
    overall: "insufficient",
    verdicts,
    reviewer_checklist: [],
    input_tokens: 1,
    output_tokens: 1,
    latency_ms: 1,
    notice: "n",
  };
  render(<ResultView result={result} timings={{}} />);
  return (criterion: string) =>
    screen.getAllByTestId("live-verdict").find((li) => li.getAttribute("data-criterion") === criterion) as HTMLElement;
}

const fact = {
  statement: "Coded disability score 48 % on 2026-08-10, before 2026-08-24.",
  inputs: ["Observation/odi-baseline"],
  rule: "disability-scale-at-baseline",
  rules_version: "v1",
  branch: null,
  cited_by_model: false,
  quote: null,
};

describe("a verdict's evidence on the Live result", () => {
  it("shows a rule's computed fact with the ids it read, and does not say there is no citation", () => {
    const row = show([verdict({ criterion: "a", outcome: "met", decided_by: "rule", computed: [fact] })])("a");
    const list = within(row).getByRole("list", { name: "Computed facts for a" });
    expect(list).toHaveTextContent(fact.statement);
    expect(list).toHaveTextContent("Rule disability-scale-at-baseline (rules version v1)");
    expect(list).toHaveTextContent("not cited by the model");
    expect(list).toHaveTextContent("Inputs: Observation/odi-baseline");
    expect(row).toHaveTextContent("No quote from the chart: the computed facts above are the evidence.");
    expect(row).not.toHaveTextContent("No citation.");
  });

  it("says a rule verdict with no computed fact has no quote because a rule decided it", () => {
    const row = show([verdict({ criterion: "b", outcome: "met", decided_by: "rule" })])("b");
    expect(within(row).queryByTestId("live-computed")).toBeNull();
    expect(within(row).getByTestId("live-no-quote")).toHaveTextContent("No quote: decided by a rule from coded data.");
  });

  it("does not invent an id for a fact that has none", () => {
    const row = show([
      verdict({ criterion: "c1", outcome: "met", decided_by: "rule", computed: [{ ...fact, inputs: [] }] }),
    ])("c1");
    expect(row).toHaveTextContent(fact.statement);
    expect(row).not.toHaveTextContent("Inputs:");
    expect(row).toHaveTextContent("No quote: decided by a rule from coded data.");
  });

  it("keeps the plain No citation. for a model verdict that is insufficient", () => {
    const row = show([verdict({ criterion: "d" })])("d");
    expect(within(row).getByTestId("live-no-quote")).toHaveTextContent(/^No citation\.$/);
  });

  it("shows a partial path's branch and that the model cited it, next to the quote", () => {
    const row = show([
      verdict({
        criterion: "e",
        outcome: "met",
        decided_by: "rule+model",
        computed: [{ ...fact, branch: "2a", cited_by_model: true, quote: "q" }],
        citations: [{ chunk_id: "DocumentReference/n#0", quote: "a quote", char_start: 1, char_end: 8 }],
      }),
    ])("e");
    expect(row).toHaveTextContent("branch 2a");
    expect(row).toHaveTextContent("cited by the model");
    expect(within(row).getByText("a quote")).toBeInTheDocument();
    expect(within(row).queryByTestId("live-no-quote")).toBeNull();
  });

  it("renders a statement or id that looks like markup as text", () => {
    const row = show([
      verdict({ criterion: "f", decided_by: "rule", computed: [{ ...fact, statement: "<img src=x onerror=alert(1)>", inputs: ["<b>x</b>"] }] }),
    ])("f");
    expect(row.querySelector("img")).toBeNull();
    expect(row.querySelector("b")).toBeNull();
    expect(row).toHaveTextContent("<img src=x onerror=alert(1)>");
  });
});
