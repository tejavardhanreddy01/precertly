import { useSyncExternalStore } from "react";

/** A parsed hash route: `#/cases/facet-01?c=criterion-id` is path ["cases","facet-01"]. */
export interface Route {
  path: string[];
  query: URLSearchParams;
}

export function parseHash(hash: string): Route {
  const raw = hash.startsWith("#") ? hash.slice(1) : hash;
  const [pathPart = "", queryPart = ""] = raw.split("?", 2);
  const path = pathPart
    .split("/")
    .filter((p) => p !== "")
    .map((p) => {
      try {
        return decodeURIComponent(p);
      } catch {
        return p;
      }
    });
  return { path, query: new URLSearchParams(queryPart) };
}

/** True for the address shapes the app has a page for; an unknown id under a known shape is still known. */
export function isKnownRoute(path: readonly string[]): boolean {
  const [first] = path;
  if (path.length === 0) return true;
  if (first === "cases" || first === "policies") return path.length <= 2;
  return first === "about" && path.length === 1;
}

export function href(path: string[], query?: Record<string, string | undefined>): string {
  const base = "#/" + path.map(encodeURIComponent).join("/");
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(query ?? {})) if (v !== undefined) params.set(k, v);
  const q = params.toString();
  return q ? `${base}?${q}` : base;
}

const listeners = new Set<() => void>();

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  window.addEventListener("hashchange", listener);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("hashchange", listener);
  };
}

/** Changes the hash without adding a history entry (used when a selection changes). */
export function replaceHash(hash: string): void {
  window.history.replaceState(null, "", hash);
  for (const l of listeners) l();
}

/** Changes the hash and adds a history entry. */
export function pushHash(hash: string): void {
  window.location.hash = hash;
}

function getHash(): string {
  return window.location.hash;
}

export function useHash(): string {
  return useSyncExternalStore(subscribe, getHash, () => "");
}
