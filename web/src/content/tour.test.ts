import { allCases, readIndex } from "../test/fixtures";
import type { CaseFile, Verdict } from "../lib/types";
import { TOUR, tourHref } from "./tour";

const cases = allCases(); // sorted by id
const index = readIndex();
const verdicts = (c: CaseFile): Verdict[] => c.result?.verdicts ?? [];

function step(n: number) {
  const s = TOUR[n - 1];
  if (!s) throw new Error(`no tour step ${n}`);
  const file = cases.find((c) => c.id === s.caseId);
  if (!file) throw new Error(`step ${n}: no case ${s.caseId}`);
  const verdict = verdicts(file).find((v) => v.criterion === s.criterion);
  if (!verdict) throw new Error(`step ${n}: no verdict ${s.criterion} in ${s.caseId}`);
  return { s, file, verdict };
}

describe("the tour", () => {
  it("has four steps in order, each a link to a case and criterion", () => {
    expect(TOUR.map((s) => s.kind)).toEqual(["verified-citation", "rule-computed", "false-approval", "invalidated"]);
    for (const [i, s] of TOUR.entries()) {
      expect(tourHref(i + 1)).toBe(`#/cases/${s.caseId}?c=${s.criterion}&tour=${i + 1}`);
      expect(s.caption.split(/(?<=[.!?])\s+/)).toHaveLength(1); // one sentence
      expect(index.cases.some((c) => c.id === s.caseId && c.scored)).toBe(true);
    }
  });

  it("step 1 is the first correct case with no invalidated verdict, and a verified met citation", () => {
    const { s, file, verdict } = step(1);
    const first = cases.find((c) => c.result?.overall_correct && !verdicts(c).some((v) => v.invalidated));
    expect(file.id).toBe(first?.id);
    const firstMatch = verdicts(file).find(
      (v) => v.decided_by === "model" && v.outcome === "met" && v.applicable && v.citations.some((c) => c.located),
    );
    expect(verdict.criterion).toBe(firstMatch?.criterion);
    expect(s.criterion).toBe(verdict.criterion);
    expect(verdict.invalidated).toBeNull();
    expect(verdict.citations.filter((c) => c.located)).toHaveLength(2); // "two passages"
    expect(verdict.correct).toBe(true);
  });

  it("step 2 is a rule-only verdict with a computed fact that matches its caption", () => {
    const { s, file, verdict } = step(2);
    const matches = (v: Verdict) => v.decided_by === "rule" && v.computed.length > 0 && v.outcome === "not_met";
    const first = cases.find((c) => verdicts(c).some(matches));
    expect(file.id).toBe(first?.id);
    expect(verdict.criterion).toBe(verdicts(file).find(matches)?.criterion);
    expect(s.criterion).toBe(verdict.criterion);
    const statement = verdict.computed[0]?.statement ?? "";
    expect(statement).toContain("86%");
    expect(statement).toContain("71%, below 80%");
    for (const figure of ["86%", "71%", "80%"]) expect(s.caption).toContain(figure);
  });

  it("step 3 is the one false approval in the run, whose contradicting records were not shown", () => {
    const { s, file, verdict } = step(3);
    const flagged = cases.flatMap((c) => verdicts(c).filter((v) => v.false_approval).map((v) => [c.id, v.criterion]));
    expect(flagged).toEqual([[file.id, s.criterion]]);
    expect(index.summary.false_approvals).toBe(1);
    expect(verdict.outcome).toBe("met");
    expect(verdict.expected).not.toBe("met");
    // The caption: a note claims 12 weeks of therapy; the therapy records say 2 of 12 sessions.
    const chunkText = (id: string) => file.chart.find((k) => k.chunk_id === id)?.text ?? "";
    expect(verdict.citations.some((c) => chunkText(c.chunk_id).includes("12 weeks of physical therapy"))).toBe(true);
    const records = file.chart.filter((k) => k.text.includes("attended 2 of the 12 sessions"));
    expect(records.length).toBeGreaterThan(0);
    for (const r of records) expect(verdict.shown_chunk_ids).not.toContain(r.chunk_id);
  });

  it("step 4 is the first invalidated verdict: met by the model, insufficient after verification", () => {
    const { s, file, verdict } = step(4);
    const first = cases.find((c) => verdicts(c).some((v) => v.invalidated));
    expect(file.id).toBe(first?.id);
    expect(verdict.criterion).toBe(verdicts(file).find((v) => v.invalidated)?.criterion);
    expect(s.criterion).toBe(verdict.criterion);
    expect(verdict.model_outcome).toBe("met");
    expect(verdict.outcome).toBe("insufficient");
    expect(verdict.invalidated).toMatch(/quote not found/);
    expect(verdict.citations).toHaveLength(0);
  });
});
