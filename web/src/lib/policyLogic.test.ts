import { readPolicy } from "../test/fixtures";
import { collapseLogic, ownCriteria, requiredItems, sharedCriteria } from "./policyLogic";
import type { Logic, PolicyVariant } from "./types";

function variant(id: string, logic: Logic): PolicyVariant {
  return { id, title: id, codes: [], logic };
}

describe("sharedCriteria", () => {
  it("is the intersection of the plain items every variant requires", () => {
    const vs = [
      variant("a", { all_of: ["x", "y", "only-a"] }),
      variant("b", { all_of: ["y", "only-b", "x"] }),
      variant("c", { all_of: ["x", "z", "y"] }),
    ];
    expect(sharedCriteria(vs)).toEqual(["x", "y"]);
    expect(ownCriteria(vs[0]!, ["x", "y"])).toEqual(["only-a"]);
    expect(ownCriteria(vs[2]!, ["x", "y"])).toEqual(["z"]);
  });

  it("follows the given policy order, else the first variant's", () => {
    const vs = [variant("a", { all_of: ["x", "y", "z"] }), variant("b", { all_of: ["z", "y", "x"] })];
    expect(sharedCriteria(vs)).toEqual(["x", "y", "z"]);
    expect(sharedCriteria(vs, ["z", "x", "y"])).toEqual(["z", "x", "y"]);
  });

  it("has no shared section for a single variant, or for none", () => {
    expect(sharedCriteria([variant("a", { all_of: ["x", "y"] })])).toEqual([]);
    expect(sharedCriteria([])).toEqual([]);
  });

  it("never hoists a member of an any_of, even if every variant has it", () => {
    const vs = [
      variant("a", { all_of: ["x", { any_of: ["p", "q"] }] }),
      variant("b", { all_of: ["x", { any_of: ["p", "q"] }, "extra"] }),
    ];
    expect(sharedCriteria(vs)).toEqual(["x"]);
    // The group stays whole in each variant's own list.
    expect(ownCriteria(vs[0]!, ["x"])).toEqual([{ any_of: ["p", "q"] }]);
    expect(ownCriteria(vs[1]!, ["x"])).toEqual([{ any_of: ["p", "q"] }, "extra"]);
  });

  it("does not count an id a variant reaches only through an any_of", () => {
    const vs = [variant("a", { all_of: ["x", "p"] }), variant("b", { all_of: ["x", { any_of: ["p", "q"] }] })];
    expect(sharedCriteria(vs)).toEqual(["x"]);
  });

  it("shares nothing when a variant's top level is an any_of", () => {
    const vs = [variant("a", { all_of: ["x", "y"] }), variant("b", { any_of: ["x", "y"] })];
    expect(sharedCriteria(vs)).toEqual([]);
    expect(requiredItems(vs[1]!)).toBeNull();
    expect(ownCriteria(vs[1]!, [])).toEqual([{ any_of: ["x", "y"] }]);
  });

  it("does not look into an all_of that sits inside an any_of", () => {
    const vs = [
      variant("a", { all_of: ["x", { any_of: [{ all_of: ["p", "q"] }, "r"] }] }),
      variant("b", { all_of: ["x", { any_of: [{ all_of: ["p", "q"] }, "r"] }] }),
    ];
    expect(sharedCriteria(vs)).toEqual(["x"]);
    expect(ownCriteria(vs[0]!, ["x"])).toEqual([{ any_of: [{ all_of: ["p", "q"] }, "r"] }]);
  });

  it("treats an all_of inside the top-level all_of as the same list", () => {
    const vs = [
      variant("a", { all_of: [{ all_of: ["x", "y"] }, "only-a"] }),
      variant("b", { all_of: [{ all_of: ["x", "y"] }, "only-b"] }),
    ];
    expect(sharedCriteria(vs)).toEqual(["x", "y"]);
    expect(ownCriteria(vs[0]!, ["x", "y"])).toEqual(["only-a"]);
  });

  it("handles a nested all_of that has one child, at any depth", () => {
    const vs = [
      variant("a", { all_of: [{ all_of: [{ all_of: ["x"] }] }, "only-a"] }),
      variant("b", { all_of: [{ all_of: ["x"] }, "only-b"] }),
    ];
    expect(sharedCriteria(vs)).toEqual(["x"]);
    expect(ownCriteria(vs[0]!, ["x"])).toEqual(["only-a"]);
  });

  it("lists a variant with nothing of its own as empty", () => {
    const vs = [variant("a", { all_of: ["x", "y"] }), variant("b", { all_of: ["x", "y", "z"] })];
    expect(ownCriteria(vs[0]!, sharedCriteria(vs))).toEqual([]);
  });
});

describe("collapseLogic", () => {
  it("removes a group that only wraps a group of the same kind", () => {
    expect(collapseLogic({ all_of: [{ all_of: ["a", "b"] }] })).toEqual({ all_of: ["a", "b"] });
    expect(collapseLogic({ any_of: [{ any_of: [{ any_of: ["a", "b"] }] }] })).toEqual({ any_of: ["a", "b"] });
  });

  it("keeps a wrapper of another kind, and groups with more than one child", () => {
    expect(collapseLogic({ all_of: [{ any_of: ["a", "b"] }] })).toEqual({ all_of: [{ any_of: ["a", "b"] }] });
    expect(collapseLogic({ all_of: ["a", { all_of: ["b", "c"] }] })).toEqual({ all_of: ["a", { all_of: ["b", "c"] }] });
  });
});

describe("the exported policies", () => {
  const load = (id: string) => readPolicy(id);
  const allIds = (logic: Logic): string[] =>
    (logic.all_of ?? logic.any_of ?? []).flatMap((i) => (typeof i === "string" ? [i] : allIds(i)));

  it("shares 15 of the 32 facet criteria and leaves each procedure its own", () => {
    const policy = load("facet-joint-interventions");
    const shared = sharedCriteria(policy.variants, policy.criteria.map((c) => c.id));
    expect(policy.criteria).toHaveLength(32);
    expect(shared).toHaveLength(15);
    const owns = Object.fromEntries(
      policy.variants.map((v) => [v.id, ownCriteria(v, shared).map((i) => (typeof i === "string" ? i : "group"))]),
    );
    expect(Object.fromEntries(Object.entries(owns).map(([id, own]) => [id, own.length]))).toEqual({
      "first-diagnostic": 4,
      "second-diagnostic": 8,
      "therapeutic-injection": 6,
      "initial-rfa": 4,
      "repeat-rfa": 4,
    });
    for (const own of Object.values(owns)) for (const id of shared) expect(own).not.toContain(id);
  });

  it("is derived from the data: shared plus own is exactly what each variant names", () => {
    for (const id of ["facet-joint-interventions", "bariatric-surgery", "pap-sleep-apnea", "spinal-cord-stimulator"]) {
      const policy = load(id);
      const shared = sharedCriteria(policy.variants, policy.criteria.map((c) => c.id));
      for (const v of policy.variants) {
        const own = ownCriteria(v, shared);
        const named = [...shared, ...own.flatMap((i) => (typeof i === "string" ? [i] : allIds(i)))];
        expect(new Set(named)).toEqual(new Set(allIds(v.logic)));
        expect(named).toHaveLength(allIds(v.logic).length);
      }
    }
  });

  it("shares nothing in the bariatric policy, whose two variants have four criteria each", () => {
    const policy = load("bariatric-surgery");
    expect(sharedCriteria(policy.variants)).toEqual([]);
    expect(policy.variants.map((v) => ownCriteria(v, []).length)).toEqual([4, 4]);
  });

  it("keeps the PAP initial any_of together in that variant's own list", () => {
    const policy = load("pap-sleep-apnea");
    const shared = sharedCriteria(policy.variants);
    const initial = policy.variants.find((v) => v.id === "initial")!;
    const own = ownCriteria(initial, shared);
    expect(own.filter((i) => typeof i !== "string")).toHaveLength(1);
    for (const member of allIds(own.find((i) => typeof i !== "string") as Logic)) expect(shared).not.toContain(member);
  });
});
