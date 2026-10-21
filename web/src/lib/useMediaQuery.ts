import { useSyncExternalStore } from "react";

/** The width at which the case workspace shows three panes side by side instead of three tabs. */
export const WIDE_QUERY = "(min-width: 1024px)";

/** Whether a media query matches now, and again whenever that changes. */
export function useMediaQuery(query: string): boolean {
  return useSyncExternalStore(
    (notify) => {
      const list = window.matchMedia(query);
      list.addEventListener("change", notify);
      return () => list.removeEventListener("change", notify);
    },
    () => window.matchMedia(query).matches,
    () => false,
  );
}
