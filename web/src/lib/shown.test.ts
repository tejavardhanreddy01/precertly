import { allCases, readIndex } from "../test/fixtures";
import { NOT_SHOWN_BADGE, SHOWN_BADGE, shownView } from "./shown";
import type { CaseFile, Verdict } from "./types";

const index = readIndex();
const topK = index.run.retrieval.top_k;
const scored = allCases().filter((c): c is CaseFile & { result: NonNullable<CaseFile["result"]> } => !!c.result);

function find(caseId: string, criterion: string) {
  const file = scored.find((c) => c.id === caseId);
  const verdict = file?.result.verdicts.find((v) => v.criterion === criterion);
  if (!file || !verdict) throw new Error(`no verdict ${criterion} in ${caseId}`);
  return { file, verdict };
}

describe("the recorded data behind the shown-to-the-model badges", () => {
  it("matches the sentence for every verdict a model saw: top-k retrieved chunks plus every request field", () => {
    expect(topK).toBeGreaterThan(0);
    let checked = 0;
    for (const file of scored) {
      const requestIds = file.chart.filter((k) => k.source === "request").map((k) => k.chunk_id);
      const chartIds = new Set(file.chart.map((k) => k.chunk_id));
      for (const v of file.result.verdicts) {
        if (v.decided_by === "rule") continue;
        expect(v.shown_chunk_ids.every((id) => chartIds.has(id)), `${file.id} ${v.criterion}`).toBe(true);
        const shownRequest = v.shown_chunk_ids.filter((id) => requestIds.includes(id));
        expect(shownRequest.sort(), `${file.id} ${v.criterion}`).toEqual([...requestIds].sort());
        expect(v.shown_chunk_ids.length - shownRequest.length, `${file.id} ${v.criterion}`).toBe(topK);
        const view = shownView(v, file.chart, topK);
        expect(view?.line).toBe(
          `Badges show what the model was given for the selected criterion (the top ${topK} retrieved chunks plus the request fields).`,
        );
        checked += 1;
      }
    }
    expect(checked).toBeGreaterThan(100);
  });

  it("makes no model call for a verdict a rule decided alone, and a call for every other", () => {
    for (const file of scored) {
      for (const v of file.result.verdicts) {
        expect(v.prompt_sha256 === null, `${file.id} ${v.criterion}`).toBe(v.decided_by === "rule");
      }
    }
  });
});

describe("shownView", () => {
  it("is null without a verdict (unscored case or nothing selected)", () => {
    const { file } = find("facet-11", "conservative-care-failed");
    expect(shownView(undefined, file.chart, topK)).toBeNull();
  });

  it("badges the chunks the model was given and none of the others", () => {
    const { file, verdict } = find("facet-11", "conservative-care-failed");
    const view = shownView(verdict, file.chart, topK);
    expect(view?.kind).toBe("badges");
    if (view?.kind !== "badges") return;
    expect(view.shown.size).toBe(verdict.shown_chunk_ids.length);
    expect(view.shown.has("DocumentReference/note-pt-records#0")).toBe(false);
    expect(view.shown.has("DocumentReference/note-pain-clinic-eval#0")).toBe(true);
    expect(SHOWN_BADGE).toBe("shown to the model");
    expect(NOT_SHOWN_BADGE).toBe("not shown to the model");
  });

  it("says a rule-only verdict showed the model nothing", () => {
    const { file, verdict } = find("facet-02", "two-mbbs-80pct-sustained-relief");
    expect(verdict.decided_by).toBe("rule");
    const view = shownView(verdict, file.chart, topK);
    expect(view?.kind).toBe("rule_only");
    expect(view?.line).toMatch(/model was not called/);
  });

  it("does not print the top-k sentence when the recorded chunks do not match it", () => {
    const { file, verdict } = find("facet-11", "conservative-care-failed");
    const fewer: Verdict = { ...verdict, shown_chunk_ids: verdict.shown_chunk_ids.slice(0, -1) };
    const view = shownView(fewer, file.chart, topK);
    expect(view?.line).not.toContain(`the top ${topK} retrieved chunks plus the request fields`);
    expect(view?.line).toContain(`retrieval limit was ${topK}`);
    expect(shownView(verdict, file.chart, topK + 1)?.line).not.toContain("the top");
  });
});
