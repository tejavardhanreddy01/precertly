import { href, isKnownRoute, parseHash } from "./router";

describe("hash routes", () => {
  it("parses a path and a query", () => {
    const r = parseHash("#/cases/facet-01?c=pain-minimum-3-months&tour=2");
    expect(r.path).toEqual(["cases", "facet-01"]);
    expect(r.query.get("c")).toBe("pain-minimum-3-months");
    expect(r.query.get("tour")).toBe("2");
  });

  it("treats the empty hash and the bare slash as the root", () => {
    expect(parseHash("").path).toEqual([]);
    expect(parseHash("#/").path).toEqual([]);
  });

  it("builds hrefs that start with a hash sign and skip undefined query values", () => {
    expect(href(["cases"])).toBe("#/cases");
    expect(href(["cases", "facet-01"], { c: "a b", tour: undefined })).toBe("#/cases/facet-01?c=a+b");
  });

  it("round-trips", () => {
    const r = parseHash(href(["policies", "facet-joint-interventions"], { c: "x" }));
    expect(r.path).toEqual(["policies", "facet-joint-interventions"]);
    expect(r.query.get("c")).toBe("x");
  });

  it("does not throw on a malformed escape", () => {
    expect(parseHash("#/cases/%E0%A4%A").path).toEqual(["cases", "%E0%A4%A"]);
  });
});

describe("isKnownRoute", () => {
  it("accepts the shapes the app has a page for, whatever the ids", () => {
    for (const path of [[], ["cases"], ["cases", "facet-01"], ["cases", "nope"], ["policies"], ["policies", "x"], ["about"]]) {
      expect(isKnownRoute(path)).toBe(true);
    }
  });

  it("rejects anything else", () => {
    for (const path of [["nope"], ["policies", "a", "b"], ["cases", "a", "b"], ["about", "x"], ["Cases"]]) {
      expect(isKnownRoute(path)).toBe(false);
    }
  });
});
