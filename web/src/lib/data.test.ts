import { DataError, DataStore } from "./data";
import { allCases, diskFetch, readIndex } from "../test/fixtures";

const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });

describe("DataStore", () => {
  it("rejects an index with another schema version", async () => {
    const store = new DataStore(async () => json({ schema_version: 2 }));
    await expect(store.index()).rejects.toThrow(/schema version 1.*version 2/);
  });

  it("rejects an index with no schema version", async () => {
    const store = new DataStore(async () => json({}));
    await expect(store.index()).rejects.toBeInstanceOf(DataError);
  });

  it("reports an HTTP failure and a network failure readably", async () => {
    await expect(new DataStore(async () => json({}, 404)).index()).rejects.toThrow(/HTTP 404/);
    const failing = new DataStore(async () => {
      throw new TypeError("network");
    });
    await expect(failing.index()).rejects.toThrow(/Could not fetch index.json/);
  });

  it("reports a body that is not JSON", async () => {
    const store = new DataStore(async () => new Response("<html>", { status: 200 }));
    await expect(store.index()).rejects.toThrow(/not valid JSON/);
  });

  it("refuses ids that are not plain slugs, without fetching", async () => {
    const fetchFn = vi.fn(diskFetch());
    const store = new DataStore(fetchFn);
    await expect(store.case("../index")).rejects.toThrow(/No such case/);
    await expect(store.policy("a/b")).rejects.toThrow(/No such policy/);
    expect(fetchFn).not.toHaveBeenCalled();
  });

  it("fetches only same-origin ./data/ files, each once", async () => {
    const fetchFn = vi.fn(diskFetch());
    const store = new DataStore(fetchFn);
    await store.index();
    await store.case("facet-01");
    await store.case("facet-01");
    await store.policy("facet-joint-interventions");
    expect(fetchFn.mock.calls.map((c) => c[0])).toEqual([
      "./data/index.json",
      "./data/cases/facet-01.json",
      "./data/policies/facet-joint-interventions.json",
    ]);
  });

  it("forgets a failed fetch so a retry can succeed", async () => {
    let calls = 0;
    const store = new DataStore(async () => {
      calls += 1;
      return calls === 1 ? json({}, 500) : json({ schema_version: 1 });
    });
    await expect(store.index()).rejects.toThrow();
    await expect(store.index()).resolves.toMatchObject({ schema_version: 1 });
  });
});

describe("the recorded data", () => {
  it("has the schema version this UI was built for and 29 cases", () => {
    const index = readIndex();
    expect(index.schema_version).toBe(1);
    expect(index.cases).toHaveLength(29);
    expect(allCases()).toHaveLength(29);
  });
});
