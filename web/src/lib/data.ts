import { createContext, useContext, useEffect, useState } from "react";
import {
  SUPPORTED_SCHEMA_VERSION,
  type CaseFile,
  type Index,
  type PolicyFile,
} from "./types";

/** A failure to load or accept the recorded data; its message is shown to the reader. */
export class DataError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "DataError";
  }
}

const SAFE_ID = /^[A-Za-z0-9_-]+$/;

type Fetch = (input: string) => Promise<Response>;

/**
 * Reads the files written by `python -m precertly.export`. The only requests it makes are
 * same-origin GETs of ./data/*.json. Each file is fetched once and kept in memory; a failed
 * fetch is forgotten so a retry can succeed.
 */
export class DataStore {
  private cache = new Map<string, Promise<unknown>>();

  constructor(
    private readonly fetchFn: Fetch = (input) => fetch(input),
    private readonly base = "./data/",
  ) {}

  private load<T>(file: string, check: (value: unknown) => T): Promise<T> {
    const cached = this.cache.get(file);
    if (cached) return cached as Promise<T>;
    const promise = (async () => {
      let response: Response;
      try {
        response = await this.fetchFn(this.base + file);
      } catch {
        throw new DataError(`Could not fetch ${file}. The recorded data files may be missing.`);
      }
      if (!response.ok) {
        throw new DataError(`Could not fetch ${file} (HTTP ${response.status}).`);
      }
      let value: unknown;
      try {
        value = await response.json();
      } catch {
        throw new DataError(`${file} is not valid JSON.`);
      }
      return check(value);
    })();
    promise.catch(() => this.cache.delete(file));
    this.cache.set(file, promise);
    return promise;
  }

  index(): Promise<Index> {
    return this.load("index.json", (value) => {
      const index = value as Partial<Index> | null;
      const version = index?.schema_version;
      if (version !== SUPPORTED_SCHEMA_VERSION) {
        throw new DataError(
          `This site was built for data schema version ${SUPPORTED_SCHEMA_VERSION}, but the data files are version ${String(version)}. Rebuild the site against the same export.`,
        );
      }
      return index as Index;
    });
  }

  policy(id: string): Promise<PolicyFile> {
    if (!SAFE_ID.test(id)) return Promise.reject(new DataError(`No such policy: ${id}.`));
    return this.load(`policies/${id}.json`, (value) => value as PolicyFile);
  }

  case(id: string): Promise<CaseFile> {
    if (!SAFE_ID.test(id)) return Promise.reject(new DataError(`No such case: ${id}.`));
    return this.load(`cases/${id}.json`, (value) => value as CaseFile);
  }
}

export const StoreContext = createContext<DataStore | null>(null);

export function useStore(): DataStore {
  const store = useContext(StoreContext);
  if (!store) throw new Error("StoreContext is missing");
  return store;
}

export type Loaded<T> =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; value: T };

/** Runs `load` when `key` changes and reports its state. */
export function useLoaded<T>(key: string, load: () => Promise<T>): Loaded<T> {
  const [state, setState] = useState<{ key: string; result: Loaded<T> }>({
    key,
    result: { status: "loading" },
  });
  useEffect(() => {
    let live = true;
    load().then(
      (value) => live && setState({ key, result: { status: "ready", value } }),
      (error: unknown) =>
        live &&
        setState({
          key,
          result: {
            status: "error",
            message: error instanceof DataError ? error.message : "Something went wrong loading the data.",
          },
        }),
    );
    return () => {
      live = false;
    };
    // `load` is recreated on each render; the key identifies what is loaded.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  // A stale result for another key counts as loading.
  return state.key === key ? state.result : { status: "loading" };
}
