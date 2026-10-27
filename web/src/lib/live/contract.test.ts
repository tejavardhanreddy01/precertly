import fixture from "./contract.fixture.json";
import {
  ACTIVE_STATUSES,
  DECIDED_BY,
  FINISHED_STATUSES,
  OUTCOMES,
  parseCitation,
  parseComputed,
  parseResult,
  parseRunState,
  parseStarted,
  parseVerdict,
  RUN_STATUSES,
} from "./guards";
import { MESSAGES } from "./messages";

// The fixture is built from the Pydantic models by scripts/build_live_contract.py. These tests
// read it through the web's own guards: a model change fails the Python test until the fixture is
// rebuilt, and then fails here until the guards agree.

const states = fixture.run_states as Record<string, unknown>;
const ended = fixture.ended_states as Record<string, unknown>[];

describe("the live API contract", () => {
  it("lists exactly the statuses, outcomes and deciders the web knows", () => {
    expect([...fixture.enums.statuses].sort()).toEqual([...RUN_STATUSES].sort());
    expect([...fixture.enums.outcomes].sort()).toEqual([...OUTCOMES].sort());
    expect([...fixture.enums.decided_by].sort()).toEqual([...DECIDED_BY].sort());
    expect([...ACTIVE_STATUSES, ...FINISHED_STATUSES].sort()).toEqual([...RUN_STATUSES].sort());
  });

  it("parses the started response", () => {
    expect(parseStarted(fixture.started)).toEqual({ run_id: fixture.started.run_id, status: "waking_database" });
  });

  it.each(["waking_database", "checking_limits", "ingesting", "judging", "done"])("parses a %s run", (status) => {
    const state = parseRunState(states[status]);
    expect(state).not.toBeNull();
    expect(state?.status).toBe(status);
    expect(state?.result !== null).toBe(status === "done");
  });

  it("parses every failed and rejected state, one per reason, with its fixed message", () => {
    expect(ended).toHaveLength(fixture.enums.reasons.length);
    for (const raw of ended) {
      const state = parseRunState(raw);
      expect(state).not.toBeNull();
      expect(["failed", "rejected"]).toContain(state?.status);
      expect(state?.message).toBe((fixture.messages as Record<string, string>)[state?.reason ?? ""]);
      expect(state?.result).toBeNull();
    }
  });

  it("parses the done result and carries the server's notice", () => {
    const state = parseRunState(states.done);
    expect(state?.result?.notice).toBe(fixture.notice);
    expect(state?.result?.verdicts).toHaveLength(4);
    expect(state?.result?.verdicts.some((v) => !v.applicable)).toBe(true);
  });

  it("carries a rule's computed fact, with its inputs, through the guards", () => {
    const verdicts = parseRunState(states.done)?.result?.verdicts ?? [];
    const rule = verdicts.find((v) => v.criterion === "example-criterion-e");
    expect(rule?.decided_by).toBe("rule");
    expect(rule?.computed).toEqual([
      {
        statement: "Example coded value on a synthetic date.",
        inputs: [
          "Observation/example-value",
          "Procedure/example-procedure-with-a-long-identifier-0001",
          "Observation/example-value-after-the-first-procedure-0002",
          "request.example_field",
        ],
        rule: "example-criterion-e",
        rules_version: "example-1",
        branch: null,
        cited_by_model: false,
        quote: null,
      },
    ]);
    // A verdict with no computed facts has an empty list.
    expect(verdicts.find((v) => v.criterion === "example-criterion-a")?.computed).toEqual([]);
  });

  it("keeps the markup in a rationale as plain text", () => {
    const text = parseRunState(states.done)?.result?.verdicts[0]?.rationale ?? "";
    expect(text).toContain("<b>");
  });

  it("agrees with the messages the page falls back on for the server's own wording", () => {
    // These two are repeated in the web on purpose (the page can show them with no server text).
    expect((fixture.messages as Record<string, string>).run_in_progress).toBe(MESSAGES.anotherRunInProgress);
  });

  describe("refuses anything else", () => {
    const done = states.done as Record<string, unknown>;
    const result = done.result as Record<string, unknown>;

    it.each([
      ["a string", "x"],
      ["null", null],
      ["an array", []],
      ["a number", 5],
    ])("a run state that is %s", (_n, value) => {
      expect(parseRunState(value)).toBeNull();
      expect(parseStarted(value)).toBeNull();
      expect(parseResult(value)).toBeNull();
    });

    it("an unknown status", () => {
      expect(parseRunState({ ...(states.judging as object), status: "queued" })).toBeNull();
      expect(parseStarted({ run_id: "x", status: "queued" })).toBeNull();
    });

    it("a done run with no result, and a running run with one", () => {
      expect(parseRunState({ ...done, result: null })).toBeNull();
      expect(parseRunState({ ...(states.judging as object), result: result })).toBeNull();
    });

    it("bad timings", () => {
      expect(parseRunState({ ...(states.judging as object), timings: { ingesting: "3" } })).toBeNull();
      expect(parseRunState({ ...(states.judging as object), timings: { ingesting: -1 } })).toBeNull();
      expect(parseRunState({ ...(states.judging as object), timings: null })).toBeNull();
    });

    it("a result with a missing or mistyped field", () => {
      for (const key of Object.keys(result)) {
        const copy = { ...result };
        delete copy[key];
        expect(parseResult(copy)).toBeNull();
      }
      expect(parseResult({ ...result, overall: "denied" })).toBeNull();
      expect(parseResult({ ...result, input_tokens: -1 })).toBeNull();
      expect(parseResult({ ...result, latency_ms: "fast" })).toBeNull();
      expect(parseResult({ ...result, verdicts: "none" })).toBeNull();
    });

    it("a verdict or citation with a wrong value", () => {
      const verdict = (result.verdicts as Record<string, unknown>[])[0] as Record<string, unknown>;
      expect(parseVerdict({ ...verdict, outcome: "denied" })).toBeNull();
      expect(parseVerdict({ ...verdict, applicable: "yes" })).toBeNull();
      expect(parseVerdict({ ...verdict, decided_by: "human" })).toBeNull();
      expect(parseVerdict({ ...verdict, citations: [{ chunk_id: "x" }] })).toBeNull();
      expect(parseCitation({ chunk_id: "a", quote: "q", char_start: 1.5, char_end: 3 })).toBeNull();
      expect(parseCitation({ chunk_id: "a", quote: "q", char_start: -1, char_end: 3 })).toBeNull();
    });

    it("a computed fact with a wrong value, or a verdict whose computed list is not a list", () => {
      const verdict = (result.verdicts as Record<string, unknown>[])[0] as Record<string, unknown>;
      const fact = { statement: "s", inputs: ["Observation/a"] };
      expect(parseComputed(fact)).not.toBeNull();
      expect(parseComputed({ ...fact, statement: 1 })).toBeNull();
      expect(parseComputed({ ...fact, inputs: "Observation/a" })).toBeNull();
      expect(parseComputed({ ...fact, inputs: [1] })).toBeNull();
      expect(parseComputed({ ...fact, rule: 3 })).toBeNull();
      expect(parseComputed({ ...fact, cited_by_model: "yes" })).toBeNull();
      expect(parseComputed("x")).toBeNull();
      expect(parseVerdict({ ...verdict, computed: "none" })).toBeNull();
      expect(parseVerdict({ ...verdict, computed: [{ inputs: [] }] })).toBeNull();
    });

    it("treats a verdict from a server that sends no computed list as having none", () => {
      const verdict = { ...((result.verdicts as Record<string, unknown>[])[0] as Record<string, unknown>) };
      delete verdict.computed;
      expect(parseVerdict(verdict)?.computed).toEqual([]);
    });

    it("ignores fields it does not know in a computed fact, copying none of them", () => {
      const parsed = parseComputed({ statement: "s", inputs: [], expected: "met", score: 1, correct: true });
      expect(parsed).not.toBeNull();
      expect(Object.keys(parsed ?? {}).sort()).toEqual(
        ["branch", "cited_by_model", "inputs", "quote", "rule", "rules_version", "statement"],
      );
    });

    it("ignores fields it does not know instead of copying them", () => {
      const parsed = parseRunState({ ...(states.judging as object), extra: "<script>", result: undefined });
      expect(parsed).not.toBeNull();
      expect(parsed).not.toHaveProperty("extra");
    });
  });
});
