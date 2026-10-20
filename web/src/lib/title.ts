import { useEffect } from "react";

/** Sets the document title for the current screen. */
export function useTitle(title: string): void {
  useEffect(() => {
    document.title = `${title} · Precertly recorded runs`;
  }, [title]);
}
